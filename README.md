# KiCad Library Manager

A Dockerized platform for managing KiCad symbol, footprint and 3D model libraries.

Currently a hello-world scaffold: FastAPI + Uvicorn behind a single container.

## Run locally

```bash
docker compose up --build
```

Then open <http://localhost:8000>.

Without Docker:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Endpoints

| Path       | Purpose                         |
| ---------- | ------------------------------- |
| `/`        | Hello-world landing page        |
| `/healthz` | Liveness probe (JSON)           |
| `/docs`    | Auto-generated OpenAPI docs     |

## Deployment (Coolify)

The app is deployed from this repository via Coolify using the `Dockerfile` build pack.

- Container port: **8000** (also settable with the `PORT` env var)
- Health check path: `/healthz`

Pushing to `main` triggers a redeploy.
