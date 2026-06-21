# dashboard.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from models.field import Field
from models.monitoring import NDVIRecord, Alert
from models.enterprise import Enterprise
from datetime import date, timedelta
from services.cache import cache_get, cache_set

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Сводка по всему кластеру для главного дашборда."""
    cache_key = "dashboard:summary"
    cached = cache_get(cache_key)
    if cached:
        return cached
    total_fields = db.query(Field).filter(Field.is_active == True).count()
    total_enterprises = db.query(Enterprise).filter(Enterprise.is_active == True).count()

    active_alerts = db.query(Alert).filter(Alert.is_active == True).count()
    critical_alerts = db.query(Alert).filter(
        Alert.is_active == True, Alert.severity == "critical"
    ).count()
    warning_alerts = db.query(Alert).filter(
        Alert.is_active == True, Alert.severity == "warning"
    ).count()

    # Среднее NDVI по всем полям (последние снимки)
    fields = db.query(Field).filter(Field.is_active == True).all()
    ndvi_values = []
    fields_with_problems = 0
    fields_no_data = 0

    for f in fields:
        last = db.query(NDVIRecord).filter(
            NDVIRecord.field_id == f.id
        ).order_by(NDVIRecord.captured_date.desc()).first()

        if last and last.mean_ndvi is not None:
            ndvi_values.append(last.mean_ndvi)
            if last.mean_ndvi < 0.3:
                fields_with_problems += 1
        else:
            fields_no_data += 1

    avg_ndvi = round(sum(ndvi_values) / len(ndvi_values), 4) if ndvi_values else None

    # Общая площадь
    total_area = db.query(func.sum(Field.area_ha)).filter(Field.is_active == True).scalar()

    result = {
        "total_fields": total_fields,
        "total_enterprises": total_enterprises,
        "total_area_ha": round(total_area, 1) if total_area else 0,
        "avg_ndvi": avg_ndvi,
        "active_alerts": active_alerts,
        "critical_alerts": critical_alerts,
        "warning_alerts": warning_alerts,
        "fields_with_problems": fields_with_problems,
        "fields_no_data": fields_no_data,
        "last_updated": date.today().isoformat(),
    }
    cache_set(cache_key, result, ttl_seconds=120)
    return result


@router.get("/enterprises/{enterprise_id}")
def get_enterprise_summary(enterprise_id: int, db: Session = Depends(get_db)):
    """Сводка по конкретному предприятию — агрегированные KPI."""
    from models.crop import CropType
    from models.field import CropSeason
    from datetime import date

    fields = db.query(Field).filter(
        Field.enterprise_id == enterprise_id,
        Field.is_active == True
    ).all()

    total_fields = len(fields)
    active_alerts = 0
    critical_alerts = 0
    fields_with_problems = 0
    ndvi_values = []
    fields_list = []

    for f in fields:
        last = db.query(NDVIRecord).filter(
            NDVIRecord.field_id == f.id
        ).order_by(NDVIRecord.captured_date.desc()).first()

        alerts_count = db.query(Alert).filter(
            Alert.field_id == f.id, Alert.is_active == True
        ).count()

        # Считаем критические алерты
        crit = db.query(Alert).filter(
            Alert.field_id == f.id,
            Alert.is_active == True,
            Alert.severity == "critical"
        ).count()

        active_alerts += alerts_count
        critical_alerts += crit

        if last and last.mean_ndvi is not None:
            ndvi_values.append(last.mean_ndvi)
            if last.mean_ndvi < 0.3:
                fields_with_problems += 1

        season = db.query(CropSeason).filter(
            CropSeason.field_id == f.id,
            CropSeason.season_year == date.today().year
        ).first()

        crop_name = None
        if season:
            crop = db.query(CropType).filter(CropType.id == season.crop_type_id).first()
            crop_name = crop.name_ru if crop else None

        fields_list.append({
            "id": f.id,
            "name": f.name,
            "area_ha": f.area_ha,
            "current_crop": crop_name,
            "ndvi": last.mean_ndvi if last else None,
            "ndvi_date": last.captured_date.isoformat() if last else None,
            "alerts": alerts_count,
        })

    avg_ndvi = round(sum(ndvi_values) / len(ndvi_values), 4) if ndvi_values else None

    return {
        "enterprise_id": enterprise_id,
        "total_fields": total_fields,
        "active_alerts": active_alerts,
        "critical_alerts": critical_alerts,
        "avg_ndvi": avg_ndvi,
        "fields_with_problems": fields_with_problems,
        "fields": fields_list,
        "last_updated": date.today().isoformat(),
    }
