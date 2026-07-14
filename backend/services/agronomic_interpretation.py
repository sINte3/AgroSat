"""Deterministic, read-only field-relative satellite interpretation helpers."""
from __future__ import annotations

import math
from datetime import date
from statistics import median
from typing import Iterable

INDEX_CODES = ("ndvi", "savi", "evi", "ndmi", "ndre")
INDEX_METADATA = {
    "ndvi": ("NDVI", "Спектральный сигнал общей зелёности и активности растительного покрова.", ["отслеживания развития полога", "сравнения поля с его собственными предыдущими наблюдениями", "выявления изменения, которое требует проверки"]),
    "savi": ("SAVI", "Спектральный сигнал растительности с уменьшенным влиянием открытой почвы.", ["разреженного или развивающегося полога", "сравнения в ранний сезон", "полей, где фон почвы влияет на NDVI"]),
    "evi": ("EVI", "Сигнал активности растительности, сохраняющий информативность при более плотном пологе.", ["динамики плотного полога", "сравнения с NDVI, когда NDVI может насыщаться"]),
    "ndmi": ("NDMI", "Спектральный сигнал, связанный с влагой в растительном пологе, а не прямое измерение влажности почвы.", ["отслеживания относительного изменения сигнала влаги", "сопоставления влажностного сигнала с индексами растительности"]),
    "ndre": ("NDRE", "Спектральный сигнал красного края, связанный с растительностью и хлорофиллом.", ["отслеживания относительного изменения полога и хлорофилла", "сравнения с NDVI на развитых стадиях полога"]),
}
QUALITY_CLOUD_MAX = 30.0
QUALITY_VALID_PIXELS_MIN = 30.0
TOP_LIMITATIONS = [
    "Спутниковые индексы являются косвенными спектральными сигналами.",
    "Интерпретацию необходимо подтвердить осмотром поля.",
    "Стадия культуры, сорт, почва, погода и недавние операции могут менять сигнал.",
    "Отсутствие принятого наблюдения не доказывает причину пропуска.",
    "Ответ не предписывает полив, внесение удобрений или применение пестицидов.",
]


def finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def rounded(value):
    value = finite_number(value)
    return round(value, 4) if value is not None else None


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile: position=(n-1)*fraction on sorted values."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile needs at least one value")
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _by_distinct_date(rows: Iterable) -> list:
    result = []
    seen = set()
    for item in sorted(rows, key=lambda item: (item.captured_date, item.id)):
        if item.captured_date not in seen and finite_number(item.value) is not None:
            result.append(item)
            seen.add(item.captured_date)
    return result


def _baseline(rows: list) -> tuple[dict, float | None, float | None]:
    values = [float(item.value) for item in rows]
    if len(values) < 3:
        return {"status": "insufficient", "point_count": len(values)}, None, None
    mid, low, high = median(values), percentile(values, .25), percentile(values, .75)
    mad = median([abs(value - mid) for value in values])
    return {"status": "available", "point_count": len(values), "median": rounded(mid),
            "p25": rounded(low), "p75": rounded(high), "mad": rounded(mad),
            "minimum": rounded(min(values)), "maximum": rounded(max(values))}, max(mad * 1.4826, (high - low) / 1.349), mid


def _confidence(rows: list, latest, today: date) -> dict:
    reasons = []
    if latest is None:
        return {"level": "insufficient", "reasons": ["нет принятого наблюдения"]}
    cloud, pixels = finite_number(latest.cloud_cover_pct), finite_number(latest.valid_pixels_pct)
    if cloud is None and pixels is None:
        reasons.append("quality_metrics_unavailable_for_this_index")
    if len(rows) < 2:
        reasons.insert(0, "недостаточно наблюдений с разными датами")
        return {"level": "insufficient", "reasons": reasons}
    freshness = (today - latest.captured_date).days
    weak = (cloud is not None and cloud > QUALITY_CLOUD_MAX) or (pixels is not None and pixels < QUALITY_VALID_PIXELS_MIN)
    if cloud is not None and cloud > QUALITY_CLOUD_MAX:
        reasons.append("облачность последнего наблюдения выше принятого порога")
    if pixels is not None and pixels < QUALITY_VALID_PIXELS_MIN:
        reasons.append("доля валидных пикселей последнего наблюдения ниже принятого порога")
    if freshness <= 7 and len(rows) >= 6 and not weak:
        level = "high"
    elif freshness <= 14 and len(rows) >= 3 and not weak:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "reasons": reasons}


def build_index_interpretation(code: str, rows: Iterable, today: date) -> dict:
    """Build one index read-model item from already queried, range-bounded rows."""
    label, meaning, useful_for = INDEX_METADATA[code]
    observations = _by_distinct_date(rows)
    latest = observations[-1] if observations else None
    previous = observations[-2] if len(observations) >= 2 else None
    baseline, robust_scale, baseline_median = _baseline(observations[:-1]) if latest else ({"status": "insufficient", "point_count": 0}, None, None)
    if latest is None:
        data_status = "no_data"
    elif len(observations) < 2:
        data_status = "insufficient_history"
    else:
        data_status = "valid" if (today - latest.captured_date).days <= 14 else "stale"
    change = {"absolute": None, "direction": "unknown", "days_between": None, "statistical_signal": "insufficient"}
    if latest and previous:
        delta = float(latest.value) - float(previous.value)
        change.update({"absolute": rounded(delta), "direction": "up" if delta > 0 else "down" if delta < 0 else "stable", "days_between": (latest.captured_date - previous.captured_date).days})
        if robust_scale and robust_scale > 0:
            deviation = abs(float(latest.value) - baseline_median) / robust_scale
            change["statistical_signal"] = "strong" if deviation >= 2.5 else "notable" if deviation >= 1.5 else "normal"
    recent = observations[-6:]
    trend = {"status": "insufficient", "direction": "unknown", "observation_count": len(recent), "duration_days": None, "slope_per_10_days": None}
    if len(recent) >= 3:
        x = [(item.captured_date - recent[0].captured_date).days for item in recent]
        y = [float(item.value) for item in recent]
        denominator = sum((item - sum(x) / len(x)) ** 2 for item in x)
        slope = 0.0 if denominator == 0 else sum((a - sum(x) / len(x)) * (b - sum(y) / len(y)) for a, b in zip(x, y)) / denominator
        duration = x[-1]
        trend.update({"status": "available", "duration_days": duration, "slope_per_10_days": rounded(slope * 10)})
        if robust_scale and robust_scale > 0:
            normalized = abs(slope * duration) / robust_scale
            trend["direction"] = "rising" if slope > 0 and normalized >= .5 else "falling" if slope < 0 and normalized >= .5 else "stable"
    confidence = _confidence(observations, latest, today)
    latest_value = None if latest is None else {"observed_at": latest.captured_date, "value": rounded(latest.value), "satellite": latest.satellite, "freshness_days": (today - latest.captured_date).days, "cloud_cover_pct": rounded(latest.cloud_cover_pct), "valid_pixels_pct": rounded(latest.valid_pixels_pct)}
    previous_value = None if previous is None else {"observed_at": previous.captured_date, "value": rounded(previous.value)}
    return {"code": code, "label": label, "meaning": meaning, "useful_for": useful_for, "data_status": data_status, "latest_observation": latest_value, "previous_valid_observation": previous_value, "change": change, "baseline": baseline, "trend": trend, "confidence": confidence, "hypotheses": [], "recommended_checks": [], "limitations": ["Сигнал интерпретируется относительно собственной истории поля и не является подтверждённым агрономическим диагнозом."]}


def add_cross_index_hypotheses(items: list[dict]) -> None:
    """Attach only cautious hypotheses supported by at least two index histories."""
    groups = {"up": [], "down": []}
    for item in items:
        if item["change"]["statistical_signal"] in {"strong", "notable"} and item["change"]["direction"] in groups:
            groups[item["change"]["direction"]].append(item["code"])
    for direction, codes in groups.items():
        if len(codes) < 2:
            continue
        family = "moisture_signal_change_to_verify" if "ndmi" in codes else "vegetation_change_to_verify"
        label = "Изменение спектрального сигнала требует проверки"
        rationale = "Несколько индексов поля показывают согласованное статистическое изменение относительно собственной истории; возможны также стадия роста, операция на поле, плотность полога или эффект сцены."
        checks = ["проверить стадию культуры и недавние операции на поле", "осмотреть равномерность полога и густоту растений", "подтвердить, видно ли изменение непосредственно в поле"]
        hypothesis = {"code": family, "label": label, "rationale": rationale, "supporting_indices": codes, "confidence": "medium" if len(codes) >= 3 else "low", "recommended_checks": checks}
        for item in items:
            if item["code"] in codes:
                item["hypotheses"].append(hypothesis)
                item["recommended_checks"] = checks


def overall_confidence(items: list[dict]) -> str:
    levels = {"high": 3, "medium": 2, "low": 1, "insufficient": 0}
    available = [item["confidence"]["level"] for item in items if item["latest_observation"]]
    return min(available, key=lambda level: levels[level]) if available else "insufficient"
