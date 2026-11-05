export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function fetchJson(
  path,
  { budgetMs = 90000, timeoutMs = 15000, onRetry } = {},
) {
  const startedAt = Date.now();
  const deadline = startedAt + budgetMs;
  let delayMs = 1000;
  let attempt = 0;
  let lastError;

  while (Date.now() < deadline) {
    const controller = new AbortController();
    const remaining = deadline - Date.now();
    const timeout = window.setTimeout(
      () => controller.abort(),
      Math.min(timeoutMs, remaining),
    );

    try {
      const response = await fetch(path, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const body = await response.json().catch(() => null);

      if (!response.ok) {
        const message = body?.error?.message || `Request failed (${response.status}).`;
        const error = new ApiError(message, response.status);
        if (response.status < 500 && response.status !== 429) {
          throw error;
        }
        lastError = error;
      } else {
        return body;
      }
    } catch (error) {
      lastError =
        error instanceof ApiError
          ? error
          : new ApiError(
              error.name === "AbortError"
                ? "The request timed out."
                : "Could not reach the GridCast API.",
            );
      if (
        error instanceof ApiError &&
        error.status > 0 &&
        error.status < 500 &&
        error.status !== 429
      ) {
        throw error;
      }
    } finally {
      window.clearTimeout(timeout);
    }

    const nextDelay = Math.min(delayMs, deadline - Date.now());
    if (nextDelay <= 0) {
      break;
    }
    onRetry?.({ attempt: attempt + 1, delayMs: nextDelay, error: lastError });
    await new Promise((resolve) => window.setTimeout(resolve, nextDelay));
    delayMs = Math.min(delayMs * 2, 15000);
    attempt += 1;
  }

  const elapsed = Math.round((Date.now() - startedAt) / 1000);
  throw new ApiError(
    `The server did not respond within the ${elapsed}-second retry window. ${lastError?.message || ""}`.trim(),
    lastError?.status || 0,
  );
}

export function queryString(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const result = params.toString();
  return result ? `?${result}` : "";
}

export function istDate(daysAgo = 0) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const part = (type) => parts.find((item) => item.type === type).value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  const calendarDate = new Date(`${today}T00:00:00Z`);
  calendarDate.setUTCDate(calendarDate.getUTCDate() - daysAgo);
  return calendarDate.toISOString().slice(0, 10);
}

export function fillModelSelect(select, models) {
  const previous = select.value;
  select.replaceChildren();
  const championPlaceholder = document.createElement("option");
  championPlaceholder.value = "";
  championPlaceholder.textContent = "Champion model";
  select.append(championPlaceholder);

  const champion = models.find((model) => model.is_champion);
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.model_id;
    option.textContent = `${model.model_id}${model.is_champion ? " (champion)" : ""}`;
    select.append(option);
  }
  if (models.some((model) => model.model_id === previous)) {
    select.value = previous;
  } else if (champion) {
    select.value = champion.model_id;
  }
}

export function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) {
    return "N/A";
  }
  return Number(value).toLocaleString("en-IN", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatTimestamp(value) {
  if (!value) {
    return "N/A";
  }
  const timestamp = String(value);
  if (/[zZ]|[+-]\d{2}:\d{2}$/.test(timestamp)) {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(timestamp));
    const part = (type) => parts.find((item) => item.type === type).value;
    return [
      `${part("year")}-${part("month")}-${part("day")}`,
      `${part("hour")}:${part("minute")}:${part("second")}`,
    ].join(" ");
  }
  return timestamp.replace("T", " ").slice(0, 19);
}
