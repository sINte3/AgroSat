"""
Сервис погоды через Open-Meteo API.
Полностью бесплатный, без API ключей. https://open-meteo.com/
"""

import logging
from datetime import date, timedelta
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def get_field_weather(lat: float, lon: float) -> Optional[dict]:
    """
    Получить прогноз погоды для координат поля.

    Returns:
        dict с текущими условиями и прогнозом на 5 дней.
    """
    try:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "wind_speed_10m",
                "wind_direction_10m",
                "precipitation",
                "weather_code",
                "is_day",
            ],
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "et0_fao_evapotranspiration",  # Эвапотранспирация (для ирригации)
                "wind_speed_10m_max",
                "wind_direction_10m_dominant",
                "weather_code",
                "sunrise",
                "sunset",
            ],
            "forecast_days": 7,
            "timezone": "Asia/Tashkent",
        }

        response = httpx.get(OPEN_METEO_URL, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        current = data.get("current", {})
        daily = data.get("daily", {})

        # Формируем прогноз
        forecast_days = []
        times = daily.get("time", [])
        for i, d in enumerate(times):
            weather_code = daily.get("weather_code", [None])[i]
            forecast_days.append({
                "date": d,
                "temp_max": daily.get("temperature_2m_max", [None])[i],
                "temp_min": daily.get("temperature_2m_min", [None])[i],
                "precipitation": daily.get("precipitation_sum", [None])[i],
                "et0_mm": daily.get("et0_fao_evapotranspiration", [None])[i],
                "wind_speed_max": daily.get("wind_speed_10m_max", [None])[i],
                "weather_code": weather_code,
                "sunrise": daily.get("sunrise", [None])[i],
                "sunset": daily.get("sunset", [None])[i],
            })

        # Анализ рисков (засуха, заморозки, сильный дождь)
        risks = detect_weather_risks(forecast_days)

        return {
            "current": {
                "temperature": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind_speed": current.get("wind_speed_10m"),
                "wind_direction": current.get("wind_direction_10m"),
                "precipitation": current.get("precipitation"),
                "weather_code": current.get("weather_code"),
                "is_day": current.get("is_day", 1) == 1,
            },
            "forecast": forecast_days[:5],  # 5-дневный прогноз
            "risks": risks,
            "location": {"lat": lat, "lon": lon},
        }

    except Exception as e:
        logger.error(f"Ошибка получения погоды: {e}")
        return None


def detect_weather_risks(forecast: list) -> list:
    """Выявить погодные риски из прогноза."""
    risks = []

    for day in forecast[:3]:  # ближайшие 3 дня
        temp_max = day.get("temp_max")
        temp_min = day.get("temp_min")
        precip = day.get("precipitation", 0) or 0
        et0 = day.get("et0_mm", 0) or 0

        # Риск заморозков
        if temp_min is not None and temp_min < 2:
            risks.append({
                "type": "frost_risk",
                "severity": "critical" if temp_min < 0 else "warning",
                "date": day["date"],
                "description": f"Риск заморозков: {temp_min:.1f}°C",
                "value": temp_min,
            })

        # Жара (стресс для растений)
        if temp_max is not None and temp_max > 38:
            risks.append({
                "type": "heat_stress",
                "severity": "warning",
                "date": day["date"],
                "description": f"Тепловой стресс: до {temp_max:.1f}°C",
                "value": temp_max,
            })

        # Засуха (высокое испарение, мало осадков)
        if et0 > 0 and precip < et0 * 0.3:
            risks.append({
                "type": "drought_risk",
                "severity": "info",
                "date": day["date"],
                "description": f"Дефицит влаги: осадки {precip:.1f} мм, испарение {et0:.1f} мм",
                "value": precip - et0,
            })

        # Сильный дождь
        if precip > 30:
            risks.append({
                "type": "heavy_rain",
                "severity": "warning",
                "date": day["date"],
                "description": f"Сильные осадки: {precip:.1f} мм — риск подтопления",
                "value": precip,
            })

    return risks


def wmo_code_to_description(code: Optional[int]) -> str:
    """Перевести WMO код погоды в описание на русском."""
    if code is None:
        return "Нет данных"

    codes = {
        0: "Ясно",
        1: "Преимущественно ясно", 2: "Переменная облачность", 3: "Пасмурно",
        45: "Туман", 48: "Изморозь",
        51: "Мелкая морось", 53: "Морось", 55: "Сильная морось",
        61: "Слабый дождь", 63: "Дождь", 65: "Сильный дождь",
        71: "Слабый снег", 73: "Снег", 75: "Сильный снег",
        77: "Снежная крупа",
        80: "Кратковременный дождь", 81: "Дождь с ливнями", 82: "Сильный ливень",
        85: "Снежные ливни", 86: "Сильные снежные ливни",
        95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом",
    }
    return codes.get(code, f"Код {code}")
