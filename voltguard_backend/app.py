from __future__ import annotations

"""
VoltGuard Flask backend (SQLite + analytics + alerts).

This file is intentionally based on the user-provided authoritative reference.
It is placed in this repository alongside the existing React frontend so the
frontend can call it via an environment-configured base URL.

How to run (example):
  python app.py

Environment variables (recommended):
  - PORT (default 3001)
  - HOST (default 0.0.0.0)
  - VOLTGUARD_DB_PATH or SQLITE_DB_PATH (default ./voltguard.db next to this file)
  - ALLOWED_ORIGINS (comma-separated, or "*")
  - REACT_APP_FRONTEND_URL (used if ALLOWED_ORIGINS not set)
  - CORS_SUPPORTS_CREDENTIALS (default true, auto-disabled if origins contains "*")
"""

import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS


def _get_env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean-like environment variable."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_allowed_origins() -> list[str]:
    """Parse allowed frontend origins from environment variables.

    Preference order:
      1) ALLOWED_ORIGINS (comma-separated) - explicit allow-list, can be "*"
      2) REACT_APP_FRONTEND_URL (single origin, when backend is co-developed with CRA frontend)
      3) Common local dev defaults

    Notes:
      - Use "*" only if you explicitly set ALLOWED_ORIGINS="*".
      - If you enable credentialed requests, you should NOT use "*" (browsers disallow it).
    """
    raw = os.getenv("ALLOWED_ORIGINS")
    if raw is not None and raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]

    # Container env lists REACT_APP_FRONTEND_URL; use it when present (dev convenience).
    frontend_url = os.getenv("REACT_APP_FRONTEND_URL")
    if frontend_url is not None and frontend_url.strip():
        return [frontend_url.strip()]

    # Sensible dev defaults for React (CRA).
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def _parse_csv_list_env(name: str, default_csv: str) -> list[str]:
    """Parse a comma-separated env var into a normalized list of strings."""
    raw = os.getenv(name, default_csv)
    return [v.strip() for v in raw.split(",") if v.strip()]


def _json_error(message: str, status_code: int = 400, **extra: Any):
    """Return a consistent JSON error response."""
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status_code


def _get_db_path() -> str:
    """
    Determine SQLite DB path.

    Preference order:
    1) VOLTGUARD_DB_PATH
    2) SQLITE_DB_PATH
    3) ./voltguard.db within this container directory
    """
    return (
        os.getenv("VOLTGUARD_DB_PATH")
        or os.getenv("SQLITE_DB_PATH")
        or os.path.join(os.path.dirname(__file__), "voltguard.db")
    )


def _get_connection() -> sqlite3.Connection:
    """Create a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(_get_db_path(), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Create required tables if they do not exist."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS meter_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                kWh REAL NOT NULL,
                customer_id TEXT NOT NULL,
                site_id TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer TEXT NOT NULL,
                site TEXT NOT NULL,
                date TEXT NOT NULL,
                deviation_pct REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'unread'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _db_has_any_meter_readings() -> bool:
    """Check whether the DB already contains meter readings."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT 1 FROM meter_readings LIMIT 1").fetchone()
        return row is not None
    finally:
        conn.close()


def _generate_demo_meter_df(days: int = 60) -> pd.DataFrame:
    """
    Generate a demo meter-readings dataframe for the past N days.

    The dataset is designed to:
      - Provide enough history for rolling-baseline analytics.
      - Include a few intentional high-usage anomaly days so alerts appear immediately.

    Returns:
        pd.DataFrame with columns: timestamp, kWh
    """
    # Deterministic (no randomness) so demos are stable across runs.
    end_day = date.today()
    start_day = end_day - timedelta(days=max(1, days) - 1)

    rows: list[dict[str, Any]] = []

    # Base daily consumption (kWh). We'll vary by day-of-week.
    base_weekday = 120.0
    base_weekend = 90.0

    # Choose anomaly offsets relative to the end of the range so there are recent alerts.
    # These are "days ago" indexes within the generated range.
    anomaly_days_ago = {2, 7, 14}  # 3 anomalies in the last 2 weeks
    low_days_ago = {4}  # a low-ish day (won't create alert; kept for variety)

    cur = start_day
    while cur <= end_day:
        days_ago = (end_day - cur).days
        is_weekend = cur.weekday() >= 5

        daily_total = base_weekend if is_weekend else base_weekday

        # Gentle weekly seasonality by weekday (Mon..Sun): -5..+5
        daily_total += (cur.weekday() - 3) * 2.0  # centered around Thu-ish

        # Add anomalies: spike consumption on selected recent days
        if days_ago in anomaly_days_ago:
            daily_total *= 1.55  # clearly above 20% threshold
        elif days_ago in low_days_ago:
            daily_total *= 0.75

        # Split daily total into 24 hourly readings. Keep simple.
        per_hour = daily_total / 24.0
        for h in range(24):
            ts = datetime(cur.year, cur.month, cur.day, h, 0, 0)
            rows.append(
                {
                    "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "kWh": float(per_hour),
                }
            )

        cur = cur + timedelta(days=1)

    return pd.DataFrame(rows, columns=["timestamp", "kWh"])


def _seed_demo_data_if_needed() -> Dict[str, Any]:
    """
    Seed the database with demo meter readings (and derived alerts) if empty.

    Seeding behavior is controlled by environment variables:
      - VOLTGUARD_SEED_DEMO_DATA: default "true". If false, seeding is skipped.
      - VOLTGUARD_SEED_DAYS: number of days of readings to generate (default 60).
      - VOLTGUARD_DEMO_CUSTOMER_ID / VOLTGUARD_DEMO_SITE_ID: defaults match frontend.

    Returns:
        dict: {seeded: bool, inserted_rows: int, alerts_created: int, customer_id, site_id}
    """
    enabled = _get_env_bool("VOLTGUARD_SEED_DEMO_DATA", default=True)
    if not enabled:
        return {
            "seeded": False,
            "reason": "VOLTGUARD_SEED_DEMO_DATA disabled",
            "inserted_rows": 0,
            "alerts_created": 0,
            "customer_id": os.getenv("VOLTGUARD_DEMO_CUSTOMER_ID", "demo_customer"),
            "site_id": os.getenv("VOLTGUARD_DEMO_SITE_ID", "demo_site"),
        }

    if _db_has_any_meter_readings():
        return {
            "seeded": False,
            "reason": "meter_readings already populated",
            "inserted_rows": 0,
            "alerts_created": 0,
            "customer_id": os.getenv("VOLTGUARD_DEMO_CUSTOMER_ID", "demo_customer"),
            "site_id": os.getenv("VOLTGUARD_DEMO_SITE_ID", "demo_site"),
        }

    customer_id = os.getenv("VOLTGUARD_DEMO_CUSTOMER_ID", "demo_customer")
    site_id = os.getenv("VOLTGUARD_DEMO_SITE_ID", "demo_site")
    days = int(os.getenv("VOLTGUARD_SEED_DAYS", "60"))

    demo_df = _generate_demo_meter_df(days=days)
    inserted = _insert_meter_readings(demo_df, customer_id=customer_id, site_id=site_id)

    # Pre-create alerts so the UI can show alerts immediately even before /analytics is called.
    readings = _fetch_meter_readings(customer_id=customer_id, site_id=site_id)
    daily_analytics = _compute_daily_analytics(readings)
    alerts_created = _upsert_alerts_from_anomalies(customer=customer_id, site=site_id, analytics=daily_analytics)

    return {
        "seeded": True,
        "inserted_rows": int(inserted),
        "alerts_created": int(alerts_created),
        "customer_id": customer_id,
        "site_id": site_id,
        "days": int(days),
    }


def _parse_timestamp(ts: Any) -> Optional[pd.Timestamp]:
    """Parse an incoming timestamp to pandas Timestamp (UTC-naive)."""
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    try:
        # Let pandas parse common formats. Keep naive to avoid timezone complexity for hackathon.
        parsed = pd.to_datetime(ts, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _read_csv_from_request() -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Read CSV content from multipart upload or raw body.

    Expected columns:
      - timestamp
      - kWh
    """
    file = request.files.get("file")
    try:
        if file is not None:
            df = pd.read_csv(file)
            return df, None

        # Fallback: accept raw CSV in body
        raw = request.get_data(as_text=True)
        if not raw.strip():
            return None, "No CSV file provided. Send multipart form-data with 'file' or raw CSV body."
        from io import StringIO

        df = pd.read_csv(StringIO(raw))
        return df, None
    except Exception as e:
        return None, f"Failed to parse CSV: {e}"


def _ensure_required_columns(df: pd.DataFrame) -> Optional[str]:
    """Validate expected columns exist."""
    cols = {c.strip(): c for c in df.columns}
    if "timestamp" not in cols or "kWh" not in cols:
        return "CSV must contain columns: timestamp, kWh"
    return None


def _normalize_meter_df(df: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Normalize and validate a meter readings dataframe."""
    err = _ensure_required_columns(df)
    if err:
        return None, err

    # Normalize columns to exact names
    df = df.rename(columns={c: c.strip() for c in df.columns})
    # Keep only needed columns
    df = df[["timestamp", "kWh"]].copy()

    df["timestamp"] = df["timestamp"].apply(_parse_timestamp)
    df = df.dropna(subset=["timestamp", "kWh"])

    # Coerce kWh to numeric
    df["kWh"] = pd.to_numeric(df["kWh"], errors="coerce")
    df = df.dropna(subset=["kWh"])

    if df.empty:
        return None, "No valid rows found after parsing. Ensure timestamp and kWh values are valid."

    # Store ISO8601-like string for SQLite
    df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df, None


def _insert_meter_readings(df: pd.DataFrame, customer_id: str, site_id: str) -> int:
    """Insert meter readings into SQLite and return inserted row count."""
    conn = _get_connection()
    try:
        cur = conn.cursor()
        rows = [(row["timestamp"], float(row["kWh"]), customer_id, site_id) for _, row in df.iterrows()]
        cur.executemany(
            "INSERT INTO meter_readings (timestamp, kWh, customer_id, site_id) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return cur.rowcount if cur.rowcount != -1 else len(rows)
    finally:
        conn.close()


def _fetch_meter_readings(customer_id: str, site_id: str) -> pd.DataFrame:
    """Fetch meter readings for a site into a DataFrame."""
    conn = _get_connection()
    try:
        df = pd.read_sql_query(
            """
            SELECT timestamp, kWh, customer_id, site_id
            FROM meter_readings
            WHERE customer_id = ? AND site_id = ?
            """,
            conn,
            params=(customer_id, site_id),
        )
        if df.empty:
            return df

        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp", "kWh"])
        df["kWh"] = pd.to_numeric(df["kWh"], errors="coerce")
        df = df.dropna(subset=["kWh"])
        return df
    finally:
        conn.close()


def _compute_daily_analytics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute daily totals, rolling 4-week baseline, anomaly flag, deviation %.

    Baseline definition (simple & hackathon-ready):
    - Aggregate to daily total kWh.
    - Compute baseline as rolling mean over prior 28 days (window=28, min_periods=1) on daily totals.
      This approximates a rolling 4-week average per day.

    Anomaly definition:
    - actual > baseline * 1.2  (i.e., > 20% above baseline)
    """
    if df.empty:
        return pd.DataFrame(columns=["date", "actual", "baseline", "deviation_pct", "anomaly"])

    daily = (
        df.assign(date=df["timestamp"].dt.date)
        .groupby("date", as_index=False)["kWh"]
        .sum()
        .rename(columns={"kWh": "actual"})
        .sort_values("date")
        .reset_index(drop=True)
    )

    # Use prior rolling window. Shift by 1 day so baseline for day D uses history up to D-1.
    daily["baseline"] = daily["actual"].rolling(window=28, min_periods=1).mean().shift(1)

    # For the first day (no history), set baseline to actual to avoid NaN and noisy anomaly flags.
    daily["baseline"] = daily["baseline"].fillna(daily["actual"])

    daily["deviation_pct"] = ((daily["actual"] - daily["baseline"]) / daily["baseline"].replace(0, pd.NA)) * 100.0
    daily["deviation_pct"] = daily["deviation_pct"].fillna(0.0)

    daily["anomaly"] = daily["actual"] > (daily["baseline"] * 1.2)

    # JSON-friendly serialization
    daily["date"] = daily["date"].astype(str)
    daily["actual"] = daily["actual"].astype(float)
    daily["baseline"] = daily["baseline"].astype(float)
    daily["deviation_pct"] = daily["deviation_pct"].astype(float)
    daily["anomaly"] = daily["anomaly"].astype(bool)

    return daily


def _upsert_alerts_from_anomalies(customer: str, site: str, analytics: pd.DataFrame) -> int:
    """
    Insert unread alerts for anomalous days if not already present.

    De-dup rule (simple):
    - Unique by (customer, site, date). If exists, do not insert a duplicate.
    """
    anomalies = analytics[analytics["anomaly"] == True]  # noqa: E712 (intentional for pandas)
    if anomalies.empty:
        return 0

    conn = _get_connection()
    inserted = 0
    try:
        cur = conn.cursor()
        for _, row in anomalies.iterrows():
            date = str(row["date"])
            deviation_pct = float(row["deviation_pct"])

            cur.execute(
                """
                SELECT id FROM alerts
                WHERE customer = ? AND site = ? AND date = ?
                """,
                (customer, site, date),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO alerts (customer, site, date, deviation_pct, status)
                    VALUES (?, ?, ?, ?, 'unread')
                    """,
                    (customer, site, date, deviation_pct),
                )
                inserted += 1

        conn.commit()
        return inserted
    finally:
        conn.close()


def _get_week_start(d: pd.Timestamp) -> pd.Timestamp:
    """Get Monday as start of week."""
    return (d - pd.to_timedelta(d.weekday(), unit="D")).normalize()


def _compute_aggregates(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute daily/weekly/monthly aggregates from raw readings."""
    if df.empty:
        return {"daily": [], "weekly": [], "monthly": []}

    tmp = df.copy()
    tmp["date"] = tmp["timestamp"].dt.normalize()

    daily = (
        tmp.groupby("date", as_index=False)["kWh"]
        .sum()
        .rename(columns={"kWh": "kWh"})
        .sort_values("date")
    )
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    daily_records = [{"date": r["date"], "kWh": float(r["kWh"])} for _, r in daily.iterrows()]

    tmp["week_start"] = tmp["timestamp"].apply(_get_week_start)
    weekly = (
        tmp.groupby("week_start", as_index=False)["kWh"]
        .sum()
        .sort_values("week_start")
        .rename(columns={"week_start": "week_start", "kWh": "kWh"})
    )
    weekly["week_start"] = weekly["week_start"].dt.strftime("%Y-%m-%d")
    weekly_records = [{"week_start": r["week_start"], "kWh": float(r["kWh"])} for _, r in weekly.iterrows()]

    tmp["month"] = tmp["timestamp"].dt.to_period("M").astype(str)
    monthly = tmp.groupby("month", as_index=False)["kWh"].sum().sort_values("month")
    monthly_records = [{"month": r["month"], "kWh": float(r["kWh"])} for _, r in monthly.iterrows()]

    return {"daily": daily_records, "weekly": weekly_records, "monthly": monthly_records}


def _mock_peer_benchmarking(daily_analytics: pd.DataFrame) -> Dict[str, Any]:
    """
    Create a mock peer dataset comparison.

    Requirement: "Create mock dataset for similar businesses; compare average consumption; return % higher/lower".
    We'll create a peer average as a stable factor around customer's mean.
    """
    if daily_analytics.empty:
        return {
            "peer_average_daily_kwh": 0.0,
            "customer_average_daily_kwh": 0.0,
            "percent_higher_lower": 0.0,
            "message": "No data available for benchmarking.",
        }

    customer_avg = float(daily_analytics["actual"].mean())
    # Mock peer average: 5% lower than customer if customer is high, otherwise 5% higher.
    peer_avg = customer_avg * (0.95 if customer_avg > 0 else 1.05)

    if peer_avg == 0:
        pct = 0.0
    else:
        pct = ((customer_avg - peer_avg) / peer_avg) * 100.0

    if abs(pct) < 1e-6:
        msg = "Your average daily consumption is about the same as similar businesses."
    elif pct > 0:
        msg = f"Your average daily consumption is {pct:.1f}% higher than similar businesses."
    else:
        msg = f"Your average daily consumption is {abs(pct):.1f}% lower than similar businesses."

    return {
        "peer_average_daily_kwh": float(peer_avg),
        "customer_average_daily_kwh": float(customer_avg),
        "percent_higher_lower": float(pct),
        "message": msg,
    }


# PUBLIC_INTERFACE
def create_app() -> Flask:
    """
    Create and configure the VoltGuard Flask application.

    Returns:
        Flask: configured app instance with routes:
          - POST /upload
          - GET /analytics
          - GET /alerts
          - PATCH /alerts/<id>/mark-read
          - GET /health
    """
    app = Flask(__name__)

    # CORS configuration from env.
    #
    # This backend is called by the React SPA. Ensure the frontend origin is allowed
    # (configurable via env) and that preflight/actual requests work for:
    #   - POST  /upload
    #   - GET   /analytics
    #   - GET   /alerts
    #   - PATCH /alerts/<id>/mark-read
    origins = _parse_allowed_origins()

    allowed_methods = _parse_csv_list_env("ALLOWED_METHODS", "GET,POST,PATCH,OPTIONS")
    allowed_headers = _parse_csv_list_env(
        "ALLOWED_HEADERS",
        # Include common headers used by fetch/AJAX clients (and uploads).
        "Content-Type,Authorization,Accept,X-Requested-With",
    )

    supports_credentials = _get_env_bool("CORS_SUPPORTS_CREDENTIALS", default=True)

    # If origins contains "*", credentialed CORS is invalid in browsers. Force-disable credentials
    # in that case to avoid hard-to-debug CORS failures.
    if "*" in origins:
        supports_credentials = False

    cors_kwargs: Dict[str, Any] = {
        # Apply CORS to all routes; endpoints above will be covered automatically.
        "resources": {r"/*": {"origins": origins}},
        "supports_credentials": supports_credentials,
        "methods": allowed_methods,
        "allow_headers": allowed_headers,
        "max_age": int(os.getenv("CORS_MAX_AGE", "3600")),
    }
    CORS(app, **cors_kwargs)

    # Ensure DB tables exist before serving requests.
    _init_db()

    # Seed demo data so the dashboard shows analytics/alerts immediately (no CSV upload required).
    # This is idempotent: it only runs when the meter_readings table is empty.
    _seed_demo_data_if_needed()

    @app.errorhandler(404)
    def not_found(_err):
        """Return JSON 404 instead of HTML."""
        return _json_error("Not found", 404)

    @app.errorhandler(405)
    def method_not_allowed(_err):
        """Return JSON 405 instead of HTML."""
        return _json_error("Method not allowed", 405)

    @app.errorhandler(Exception)
    def unhandled_exception(err):
        """Return JSON 500 for unexpected errors (keeps frontend error handling predictable)."""
        # Avoid leaking internals unless explicitly in development mode.
        debug_errors = _get_env_bool("DEBUG_ERRORS", default=False) or os.getenv("NODE_ENV", "") == "development"
        if debug_errors:
            return _json_error("Internal server error", 500, detail=str(err))
        return _json_error("Internal server error", 500)

    @app.get("/health")
    def health():
        """Simple health check endpoint."""
        return jsonify({"ok": True, "service": "voltguard-flask-backend"})

    @app.post("/upload")
    def upload():
        """
        Upload meter CSV data (timestamp, kWh) and store into SQLite.

        Accepts:
          - multipart/form-data with `file` containing CSV
          - optional form fields: customer_id, site_id
          - alternatively, query params: ?customer_id=...&site_id=...
          - or JSON body containing customer_id/site_id (when uploading raw CSV body not recommended)

        Returns:
          JSON: { ok, inserted_rows, customer_id, site_id }
        """
        customer_id = (
            request.form.get("customer_id")
            or request.args.get("customer_id")
            or (request.json.get("customer_id") if request.is_json else None)
            or "demo_customer"
        )
        site_id = (
            request.form.get("site_id")
            or request.args.get("site_id")
            or (request.json.get("site_id") if request.is_json else None)
            or "demo_site"
        )

        df_raw, err = _read_csv_from_request()
        if err:
            return _json_error(err, 400)

        df, err = _normalize_meter_df(df_raw)  # type: ignore[arg-type]
        if err:
            return _json_error(err, 400)

        inserted = _insert_meter_readings(df, customer_id=customer_id, site_id=site_id)

        return jsonify(
            {
                "ok": True,
                "inserted_rows": int(inserted),
                "customer_id": customer_id,
                "site_id": site_id,
            }
        )

    @app.get("/analytics")
    def analytics():
        """
        Compute analytics for a given customer/site.

        Query params:
          - customer_id (default: demo_customer)
          - site_id (default: demo_site)

        Returns:
          JSON with:
            - aggregates: daily/weekly/monthly totals
            - baseline_and_anomalies: per-day {date, actual, baseline, deviation_pct, anomaly}
            - benchmark: mock peer benchmarking
            - alerts_created: number of new alerts inserted from anomalies
        """
        customer_id = request.args.get("customer_id", "demo_customer")
        site_id = request.args.get("site_id", "demo_site")

        readings = _fetch_meter_readings(customer_id=customer_id, site_id=site_id)
        if readings.empty:
            return jsonify(
                {
                    "ok": True,
                    "customer_id": customer_id,
                    "site_id": site_id,
                    "aggregates": {"daily": [], "weekly": [], "monthly": []},
                    "baseline_and_anomalies": [],
                    "benchmark": _mock_peer_benchmarking(pd.DataFrame()),
                    "alerts_created": 0,
                }
            )

        aggregates = _compute_aggregates(readings)
        daily_analytics = _compute_daily_analytics(readings)
        created = _upsert_alerts_from_anomalies(customer=customer_id, site=site_id, analytics=daily_analytics)
        benchmark = _mock_peer_benchmarking(daily_analytics)

        return jsonify(
            {
                "ok": True,
                "customer_id": customer_id,
                "site_id": site_id,
                "aggregates": aggregates,
                "baseline_and_anomalies": daily_analytics.to_dict(orient="records"),
                "benchmark": benchmark,
                "alerts_created": int(created),
            }
        )

    @app.get("/alerts")
    def get_alerts():
        """
        List alerts stored in SQLite.

        Optional query params:
          - customer (filter)
          - site (filter)
          - status (read/unread)

        Returns:
          JSON: { ok, alerts: [...] }
        """
        customer = request.args.get("customer")
        site = request.args.get("site")
        status = request.args.get("status")

        conn = _get_connection()
        try:
            where = []
            params = []
            if customer:
                where.append("customer = ?")
                params.append(customer)
            if site:
                where.append("site = ?")
                params.append(site)
            if status:
                where.append("status = ?")
                params.append(status)

            sql = "SELECT id, customer, site, date, deviation_pct, status FROM alerts"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY date DESC, id DESC"

            rows = conn.execute(sql, params).fetchall()
            alerts = [
                {
                    "id": int(r["id"]),
                    "customer": r["customer"],
                    "site": r["site"],
                    "date": r["date"],
                    "deviation_pct": float(r["deviation_pct"]),
                    "status": r["status"],
                }
                for r in rows
            ]

            return jsonify({"ok": True, "alerts": alerts})
        finally:
            conn.close()

    @app.patch("/alerts/<int:alert_id>/mark-read")
    def mark_alert_read(alert_id: int):
        """
        Mark an alert as read.

        Path:
          - alert_id: integer

        Returns:
          JSON: { ok, updated: true/false }
        """
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE alerts SET status = 'read' WHERE id = ?", (alert_id,))
            conn.commit()
            updated = cur.rowcount > 0
            if not updated:
                return _json_error("Alert not found", 404, alert_id=alert_id)
            return jsonify({"ok": True, "updated": True, "id": alert_id})
        finally:
            conn.close()

    return app


if __name__ == "__main__":
    # Keep runnable via `python app.py`.
    # Note: PORT may exist for the frontend; default to 3001 for backend.
    port = int(os.getenv("PORT", "3001"))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("NODE_ENV", "development") == "development"
    app = create_app()
    app.run(host=host, port=port, debug=debug)
