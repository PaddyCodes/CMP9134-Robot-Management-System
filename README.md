# Ground Control Station — CMP9134 Starter Scaffold

This repository provides a **starter scaffold** for the CMP9134 assessment.
It gives you a working Docker Compose stack and a VS Code Dev Container so you
can focus on implementing the required functionality rather than configuring
infrastructure.

> **This is not a solution.** The backend contains only stub code. You must
> implement authentication, robot control endpoints, mission logging, and the
> frontend dashboard yourself.

## What is provided

| Component | What you get |
|-----------|-------------|
| Virtual Robot API | Pre-built simulator image — do not modify |
| FastAPI backend | Minimal scaffold with a `/health` endpoint and a basic `RobotClient` |
| Nginx frontend | Static file server with `/api/` proxy pre-configured |
| Docker Compose | Full stack orchestration (robot + backend + frontend) |
| Dev Container | VS Code environment with the stack running automatically |

## Quick Start

```bash
# 1. Fork / clone this repository

# 2. Start the full stack
docker compose up --build

# 3. Open the dashboard (currently a placeholder)
open http://localhost:8080
```

Default port: **8080**. Change via `GCS_PORT` in `.env`.

## Architecture

```
Browser → Nginx (frontend) → FastAPI (backend) → Robot API
```

The database service is **not active** by default. A commented-out PostgreSQL
example is included in `docker-compose.yml` — uncomment and adapt it if your
implementation requires persistent storage.

## Development (recommended)

Open the repository in VS Code with the **Dev Containers** extension, then
choose **"Reopen in Container"**. This starts the robot API and backend
automatically with hot-reload enabled.

```bash
# Run the backend with hot reload (inside devcontainer)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The interactive API docs are available at http://localhost:8000/docs while
the backend is running.

## What you need to implement

Suggested areas (see the assessment brief for full requirements):

- **Authentication** — e.g. JWT tokens, user registration/login
- **Robot control endpoints** — move, reset, status, sensor data
- **Mission logging** — record commands and outcomes (consider a database)
- **WebSocket telemetry** — proxy the robot's real-time stream to the browser
- **Frontend dashboard** — maps, controls, status display

## Project Structure

```
├── backend/
│   ├── Dockerfile          # Multi-stage: base / development / production
│   ├── main.py             # FastAPI stub — add your routes here
│   ├── robot_client.py     # Basic robot HTTP client — extend as needed
│   └── requirements.txt    # fastapi, uvicorn, httpx — add your dependencies
├── frontend/
│   ├── Dockerfile          # nginx serving static files
│   ├── nginx.conf          # Proxies /api/ and /ws/ to the backend
│   └── public/             # HTML/JS placeholder — replace with your dashboard
├── .devcontainer/          # VS Code Dev Container configuration
├── docker-compose.yml      # Stack orchestration (database section commented out)
└── docs/report.md          # Assessment report template
```
