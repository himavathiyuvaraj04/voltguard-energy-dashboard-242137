import React, { useEffect, useMemo, useState } from "react";
import "./App.css";
import { getAlerts, getAnalytics, getHealth, markAlertRead } from "./api/client";

// PUBLIC_INTERFACE
function App() {
  const [theme, setTheme] = useState("light");

  const [health, setHealth] = useState({ loading: true, ok: false, error: null, data: null });
  const [analytics, setAnalytics] = useState({ loading: true, error: null, data: null });
  const [alerts, setAlerts] = useState({ loading: true, error: null, items: [] });
  const [busyAlertIds, setBusyAlertIds] = useState(() => new Set());

  // Demo identifiers (hackathon-friendly). Could later be driven by UI controls.
  const customerId = "demo_customer";
  const siteId = "demo_site";

  // Effect to apply theme to document element
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // PUBLIC_INTERFACE
  const toggleTheme = () => {
    /** Toggle light/dark theme. */
    setTheme((prevTheme) => (prevTheme === "light" ? "dark" : "light"));
  };

  async function refreshAll() {
    // Health
    setHealth((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await getHealth();
      setHealth({ loading: false, ok: !!data?.ok, error: null, data });
    } catch (e) {
      setHealth({ loading: false, ok: false, error: e?.message || "Health check failed", data: null });
    }

    // Analytics
    setAnalytics((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await getAnalytics({ customerId, siteId });
      setAnalytics({ loading: false, error: null, data });
    } catch (e) {
      setAnalytics({ loading: false, error: e?.message || "Failed to load analytics", data: null });
    }

    // Alerts
    setAlerts((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await getAlerts({ customer: customerId, site: siteId });
      setAlerts({ loading: false, error: null, items: Array.isArray(data?.alerts) ? data.alerts : [] });
    } catch (e) {
      setAlerts({ loading: false, error: e?.message || "Failed to load alerts", items: [] });
    }
  }

  useEffect(() => {
    refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const baselineSeries = useMemo(() => {
    const rows = analytics?.data?.baseline_and_anomalies || [];
    if (!Array.isArray(rows) || rows.length === 0) return [];
    // show last 10 days for compact UI
    return rows.slice(-10);
  }, [analytics?.data]);

  const aggregates = analytics?.data?.aggregates || { daily: [], weekly: [], monthly: [] };
  const benchmark = analytics?.data?.benchmark || null;

  const lastDaily = useMemo(() => {
    const daily = aggregates?.daily || [];
    if (!Array.isArray(daily) || daily.length === 0) return null;
    return daily[daily.length - 1];
  }, [aggregates]);

  async function onMarkRead(id) {
    setBusyAlertIds((prev) => new Set([...prev, id]));
    try {
      await markAlertRead(id);
      // optimistic update
      setAlerts((prev) => ({
        ...prev,
        items: prev.items.map((a) => (a.id === id ? { ...a, status: "read" } : a)),
      }));
    } catch (e) {
      // If mark-read fails, re-fetch alerts to reconcile
      try {
        const data = await getAlerts({ customer: customerId, site: siteId });
        setAlerts({ loading: false, error: e?.message || "Failed to mark alert read", items: data?.alerts || [] });
      } catch (_e2) {
        setAlerts((prev) => ({ ...prev, error: e?.message || "Failed to mark alert read" }));
      }
    } finally {
      setBusyAlertIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  return (
    <div className="App">
      <header className="App-header" style={{ justifyContent: "flex-start", padding: "72px 20px 24px" }}>
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
        >
          {theme === "light" ? "🌙 Dark" : "☀️ Light"}
        </button>

        {/* Header */}
        <div style={{ width: "min(1100px, 100%)", textAlign: "left" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <h1 style={{ margin: 0, fontSize: 28, lineHeight: 1.2 }}>VoltGuard Energy Dashboard</h1>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 10px",
                borderRadius: 999,
                border: "1px solid var(--border-color)",
                background: "rgba(97, 218, 251, 0.08)",
                fontSize: 13,
                fontWeight: 700,
              }}
            >
              Demo Mode
            </span>
          </div>

          <div style={{ marginTop: 10, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <span style={{ fontSize: 14, opacity: 0.9 }}>
              Customer: <strong>{customerId}</strong> • Site: <strong>{siteId}</strong>
            </span>

            <span style={{ fontSize: 14, opacity: 0.9 }}>
              Backend:{" "}
              {health.loading ? (
                "checking…"
              ) : health.ok ? (
                <strong style={{ color: "var(--text-secondary)" }}>online</strong>
              ) : (
                <strong style={{ color: "#EF4444" }}>offline</strong>
              )}
              {health.error ? <span style={{ marginLeft: 8, opacity: 0.8 }}>({health.error})</span> : null}
            </span>

            <button
              onClick={refreshAll}
              style={{
                marginLeft: "auto",
                padding: "10px 14px",
                borderRadius: 10,
                border: "1px solid var(--border-color)",
                background: "var(--button-bg)",
                color: "var(--button-text)",
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Main content */}
        <div
          style={{
            width: "min(1100px, 100%)",
            display: "grid",
            gridTemplateColumns: "1.3fr 0.9fr",
            gap: 16,
            marginTop: 18,
          }}
        >
          {/* Analytics panel */}
          <section
            style={{
              textAlign: "left",
              border: "1px solid var(--border-color)",
              borderRadius: 14,
              background: "var(--bg-primary)",
              padding: 16,
            }}
          >
            <h2 style={{ margin: 0, fontSize: 18 }}>Analytics</h2>
            <p style={{ marginTop: 6, marginBottom: 14, opacity: 0.8, fontSize: 14 }}>
              Actual vs baseline (last 10 days) with anomaly flags.
            </p>

            {analytics.loading ? (
              <div style={{ opacity: 0.8 }}>Loading analytics…</div>
            ) : analytics.error ? (
              <div style={{ color: "#EF4444" }}>{analytics.error}</div>
            ) : (
              <>
                {/* Lightweight "chart" table for template project (no chart libs) */}
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ textAlign: "left" }}>
                        <th style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>Date</th>
                        <th style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>Actual</th>
                        <th style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>Baseline</th>
                        <th style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                          Deviation %
                        </th>
                        <th style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>Anomaly</th>
                      </tr>
                    </thead>
                    <tbody>
                      {baselineSeries.length === 0 ? (
                        <tr>
                          <td colSpan={5} style={{ padding: 10, opacity: 0.8 }}>
                            No analytics available yet. Upload data to backend then refresh.
                          </td>
                        </tr>
                      ) : (
                        baselineSeries.map((r) => (
                          <tr key={r.date} style={{ background: r.anomaly ? "rgba(239, 68, 68, 0.08)" : "transparent" }}>
                            <td style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                              {r.date}
                            </td>
                            <td style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                              {Number(r.actual).toFixed(2)}
                            </td>
                            <td style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                              {Number(r.baseline).toFixed(2)}
                            </td>
                            <td style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                              {Number(r.deviation_pct).toFixed(1)}%
                            </td>
                            <td style={{ padding: "8px 6px", borderBottom: "1px solid var(--border-color)" }}>
                              {r.anomaly ? "Yes" : "No"}
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>

                {/* Summary cards */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                    gap: 12,
                    marginTop: 14,
                  }}
                >
                  <div
                    style={{
                      border: "1px solid var(--border-color)",
                      borderRadius: 12,
                      padding: 12,
                      background: "var(--bg-secondary)",
                    }}
                  >
                    <div style={{ fontSize: 12, opacity: 0.8 }}>Latest daily kWh</div>
                    <div style={{ fontSize: 18, fontWeight: 800, marginTop: 6 }}>
                      {lastDaily ? Number(lastDaily.kWh).toFixed(2) : "—"}
                    </div>
                  </div>

                  <div
                    style={{
                      border: "1px solid var(--border-color)",
                      borderRadius: 12,
                      padding: 12,
                      background: "var(--bg-secondary)",
                    }}
                  >
                    <div style={{ fontSize: 12, opacity: 0.8 }}>New alerts created</div>
                    <div style={{ fontSize: 18, fontWeight: 800, marginTop: 6 }}>
                      {analytics?.data?.alerts_created ?? "—"}
                    </div>
                  </div>

                  <div
                    style={{
                      border: "1px solid var(--border-color)",
                      borderRadius: 12,
                      padding: 12,
                      background: "var(--bg-secondary)",
                    }}
                  >
                    <div style={{ fontSize: 12, opacity: 0.8 }}>Peer benchmark</div>
                    <div style={{ fontSize: 13, marginTop: 6, lineHeight: 1.35 }}>
                      {benchmark?.message || "—"}
                    </div>
                  </div>
                </div>
              </>
            )}
          </section>

          {/* Alerts panel */}
          <section
            style={{
              textAlign: "left",
              border: "1px solid var(--border-color)",
              borderRadius: 14,
              background: "var(--bg-primary)",
              padding: 16,
              height: "fit-content",
            }}
          >
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>Alerts</h2>
              <span style={{ opacity: 0.75, fontSize: 13 }}>
                {alerts.loading ? "Loading…" : `${alerts.items.length} total`}
              </span>
            </div>

            {alerts.error ? <div style={{ color: "#EF4444", marginTop: 8 }}>{alerts.error}</div> : null}

            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
              {alerts.loading ? (
                <div style={{ opacity: 0.8 }}>Fetching alerts…</div>
              ) : alerts.items.length === 0 ? (
                <div style={{ opacity: 0.8 }}>No alerts yet.</div>
              ) : (
                alerts.items.map((a) => {
                  const isBusy = busyAlertIds.has(a.id);
                  const isUnread = a.status === "unread";
                  return (
                    <div
                      key={a.id}
                      style={{
                        border: "1px solid var(--border-color)",
                        borderRadius: 12,
                        padding: 12,
                        background: isUnread ? "rgba(97, 218, 251, 0.07)" : "var(--bg-secondary)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                        <div style={{ fontWeight: 800, fontSize: 14 }}>
                          {a.date} • {isUnread ? "Unread" : "Read"}
                        </div>
                        {isUnread ? (
                          <button
                            onClick={() => onMarkRead(a.id)}
                            disabled={isBusy}
                            style={{
                              padding: "8px 10px",
                              borderRadius: 10,
                              border: "1px solid var(--border-color)",
                              background: "var(--button-bg)",
                              color: "var(--button-text)",
                              fontWeight: 800,
                              cursor: isBusy ? "not-allowed" : "pointer",
                              opacity: isBusy ? 0.6 : 1,
                            }}
                            aria-label={`Mark alert ${a.id} as read`}
                          >
                            {isBusy ? "Saving…" : "Mark read"}
                          </button>
                        ) : null}
                      </div>
                      <div style={{ marginTop: 6, fontSize: 13, opacity: 0.9, lineHeight: 1.35 }}>
                        Deviation: <strong>{Number(a.deviation_pct).toFixed(1)}%</strong>
                        <div style={{ opacity: 0.8, marginTop: 4, fontSize: 12 }}>
                          Customer: {a.customer} • Site: {a.site} • ID: {a.id}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </section>
        </div>

        {/* Footer help */}
        <div style={{ width: "min(1100px, 100%)", textAlign: "left", marginTop: 18, opacity: 0.8, fontSize: 13 }}>
          Tip: Set <code>REACT_APP_BACKEND_URL</code> (or <code>REACT_APP_API_BASE</code>) to your Flask server, e.g.{" "}
          <code>http://localhost:3001</code>.
        </div>
      </header>
    </div>
  );
}

export default App;
