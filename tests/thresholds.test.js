import assert from "node:assert/strict";
import test from "node:test";

import {
  BASELINE_MODEL_ID,
  COVERAGE_BANDS,
  MAPE_GREEN_FACTOR,
  MIN_POINTS,
  NOMINAL_COVERAGE,
  coverageStatus,
  mapeStatus,
} from "../src/gridcast/static/js/thresholds.js";

test("threshold constants match the approved project rules", () => {
  assert.equal(NOMINAL_COVERAGE, 80);
  assert.equal(MAPE_GREEN_FACTOR, 1.15);
  assert.deepEqual(COVERAGE_BANDS, {
    greenMin: 75,
    greenMax: 85,
    yellowMin: 70,
    yellowMax: 90,
  });
  assert.equal(MIN_POINTS, 288);
  assert.equal(BASELINE_MODEL_ID, "blend_1d_1w");
});

test("MAPE status respects inclusive green and yellow boundaries", () => {
  const champ = 4;
  const base = 6;
  const greenLimit = Math.min(MAPE_GREEN_FACTOR * champ, base);

  assert.equal(mapeStatus(greenLimit, champ, base), "green");
  assert.equal(mapeStatus(greenLimit + 0.001, champ, base), "yellow");
  assert.equal(mapeStatus(base, champ, base), "yellow");
  assert.equal(mapeStatus(base + 0.001, champ, base), "red");
});

test("MAPE uses the baseline as the green ceiling when it is lower", () => {
  assert.equal(mapeStatus(8, 10, 8), "green");
  assert.equal(mapeStatus(8.001, 10, 8), "red");
});

test("MAPE status is neutral for absent or invalid inputs", () => {
  assert.equal(mapeStatus(null, 4, 6), "neutral");
  assert.equal(mapeStatus(4, null, 6), "neutral");
  assert.equal(mapeStatus(4, 4, undefined), "neutral");
});

test("coverage status respects inclusive nominal and caution boundaries", () => {
  assert.equal(coverageStatus(75, MIN_POINTS), "green");
  assert.equal(coverageStatus(85, MIN_POINTS), "green");
  assert.equal(coverageStatus(COVERAGE_BANDS.yellowMin, MIN_POINTS), "yellow");
  assert.equal(coverageStatus(COVERAGE_BANDS.yellowMax, MIN_POINTS), "yellow");
  assert.equal(coverageStatus(69.99, MIN_POINTS), "red");
  assert.equal(coverageStatus(90.01, MIN_POINTS), "red");
});

test("coverage status is neutral for missing and insufficient samples", () => {
  assert.equal(coverageStatus(null, MIN_POINTS), "neutral");
  assert.equal(coverageStatus(80, MIN_POINTS - 1), "neutral");
  assert.equal(coverageStatus(80, MIN_POINTS), "green");
});
