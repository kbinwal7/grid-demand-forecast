from gridcast.api.kpis import (
    MAPE_GREEN_FACTOR,
    MIN_COVERAGE_POINTS,
    coverage_status,
    freshness_status,
    mape_status,
)


def test_mape_green_yellow_red_boundaries():
    champ, base = 4.0, 6.0
    green_limit = min(MAPE_GREEN_FACTOR * champ, base)
    assert mape_status(green_limit, champ, base) == "green"
    assert mape_status(green_limit + 0.001, champ, base) == "yellow"
    assert mape_status(base, champ, base) == "yellow"
    assert mape_status(base + 0.001, champ, base) == "red"


def test_mape_baseline_caps_green_and_missing_inputs_are_neutral():
    assert mape_status(8, 10, 8) == "green"
    assert mape_status(8.001, 10, 8) == "red"
    assert mape_status(None, 4, 6) == "neutral"
    assert mape_status(4, None, 6) == "neutral"


def test_coverage_bands_and_minimum_sample():
    n = MIN_COVERAGE_POINTS
    assert coverage_status(75, n) == coverage_status(85, n) == "green"
    assert coverage_status(70, n) == coverage_status(90, n) == "yellow"
    assert coverage_status(69.99, n) == coverage_status(90.01, n) == "red"
    assert coverage_status(80, n - 1) == "neutral"
    assert coverage_status(None, n) == "neutral"


def test_freshness():
    assert freshness_status(1, 0) == "green"
    assert freshness_status(1, 2) == "yellow"
    assert freshness_status(5, 0) == "red"
    assert freshness_status(None, 0) == "neutral"
