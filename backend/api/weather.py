from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from services.weather import get_field_weather

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/field/{field_id}")
def get_weather_for_field(field_id: int, db: Session = Depends(get_db)):
    """Погода для конкретного поля по его координатам."""
    f = db.execute(
        text("SELECT id, name, centroid_lat, centroid_lon FROM fields WHERE id = :fid OR code = CAST(:fid AS TEXT) LIMIT 1"),
        {"fid": field_id}
    ).fetchone()
    if not f:
        raise HTTPException(status_code=404, detail="Поле не найдено")

    if not f.centroid_lat or not f.centroid_lon:
        raise HTTPException(status_code=400, detail="У поля не заданы координаты центра")

    weather = get_field_weather(f.centroid_lat, f.centroid_lon)
    if not weather:
        raise HTTPException(status_code=503, detail="Не удалось получить данные погоды")

    weather["field_id"] = f.id
    weather["field_name"] = f.name
    return weather


@router.get("/location")
def get_weather_by_location(lat: float, lon: float):
    """Погода по координатам."""
    weather = get_field_weather(lat, lon)
    if not weather:
        raise HTTPException(status_code=503, detail="Не удалось получить данные погоды")
    return weather
