import { queryString, formatNumber } from "./api.js";
import { chartLayout, drawPlot, runChart, timeLabels } from "./charts.js";
import { applyTileStatus, initDashboardControls } from "./controls.js";
import {
  BASELINE_MODEL_ID,
  coverageStatus,
  mapeStatus,
} from "./thresholds.js";

const chartRoot = document.querySelector("#forecast-chart-root");

function dayOffset(day, days) {
  const result = new Date(`${day}T00:00:00Z`);
  result.setUTCDate(result.getUTCDate() + days);
  return result.toISOString().slice(0, 10);
}

function resetMetricTiles(source) {
  document.querySelector("#forecast-mape").textContent = "n/a";
  document.querySelector("#forecast-mape-note").textContent =
    `last 7 days · ${source || "source unavailable"}`;
  document.querySelector("#forecast-coverage").textContent = "n/a";
  document.querySelector("#forecast-coverage-note").textContent =
    `last 7 days · ${source || "source unavailable"}`;
  applyTileStatus(document.querySelector("#forecast-mape-tile"), "neutral");
  applyTileStatus(document.querySelector("#forecast-coverage-tile"), "neutral");
}

function updateAccuracyTiles(dailyItems, coverageItems, models, modelId, source) {
  const champion = models.find((item) => item.is_champion);
  const baseline = models.find((item) => item.model_id === BASELINE_MODEL_ID);
  const scoredDays = dailyItems.filter(
    (item) => item.source === source && item.model_id === modelId && item.mape !== null,
  );
  const uniqueDays = new Set(scoredDays.map((item) => item.day)).size;
  const totalPoints = scoredDays.reduce((sum, item) => sum + item.n, 0);
  const mapeValue =
    uniqueDays < 3 || totalPoints === 0
      ? null
      : scoredDays.reduce((sum, item) => sum + item.mape * item.n, 0) / totalPoints;
  const mapeTile = document.querySelector("#forecast-mape-tile");
  const mapeStatusValue = mapeStatus(
    mapeValue,
    champion?.cv_mape,
    baseline?.cv_mape,
  );
  document.querySelector("#forecast-mape").textContent =
    mapeStatusValue === "neutral" ? "n/a" : `${formatNumber(mapeValue)}%`;
  document.querySelector("#forecast-mape-note").textContent =
    `last 7 days · ${source}`;
  applyTileStatus(mapeTile, mapeStatusValue);

  const coverage = coverageItems.find(
    (item) => item.model_id === modelId && item.source === source,
  );
  const coveragePct =
    coverage?.share === null || coverage?.share === undefined
      ? null
      : coverage.share * 100;
  const coverageResult = coverageStatus(coveragePct, coverage?.total || 0);
  document.querySelector("#forecast-coverage").textContent =
    coverageResult === "neutral" ? "n/a" : `${formatNumber(coveragePct)}%`;
  document.querySelector("#forecast-coverage-note").textContent =
    `last 7 days · ${source} · ${coverage?.total || 0} scored points`;
  applyTileStatus(
    document.querySelector("#forecast-coverage-tile"),
    coverageResult,
  );
}

async function renderForecast(controls, chart, request) {
  const date = controls.date();
  const requestedModel = controls.modelId();
  const selectedModel =
    requestedModel ||
    controls.models().find((item) => item.is_champion)?.model_id;
  const forecast = await request(
    `/v1/forecast${queryString({
      date,
      model_id: selectedModel,
      source: controls.source(),
    })}`,
  ).catch((error) => {
    if (error.status === 404) {
      return null;
    }
    throw error;
  });

  if (!forecast?.points?.length) {
    controls.setSource(null);
    document.querySelector("#forecast-peak").textContent = "n/a MW";
    document.querySelector("#forecast-peak-time").textContent = "— IST";
    document.querySelector("#forecast-max-temp").textContent = "n/a °C";
    resetMetricTiles(null);
    return false;
  }

  const { points, model_id: modelId, source } = forecast;
  controls.setSource(source);
  const peak = points.reduce(
    (best, point) => (point.yhat_mw > best.yhat_mw ? point : best),
    points[0],
  );
  document.querySelector("#forecast-peak").textContent =
    `${formatNumber(peak.yhat_mw)} MW`;
  document.querySelector("#forecast-peak-time").textContent =
    `${String(peak.ts).slice(11, 16)} IST`;
  resetMetricTiles(source);

  const windowStart = dayOffset(date, -6);
  const query = queryString({
    start: windowStart,
    end: date,
    model_id: modelId,
    source,
  });
  const auxiliary = await Promise.allSettled([
    request(`/v1/weather${queryString({ date })}`),
    request(`/v1/accuracy/daily${query}`),
    request(`/v1/accuracy/coverage${query}`),
    request(`/v1/actuals${queryString({ start: date, end: date })}`),
  ]);
  const weather = auxiliary[0].status === "fulfilled" ? auxiliary[0].value : null;
  document.querySelector("#forecast-max-temp").textContent =
    weather?.max_temp_c === null || weather?.max_temp_c === undefined
      ? "n/a °C"
      : `${formatNumber(weather.max_temp_c)} °C`;
  document.querySelector("#forecast-weather-note").textContent =
    auxiliary[0].status === "rejected"
      ? `Weather unavailable: ${auxiliary[0].reason.message}`
      : "Weather forecast for selected date";
  if (auxiliary[1].status === "fulfilled" && auxiliary[2].status === "fulfilled") {
    updateAccuracyTiles(
      auxiliary[1].value.items,
      auxiliary[2].value.items,
      controls.models(),
      modelId,
      source,
    );
  } else {
    const error =
      auxiliary[1].status === "rejected"
        ? auxiliary[1].reason
        : auxiliary[2].reason;
    document.querySelector("#forecast-mape-note").textContent =
      `last 7 days · ${source} · unavailable: ${error.message}`;
    document.querySelector("#forecast-coverage-note").textContent =
      `last 7 days · ${source} · unavailable: ${error.message}`;
  }

  const actuals =
    auxiliary[3].status === "fulfilled" ? auxiliary[3].value.points : [];
  document.querySelector("#forecast-chart-note").textContent =
    auxiliary[3].status === "rejected"
      ? `Actual overlay unavailable: ${auxiliary[3].reason.message}`
      : `Forecast source: ${source}. Actual demand is shown when available.`;
  const actualByTimestamp = new Map(
    actuals.map((point) => [
      String(point.ts).replace(" ", "T").slice(0, 19),
      point.actual_mw,
    ]),
  );
  const labels = timeLabels(points);
  const traces = [];
  if (points.some((point) => point.q10_mw !== null && point.q90_mw !== null)) {
    traces.push(
      {
        x: labels,
        y: points.map((point) => point.q10_mw),
        type: "scatter",
        mode: "lines",
        line: { width: 0 },
        name: "q10",
      },
      {
        x: labels,
        y: points.map((point) => point.q90_mw),
        type: "scatter",
        mode: "lines",
        line: { width: 0 },
        fill: "tonexty",
        fillcolor: "rgba(115, 191, 105, 0.18)",
        name: "q10–q90",
      },
    );
  }
  traces.push({
    x: labels,
    y: points.map((point) => point.yhat_mw),
    type: "scatter",
    mode: "lines",
    line: { color: "#73bf69", width: 2 },
    name: `Forecast · ${source}`,
  });
  const actualValues = points.map((point) =>
    actualByTimestamp.get(String(point.ts).replace(" ", "T").slice(0, 19)) ?? null,
  );
  if (actualValues.some((value) => value !== null)) {
    traces.push({
      x: labels,
      y: actualValues,
      type: "scatter",
      mode: "lines",
      line: { color: "#5794f2", width: 1.5 },
      name: "Actual",
    });
  }
  await drawPlot(
    chart,
    traces,
    chartLayout({
      title: `${date} · ${modelId} · ${source}`,
      yTitle: "Demand (MW)",
      xTitle: "Time (IST)",
    }),
  );
  return true;
}

function loadDashboard(controls) {
  void runChart(
    chartRoot,
    (chart, request) => renderForecast(controls, chart, request),
    "No forecast data is available for this date and model.",
  );
}

initDashboardControls(loadDashboard);
