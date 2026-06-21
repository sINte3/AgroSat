"""
AgroSat — Импорт полей из KML файлов (экспорт Wialon)

Использование:
    python import_kml.py

Файлы KML должны лежать рядом со скриптом или указать путь ниже.

Что делает скрипт:
- Читает 3 KML файла (галла, пахта томчи, пахта очиқ)
- Парсит полигоны и названия полей
- Вычисляет площадь каждого поля в гектарах
- Определяет центроид для запросов погоды
- Загружает в базу данных AgroSat
- Привязывает культуру и сезон 2026
"""

import sys
import os
import xml.etree.ElementTree as ET
import re
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Настройки ───────────────────────────────────────────────────────────────

# Пути к KML файлам — поправь если лежат в другом месте
KML_FILES = {
    "wheat": {
        "path": "Garden_galla.kml",
        "crop_code": "wheat",
        "irrigation": "canal",
        "label": "Галла (зерновые)",
    },
    "cotton_drip": {
        "path": "Garden_paxta_tomchi.kml",
        "crop_code": "cotton",
        "irrigation": "drip",
        "label": "Пахта томчи (капельный)",
    },
    "cotton_open": {
        "path": "Garden_paxta_ochiq.kml",
        "crop_code": "cotton",
        "irrigation": "canal",
        "label": "Пахта очиқ (открытый полив)",
    },
}

# ID предприятия в базе — замени на нужное
# Проверь через GET /api/enterprises/ или в seed_data.py
ENTERPRISE_ID = 7  # Замени на реальный ID предприятия "Гарден"

SEASON_YEAR = 2026

# ─── Парсинг KML ─────────────────────────────────────────────────────────────

def parse_kml(filepath: str) -> list:
    """Парсим KML и возвращаем список полей."""
    if not os.path.exists(filepath):
        # Ищем в папке scripts/
        alt_path = os.path.join(os.path.dirname(__file__), filepath)
        if os.path.exists(alt_path):
            filepath = alt_path
        else:
            print(f"  ❌ Файл не найден: {filepath}")
            return []

    tree = ET.parse(filepath)
    root = tree.getroot()

    # KML может быть с namespace или без
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    fields = []
    for placemark in root.iter(f"{ns}Placemark"):
        name_el = placemark.find(f"{ns}name")
        if name_el is None:
            continue
        raw_name = name_el.text.strip() if name_el.text else "Без названия"

        # Парсим координаты из Polygon
        coords_el = placemark.find(f".//{ns}coordinates")
        if coords_el is None:
            continue

        coords_text = coords_el.text.strip()
        coords = parse_coordinates(coords_text)
        if len(coords) < 3:
            continue

        # Извлекаем номер поля из названия (например "1361 Гарден галла 2026" → "1361")
        code_match = re.match(r"^(\d+[-\d]*)", raw_name)
        field_code = code_match.group(1) if code_match else None

        # Вычисляем площадь и центроид
        area_ha = calculate_area_ha(coords)
        centroid = calculate_centroid(coords)

        # Строим WKT полигон (PostGIS принимает lon lat)
        wkt = coords_to_wkt(coords)

        fields.append({
            "name": raw_name,
            "code": field_code,
            "coords": coords,
            "wkt": wkt,
            "area_ha": area_ha,
            "centroid_lat": centroid[1],
            "centroid_lon": centroid[0],
        })

    return fields


def parse_coordinates(coords_text: str) -> list:
    """
    KML координаты: lon,lat,alt lon,lat,alt ...
    Возвращаем список (lon, lat).
    """
    points = []
    for part in coords_text.split():
        parts = part.strip().split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                points.append((lon, lat))
            except ValueError:
                continue
    return points


def coords_to_wkt(coords: list) -> str:
    """Конвертировать список (lon, lat) в WKT POLYGON."""
    # Замыкаем полигон если нужно
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    coord_str = ", ".join(f"{lon} {lat}" for lon, lat in coords)
    return f"POLYGON(({coord_str}))"


def calculate_area_ha(coords: list) -> float:
    """
    Вычислить площадь полигона в гектарах.
    Используем формулу Shoelace + поправку на широту для Узбекистана.
    """
    if len(coords) < 3:
        return 0.0

    # Средняя широта поля (для Бухарской области ~39-40°)
    avg_lat = sum(lat for _, lat in coords) / len(coords)

    # Перевод градусов в метры на данной широте
    import math
    lat_m = 111320.0  # метров на 1 градус широты
    lon_m = 111320.0 * math.cos(math.radians(avg_lat))

    # Формула Shoelace в метрах
    area = 0.0
    n = len(coords)
    for i in range(n):
        j = (i + 1) % n
        x1 = coords[i][0] * lon_m
        y1 = coords[i][1] * lat_m
        x2 = coords[j][0] * lon_m
        y2 = coords[j][1] * lat_m
        area += x1 * y2 - x2 * y1

    area_m2 = abs(area) / 2.0
    return round(area_m2 / 10000.0, 2)  # м² → га


def calculate_centroid(coords: list) -> tuple:
    """Центр масс полигона."""
    if not coords:
        return (0.0, 0.0)
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return (round(lon, 6), round(lat, 6))


# ─── Импорт в базу данных ────────────────────────────────────────────────────

def import_to_db(fields_by_type: dict):
    """Загрузить поля в базу данных AgroSat."""
    from database import SessionLocal, init_db
    from models.enterprise import Enterprise
    from models.crop import CropType
    from models.field import Field, CropSeason
    from sqlalchemy import text

    init_db()
    db = SessionLocal()

    try:
        # Проверяем предприятие
        enterprise = db.query(Enterprise).filter(
            Enterprise.id == ENTERPRISE_ID
        ).first()
        if not enterprise:
            print(f"\n❌ Предприятие с ID={ENTERPRISE_ID} не найдено!")
            print("   Доступные предприятия:")
            for e in db.query(Enterprise).all():
                print(f"   ID={e.id}: {e.name}")
            print(f"\n   Измени ENTERPRISE_ID в скрипте и запусти снова.")
            return

        print(f"\n✅ Предприятие: {enterprise.name}")

        total_imported = 0
        total_skipped = 0

        for type_key, type_info in fields_by_type.items():
            fields = type_info["fields"]
            crop_code = type_info["crop_code"]
            irrigation = type_info["irrigation"]

            if not fields:
                continue

            # Получаем культуру
            crop = db.query(CropType).filter(CropType.code == crop_code).first()
            if not crop:
                print(f"  ❌ Культура '{crop_code}' не найдена в справочнике")
                continue

            print(f"\n📁 {type_info['label']}: {len(fields)} полей")
            print(f"   Культура: {crop.name_ru}, Орошение: {irrigation}")

            for f in fields:
                # Проверяем — нет ли уже поля с таким кодом
                if f["code"]:
                    existing = db.query(Field).filter(
                        Field.code == f["code"],
                        Field.enterprise_id == ENTERPRISE_ID
                    ).first()
                    if existing:
                        print(f"   ⏭️  Пропуск {f['code']} — уже существует")
                        total_skipped += 1
                        continue

                try:
                    # Вставляем поле через PostGIS
                    result = db.execute(
                        text("""
                            INSERT INTO fields
                                (enterprise_id, name, code, geometry, area_ha,
                                 centroid_lat, centroid_lon, irrigation_type,
                                 is_active, created_at, updated_at)
                            VALUES
                                (:eid, :name, :code,
                                 ST_GeomFromText(:wkt, 4326),
                                 :area, :lat, :lon, :irr,
                                 true, NOW(), NOW())
                            RETURNING id
                        """),
                        {
                            "eid": ENTERPRISE_ID,
                            "name": f["name"],
                            "code": f["code"],
                            "wkt": f["wkt"],
                            "area": f["area_ha"],
                            "lat": f["centroid_lat"],
                            "lon": f["centroid_lon"],
                            "irr": irrigation,
                        }
                    )
                    field_id = result.fetchone()[0]
                    db.commit()

                    # Добавляем сезон 2026
                    season = CropSeason(
                        field_id=field_id,
                        crop_type_id=crop.id,
                        season_year=SEASON_YEAR,
                        planting_date=date(SEASON_YEAR, 4, 10)
                        if crop_code == "cotton"
                        else date(SEASON_YEAR - 1, 10, 15),
                    )
                    db.add(season)
                    db.commit()

                    print(
                        f"   ✅ {f['code'] or '?':10s} | "
                        f"{f['name'][:35]:35s} | "
                        f"{f['area_ha']:6.1f} га"
                    )
                    total_imported += 1

                except Exception as e:
                    db.rollback()
                    print(f"   ❌ Ошибка импорта {f['name']}: {e}")

        print(f"\n{'='*60}")
        print(f"✅ Импортировано: {total_imported} полей")
        print(f"⏭️  Пропущено:    {total_skipped} (уже были в БД)")
        print(f"{'='*60}")
        print(f"\nОбновите браузер — поля появятся на карте!")
        print(f"Swagger: POST /api/ndvi/{{field_id}}/refresh — запустить NDVI")

    finally:
        db.close()


# ─── Главная функция ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🌾 AgroSat — Импорт полей из KML (Wialon)")
    print("=" * 60)

    fields_by_type = {}
    total_fields = 0

    for type_key, type_info in KML_FILES.items():
        print(f"\n📂 Читаю: {type_info['path']}")
        fields = parse_kml(type_info["path"])
        print(f"   Найдено полигонов: {len(fields)}")

        if fields:
            # Показываем превью первых 3 полей
            for f in fields[:3]:
                print(f"   → {f['code']:10s} | {f['name'][:40]:40s} | {f['area_ha']:.1f} га")
            if len(fields) > 3:
                print(f"   ... и ещё {len(fields)-3} полей")

        fields_by_type[type_key] = {**type_info, "fields": fields}
        total_fields += len(fields)

    print(f"\n{'='*60}")
    print(f"Итого полей для импорта: {total_fields}")
    print(f"Предприятие ID: {ENTERPRISE_ID}")
    print(f"Сезон: {SEASON_YEAR}")
    print(f"{'='*60}")

    if total_fields == 0:
        print("\n❌ Нет полей для импорта. Проверьте пути к KML файлам.")
        sys.exit(1)

    answer = input("\nПродолжить импорт? (y/n): ").strip().lower()
    if answer != "y":
        print("Отменено.")
        sys.exit(0)

    import_to_db(fields_by_type)
