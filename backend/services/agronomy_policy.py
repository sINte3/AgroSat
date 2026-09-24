"""Versioned, deterministic decision support. No network or generative service."""

from datetime import date, datetime, timedelta

from services import observation_quality

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
    """Classify one observation through the canonical quality contract.

    Returns ``None`` when the observation is accepted, otherwise the
    ``agronomy_plans.verification_status`` value that describes the rejection.
    A NULL ``cloud`` no longer blocks: see services/observation_quality.py.
    """
    if not observation:
        return "PENDING_DATA"
    verdict = observation_quality.evaluate(
        value=observation.get("value"),
        valid_pixels_pct=observation.get("valid"),
        cloud_cover_pct=observation.get("cloud"),
        minimum_valid_pixels_pct=observation_quality.MIN_VALID_PIXELS_ANALYSIS_PCT,
    )
    return observation_quality.legacy_blocked_status(verdict)


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


SCOPE_EXPLANATIONS = {
    "comparable": "Область проверки — всё поле: зона обнаружения совпадает с полем, исходное и последующее значения измерены по одной и той же границе. ",
    "zone_statistics_unavailable": "Для локальной зоны нет сохранённой статистики наблюдений; среднее по полю не подтверждает восстановление зоны. ",
    "zone_geometry_changed": "Граница поля изменилась после фиксации области проверки; значения несопоставимы. ",
    "baseline_missing": "Нет принятого исходного наблюдения по области проверки. ",
    "baseline_outside_scope": "Исходное наблюдение относится к другой области, чем зона проверки. ",
    "post_outside_scope": "Последующее наблюдение относится к другой области, чем зона проверки. ",
    "scope_not_recorded": "Область проверки не зафиксирована; среднее по полю не подтверждает восстановление локальной зоны. ",
}


def evaluate(baseline, post, completed_at, as_of, *, freshness=None, zone_required=False, scope=None):
    """Dates are day-resolution; the first eligible date is conservatively after wait.

    ``scope`` is the spatial-scope comparison (services.closed_loop_agronomy
    .scope_comparison). It never changes the thresholds; it only explains why
    a zone-required verification is or is not comparable.
    """
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
    if scope is None:
        scope_text = ("Среднее по полю не подтверждает восстановление локальной зоны. "
                      if zone_required and status == "INCONCLUSIVE" else "")
        statistics_scope = "zone" if post and post.get("zone_key") else "field"
    else:
        # Only persisted field statistics exist; the scope says whether the
        # field is the zone being verified.
        scope_text = SCOPE_EXPLANATIONS.get(scope.get("reason"), "") if zone_required else ""
        statistics_scope = "field"
    return {
        "status": status, "baseline": baseline, "post": post, "delta": delta,
        "policy_version": POLICY, "eligible_from": eligible.isoformat(),
        "statistics_scope": statistics_scope,
        "scope": scope,
        "explanation": f"{labels[status]}. Исходное: {describe(baseline)}. Последующее: {describe(post)}. Порог ±{MATERIAL_DELTA}; ожидание {WAIT_DAYS} полных дней; правило {POLICY}. "
                       + scope_text
                       + "Наблюдаемое изменение не доказывает причинный эффект выполненных работ.",
    }
