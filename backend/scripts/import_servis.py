"""
AgroSat — Импорт полей Бухара Сервис Агрокластер
187 полей: 52 галла + 56 пахта томчи + 79 пахта очик

Запуск: python import_servis.py
"""

import sys
import os
import xml.etree.ElementTree as ET
import re
import math
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Настройки ───────────────────────────────────────────────────────────────

ENTERPRISE_NAME = "Бухара Сервис Агрокластер"
ENTERPRISE_CODE = "BAK-08"
ENTERPRISE_REGION = "Бухарский район"
SEASON_YEAR = 2026

KML_FILES = [
    {
        "path": "Servis_galla.kml",
        "crop_code": "wheat",
        "irrigation": "canal",
        "label": "Галла (зерновые)",
    },
    {
        "path": "Servis_paxta_tomchi.kml",
        "crop_code": "cotton",
        "irrigation": "drip",
        "label": "Пахта томчи (капельный)",
    },
    {
        "path": "Servis_paxta_ochiq.kml",
        "crop_code": "cotton",
        "irrigation": "canal",
        "label": "Пахта очик (открытый полив)",
    },
]


# ─── Парсинг KML ─────────────────────────────────────────────────────────────

def parse_kml(filepath):
    if not os.path.exists(filepath):
        alt = os.path.join(os.path.dirname(__file__), filepath)
        if os.path.exists(alt):
            filepath = alt
        else:
            print(f"  ❌ Файл не найден: {filepath}")
            return []

    tree = ET.parse(filepath)
    root = tree.getroot()
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""

    fields = []
    for placemark in root.iter(f"{ns}Placemark"):
        name_el = placemark.find(f"{ns}name")
        if name_el is None:
            continue
        raw_name = name_el.text.strip() if name_el.text else "Без названия"

        coords_el = placemark.find(f".//{ns}coordinates")
        if coords_el is None:
            continue

        coords = parse_coordinates(coords_el.text.strip())
        if len(coords) < 3:
            continue

        code_match = re.match(r"^([\d\w\-]+)", raw_name)
        field_code = code_match.group(1) if code_match else None

        area_ha = calculate_area_ha(coords)
        centroid = calculate_centroid(coords)
        wkt = coords_to_wkt(coords)

        fields.append({
            "name": raw_name,
            "code": field_code,
            "wkt": wkt,
            "area_ha": area_ha,
            "centroid_lat": centroid[1],
            "centroid_lon": centroid[0],
        })

    return fields


def parse_coordinates(text):
    points = []
    for part in text.split():
        parts = part.strip().split(",")
        if len(parts) >= 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                points.append((lon, lat))
            except ValueError:
                continue
    return points


def coords_to_wkt(coords):
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    return "POLYGON((" + ", ".join(f"{lon} {lat}" for lon, lat in coords) + "))"


def calculate_area_ha(coords):
    if len(coords) < 3:
        return 0.0
    avg_lat = sum(lat for _, lat in coords) / len(coords)
    lat_m = 111320.0
    lon_m = 111320.0 * math.cos(math.radians(avg_lat))
    area = 0.0
    n = len(coords)
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * lon_m * coords[j][1] * lat_m
        area -= coords[j][0] * lon_m * coords[i][1] * lat_m
    return round(abs(area) / 2.0 / 10000.0, 2)


def calculate_centroid(coords):
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return (round(lon, 6), round(lat, 6))


# ─── Импорт ──────────────────────────────────────────────────────────────────

def get_or_create_enterprise(db):
    from models.enterprise import Enterprise
    existing = db.query(Enterprise).filter(
        Enterprise.code == ENTERPRISE_CODE
    ).first()
    if existing:
        print(f"✅ Предприятие уже существует: {existing.name} (ID={existing.id})")
        return existing

    e = Enterprise(
        name=ENTERPRISE_NAME,
        code=ENTERPRISE_CODE,
        region=ENTERPRISE_REGION,
        is_active=True,
        notes="Хлопководство (капельный и открытый полив), зерновые",
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    print(f"✅ Создано предприятие: {e.name} (ID={e.id})")
    return e


def import_to_db(enterprise, fields_by_type, db):
    from models.crop import CropType
    from models.field import CropSeason
    from sqlalchemy import text

    total_imported = 0
    total_skipped = 0

    for type_info in fields_by_type:
        fields = type_info["fields"]
        crop_code = type_info["crop_code"]
        irrigation = type_info["irrigation"]

        if not fields:
            continue

        crop = db.query(CropType).filter(CropType.code == crop_code).first()
        if not crop:
            print(f"  ❌ Культура '{crop_code}' не найдена")
            continue

        print(f"\n📁 {type_info['label']}: {len(fields)} полей")
        print(f"   Культура: {crop.name_ru}, Орошение: {irrigation}")

        for f in fields:
            if f["code"]:
                from models.field import Field
                existing = db.query(Field).filter(
                    Field.code == f["code"],
                    Field.enterprise_id == enterprise.id
                ).first()
                if existing:
                    print(f"   ⏭️  Пропуск {f['code']} — уже существует")
                    total_skipped += 1
                    continue

            try:
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
                        "eid": enterprise.id,
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
                    f"   ✅ {str(f['code'] or '?'):12s} | "
                    f"{f['name'][:35]:35s} | "
                    f"{f['area_ha']:6.1f} га"
                )
                total_imported += 1

            except Exception as e:
                db.rollback()
                print(f"   ❌ Ошибка {f['name']}: {e}")

    return total_imported, total_skipped


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🌾 AgroSat — Импорт полей Бухара Сервис Агрокластер")
    print("=" * 60)

    from database import SessionLocal, init_db
    from models.enterprise import Enterprise
    from models.field import Field, CropSeason
    from models.monitoring import NDVIRecord, Alert, User
    from models.crop import CropType

    # Читаем KML
    fields_by_type = []
    total = 0
    for info in KML_FILES:
        print(f"\n📂 Читаю: {info['path']}")
        fields = parse_kml(info["path"])
        print(f"   Найдено полигонов: {len(fields)}")
        for f in fields[:3]:
            print(f"   → {str(f['code']):12s} | {f['name'][:40]:40s} | {f['area_ha']:.1f} га")
        if len(fields) > 3:
            print(f"   ... и ещё {len(fields)-3} полей")
        fields_by_type.append({**info, "fields": fields})
        total += len(fields)

    print(f"\n{'='*60}")
    print(f"Итого полей: {total}")
    print(f"Предприятие: {ENTERPRISE_NAME} ({ENTERPRISE_CODE})")
    print(f"{'='*60}")

    if total == 0:
        print("❌ Нет полей. Проверьте KML файлы.")
        sys.exit(1)

    ans = input("\nПродолжить импорт? (y/n): ").strip().lower()
    if ans != "y":
        print("Отменено.")
        sys.exit(0)

    init_db()
    db = SessionLocal()
    try:
        enterprise = get_or_create_enterprise(db)
        imported, skipped = import_to_db(enterprise, fields_by_type, db)

        print(f"\n{'='*60}")
        print(f"✅ Импортировано: {imported} полей")
        print(f"⏭️  Пропущено:    {skipped}")
        print(f"{'='*60}")
        print(f"\nТеперь запусти: python fetch_all_ndvi.py")
    finally:
        db.close()
