export const NOMINAL_COVERAGE = 80;
export const MAPE_GREEN_FACTOR = 1.15;
export const COVERAGE_BANDS = Object.freeze({
  greenMin: 75,
  greenMax: 85,
  yellowMin: 70,
  yellowMax: 90,
});
export const MIN_POINTS = 288;
export const BASELINE_MODEL_ID = "blend_1d_1w";

export function mapeStatus(value, champ, base) {
  if (
    !Number.isFinite(value) ||
    !Number.isFinite(champ) ||
    !Number.isFinite(base) ||
    value < 0 ||
    champ < 0 ||
    base < 0
  ) {
    return "neutral";
  }

  const greenLimit = Math.min(MAPE_GREEN_FACTOR * champ, base);
  if (value <= greenLimit) {
    return "green";
  }
  if (value <= base) {
    return "yellow";
  }
  return "red";
}

export function coverageStatus(pct, n) {
  if (!Number.isFinite(pct) || !Number.isFinite(n) || n < MIN_POINTS) {
    return "neutral";
  }
  if (pct >= COVERAGE_BANDS.greenMin && pct <= COVERAGE_BANDS.greenMax) {
    return "green";
  }
  if (
    (pct >= COVERAGE_BANDS.yellowMin && pct < COVERAGE_BANDS.greenMin) ||
    (pct > COVERAGE_BANDS.greenMax && pct <= COVERAGE_BANDS.yellowMax)
  ) {
    return "yellow";
  }
  return "red";
}
