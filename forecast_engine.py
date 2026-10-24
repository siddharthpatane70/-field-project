"""Evidence-led monthly demand forecasting for the TVS Parts Hub.

This module intentionally uses only the Python standard library. It does not
load the legacy pickle artifact: that artifact forecasts daily rows and its
metadata is not a validated measure of monthly SKU performance.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
import statistics
from typing import Any


class ForecastInputError(ValueError):
    """A forecast request cannot be supported by the supplied data."""


def _month_start(value: dt.date) -> dt.date:
    return value.replace(day=1)


def _add_months(value: dt.date, count: int) -> dt.date:
    month_index = value.year * 12 + value.month - 1 + count
    year, month_zero = divmod(month_index, 12)
    return dt.date(year, month_zero + 1, 1)


def _month_label(value: dt.date) -> str:
    return value.strftime("%Y-%m")


def _ceil_nonnegative(value: float) -> int:
    return max(0, int(math.ceil(value)))


def _clean_quantity(value: Any) -> int | None:
    try:
        quantity = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return quantity if quantity >= 0 else None


def load_monthly_series(conn: Any, part_id: int, today: dt.date | None = None) -> dict[str, Any]:
    """Read and aggregate valid, non-future sales rows for one inventory SKU."""
    today = today or dt.date.today()
    rows = conn.execute(
        "SELECT sale_date, quantity FROM sales WHERE part_id = ? ORDER BY sale_date",
        (str(part_id),),
    ).fetchall()
    totals: dict[dt.date, int] = {}
    invalid_rows = 0
    future_rows = 0
    transaction_rows = 0
    last_sale_date: dt.date | None = None
    for row in rows:
        try:
            sale_date = dt.date.fromisoformat(str(row["sale_date"])[:10])
        except (TypeError, ValueError):
            invalid_rows += 1
            continue
        quantity = _clean_quantity(row["quantity"])
        if quantity is None:
            invalid_rows += 1
            continue
        if sale_date > today:
            future_rows += 1
            continue
        month = _month_start(sale_date)
        totals[month] = totals.get(month, 0) + quantity
        transaction_rows += 1
        if last_sale_date is None or sale_date > last_sale_date:
            last_sale_date = sale_date

    if not totals:
        return {
            "values": [], "history": [], "months_with_sales": 0,
            "transaction_rows": transaction_rows, "invalid_rows": invalid_rows,
            "future_rows": future_rows, "last_sale_date": None,
        }

    first_month = min(totals)
    last_month = max(totals)
    history: list[dict[str, Any]] = []
    cursor = first_month
    while cursor <= last_month:
        history.append({"month": _month_label(cursor), "units": totals.get(cursor, 0)})
        cursor = _add_months(cursor, 1)

    return {
        "values": [entry["units"] for entry in history],
        "history": history,
        "months_with_sales": len(totals),
        "transaction_rows": transaction_rows,
        "invalid_rows": invalid_rows,
        "future_rows": future_rows,
        "last_sale_date": last_sale_date.isoformat() if last_sale_date else None,
    }


def _level_and_trend(values: list[float]) -> tuple[float, float]:
    """Robust recency-weighted level and damped local trend."""
    if not values:
        return 0.0, 0.0
    recent = values[-12:]
    clipped = list(recent)
    if len(recent) >= 5:
        median = statistics.median(recent)
        deviations = [abs(value - median) for value in recent]
        mad = statistics.median(deviations)
        if mad > 0:
            cap = 4.0 * 1.4826 * mad
            clipped = [min(median + cap, max(0.0, median - cap, value)) for value in recent]

    alpha = 0.35
    level = clipped[0]
    for value in clipped[1:]:
        level = alpha * value + (1.0 - alpha) * level
    recent_mean = statistics.mean(clipped[-3:])
    level = 0.65 * level + 0.35 * recent_mean

    trend = 0.0
    if len(clipped) >= 6:
        previous_mean = statistics.mean(clipped[-6:-3])
        trend = (recent_mean - previous_mean) / 3.0
        trend_limit = max(1.0, level * 0.20)
        trend = min(trend_limit, max(-trend_limit, trend))
    return max(0.0, level), trend


def _seasonality_factor(values: list[float], target_month: int) -> float:
    """Return a conservative month-of-year factor only after two full years."""
    if len(values) < 24:
        return 1.0
    global_mean = statistics.mean(values[-36:])
    if global_mean <= 0:
        return 1.0
    matching = [value for index, value in enumerate(values) if (_add_months(dt.date(2000, 1, 1), index).month == target_month)]
    if len(matching) < 2:
        return 1.0
    raw_factor = statistics.mean(matching[-3:]) / global_mean
    return min(1.6, max(0.6, 0.5 + 0.5 * raw_factor))


def _predict_values(values: list[float], month_numbers: list[int]) -> list[float]:
    level, trend = _level_and_trend(values)
    predictions: list[float] = []
    for step, month_number in enumerate(month_numbers, start=1):
        damped_trend = trend * sum(0.65 ** power for power in range(step))
        seasonal = _seasonality_factor(values, month_number) if len(values) >= 24 else 1.0
        predictions.append(max(0.0, (level + damped_trend) * seasonal))
    return predictions


def _backtest(values: list[float], month_labels: list[str]) -> dict[str, Any] | None:
    if len(values) < 6:
        return None
    start = max(3, len(values) - 6)
    errors: list[float] = []
    actuals: list[float] = []
    for target_index in range(start, len(values)):
        history = values[:target_index]
        target_month = dt.date.fromisoformat(month_labels[target_index] + "-01").month
        predicted = _predict_values(history, [target_month])[0]
        errors.append(abs(values[target_index] - predicted))
        actuals.append(values[target_index])
    actual_total = sum(actuals)
    return {
        "periods": len(errors),
        "mae_units": round(statistics.mean(errors), 2) if errors else None,
        "wape_percent": round(100.0 * sum(errors) / actual_total, 2) if actual_total > 0 else None,
    }


def _series_forecast(series: dict[str, Any], horizon: int, today: dt.date) -> dict[str, Any]:
    values = [float(value) for value in series["values"]]
    history = series["history"]
    start_month = _add_months(_month_start(today), 1)
    months = [_add_months(start_month, offset) for offset in range(horizon)]
    predictions = _predict_values(values, [month.month for month in months])
    month_labels = [entry["month"] for entry in history]
    validation = _backtest(values, month_labels)
    has_backtest = validation is not None and validation["periods"] >= 3

    if len(values) >= 24 and has_backtest:
        confidence = "moderate"
    elif len(values) >= 12 and has_backtest:
        confidence = "low to moderate"
    elif len(values) >= 3:
        confidence = "low"
    else:
        confidence = "very low"

    error_scale = validation["mae_units"] if has_backtest and validation["mae_units"] is not None else 0.0
    uncertainty_floor = max(1.0, (statistics.mean(values[-3:]) if values else 0.0) * 0.50)
    forecasts = []
    for step, (month, predicted) in enumerate(zip(months, predictions), start=1):
        # This is a planning range, not a calibrated statistical confidence interval.
        width = max(error_scale * math.sqrt(step), uncertainty_floor * math.sqrt(step))
        if len(values) < 12:
            width = max(width, predicted * 0.75)
        forecasts.append({
            "month": _month_label(month),
            "units": _ceil_nonnegative(predicted),
            "range_low": _ceil_nonnegative(max(0.0, predicted - width)),
            "range_high": _ceil_nonnegative(predicted + width),
        })

    warnings: list[str] = []
    if len(values) < 6:
        warnings.append("Fewer than six monthly observations are available; this is a directional estimate, not a validated forecast.")
    if not has_backtest:
        warnings.append("There is not enough history for a rolling-origin accuracy check, so no accuracy score is shown.")
    if series["last_sale_date"]:
        age_days = (today - dt.date.fromisoformat(series["last_sale_date"])).days
        if age_days > 60:
            warnings.append(f"The latest recorded sale is {age_days} days old. Import recent sales before relying on this projection.")
    warnings.append("Sales rows record transactions; an unrecorded month cannot be distinguished from a true zero-demand month.")

    method = "Damped-trend monthly baseline"
    if len(values) >= 24:
        method += " with conservative annual seasonality"
    return {
        "forecast": forecasts,
        "history": history[-24:],
        "validation": validation if has_backtest else None,
        "confidence": confidence,
        "method": method,
        "history_months": len(values),
        "months_with_sales": series["months_with_sales"],
        "transaction_rows": series["transaction_rows"],
        "last_sale_date": series["last_sale_date"],
        "warnings": warnings,
    }


def monthly_forecast(
    conn: Any,
    part_id: int,
    horizon: int = 6,
    analog_part_id: int | None = None,
    baseline_estimate: float | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Forecast a SKU using its own history, a selected same-category analog,
    or an explicit user-entered cold-start scenario.
    """
    today = today or dt.date.today()
    if not 1 <= horizon <= 12:
        raise ForecastInputError("horizon must be between 1 and 12 months.")

    item = conn.execute("SELECT * FROM inventory WHERE id = ?", (part_id,)).fetchone()
    if not item:
        raise ForecastInputError("The selected inventory item does not exist.")
    item_dict = dict(item)
    series = load_monthly_series(conn, part_id, today)

    if series["values"]:
        result = _series_forecast(series, horizon, today)
        result.update({"forecast_available": True, "source": "sku_history", "source_part_id": part_id})
    elif baseline_estimate is not None:
        try:
            baseline = float(baseline_estimate)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ForecastInputError("baseline_estimate must be a finite, non-negative number.") from exc
        if not math.isfinite(baseline) or baseline < 0 or baseline > 1_000_000:
            raise ForecastInputError("baseline_estimate must be between 0 and 1,000,000 units per month.")
        start_month = _add_months(_month_start(today), 1)
        forecasts = []
        width = max(2.0, baseline * 0.75)
        for offset in range(horizon):
            month = _add_months(start_month, offset)
            forecasts.append({
                "month": _month_label(month),
                "units": _ceil_nonnegative(baseline),
                "range_low": _ceil_nonnegative(max(0.0, baseline - width)),
                "range_high": _ceil_nonnegative(baseline + width),
            })
        result = {
            "forecast_available": True,
            "forecast": forecasts,
            "history": [],
            "validation": None,
            "confidence": "user estimate only",
            "method": "Manual monthly baseline scenario",
            "source": "manual_baseline",
            "source_part_id": None,
            "history_months": 0,
            "months_with_sales": 0,
            "transaction_rows": 0,
            "last_sale_date": None,
            "warnings": ["No sales history is linked to this product. Forecast values are your entered planning estimate and have not been model-validated."],
        }
    elif analog_part_id is not None:
        analog = conn.execute("SELECT * FROM inventory WHERE id = ?", (analog_part_id,)).fetchone()
        if not analog:
            raise ForecastInputError("The selected comparable product does not exist.")
        if str(analog["category"]).strip().casefold() != str(item_dict["category"]).strip().casefold():
            raise ForecastInputError("Choose a comparable product in the same category.")
        analog_series = load_monthly_series(conn, analog_part_id, today)
        if len(analog_series["values"]) < 3:
            raise ForecastInputError("The selected comparable product has fewer than three months of recorded history.")
        result = _series_forecast(analog_series, horizon, today)
        for entry in result["forecast"]:
            entry["range_low"] = _ceil_nonnegative(entry["units"] - max(entry["units"] - entry["range_low"], entry["units"] * 0.5))
            entry["range_high"] = _ceil_nonnegative(entry["units"] + max(entry["range_high"] - entry["units"], entry["units"] * 0.5))
        result["confidence"] = "low (transferred from comparable product)"
        result["method"] = "Same-category comparable product transfer"
        result["source"] = "category_analog"
        result["source_part_id"] = analog_part_id
        result["warnings"].insert(0, f"No sales are linked to this item. This forecast transfers the history of {analog['part_name']} and widens the planning range.")
        result["forecast_available"] = True
    else:
        peers = conn.execute(
            "SELECT id, part_name, category, unit_price FROM inventory WHERE id <> ? AND lower(trim(category)) = lower(trim(?)) ORDER BY id",
            (part_id, item_dict["category"]),
        ).fetchall()
        peer_candidates = []
        for peer in peers:
            peer_series = load_monthly_series(conn, peer["id"], today)
            if len(peer_series["values"]) >= 3:
                peer_candidates.append((peer, peer_series))
        if len(peer_candidates) == 1:
            peer, peer_series = peer_candidates[0]
            result = _series_forecast(peer_series, horizon, today)
            for entry in result["forecast"]:
                entry["range_low"] = _ceil_nonnegative(entry["units"] - max(entry["units"] - entry["range_low"], entry["units"] * 0.5))
                entry["range_high"] = _ceil_nonnegative(entry["units"] + max(entry["range_high"] - entry["units"], entry["units"] * 0.5))
            result["confidence"] = "low (transferred from comparable product)"
            result["method"] = "Same-category comparable product transfer"
            result["source"] = "category_analog"
            result["source_part_id"] = peer["id"]
            result["warnings"].insert(0, f"No sales are linked to this item. This forecast transfers the history of {peer['part_name']} and widens the planning range.")
            result["forecast_available"] = True
        elif len(peer_candidates) > 1:
            raise ForecastInputError("Several same-category products have sales history. Select the comparable product to use, or enter a monthly planning estimate.")
        else:
            return {
                "forecast_available": False,
                "item": item_dict,
                "forecast": [],
                "history": [],
                "validation": None,
                "confidence": "not available",
                "method": "Insufficient linked sales history",
                "source": "none",
                "source_part_id": None,
                "history_months": 0,
                "months_with_sales": 0,
                "transaction_rows": 0,
                "last_sale_date": None,
                "warnings": ["No sales are linked to this product, and no same-category comparable with at least three months of history was found. Select a comparable item after recording history, or enter a clearly labeled manual baseline."],
            }

    result["item"] = item_dict
    forecasts = result["forecast"]
    first_forecast = forecasts[0] if forecasts else {"units": 0, "range_high": 0}
    lead_demand = sum(entry["units"] for entry in forecasts[: min(3, horizon)])
    first_month_buffer = max(0, first_forecast["range_high"] - first_forecast["units"])
    reorder_target = max(int(item_dict["reorder_level"]), lead_demand + first_month_buffer)
    result["reorder"] = {
        "current_stock": int(item_dict["current_stock"]),
        "lead_demand_units": int(lead_demand),
        "target_stock": int(reorder_target),
        "suggested_order_units": max(0, int(reorder_target - int(item_dict["current_stock"]))),
        "basis": "Forecast demand for up to three months plus first-month planning-range buffer; review supplier lead time before ordering.",
    }
    if int(item_dict["current_stock"]) < 0:
        result["warnings"].append("Inventory stock is negative in the database. Reconcile stock before using the reorder suggestion.")
    if result.get("source") == "manual_baseline":
        result["reorder"]["basis"] = "Manual estimate scenario only; confirm the quantity before ordering."
    return result

