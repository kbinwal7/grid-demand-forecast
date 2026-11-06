// Tiny client for the GridCast JSON API (same origin).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** GET /v1/... with query params; empty values are dropped. Throws ApiError on non-2xx. */
export async function getJson(path, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""),
  );
  const url = query.size ? `${path}?${query}` : path;
  let response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("Cannot reach the GridCast API. Is uvicorn running?", 0);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(body?.error?.message || `Request failed (${response.status})`, response.status);
  }
  return body;
}

export function fmt(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
