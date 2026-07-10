"""
Скрипт начального заполнения БД:
- Предприятия Бухоро Агрокластера (30 дочерних)
- Справочник культур Узбекистана
- Тестовые поля для демонстрации

Запуск: python scripts/seed_data.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, init_db
from models.enterprise import Enterprise
from models.crop import CropType, UZBEKISTAN_CROPS
from models.monitoring import User, UserRole
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# The previous hardcoded seed password — reject if supplied.
_KNOWN_SEED_PASSWORD = "AgroSat2024!"


def _validate_bootstrap_password(password: str) -> str:
    """Validate and return the bootstrap admin password, or raise."""
    if not password or not password.strip():
        raise RuntimeError(
            "AGROSAT_BOOTSTRAP_ADMIN_PASSWORD is empty or whitespace-only."
        )

    # Check for known insecure value *before* length so that the correct
    # message is returned regardless of the password length.
    if password == _KNOWN_SEED_PASSWORD:
        raise RuntimeError(
            "AGROSAT_BOOTSTRAP_ADMIN_PASSWORD matches a known insecure value. "
            "Set a unique strong password."
        )

    if len(password) < 16:
        raise RuntimeError(
            "AGROSAT_BOOTSTRAP_ADMIN_PASSWORD must be at least 16 characters."
        )

    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)

    if not (has_upper and has_lower and has_digit and has_special):
        raise RuntimeError(
            "AGROSAT_BOOTSTRAP_ADMIN_PASSWORD must contain at least one uppercase "
            "letter, one lowercase letter, one digit, and one non-alphanumeric character."
        )

    return password


def seed_enterprises(db):
    """Заполнить справочник предприятий."""
    if db.query(Enterprise).count() > 0:
        print("⏭️  Предприятия уже заполнены")
        return

    # Типовые предприятия агрокластера (замените на реальные названия)
    enterprises = [
        Enterprise(name="Бухоро Агрокластер (головная компания)", code="BAK-00",
                   region="Бухара", is_active=True),
        Enterprise(name="ДП «Мевали боғ»", code="BAK-01",
                   region="Бухарский район", is_active=True,
                   notes="Садоводство, виноградарство"),
        Enterprise(name="ДП «Далла»", code="BAK-02",
                   region="Каракульский район", is_active=True,
                   notes="Хлопководство, пшеница"),
        Enterprise(name="ДП «Сувли ер»", code="BAK-03",
                   region="Алатский район", is_active=True,
                   notes="Рисоводство, овощеводство"),
        Enterprise(name="ДП «Буйрак»", code="BAK-04",
                   region="Бухарский район", is_active=True),
        Enterprise(name="ДП «Зафар»", code="BAK-05",
                   region="Гиждуванский район", is_active=True),
        Enterprise(name="Гарден Бухоро Агрокластер", code="BAK-06",
                   region="Бухарский район", is_active=True,
                   notes="Хлопководство (капельный и открытый полив), зерновые"),
        # Добавьте остальные 24 предприятия по реальным данным
    ]

    for e in enterprises:
        db.add(e)
    db.commit()
    print(f"✅ Добавлено {len(enterprises)} предприятий")


def seed_crop_types(db):
    """Заполнить справочник культур."""
    if db.query(CropType).count() > 0:
        print("⏭️  Культуры уже заполнены")
        return

    for crop_data in UZBEKISTAN_CROPS:
        crop = CropType(
            code=crop_data["code"],
            name_ru=crop_data["name_ru"],
            name_uz=crop_data["name_uz"],
            name_en=crop_data["name_en"],
            typical_sowing_month=crop_data.get("typical_sowing_month"),
            typical_harvest_month=crop_data.get("typical_harvest_month"),
            growth_stages=crop_data.get("growth_stages", []),
            alerts_config=crop_data.get("alerts", {}),
        )
        db.add(crop)

    db.commit()
    print(f"✅ Добавлено {len(UZBEKISTAN_CROPS)} культур")


def seed_demo_fields(db):
    """Создать несколько демонстрационных полей в Бухарской области."""
    from models.field import Field, CropSeason
    from sqlalchemy import text

    if db.query(Field).count() > 0:
        print("⏭️  Поля уже заполнены")
        return

    enterprise = db.query(Enterprise).filter(Enterprise.code == "BAK-02").first()
    cotton = db.query(CropType).filter(CropType.code == "cotton").first()
    wheat = db.query(CropType).filter(CropType.code == "wheat").first()

    if not enterprise:
        print("❌ Предприятие не найдено — сначала запустите seed_enterprises")
        return

    # Демонстрационные поля в Каракульском районе Бухарской области
    # Координаты примерные — замените на реальные из Wialon/GPS
    demo_fields = [
        {
            "name": "Поле №1 (Далла-Северный)",
            "code": "DAL-01",
            "wkt": "POLYGON((63.820 39.550, 63.825 39.550, 63.825 39.545, 63.820 39.545, 63.820 39.550))",
            "area_ha": 28.5,
            "centroid_lat": 39.5475,
            "centroid_lon": 63.8225,
            "irrigation_type": "canal",
            "crop": cotton,
        },
        {
            "name": "Поле №2 (Далла-Южный)",
            "code": "DAL-02",
            "wkt": "POLYGON((63.825 39.550, 63.833 39.550, 63.833 39.544, 63.825 39.544, 63.825 39.550))",
            "area_ha": 42.0,
            "centroid_lat": 39.547,
            "centroid_lon": 63.829,
            "irrigation_type": "canal",
            "crop": cotton,
        },
        {
            "name": "Поле №3 (Пшеничный участок)",
            "code": "DAL-03",
            "wkt": "POLYGON((63.815 39.555, 63.820 39.555, 63.820 39.548, 63.815 39.548, 63.815 39.555))",
            "area_ha": 35.0,
            "centroid_lat": 39.5515,
            "centroid_lon": 63.8175,
            "irrigation_type": "canal",
            "crop": wheat,
        },
    ]

    from datetime import date

    for f_data in demo_fields:
        # Создаём поле через raw SQL для PostGIS
        result = db.execute(
            text("""
                INSERT INTO fields (enterprise_id, name, code, geometry, area_ha,
                    centroid_lat, centroid_lon, irrigation_type, is_active, created_at, updated_at)
                VALUES (:eid, :name, :code, ST_GeomFromText(:wkt, 4326), :area,
                    :lat, :lon, :irr, true, NOW(), NOW())
                RETURNING id
            """),
            {
                "eid": enterprise.id,
                "name": f_data["name"],
                "code": f_data["code"],
                "wkt": f_data["wkt"],
                "area": f_data["area_ha"],
                "lat": f_data["centroid_lat"],
                "lon": f_data["centroid_lon"],
                "irr": f_data["irrigation_type"],
            }
        )
        field_id = result.fetchone()[0]
        db.commit()

        # Добавляем сезон
        if f_data.get("crop"):
            season = CropSeason(
                field_id=field_id,
                crop_type_id=f_data["crop"].id,
                season_year=date.today().year,
                planting_date=date(date.today().year, 4, 15) if f_data["crop"].code == "cotton"
                             else date(date.today().year - 1, 10, 20),
            )
            db.add(season)
            db.commit()

    print(f"✅ Добавлено {len(demo_fields)} демонстрационных полей")


def seed_admin_user(db):
    """Создать администратора системы, используя AGROSAT_BOOTSTRAP_ADMIN_PASSWORD."""
    if db.query(User).count() > 0:
        print("⏭️  Пользователи уже есть")
        return

    password = os.environ.get("AGROSAT_BOOTSTRAP_ADMIN_PASSWORD", "")
    _validate_bootstrap_password(password)

    admin = User(
        email="admin@agrosat.uz",
        full_name="Администратор AgroSat",
        role=UserRole.ADMIN,
        hashed_password=pwd_context.hash(password),
        is_active=True,
    )
    db.add(admin)
    db.commit()
    print("✅ Администратор создан. Удалите AGROSAT_BOOTSTRAP_ADMIN_PASSWORD из среды после использования.")


if __name__ == "__main__":
    print("🌾 AgroSat — инициализация базы данных...")
    init_db()
    db = SessionLocal()
    try:
        seed_crop_types(db)
        seed_enterprises(db)
        seed_demo_fields(db)
        seed_admin_user(db)
        print("\n✅ База данных успешно инициализирована!")
        print("   Запустите: uvicorn main:app --reload")
        print("   API: http://localhost:8000/api/docs")
    finally:
        db.close()
