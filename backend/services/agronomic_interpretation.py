"""Deterministic, cautious, field-relative agronomic interpretation read model."""
from __future__ import annotations

import math
from datetime import date
from statistics import median
from typing import Iterable

INDEX_CODES = ("ndvi", "savi", "evi", "ndmi", "ndre")
HISTORY_RECORD_LIMIT = 120
BASELINE_MIN_PRIOR = 5
TREND_MIN_POINTS = 3
TREND_MAX_POINTS = 8
CHANGE_STABLE_EPSILON = 0.001
TREND_STABLE_SLOPE_PER_DAY = 0.0005
STALE_DAYS = 21
QUALITY_VALID_PIXELS_MIN = 50.0
QUALITY_CLOUD_MAX = 30.0
MAX_INDEX_CHECKS = 5
MAX_SUMMARY_CHECKS = 7

INDEX_METADATA = {
    "ndvi": ("NDVI", "Измеряет относительную зелёность и активность растительного покрова.", ["наблюдения за развитием покрова", "сравнения поля с его историей"], "Может насыщаться при плотном покрове и не определяет причину изменения."),
    "savi": ("SAVI", "Измеряет сигнал растительности с поправкой на влияние открытой почвы.", ["разреженного или развивающегося покрова", "условий с заметным фоном почвы"], "Результат зависит от принятой поправки и не заменяет осмотр поля."),
    "evi": ("EVI", "Измеряет активность растительности с меньшим насыщением при плотном покрове.", ["динамики плотного покрова", "сопоставления с NDVI"], "Чувствителен к качеству атмосферной коррекции и сцены."),
    "ndmi": ("NDMI", "Измеряет спектральный сигнал, связанный с влагой в растительном покрове.", ["относительной динамики влагового сигнала", "сопоставления с индексами растительности"], "Не является прямым измерением влажности почвы и не задаёт норму полива."),
    "ndre": ("NDRE", "Измеряет сигнал красного края, связанный с растительностью и хлорофиллом.", ["динамики развитого покрова", "сопоставления с NDVI"], "Не доказывает дефицит питания и зависит от культуры и стадии."),
}
TOP_LIMITATIONS = [
    "Спутниковые индексы являются косвенными спектральными сигналами.",
    "Гипотезы требуют проверки на поле и не являются диагнозом.",
    "Погода, почва, стадия развития и результаты осмотра в этом read model не подтверждены.",
    "Ответ не предписывает полив, удобрения, пестициды или сроки уборки.",
]
DISCLAIMER = "Интерпретация описывает сигналы данных, а не диагноз; возможные причины необходимо подтвердить осмотром поля."
DEFAULT_CHECKS = ["сравнить с следующим безоблачным наблюдением", "осмотреть репрезентативные зоны с низкими и высокими значениями"]


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rounded(value):
    number = finite_number(value)
    return round(number, 4) if number is not None else None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile needs at least one value")
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latest_percentile(values: list[float], latest: float) -> float | None:
    if not values:
        return None
    below = sum(value < latest for value in values)
    equal = sum(value == latest for value in values)
    return round(100.0 * (below + 0.5 * equal) / len(values), 2)


def _prepare_rows(rows: Iterable) -> tuple[list, bool]:
    valid = sorted((row for row in rows if finite_number(getattr(row, "value", None)) is not None), key=lambda row: (row.captured_date, row.id))
    duplicate = any(valid[index - 1].captured_date == valid[index].captured_date for index in range(1, len(valid)))
    canonical = []
    seen = set()
    for row in valid:
        if row.captured_date not in seen:
            canonical.append(row)
            seen.add(row.captured_date)
    return canonical, duplicate


def calculate_change(latest, previous) -> dict:
    result = {"absolute": None, "percent": None, "direction": "unknown", "days_between": None, "statistical_signal": "insufficient"}
    if not latest or not previous:
        return result
    current, prior = float(latest.value), float(previous.value)
    delta = current - prior
    direction = "stable" if abs(delta) <= CHANGE_STABLE_EPSILON else "rising" if delta > 0 else "falling"
    percent = None if abs(prior) <= CHANGE_STABLE_EPSILON else delta / abs(prior) * 100.0
    result.update(absolute=rounded(delta), percent=rounded(percent), direction=direction, days_between=(latest.captured_date - previous.captured_date).days)
    return result


def calculate_baseline(prior_rows: list, latest_value: float | None) -> dict:
    values = [float(row.value) for row in prior_rows]
    result = {"status": "insufficient_history", "position_status": "insufficient_history", "sample_count": len(values), "point_count": len(values), "median": None, "p25": None, "p75": None, "min": None, "max": None, "minimum": None, "maximum": None, "latest_percentile": None, "deviation_from_median": None, "mad": None}
    if len(values) < BASELINE_MIN_PRIOR:
        return result
    middle, low, high = median(values), percentile(values, .25), percentile(values, .75)
    position_status = "below_field_range" if latest_value < low else "above_field_range" if latest_value > high else "within_field_range"
    result.update(status="available", position_status=position_status, median=rounded(middle), p25=rounded(low), p75=rounded(high), min=rounded(min(values)), max=rounded(max(values)), minimum=rounded(min(values)), maximum=rounded(max(values)), latest_percentile=latest_percentile(values, latest_value), deviation_from_median=rounded(latest_value - middle), mad=rounded(median(abs(value - middle) for value in values)))
    return result


def calculate_trend(rows: list) -> dict:
    recent = rows[-TREND_MAX_POINTS:]
    result = {"sample_count": len(recent), "observation_count": len(recent), "start_date": None, "end_date": None, "duration_days": None, "slope_per_day": None, "slope_per_10_days": None, "direction": "unknown", "strength": "unknown", "status": "insufficient"}
    if len(recent) < TREND_MIN_POINTS:
        return result
    x = [(row.captured_date - recent[0].captured_date).days for row in recent]
    y = [float(row.value) for row in recent]
    mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((value - mean_x) ** 2 for value in x)
    slope = 0.0 if denominator == 0 else sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / denominator
    direction = "stable" if abs(slope) <= TREND_STABLE_SLOPE_PER_DAY else "rising" if slope > 0 else "falling"
    magnitude = abs(slope) * max(x[-1], 1)
    strength = "weak" if magnitude < .03 else "moderate" if magnitude < .1 else "strong"
    result.update(status="available", start_date=recent[0].captured_date, end_date=recent[-1].captured_date, duration_days=x[-1], slope_per_day=rounded(slope), slope_per_10_days=rounded(slope * 10), direction=direction, strength=strength)
    return result


def calculate_heterogeneity(latest, code: str) -> tuple[dict, list[str]]:
    unknown = {"available": False, "spread": None, "relative_spread": None, "classification": "unknown", "evidence": []}
    if latest is None:
        return unknown, []
    minimum, maximum = finite_number(getattr(latest, "min_value", None)), finite_number(getattr(latest, "max_value", None))
    std = finite_number(getattr(latest, "std_value", None))
    p10, p90 = finite_number(getattr(latest, "p10_value", None)), finite_number(getattr(latest, "p90_value", None))
    center = abs(float(latest.value))
    if p10 is not None and p90 is not None:
        spread, evidence, limitation = p90 - p10, ["p10_p90"], []
    elif minimum is not None and maximum is not None:
        spread, evidence, limitation = maximum - minimum, ["min_max_range"], ["Классификация неоднородности основана только на диапазоне min/max."]
    elif std is not None:
        spread, evidence, limitation = std * 2, ["standard_deviation"], []
    else:
        return unknown, ["Дисперсия последнего наблюдения недоступна."]
    relative = None if center <= CHANGE_STABLE_EPSILON else spread / center
    metric = relative if relative is not None else abs(spread)
    classification = "low" if metric < .2 else "moderate" if metric < .5 else "high"
    return {"available": True, "spread": rounded(spread), "relative_spread": rounded(relative), "classification": classification, "evidence": evidence}, limitation


def calculate_confidence(rows: list, latest, today: date, dispersion_available: bool, context: dict) -> tuple[dict, list[str], int]:
    if latest is None:
        return {"score": 0, "level": "insufficient", "contextual_level": "none", "reasons": ["no_data"]}, ["no_data"], 0
    freshness = max(0, (today - latest.captured_date).days)
    cloud, pixels = finite_number(getattr(latest, "cloud_cover_pct", None)), finite_number(getattr(latest, "valid_pixels_pct", None))
    flags = []
    if len(rows) == 1: flags.append("single_observation")
    if len(rows) - 1 < BASELINE_MIN_PRIOR: flags.append("insufficient_history")
    if freshness > STALE_DAYS: flags.append("stale")
    if pixels is not None and pixels < QUALITY_VALID_PIXELS_MIN: flags.append("low_valid_pixels")
    if cloud is not None and cloud > QUALITY_CLOUD_MAX: flags.append("high_cloud_cover")
    if not dispersion_available: flags.append("missing_dispersion")
    if latest.captured_date > today: flags.append("future_observation_date")
    score = 20 + min(30, len(rows) * 5)
    score += 15 if freshness <= 7 else 8 if freshness <= STALE_DAYS else 0
    score += 10 if pixels is not None and pixels >= 80 else 4 if pixels is not None and pixels >= QUALITY_VALID_PIXELS_MIN else 0
    score += 10 if cloud is not None and cloud <= 10 else 5 if cloud is not None and cloud <= QUALITY_CLOUD_MAX else 0
    score += 5 if dispersion_available else 0
    score += 5 if context.get("crop_available") else 0
    score += 5 if context.get("growth_stage_available") else 0
    score = max(0, min(100, int(score)))
    level = "low" if len(rows) == 1 or score < 50 else "medium" if score < 75 else "high"
    return {"score": score, "level": level, "contextual_level": level, "reasons": flags or ["fresh_dense_clean_history"]}, flags, freshness


def build_index_interpretation(code: str, rows: Iterable, today: date, context: dict | None = None) -> dict:
    context = context or {}
    observations, duplicate = _prepare_rows(rows)
    latest = observations[-1] if observations else None
    previous = observations[-2] if len(observations) > 1 else None
    heterogeneity, heterogeneity_limits = calculate_heterogeneity(latest, code)
    confidence, quality_flags, freshness = calculate_confidence(observations, latest, today, heterogeneity["available"], context)
    if duplicate: quality_flags.append("duplicate_date")
    change = calculate_change(latest, previous)
    baseline = calculate_baseline(observations[:-1], float(latest.value) if latest else None)
    trend = calculate_trend(observations)
    if baseline["status"] == "available" and latest:
        scale = max((baseline["p75"] - baseline["p25"]), CHANGE_STABLE_EPSILON)
        deviation = abs(float(latest.value) - baseline["median"]) / scale
        change["statistical_signal"] = "strong" if deviation >= 2 else "notable" if deviation >= 1 else "normal"
    label, meaning, useful_for, metadata_limit = INDEX_METADATA[code]
    latest_payload = None if latest is None else {"captured_date": latest.captured_date, "observed_at": latest.captured_date, "value": rounded(latest.value), "min_value": rounded(getattr(latest, "min_value", None)), "max_value": rounded(getattr(latest, "max_value", None)), "valid_pixels_pct": rounded(getattr(latest, "valid_pixels_pct", None)), "cloud_cover_pct": rounded(getattr(latest, "cloud_cover_pct", None)), "satellite": getattr(latest, "satellite", None), "freshness_days": freshness}
    previous_payload = None if previous is None else {"captured_date": previous.captured_date, "observed_at": previous.captured_date, "value": rounded(previous.value), "min_value": rounded(getattr(previous, "min_value", None)), "max_value": rounded(getattr(previous, "max_value", None)), "valid_pixels_pct": rounded(getattr(previous, "valid_pixels_pct", None)), "cloud_cover_pct": rounded(getattr(previous, "cloud_cover_pct", None))}
    limitations = [metadata_limit] + heterogeneity_limits
    if duplicate: limitations.append("Обнаружены повторяющиеся даты; использована первая строка в стабильном порядке id.")
    status = "no_data" if not latest else "insufficient_history" if len(observations) == 1 else "stale" if freshness > STALE_DAYS else "valid"
    return {"index_code": code, "display_name": label, "plain_language_meaning": meaning, "code": code, "label": label, "meaning": meaning, "useful_for": useful_for, "data_status": status, "latest": latest_payload, "latest_observation": latest_payload, "previous": previous_payload, "previous_valid_observation": previous_payload, "change": change, "baseline": baseline, "trend": trend, "heterogeneity": heterogeneity, "data_quality": {"observation_count": len(observations), "valid_pixels_pct": latest_payload["valid_pixels_pct"] if latest_payload else None, "cloud_cover_pct": latest_payload["cloud_cover_pct"] if latest_payload else None, "freshness_days": freshness if latest else None, "quality_flags": quality_flags}, "confidence": confidence, "hypotheses": [], "recommended_checks": DEFAULT_CHECKS.copy() if latest else [], "limitations": limitations}


def _hypothesis(code, title, reason, signals, missing, confidence="low"):
    return {"code": code, "title": title, "label": title, "reason": reason, "rationale": reason, "supporting_signals": signals, "supporting_indices": signals, "contradicting_or_missing_context": missing, "confidence": "medium" if confidence == "medium" else "low", "requires_field_check": True, "recommended_checks": []}


def add_cross_index_hypotheses(items: list[dict]) -> None:
    notable = [item for item in items if item["confidence"].get("contextual_level", "none") != "none" and item["change"]["statistical_signal"] in {"notable", "strong"}]
    directions = {direction: [item for item in notable if item["change"]["direction"] == direction] for direction in ("rising", "falling")}
    for direction, matching in directions.items():
        if len(matching) < 2:
            continue
        codes = [item["index_code"] for item in matching]
        hypothesis = _hypothesis("possible_canopy_change", "Возможное изменение растительного покрова", "Несколько индексов согласованно изменились относительно истории этого поля.", codes, ["growth_stage", "weather", "inspection_evidence"], "medium" if len(codes) >= 3 else "low")
        for item in matching:
            item["hypotheses"].append(hypothesis.copy())
            item["recommended_checks"] = list(dict.fromkeys(item["recommended_checks"] + ["проверить недавние полевые операции", "осмотреть покров, вредителей и симптомы болезней"]))[:MAX_INDEX_CHECKS]
    for item in items:
        if "high_cloud_cover" in item["data_quality"]["quality_flags"]:
            item["hypotheses"].append(_hypothesis("possible_cloud_or_data_artifact", "Возможный артефакт облаков или тени", "Качество последнего наблюдения ограничено облачностью.", [item["index_code"]], ["cloud_free_comparison"]))
            item["recommended_checks"] = list(dict.fromkeys(item["recommended_checks"] + ["проверить наличие облаков или теней на изображении"]))[:MAX_INDEX_CHECKS]


def overall_confidence(items: list[dict]) -> str:
    available = [item["confidence"]["level"] for item in items if item.get("latest", item.get("latest_observation"))]
    if not available:
        return "insufficient"
    order = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}
    return min(available, key=lambda level: order.get(level, 0))


def build_summary(items: list[dict]) -> dict:
    with_data = [item for item in items if item["latest"]]
    sufficient = [item for item in items if item["baseline"]["status"] == "available"]
    latest_dates = [item["latest"]["captured_date"] for item in with_data]
    scores = [item["confidence"]["score"] for item in with_data]
    confidence_score = min(scores) if scores else 0
    level = "none" if not scores else "low" if confidence_score < 50 else "medium" if confidence_score < 75 else "high"
    attention = any(item["baseline"]["position_status"] in {"above_field_range", "below_field_range"} and item["confidence"].get("contextual_level") in {"medium", "high"} for item in items)
    status = "insufficient_data" if not with_data else "attention" if attention else "monitor"
    checks = list(dict.fromkeys(check for item in items for check in item["recommended_checks"]))[:MAX_SUMMARY_CHECKS]
    signals = [f"{item['index_code']}:{item['baseline']['position_status']}" for item in items if item["baseline"]["position_status"] in {"above_field_range", "below_field_range"}]
    latest = max(latest_dates) if latest_dates else None
    freshness = min((item["data_quality"]["freshness_days"] for item in with_data), default=None)
    return {"status": status, "confidence": level, "confidence_score": confidence_score, "latest_observation_date": latest, "freshness_days": freshness, "indices_with_data": len(with_data), "indices_with_sufficient_history": len(sufficient), "primary_signals": signals, "recommended_next_checks": checks, "disclaimer": DISCLAIMER}
