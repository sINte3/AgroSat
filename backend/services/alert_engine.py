"""
Движок алертов. Анализирует NDVI и генерирует предупреждения с учётом:
- Культуры и её текущей фенологической фазы
- Исторического NDVI этого поля
- Норм NDVI для данной фазы роста
- Погодного контекста
"""

import logging
from datetime import date, datetime
from typing import Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def safe_pct_change(new_val, old_val):
    """
    Возвращает процент изменения ТОЛЬКО когда это имеет смысл.
    Возвращает None если old_val слишком мал или отрицателен — caller должен показывать абсолютное изменение.
    """
    if old_val is None or new_val is None:
        return None
    if old_val < 0.15:           # слишком близко к нулю / почва — процент вводит в заблуждение
        return None
    if old_val < 0:              # отрицательный NDVI = вода/облака — не база
        return None
    return round((new_val - old_val) / old_val * 100, 1)


def get_current_growth_stage(crop_type, today: date = None) -> Optional[dict]:
    """Определить текущую фенологическую фазу по месяцу."""
    if today is None:
        today = date.today()

    if not crop_type or not crop_type.growth_stages:
        return None

    month = today.month
    for stage in crop_type.growth_stages:
        start = stage.get("month_start", 1)
        end = stage.get("month_end", 12)

        # Обработка переходящих сезонов (напр. кущение: ноябрь → февраль)
        if start <= end:
            if start <= month <= end:
                return stage
        else:  # start > end (сезон переходит через Новый год)
            if month >= start or month <= end:
                return stage

    return None


def analyze_field_ndvi(
    field,
    ndvi_record,
    db: Session,
) -> list[dict]:
    """
    Проанализировать новый NDVI снимок и вернуть список алертов.

    Returns:
        Список dict с полями: alert_type, severity, title, description,
        recommendation, triggered_value, threshold_value
    """
    alerts = []

    if ndvi_record.mean_ndvi is None:
        return alerts

    current_ndvi = ndvi_record.mean_ndvi
    today = ndvi_record.captured_date if isinstance(ndvi_record.captured_date, date) else date.today()

    # ─── 1. Проверка валидности данных ───────────────────────────────────────
    # Skip unreliable readings — clouds or sensor noise
    if ndvi_record.cloud_cover_pct is not None and ndvi_record.cloud_cover_pct > 30:
        return alerts  # do not create alert from cloudy snapshot
    if ndvi_record.mean_ndvi is None or ndvi_record.mean_ndvi < -0.5:
        return alerts  # sensor noise / water — not a real reading

    if ndvi_record.valid_pixels_pct is not None and ndvi_record.valid_pixels_pct < 30:
        alerts.append({
            "alert_type": "no_data",
            "severity": "info",
            "title": "Мало данных: высокая облачность",
            "description": f"Только {ndvi_record.valid_pixels_pct:.0f}% пикселей свободны от облаков. "
                           f"Данные могут быть неточными.",
            "recommendation": "Дождитесь ясной погоды для получения корректного снимка.",
            "triggered_value": ndvi_record.valid_pixels_pct,
            "threshold_value": 30,
        })
        return alerts  # Дальше нет смысла анализировать

    # ─── 2. Резкое падение NDVI (по сравнению с предыдущим снимком) ─────────
    ndvi_change = ndvi_record.ndvi_change
    ndvi_change_pct = ndvi_record.ndvi_change_pct
    if ndvi_change_pct is not None:
        # Пересчитываем через safe_pct_change, т.к. сохранённый % может быть от низкой базы
        prev_ndvi = (current_ndvi - ndvi_change) if ndvi_change is not None else None
        safe_pct = safe_pct_change(current_ndvi, prev_ndvi) if prev_ndvi is not None else None
        change = safe_pct if safe_pct is not None else ndvi_change_pct  # fallback
        use_absolute = safe_pct is None and ndvi_change is not None  # percent unreliable, show absolute

        if use_absolute and ndvi_change is not None and ndvi_change <= -0.04:
            # Процент ненадёжный — показываем абсолютное падение
            abs_change = round(ndvi_change, 4)
            if abs_change <= -0.08:
                alerts.append({
                    "alert_type": "ndvi_drop",
                    "severity": "critical",
                    "title": f"🚨 Критическое падение NDVI ({abs_change:+.3f})",
                    "description": f"NDVI снизился с {prev_ndvi:.3f} до {current_ndvi:.3f} "
                                   f"({abs_change:+.3f}). "
                                   f"Это может указывать на: болезнь, вредителя, засуху, "
                                   f"механическое повреждение или подтопление.",
                    "recommendation": "⚡ Требуется немедленный выезд на поле для осмотра. "
                                      "Проверьте: наличие признаков болезней, состояние листьев, "
                                      "работу ирригационной системы, следы вредителей.",
                    "triggered_value": current_ndvi,
                    "threshold_value": prev_ndvi,
                })
        elif not use_absolute and change <= -25:
            alerts.append({
                "alert_type": "ndvi_drop",
                "severity": "critical",
                "title": f"🚨 Критическое падение NDVI (-{abs(change):.1f}%)",
                "description": f"NDVI упал с {prev_ndvi:.3f} "
                               f"до {current_ndvi:.3f} — снижение на {abs(change):.1f}%. "
                               f"Это может указывать на: болезнь, вредителя, засуху, "
                               f"механическое повреждение или подтопление.",
                "recommendation": "⚡ Требуется немедленный выезд на поле для осмотра. "
                                  "Проверьте: наличие признаков болезней, состояние листьев, "
                                  "работу ирригационной системы, следы вредителей.",
                "triggered_value": current_ndvi,
                "threshold_value": prev_ndvi,
            })
        elif not use_absolute and change <= -15:
            alerts.append({
                "alert_type": "ndvi_drop",
                "severity": "warning",
                "title": f"⚠️ Значительное снижение NDVI (-{abs(change):.1f}%)",
                "description": f"NDVI снизился на {abs(change):.1f}% по сравнению с предыдущим снимком. "
                               f"Текущее значение: {current_ndvi:.3f}.",
                "recommendation": "Рекомендуется выезд на поле в течение 2-3 дней. "
                                  "Проверьте состояние посевов и условия влагообеспеченности.",
                "triggered_value": current_ndvi,
                "threshold_value": prev_ndvi,
            })

    # ─── 3. Сравнение с нормой для фазы роста ───────────────────────────────
    current_season = None
    for season in field.seasons:
        if season.season_year == today.year:
            current_season = season
            break

    if current_season and current_season.crop_type:
        crop = current_season.crop_type
        stage = get_current_growth_stage(crop, today)

        if stage:
            ndvi_min = stage.get("ndvi_min", 0)
            ndvi_max = stage.get("ndvi_max", 1)
            stage_name = stage.get("name", "текущая фаза")
            crop_name = crop.name_ru

            if current_ndvi < ndvi_min:
                deficit = ndvi_min - current_ndvi
                severity = "critical" if deficit > 0.15 else "warning"
                alerts.append({
                    "alert_type": "ndvi_low",
                    "severity": severity,
                    "title": f"NDVI ниже нормы для {crop_name} ({stage_name})",
                    "description": f"Текущий NDVI: {current_ndvi:.3f}. "
                                   f"Норма для «{stage_name}»: {ndvi_min:.2f}–{ndvi_max:.2f}. "
                                   f"Отклонение: -{deficit:.3f}.",
                    "recommendation": _get_low_ndvi_recommendation(crop, stage, current_ndvi),
                    "triggered_value": current_ndvi,
                    "threshold_value": ndvi_min,
                })

            elif current_ndvi > ndvi_max + 0.05:
                alerts.append({
                    "alert_type": "ndvi_high",
                    "severity": "info",
                    "title": f"NDVI выше нормы для {crop_name} ({stage_name})",
                    "description": f"Текущий NDVI: {current_ndvi:.3f}. "
                                   f"Норма: {ndvi_min:.2f}–{ndvi_max:.2f}. "
                                   f"Отклонение: +{current_ndvi - ndvi_max:.3f}.",
                    "recommendation": "Хороший показатель. Убедитесь в отсутствии полегания посевов "
                                      "или избыточного ветвления (применимо к хлопку).",
                    "triggered_value": current_ndvi,
                    "threshold_value": ndvi_max,
                })

    # ─── 4. Неоднородность поля ──────────────────────────────────────────────
    if ndvi_record.std_ndvi is not None and ndvi_record.std_ndvi > 0.12:
        alerts.append({
            "alert_type": "ndvi_uneven",
            "severity": "info",
            "title": "Неравномерное состояние посевов по полю",
            "description": f"Стандартное отклонение NDVI: {ndvi_record.std_ndvi:.3f} "
                           f"(мин: {ndvi_record.min_ndvi:.3f}, макс: {ndvi_record.max_ndvi:.3f}). "
                           f"Высокая вариабельность указывает на проблемные участки.",
            "recommendation": "Выявите неоднородные зоны на карте NDVI и проведите "
                              "точечный осмотр. Возможные причины: неравномерный полив, "
                              "засолённые пятна, уплотнение почвы, болезни очагового характера.",
            "triggered_value": ndvi_record.std_ndvi,
            "threshold_value": 0.12,
        })

    return alerts


def _get_low_ndvi_recommendation(crop, stage, current_ndvi: float) -> str:
    """Сформировать рекомендацию исходя из культуры и фазы."""
    crop_code = crop.code
    stage_name = stage.get("name", "")
    alerts_config = crop.alerts_config or {}

    base = (
        f"Проверьте: обеспеченность влагой, признаки болезней "
        f"({', '.join(alerts_config.get('disease_risk', [])[:2]) or 'нет данных'}), "
        f"наличие вредителей ({', '.join(alerts_config.get('pest_risk', [])[:2]) or 'нет данных'})."
    )

    if crop_code == "cotton":
        if "Вегетация" in stage_name or "Бутонизация" in stage_name:
            return (
                "Хлопок в критической фазе роста. " + base +
                " Особое внимание: своевременный полив (норма 700-900 м³/га), "
                "контроль вертициллёза и фузариоза."
            )
    elif crop_code == "wheat":
        if "Трубкование" in stage_name or "Колошение" in stage_name:
            return (
                "Критическая фаза формирования урожая пшеницы. " + base +
                " Проверьте наличие ржавчины на листьях."
            )

    return base


# ─── Сохранение алертов в БД ─────────────────────────────────────────────────

def save_alerts(field, ndvi_record, alerts_data: list, db: Session) -> int:
    """Сохранить новые алерты в базу данных. Возвращает количество сохранённых."""
    from models.monitoring import Alert

    saved = 0
    for alert_data in alerts_data:
        # Дедупликация: не создаём одинаковый алерт если он уже активен
        existing = db.query(Alert).filter(
            Alert.field_id == field.id,
            Alert.alert_type == alert_data["alert_type"],
            Alert.is_active == True
        ).first()

        if existing:
            continue

        alert = Alert(
            field_id=field.id,
            ndvi_record_id=ndvi_record.id if ndvi_record else None,
            alert_type=alert_data["alert_type"],
            severity=alert_data["severity"],
            title=alert_data["title"],
            description=alert_data["description"],
            recommendation=alert_data.get("recommendation"),
            triggered_value=alert_data.get("triggered_value"),
            threshold_value=alert_data.get("threshold_value"),
        )
        db.add(alert)
        saved += 1

    if saved > 0:
        db.commit()

    return saved
