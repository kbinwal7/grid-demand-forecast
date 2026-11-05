import assert from "node:assert/strict";
import test from "node:test";

import { createTabLoader, tabFromHash } from "../src/gridcast/static/js/tabs.js";
import { aggregateBiasCells } from "../src/gridcast/static/js/failures.js";

test("dashboard hashes resolve to their matching tabs", () => {
  for (const id of ["forecast", "accuracy", "failures", "models", "data-health"]) {
    assert.equal(tabFromHash(`#${id}`), id);
  }
  assert.equal(tabFromHash(""), "forecast");
  assert.equal(tabFromHash("#unknown"), "forecast");
});

test("a tab loads once until explicitly refreshed", async () => {
  let fetchCount = 0;
  const load = createTabLoader({
    forecast: async () => {
      fetchCount += 1;
    },
  });

  await load("forecast");
  await load("forecast");
  assert.equal(fetchCount, 1);
  await load("forecast", { refresh: true });
  assert.equal(fetchCount, 2);
});

test("heatmap combines source bias by scored interval count", () => {
  const result = aggregateBiasCells([
    { dow: 1, hour: 9, bias_mw: 10, n: 2, source: "live" },
    { dow: 1, hour: 9, bias_mw: -2, n: 6, source: "backtest" },
  ]);

  assert.equal(result.z[1][9], 1);
  assert.deepEqual(result.customdata[1][9], [8, "backtest, live"]);
  assert.equal(result.z[0][0], null);
});
