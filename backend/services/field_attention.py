"""Deterministic scoring and constant-query assembly for the attention queue."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from api.dependencies import ALLOWED_ROLES, is_tenant_role, normalize_role
from services.agronomic_interpretation import (
    INDEX_CODES,
    add_cross_index_hypotheses,
    build_index_interpretation,
    overall_confidence,
)

PRIORITY_WEIGHT = {"low": 0, "medium": 1, "high": 2, "critical": 3}
INDEX_LABEL = dict(zip(INDEX_CODES, ("NDVI", "SAVI", "EVI", "NDMI", "NDRE")))
TASHKENT = ZoneInfo("Asia/Tashkent")
MAX_SCOPE_FIELDS = 2000
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


def build_attention_queue(
    db,
    current_user,
    *,
    enterprise_id=None,
    crop_type_id=None,
    date_to=None,
    lookback_days=180,
    min_priority="medium",
    limit=100,
):
    """Build one bounded queue with four constant queries for a non-empty scope."""
    generated_at = datetime.now(TASHKENT)
    resolved_to = date_to or generated_at.date()
    role = normalize_role(current_user)
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Unknown role")
    resolved_enterprise = enterprise_id
    if is_tenant_role(role):
        if current_user.enterprise_id is None:
            raise HTTPException(status_code=403, detail="User has no enterprise_id")
        if enterprise_id is not None and enterprise_id != current_user.enterprise_id:
            raise HTTPException(
                status_code=403,
                detail="Доступ запрещён для данного предприятия",
            )
        resolved_enterprise = current_user.enterprise_id

    conditions = ["f.is_active = true", "cs.season_year = :season_year"]
    params = {
        "season_year": resolved_to.year,
        "scope_limit": MAX_SCOPE_FIELDS + 1,
    }
    if resolved_enterprise is not None:
        conditions.append("f.enterprise_id = :enterprise_id")
        params["enterprise_id"] = resolved_enterprise
    if crop_type_id is not None:
        conditions.append("cs.crop_type_id = :crop_type_id")
        params["crop_type_id"] = crop_type_id
    fields = db.execute(
        text(
            f"""
            SELECT f.id, f.name, f.enterprise_id, e.name AS enterprise_name,
                   cs.crop_type_id, ct.name_ru AS crop_name, cs.season_year
            FROM fields f
            JOIN enterprises e ON e.id = f.enterprise_id
            JOIN crop_seasons cs ON cs.field_id = f.id
            JOIN crop_types ct ON ct.id = cs.crop_type_id
            WHERE {' AND '.join(conditions)}
            ORDER BY f.id ASC
            LIMIT :scope_limit
            """
        ),
        params,
    ).fetchall()
    if len(fields) > MAX_SCOPE_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Select an enterprise or a narrower crop filter; "
                "authorized scope exceeds 2000 fields"
            ),
        )
    base = {
        "generated_at": generated_at,
        "date_to": resolved_to,
        "lookback_days": lookback_days,
        "scope": {
            "enterprise_id": enterprise_id,
            "crop_type_id": crop_type_id,
            "max_scope_fields": MAX_SCOPE_FIELDS,
        },
    }
    if not fields:
        return {
            **base,
            "summary": {
                "fields_evaluated": 0,
                "attention_fields": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "returned": 0,
            },
            "items": [],
        }

    field_ids = [field.id for field in fields]
    common = {
        "field_ids": field_ids,
        "date_from": resolved_to - timedelta(days=lookback_days),
        "date_to": resolved_to,
    }
    alerts = db.execute(
        text(
            """
            SELECT id, field_id, alert_type, severity, title, triggered_at
            FROM alerts
            WHERE field_id = ANY(:field_ids) AND is_active = true
            ORDER BY field_id ASC, triggered_at DESC, id ASC
            """
        ),
        {"field_ids": field_ids},
    ).fetchall()
    ndvi = db.execute(
        text(
            """
            SELECT id, field_id, captured_date, mean_ndvi AS value, satellite,
                   cloud_cover_pct, valid_pixels_pct
            FROM ndvi_records
            WHERE field_id = ANY(:field_ids)
              AND captured_date >= :date_from
              AND captured_date <= :date_to
              AND mean_ndvi IS NOT NULL
            ORDER BY field_id ASC, captured_date ASC, id ASC
            """
        ),
        common,
    ).fetchall()
    multi = db.execute(
        text(
            """
            SELECT id, field_id, captured_date, index_code,
                   mean_value AS value, satellite, cloud_cover_pct,
                   valid_pixels_pct
            FROM satellite_index_records
            WHERE field_id = ANY(:field_ids)
              AND captured_date >= :date_from
              AND captured_date <= :date_to
              AND mean_value IS NOT NULL
              AND index_code = ANY(:index_codes)
            ORDER BY field_id ASC, captured_date ASC, id ASC
            """
        ),
        {**common, "index_codes": list(INDEX_CODES[1:])},
    ).fetchall()
    by_alert = defaultdict(list)
    by_index = defaultdict(lambda: {code: [] for code in INDEX_CODES})
    for row in alerts:
        by_alert[row.field_id].append(row)
    for row in ndvi:
        by_index[row.field_id]["ndvi"].append(row)
    for row in multi:
        if row.index_code in INDEX_CODES[1:]:
            by_index[row.field_id][row.index_code].append(row)
    items = []
    for field in fields:
        interpretations = [
            build_index_interpretation(
                code,
                by_index[field.id][code],
                resolved_to,
            )
            for code in INDEX_CODES
        ]
        add_cross_index_hypotheses(interpretations)
        item = {
            "rank": 0,
            "field": {
                key: getattr(field, key)
                for key in (
                    "id",
                    "name",
                    "enterprise_id",
                    "enterprise_name",
                    "crop_type_id",
                    "crop_name",
                    "season_year",
                )
            },
        }
        item.update(score_field(by_alert[field.id], interpretations, resolved_to))
        items.append(item)
    counts = {
        priority: sum(item["priority"] == priority for item in items)
        for priority in PRIORITY_WEIGHT
    }
    attention = sum(item["priority"] != "low" for item in items)
    selected = [
        item
        for item in items
        if PRIORITY_WEIGHT[item["priority"]] >= PRIORITY_WEIGHT[min_priority]
    ]
    selected.sort(key=queue_sort_key)
    selected = selected[:limit]
    for rank, item in enumerate(selected, 1):
        item["rank"] = rank
    return {
        **base,
        "summary": {
            "fields_evaluated": len(items),
            "attention_fields": attention,
            **counts,
            "returned": len(selected),
        },
        "items": selected,
    }
