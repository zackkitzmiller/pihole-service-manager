import os
import sqlite3
import subprocess

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(
    directory="templates"
)

DB_FILE = os.getenv("DB_FILE", "data/app.db")

PIHOLE_URL = os.getenv("PIHOLE_URL")
PIHOLE_PASSWORD = os.getenv("PIHOLE_PASSWORD")

PIHOLE_SID = None


SCHEMA = """
create table if not exists services(
    id text primary key,
    name text not null,
    category text not null
);

create table if not exists service_domains(
    service_id text not null,
    domain text not null
);

create index if not exists service_domains_service_id
    on service_domains(service_id);

create table if not exists state(
    service_id text primary key,
    enabled integer not null default 1
);

create table if not exists service_groups(
    service_id text not null,
    group_id integer not null,
    primary key(service_id, group_id)
);

create table if not exists catalog_meta(
    key text primary key,
    value text
);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)

    conn = db()
    conn.executescript(SCHEMA)
    conn.close()


init_db()


def get_domains(service_id: str) -> list[str]:

    conn = db()

    rows = conn.execute(
        """
        select domain
        from service_domains
        where service_id = ?
        order by domain
        """,
        (service_id,)
    ).fetchall()

    conn.close()

    return [x["domain"] for x in rows]


def get_group_ids(conn: sqlite3.Connection, service_id: str) -> list[int]:

    rows = conn.execute(
        """
        select group_id
        from service_groups
        where service_id = ?
        order by group_id
        """,
        (service_id,)
    ).fetchall()

    return [row["group_id"] for row in rows]


def pihole_login() -> str:

    r = requests.post(
        f"{PIHOLE_URL}/api/auth",
        json={
            "password": PIHOLE_PASSWORD
        },
        timeout=15
    )

    r.raise_for_status()

    data = r.json()

    return data["session"]["sid"]


def get_sid() -> str:

    global PIHOLE_SID

    if PIHOLE_SID:
        return PIHOLE_SID

    PIHOLE_SID = pihole_login()

    return PIHOLE_SID


def reset_sid() -> None:

    global PIHOLE_SID

    PIHOLE_SID = None


def pihole_request(method: str, path: str, **kwargs) -> requests.Response:
    """Call the Pi-hole API, logging in again once if the session expired."""

    url = f"{PIHOLE_URL}/api/{path}"

    r = requests.request(
        method,
        url,
        headers={"X-FTL-SID": get_sid()},
        timeout=15,
        **kwargs
    )

    if r.status_code in (401, 403):

        reset_sid()

        r = requests.request(
            method,
            url,
            headers={"X-FTL-SID": get_sid()},
            timeout=15,
            **kwargs
        )

    return r


@app.get("/")
async def index(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/api/services")
async def services():

    conn = db()

    rows = conn.execute(
        """
        select

            s.id,
            s.name,
            s.category,

            count(sd.domain) as domain_count,

            coalesce(st.enabled,0) as enabled

        from services s

        left join service_domains sd
            on sd.service_id = s.id

        left join state st
            on st.service_id = s.id

        group by
            s.id,
            s.name,
            s.category,
            st.enabled

        order by
            s.category,
            s.name
        """
    ).fetchall()

    stats = conn.execute(
        """
        select
            (select count(*) from services) as services,
            (select count(*) from service_domains) as domains,
            (select count(*) from state) as enabled
        """
    ).fetchone()

    conn.close()

    return JSONResponse(
        {
            "stats": dict(stats),
            "services": [dict(x) for x in rows]
        }
    )


@app.post("/api/toggle/{service_id}")
def toggle(service_id: str):

    conn = db()

    try:

        state = conn.execute(
            """
            select enabled
            from state
            where service_id = ?
            """,
            (service_id,)
        ).fetchone()

        enabled = bool(state["enabled"]) if state else False

        domains = get_domains(service_id)

        # Pi-hole's "Default" group (id 0) when none are assigned.
        groups = get_group_ids(conn, service_id) or [0]

        if enabled:

            conn.execute(
                """
                delete
                from state
                where service_id = ?
                """,
                (service_id,)
            )

            for domain in domains:
                pihole_request("DELETE", f"domains/deny/exact/{domain}")

            new_state = False

        else:

            conn.execute(
                """
                insert into state(service_id,enabled)
                values(?,1)

                on conflict(service_id)
                do update set enabled=1
                """,
                (service_id,)
            )

            for domain in domains:
                pihole_request(
                    "POST",
                    "domains/deny/exact",
                    json={
                        "domain": domain,
                        "groups": groups,
                        "comment": "Service Manager V3"
                    }
                )

            new_state = True

        conn.commit()

        return JSONResponse(
            {
                "success": True,
                "service_id": service_id,
                "enabled": new_state,
                "domain_count": len(domains)
            }
        )

    except Exception as ex:  # noqa: BLE001 - report any failure as JSON

        conn.rollback()

        return JSONResponse(
            {
                "success": False,
                "error": str(ex)
            },
            status_code=500
        )

    finally:

        conn.close()


@app.post("/api/catalog/update")
def catalog_update():

    try:

        result = subprocess.run(
            ["python3", "catalog_update.py"],
            cwd="/app",
            capture_output=True,
            check=False,
            text=True,
            timeout=300
        )

        if result.returncode != 0:

            return JSONResponse(
                {
                    "success": False,
                    "error": result.stderr
                },
                status_code=500
            )

        conn = db()

        rows = conn.execute("select key, value from catalog_meta").fetchall()

        meta = {row["key"]: row["value"] for row in rows}

        conn.close()

        services_count = meta.get("services_count", "0")
        domains_count = meta.get("domains_count", "0")

        return JSONResponse(
            {
                "success": True,
                "services": services_count,
                "domains": domains_count,
                "last_update": meta.get("last_update", "unknown"),
                "message": (
                    f"Catalog updated successfully. "
                    f"{services_count} services, "
                    f"{domains_count} domains."
                )
            }
        )

    except Exception as ex:  # noqa: BLE001 - report any failure as JSON

        return JSONResponse(
            {
                "success": False,
                "error": str(ex)
            },
            status_code=500
        )


@app.get("/api/groups")
def groups():

    r = pihole_request("GET", "groups")

    r.raise_for_status()

    return JSONResponse(r.json())


@app.get("/api/service/{service_id}/groups")
async def service_groups(service_id: str):

    conn = db()

    group_ids = get_group_ids(conn, service_id)

    conn.close()

    return JSONResponse(
        {
            "service_id": service_id,
            "groups": group_ids
        }
    )


@app.post("/api/service/{service_id}/groups")
async def save_service_groups(
    service_id: str,
    request: Request
):

    data = await request.json()

    groups = data.get("groups", [])

    conn = db()

    try:

        conn.execute(
            """
            delete
            from service_groups
            where service_id = ?
            """,
            (service_id,)
        )

        for group_id in groups:

            conn.execute(
                """
                insert into service_groups(
                    service_id,
                    group_id
                )
                values(?,?)
                """,
                (
                    service_id,
                    int(group_id)
                )
            )

        conn.commit()

        return JSONResponse(
            {
                "success": True,
                "service_id": service_id,
                "groups": groups
            }
        )

    except Exception as ex:  # noqa: BLE001 - report any failure as JSON

        conn.rollback()

        return JSONResponse(
            {
                "success": False,
                "error": str(ex)
            },
            status_code=500
        )

    finally:

        conn.close()
