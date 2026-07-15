"""Pure deterministic scoring and assembly for the field attention queue."""
from __future__ import annotations

from datetime import date

from services.agronomic_interpretation import INDEX_CODES, overall_confidence

PRIORITY_WEIGHT = {"low": 0, "medium": 1, "high": 2, "critical": 3}
INDEX_LABEL = dict(zip(INDEX_CODES, ("NDVI", "SAVI", "EVI", "NDMI", "NDRE")))
LIMITATIONS = [
    "Приоритет является детерминированным операционным ранжированием.",
    "Спутниковые сигналы являются косвенными.",
    "Очередь не является агрономическим диагнозом.",
    "Требуется осмотр поля.",
    "Отсутствие данных не доказывает стресс на поле.",
]
ALERT_CHECK = "Открыть активный алерт и проверить обстоятельства непосредственно на поле."
DATA_CHECK = "Проверить доступность и актуальность спутниковых наблюдений."
CHANGE_CHECKS = [
    "Сопоставить изменение со стадией культуры и недавними операциями на поле.",
    "Осмотреть равномерность растительного покрова на поле.",
]


def _reason(code, label, points, indices=None, count=1):
    return {"code": code, "label": label, "points": points,
            "indices": indices or [], "count": count}


def score_field(alerts: list, interpretations: list[dict], date_to: date) -> dict:
    """Score one field using only active alerts and accepted interpretations."""
    counts = {severity: sum(a.severity == severity for a in alerts)
              for severity in ("critical", "warning", "info")}
    reasons = []
    alert_points = 0
    if counts["critical"]:
        points = min(55, 45 + 5 * (counts["critical"] - 1))
        alert_points += points
        reasons.append(_reason("active_critical_alert", "Есть активный критический алерт", points, count=counts["critical"]))
    if counts["warning"]:
        points = min(26, 20 + 3 * (counts["warning"] - 1))
        alert_points += points
        reasons.append(_reason("active_warning_alert", "Есть активный предупреждающий алерт", points, count=counts["warning"]))
    if counts["info"]:
        alert_points += 5
        reasons.append(_reason("active_info_alert", "Есть активный информационный алерт", 5, count=counts["info"]))

    ndvi = interpretations[0]
    latest_ndvi = ndvi.get("latest_observation")
    freshness = latest_ndvi.get("freshness_days") if latest_ndvi else None
    freshness_points = 0
    if freshness is None:
        freshness_points = 35
        reasons.append(_reason("ndvi_no_data", "Нет принятых наблюдений NDVI в выбранном периоде", 35))
    elif freshness > 30:
        freshness_points = 30
        reasons.append(_reason("ndvi_stale_over_30_days", "Наблюдение NDVI старше 30 дней", 30))
    elif freshness >= 15:
        freshness_points = 20
        reasons.append(_reason("ndvi_stale_15_30_days", "Наблюдение NDVI получено 15–30 дней назад", 20))
    elif freshness >= 8:
        freshness_points = 10
        reasons.append(_reason("ndvi_stale_8_14_days", "Наблюдение NDVI получено 8–14 дней назад", 10))

    strong, notable, falling = [], [], []
    raw_spectral = 0
    downward = []
    for item in interpretations:
        confidence = item["confidence"]["level"]
        change = item["change"]
        trend = item["trend"]
        eligible = confidence in ("high", "medium") and change["direction"] == "down"
        signal = change["statistical_signal"]
        if eligible and signal == "strong":
            raw_spectral += 12; strong.append(INDEX_LABEL[item["code"]])
        elif eligible and signal == "notable":
            raw_spectral += 7; notable.append(INDEX_LABEL[item["code"]])
        if eligible and trend["direction"] == "falling":
            raw_spectral += 4; falling.append(INDEX_LABEL[item["code"]])
        if eligible and (signal in ("strong", "notable") or trend["direction"] == "falling"):
            downward.append(INDEX_LABEL[item["code"]])
    spectral_points = min(30, raw_spectral)
    remaining = spectral_points
    for code, label, points_each, indices in (
        ("strong_downward_spectral_signal", "Сильный нисходящий спектральный сигнал", 12, strong),
        ("notable_downward_spectral_signal", "Заметный нисходящий спектральный сигнал", 7, notable),
        ("falling_spectral_trend", "Нисходящий спектральный тренд", 4, falling),
    ):
        if indices:
            points = min(remaining, points_each * len(indices))
            reasons.append(_reason(code, label, points, indices, len(indices)))
            remaining -= points

    score = min(100, alert_points + freshness_points + spectral_points)
    if counts["critical"]:
        score = max(70, score)
    elif counts["warning"]:
        score = max(45, score)
    if counts["critical"] or score >= 70:
        priority = "critical"
    elif counts["warning"] or score >= 45:
        priority = "high"
    elif score >= 20:
        priority = "medium"
    else:
        priority = "low"

    checks = []
    if counts["critical"] or counts["warning"]: checks.append(ALERT_CHECK)
    if freshness is None or freshness > 14: checks.append(DATA_CHECK)
    if downward: checks.extend(CHANGE_CHECKS)
    for item in interpretations:
        checks.extend(item.get("recommended_checks", []))
    checks = list(dict.fromkeys(checks))[:5]
    observed = [i["latest_observation"]["observed_at"] for i in interpretations if i.get("latest_observation")]
    stale = [INDEX_LABEL[i["code"]] for i in interpretations if i["data_status"] == "stale"]
    ordered_alerts = sorted(alerts, key=lambda a: ({"critical": 0, "warning": 1, "info": 2}.get(a.severity, 3), -a.triggered_at.timestamp(), a.id))
    return {
        "priority": priority, "attention_score": score, "reasons": reasons,
        "alert_summary": {"active_total": len(alerts), **counts,
            "latest_triggered_at": max((a.triggered_at for a in alerts), default=None),
            "top_alerts": [{"id": a.id, "type": a.alert_type, "severity": a.severity,
                            "title": a.title, "triggered_at": a.triggered_at} for a in ordered_alerts[:3]]},
        "spectral_summary": {"overall_confidence": overall_confidence(interpretations),
            "latest_observation_date": max(observed) if observed else None,
            "freshness_days": freshness,
            "data_status": "no_data" if freshness is None else ("stale" if freshness > 14 else "current"),
            "downward_indices": downward, "stale_indices": stale},
        "recommended_checks": checks, "limitations": LIMITATIONS,
    }


def queue_sort_key(item):
    freshness = item["spectral_summary"]["freshness_days"]
    return (-PRIORITY_WEIGHT[item["priority"]], -item["attention_score"],
            -item["alert_summary"]["critical"], -item["alert_summary"]["warning"],
            0 if freshness is None else 1, -(freshness or 0), item["field"]["id"])
