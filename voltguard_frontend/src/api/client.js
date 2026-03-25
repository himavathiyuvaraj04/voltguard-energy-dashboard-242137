const DEFAULT_TIMEOUT_MS = 15000;

/**
 * Resolve backend base URL from environment.
 * Preference order:
 *  1) REACT_APP_BACKEND_URL (requested by task)
 *  2) REACT_APP_API_BASE (compat)
 *  3) http://localhost:3001 (local dev default)
 */
function getApiBaseUrl() {
  const base =
    (process.env.REACT_APP_BACKEND_URL || process.env.REACT_APP_API_BASE || "http://localhost:3001").trim();

  // Normalize: remove trailing slash
  return base.endsWith("/") ? base.slice(0, -1) : base;
}

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  try {
    const url = `${getApiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;

    const res = await fetch(url, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });

    const contentType = res.headers.get("content-type") || "";
    const isJson = contentType.includes("application/json");

    const body = isJson ? await res.json() : await res.text();

    if (!res.ok) {
      const message =
        (isJson && body && body.error) ? body.error : `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      err.body = body;
      throw err;
    }

    return body;
  } finally {
    clearTimeout(timeout);
  }
}

// PUBLIC_INTERFACE
export async function getHealth() {
  /** Get backend health status. */
  return fetchJson("/health", { method: "GET" });
}

// PUBLIC_INTERFACE
export async function getAnalytics({ customerId = "demo_customer", siteId = "demo_site" } = {}) {
  /** Fetch analytics for a given customer/site. */
  const qs = new URLSearchParams({ customer_id: customerId, site_id: siteId });
  return fetchJson(`/analytics?${qs.toString()}`, { method: "GET" });
}

// PUBLIC_INTERFACE
export async function getAlerts({ customer, site, status } = {}) {
  /** Fetch alerts list with optional filters. */
  const qs = new URLSearchParams();
  if (customer) qs.set("customer", customer);
  if (site) qs.set("site", site);
  if (status) qs.set("status", status);

  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return fetchJson(`/alerts${suffix}`, { method: "GET" });
}

// PUBLIC_INTERFACE
export async function markAlertRead(alertId) {
  /** Mark an alert as read by id. */
  if (alertId === undefined || alertId === null) {
    throw new Error("alertId is required");
  }
  return fetchJson(`/alerts/${encodeURIComponent(String(alertId))}/mark-read`, { method: "PATCH" });
}
