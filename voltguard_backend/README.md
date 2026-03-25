# VoltGuard Backend (Flask + SQLite)

This folder contains the VoltGuard Flask backend implementation (SQLite storage, analytics computation, and alert generation) as provided by the user reference.

## Endpoints

- `GET /health`
- `POST /upload` (CSV upload; columns: `timestamp`, `kWh`)
- `GET /analytics?customer_id=...&site_id=...`
- `GET /alerts?customer=...&site=...&status=...`
- `PATCH /alerts/<id>/mark-read`

## Environment variables

- `PORT` (default `3001`)
- `HOST` (default `0.0.0.0`)
- `VOLTGUARD_DB_PATH` or `SQLITE_DB_PATH` (defaults to `./voltguard.db` in this folder)
- `ALLOWED_ORIGINS` (comma-separated list, or `"*"`)
- `REACT_APP_FRONTEND_URL` (used as allowed origin when `ALLOWED_ORIGINS` is not set)
- `CORS_SUPPORTS_CREDENTIALS` (default `true`; auto-disabled if origins contains `"*"`)
- `NODE_ENV` (if `development`, enables Flask debug mode)

## Install & run (example)

Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

By default it listens on port 3001.
