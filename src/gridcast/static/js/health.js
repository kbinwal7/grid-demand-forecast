import { formatNumber, formatTimestamp } from "./api.js";
import { chartLayout, drawPlot, runChart, runPanel } from "./charts.js";

function setHealthMetrics(data) {
  document.querySelector("#health-last-ingested").textContent =
    data.last_ingested_at
      ? `${formatTimestamp(data.last_ingested_at)} IST`
      : "n/a";
  document.querySelector("#health-missing-count").textContent = formatNumber(
    data.missing_days.length,
    0,
  );
  document.querySelector("#health-demand-rows").textContent = formatNumber(
    data.demand_rows,
    0,
  );
  document.querySelector("#health-weather-rows").textContent = formatNumber(
    data.weather_rows,
    0,
  );
  document.querySelector("#health-weather-coverage").textContent =
    data.weather_rows > 0 ? "Available" : "No records";
  document.querySelector("#health-forecast-rows").textContent = formatNumber(
    data.forecast_rows,
    0,
  );
}

async function loadHealth(request) {
  const data = await request("/v1/data-health");
  setHealthMetrics(data);
  return data;
}

async function renderCountChart(chart, getHealth) {
  const data = await getHealth();
  await drawPlot(
    chart,
    [
      {
        x: ["Demand", "Weather", "Forecasts · live + backtest"],
        y: [data.demand_rows, data.weather_rows, data.forecast_rows],
        type: "bar",
        marker: { color: ["#5794f2", "#b877d9", "#73bf69"] },
        hovertemplate: "%{x}<br>%{y:,} rows<extra></extra>",
      },
    ],
    chartLayout({
      title: "Registered table row counts",
      yTitle: "Rows",
      xTitle: "",
    }),
  );
  return true;
}

async function renderMissingDays(root, getHealth) {
  const data = await getHealth();
  const list = root.querySelector("#missing-days-list");
  list.replaceChildren();
  if (!data.missing_days.length) {
    const item = document.createElement("li");
    item.textContent = "No missing demand days in the observed date range.";
    list.append(item);
    return true;
  }
  for (const day of data.missing_days) {
    const item = document.createElement("li");
    item.textContent = day;
    list.append(item);
  }
  return true;
}

export function renderPage() {
  let healthPromise;
  const getHealth = (request) => {
    healthPromise ??= loadHealth(request);
    return healthPromise;
  };
  return Promise.all([
    runChart(
      document.querySelector("#health-chart-root"),
      (chart, request) => renderCountChart(chart, () => getHealth(request)),
      "No data-health metrics are available.",
    ),
    runPanel(
      document.querySelector("#missing-days-root"),
      (root, request) => renderMissingDays(root, () => getHealth(request)),
      "Missing demand-day information is unavailable.",
    ),
  ]);
}
