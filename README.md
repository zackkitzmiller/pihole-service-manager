# Pi-hole Service Manager

A service-oriented management layer for Pi-hole.

Pi-hole Service Manager allows administrators to manage online services instead of individual domains. Services such as YouTube, TikTok, Discord, Netflix, Twitch, Reddit, Telegram, and Facebook can be blocked or allowed through a simple web interface.

## Screenshot

![Dashboard](docs/images/dashboard.png)

Service-based blocking with Pi-hole Groups support, category filtering, search, and automatic catalog updates.

## Why?

Pi-hole is excellent at domain-level filtering, but managing large collections of domains can become difficult over time.

Service Manager introduces a higher-level abstraction:

Service
→ Managed Domains
→ Pi-hole Rules
→ Assigned Device Groups

This enables service-based policy management while continuing to use standard Pi-hole functionality.

## Features

- Service-based blocking and unblocking
- Native Pi-hole API integration
- Dynamic Pi-hole Group discovery
- Per-group service assignment
- Automatic catalog updates
- Category-based service organization
- Full-text service search
- SQLite-backed configuration storage
- FastAPI backend
- Responsive web interface

## Pi-hole Group Support

Examples:

- Discord → Kids
- TikTok → Default, TV3
- Netflix → TV1, TV2, TV3
- YouTube → Kids, TV1

When a service is enabled, Service Manager automatically creates Pi-hole rules using the selected groups.

## Architecture

Service Manager UI

↓

FastAPI Backend

↓

SQLite Configuration

↓

Pi-hole API

↓

Pi-hole DNS Engine

No direct modification of Pi-hole databases is required.

## Service Catalog

The service catalog is automatically generated from AdGuard HostlistsRegistry.

Catalog updates preserve service configuration and group assignments.

## Installation

Requirements:

- Pi-hole 6.x
- Docker
- Docker Compose
- Git

Clone repository:

git clone https://github.com/4uba/pihole-service-manager.git

cd pihole-service-manager

Create .env:

PIHOLE_URL=http://YOUR_PIHOLE_IP

PIHOLE_PASSWORD=YOUR_PIHOLE_PASSWORD

APP_PASSWORD=CHOOSE_A_PASSWORD_FOR_THIS_APP

Example:

PIHOLE_URL=http://192.168.1.10

PIHOLE_PASSWORD=my_secret_password

APP_PASSWORD=another_secret

APP_PASSWORD protects the web page. The browser asks for it once; any
username works. Without it, anyone on your network could unblock
services.

Start:

docker compose up -d

Open:

http://SERVER_IP:8081

## Updating

git pull

docker compose build

docker compose up -d

## Development

pip install -r requirements-dev.txt

pytest

ruff check .

The tests use a fake Pi-hole, so no real Pi-hole is needed.

## Technology Stack

- FastAPI
- SQLite
- Vanilla JavaScript
- Pi-hole API
- Docker
- AdGuard HostlistsRegistry

## Current Status

Version: v3.2.0-alpha1

Implemented:

- Service catalog management
- Dynamic catalog updates
- Category filtering
- Service search
- Pi-hole API integration
- Dynamic Pi-hole Group discovery
- Group-aware service blocking
- Persistent group assignments
- Web management interface

## License

MIT License
