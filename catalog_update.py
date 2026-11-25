#!/usr/bin/env python3

import os
import sqlite3
from datetime import UTC, datetime

import requests

DB_FILE = os.getenv("DB_FILE", "data/app.db")

URL = "https://adguardteam.github.io/HostlistsRegistry/assets/services.json"


def rule_to_domain(rule: str) -> str:
    """Turn an AdGuard rule like '||example.com^' into 'example.com' ('' if not a domain rule)."""

    rule = str(rule).strip()

    if not rule.startswith("||"):
        return ""

    return rule.replace("||", "", 1).split("^")[0].strip()


print("Downloading services.json...")

r = requests.get(URL, timeout=60)
r.raise_for_status()

services = r.json().get("blocked_services", [])

print(f"Found {len(services)} services")

conn = sqlite3.connect(DB_FILE)

# The state table is left alone so enabled services survive a catalog refresh.
conn.execute("delete from services")
conn.execute("delete from service_domains")

service_count = 0
domain_count = 0

for svc in services:

    service_id = svc.get("id", "").strip().lower()

    if not service_id:
        continue

    name = svc.get("name") or service_id
    category = svc.get("group") or "software"

    conn.execute(
        "insert into services(id, name, category) values(?, ?, ?)",
        (service_id, name, category)
    )

    service_count += 1

    for rule in svc.get("rules", []):

        domain = rule_to_domain(rule)

        if not domain:
            continue

        conn.execute(
            "insert into service_domains(service_id, domain) values(?, ?)",
            (service_id, domain)
        )

        domain_count += 1

meta = {
    "source": "HostlistsRegistry",
    # Naive UTC timestamp, same format as the old datetime.utcnow().
    "last_update": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    "services_count": str(service_count),
    "domains_count": str(domain_count),
}

for key, value in meta.items():
    conn.execute(
        """
        insert into catalog_meta(key, value)
        values(?, ?)
        on conflict(key)
        do update set value=excluded.value
        """,
        (key, value)
    )

conn.commit()
conn.close()

print()
print("Catalog updated successfully")
print(f"Services : {service_count}")
print(f"Domains  : {domain_count}")
