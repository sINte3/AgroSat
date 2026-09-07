"""Versioned, deterministic decision support. No network or generative service."""

from datetime import date, datetime, timedelta
import math

POLICY = "r3-f-v1"
WAIT_DAYS = 7
MATERIAL_DELTA = 0.05
MAX_BASELINE_AGE_DAYS = 20
STATUSES = (
    "PENDING_DATA", "TOO_EARLY", "CLOUD_BLOCKED", "QUALITY_BLOCKED",
    "PROVIDER_DEGRADED", "INCONCLUSIVE", "IMPROVED", "NO_MATERIAL_CHANGE", "WORSENED",
)
CATEGORIES = (
    "irrigation", "nutrition", "crop_protection", "drainage", "reinspection",
    "sampling", "cultivation", "other",
)
SUGGESTIONS = {
    "water_stress": ("irrigation", "Проверить влажность почвы и работу полива; согласовать корректировку по результатам замеров."),
    "irrigation_failure": ("irrigation", "Проверить и устранить подтверждённую неисправность полива; записать контрольные замеры."),
    "nutrient_deficiency": ("sampling", "Отобрать почвенные и растительные пробы для проверки питания; передать результаты агроному."),
    "pest": ("crop_protection", "Повторно обследовать очаг, подтвердить вид и распространённость вредителя; согласовать меры с агрономом."),
    "disease": ("sampling", "Отобрать образцы и подтвердить причину повреждения перед выбором мер защиты."),
    "soil_salinity": ("sampling", "Проверить засоление почвы и состояние дренажа; записать измерения."),
    "weed_pressure": ("cultivation", "Обследовать засорённость и согласовать механические меры с учётом культуры и фазы развития."),
}


def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def quality(observation):
    if not observation:
        return "PENDING_DATA"
    value, cloud, valid = (observation.get(k) for k in ("value", "cloud", "valid"))
    if cloud is not None and math.isfinite(cloud) and cloud > 30:
        return "CLOUD_BLOCKED"
    if any(v is None or not math.isfinite(v) for v in (value, cloud, valid)):
        return "QUALITY_BLOCKED"
    if not -1 <= value <= 1 or not 0 <= cloud <= 30 or not 50 <= valid <= 100:
        return "QUALITY_BLOCKED"
    return None


def recommend(snapshot):
    finding = snapshot.get("finding") or {}
    cause = finding.get("cause_code", "unconfirmed")
    category, instruction = SUGGESTIONS.get(cause, (
        "reinspection", "Повторно обследовать участок, уточнить причину и зафиксировать контрольные замеры."))
    limitations = []
    observation = snapshot.get("baseline")
    if quality(observation):
        limitations.append("Нет принятого спутникового наблюдения достаточного качества.")
    elif (day(snapshot["as_of"]) - day(observation["date"])).days > MAX_BASELINE_AGE_DAYS:
        limitations.append("Спутниковая исходная точка старше 20 дней.")
    if not snapshot.get("season"):
        limitations.append("Контекст культуры и сезона отсутствует.")
    if cause in {"unconfirmed", "other"}:
        limitations.append("Причина не подтверждена осмотром.")
    freshness = (snapshot.get("freshness") or {}).get("status")
    if freshness not in {"FRESH", "AGING"}:
        limitations.append("Свежесть спутниковых данных не подтверждена: " + str(freshness or "нет данных") + ".")
    return {
        "policy_version": POLICY, "requires_approval": True,
        "decision": "По результатам осмотра требуется проверяемое полевое действие. " + instruction,
        "objective": "Устранить подтверждённую причину и проверить состояние участка повторным осмотром и более поздним спутниковым наблюдением.",
        "suggestions": [{"category": category, "instruction": instruction}],
        "expected_outcome": "Подтверждённое выполнение работ и улучшение состояния растительности при достаточном качестве наблюдений.",
        "verification_wait_days": WAIT_DAYS,
        "confidence": "low" if limitations else "moderate",
        "limitations": limitations,
        "explanation": "Рекомендация основана на записанных находках осмотра; NDVI не определяет болезнь, вредителя или дефицит питания. Средства и дозы не назначаются. Требуется утверждение агронома.",
    }


def evaluate(baseline, post, completed_at, as_of, *, freshness=None, zone_required=False):
    """Dates are day-resolution; the first eligible date is conservatively after wait."""
    eligible = day(completed_at) + timedelta(days=WAIT_DAYS + 1)
    status, delta = "PENDING_DATA", None
    if day(as_of) < eligible:
        status = "TOO_EARLY"
    elif post and day(post["date"]) < eligible:
        raise ValueError("Observation predates the post-completion verification window")
    elif not post:
        status = freshness if freshness in {"CLOUD_BLOCKED", "QUALITY_BLOCKED", "PROVIDER_DEGRADED"} else "PENDING_DATA"
    elif quality(post):
        status = quality(post)
    elif quality(baseline) or day(baseline["date"]) > day(completed_at):
        status = "INCONCLUSIVE"
    elif (day(completed_at) - day(baseline["date"])).days > MAX_BASELINE_AGE_DAYS:
        status = "INCONCLUSIVE"
    else:
        delta = round(post["value"] - baseline["value"], 6)
        status = "IMPROVED" if delta >= MATERIAL_DELTA else "WORSENED" if delta <= -MATERIAL_DELTA else "NO_MATERIAL_CHANGE"
        if zone_required and not (baseline.get("zone_key") and baseline.get("zone_key") == post.get("zone_key")):
            status = "INCONCLUSIVE"
    labels = {
        "PENDING_DATA": "Ожидается более позднее наблюдение", "TOO_EARLY": "Минимальное время ожидания ещё не прошло",
        "CLOUD_BLOCKED": "Наблюдение закрыто облаками", "QUALITY_BLOCKED": "Недостаточно качественных пикселей",
        "PROVIDER_DEGRADED": "Поставщик данных недоступен или работает с ограничениями",
        "INCONCLUSIVE": "Недостаточно сопоставимых данных для вывода по участку",
        "IMPROVED": "Зафиксировано улучшение", "NO_MATERIAL_CHANGE": "Существенного изменения нет",
        "WORSENED": "Зафиксировано ухудшение",
    }
    def describe(value):
        return f"{value['date']}: NDVI {value.get('value')}, облачность {value.get('cloud')}%, пригодные пиксели {value.get('valid')}%" if value else "нет данных"
    return {
        "status": status, "baseline": baseline, "post": post, "delta": delta,
        "policy_version": POLICY, "eligible_from": eligible.isoformat(),
        "statistics_scope": "zone" if post and post.get("zone_key") else "field",
        "explanation": f"{labels[status]}. Исходное: {describe(baseline)}. Последующее: {describe(post)}. Порог ±{MATERIAL_DELTA}; ожидание {WAIT_DAYS} полных дней; правило {POLICY}. "
                       + ("Среднее по полю не подтверждает восстановление локальной зоны. " if zone_required and status == "INCONCLUSIVE" else "")
                       + "Наблюдаемое изменение не доказывает причинный эффект выполненных работ.",
    }
