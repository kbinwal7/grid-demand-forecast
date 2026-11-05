import { formatNumber, formatTimestamp } from "./api.js";
import { chartLayout, drawPlot, runChart, runPanel } from "./charts.js";
import { initDashboardControls } from "./controls.js";

function appendCell(row, value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  row.append(cell);
}

async function renderModelChart(controls, chart) {
  const models = controls.models();
  const withMetrics = models.filter(
    (model) => model.cv_mape !== null || model.cv_peak_mape !== null,
  );
  if (!withMetrics.length) {
    return false;
  }
  await drawPlot(
    chart,
    [
      {
        x: withMetrics.map((model) => model.model_id),
        y: withMetrics.map((model) => model.cv_mape),
        type: "bar",
        name: "CV MAPE",
        marker: { color: "#5794f2" },
      },
      {
        x: withMetrics.map((model) => model.model_id),
        y: withMetrics.map((model) => model.cv_peak_mape),
        type: "bar",
        name: "CV peak MAPE · 18:00–22:00",
        marker: { color: "#ff9830" },
      },
    ],
    {
      ...chartLayout({
        title: "Stored cross-validation metrics · backtest",
        yTitle: "MAPE (%)",
        xTitle: "Model",
      }),
      barmode: "group",
    },
  );
  return true;
}

function renderModelTable(root, controls) {
  const models = controls.models();
  const table = root.querySelector("#models-table");
  const body = root.querySelector("#models-body");
  body.replaceChildren();
  for (const model of models) {
    const row = document.createElement("tr");
    appendCell(row, model.model_id);
    appendCell(row, model.algo);
    appendCell(row, model.feature_set || "n/a");
    appendCell(row, `${formatTimestamp(model.trained_at)} IST`);
    appendCell(
      row,
      model.train_start && model.train_end
        ? `${model.train_start} – ${model.train_end}`
        : "n/a",
    );
    appendCell(
      row,
      model.cv_mape === null ? "n/a" : `${formatNumber(model.cv_mape)}%`,
    );
    appendCell(
      row,
      model.cv_peak_mape === null
        ? "n/a"
        : `${formatNumber(model.cv_peak_mape)}%`,
    );
    appendCell(
      row,
      model.cv_mase === null ? "n/a" : formatNumber(model.cv_mase),
    );
    appendCell(row, model.is_champion ? "Champion" : "—");
    body.append(row);
  }
  return models.length > 0;
}

function renderPage(controls) {
  void Promise.all([
    runChart(
      document.querySelector("#models-chart-root"),
      (chart) => renderModelChart(controls, chart),
      "No cross-validation metrics are available.",
    ),
    runPanel(
      document.querySelector("#models-table-root"),
      (root) => renderModelTable(root, controls),
      "No model versions are registered.",
    ),
  ]);
}

initDashboardControls(renderPage);
