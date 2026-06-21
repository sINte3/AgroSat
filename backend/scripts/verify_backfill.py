# -*- coding: utf-8 -*-
"""
verify_backfill.py — проверка полноты исторического бэкфилла NDVI.

Запуск (PowerShell, из C:\\AgroSat\\backend):
    python scripts\\verify_backfill.py

Скрипт READ-ONLY: ничего не пишет в БД. Отвечает на два вопроса:
  1) У всех ли 275 полей есть история NDVI начиная с января?
  2) Это непрерывная помесячная кривая (что увидит FieldDetailPanel),
     или у поля просто пара точек?

Блок [5] (полей в каждом месяце) и [6] (распределение по числу
покрытых месяцев) — главные. Если в январе данные есть у ~275 полей,
а не у ~90 — бэкфилл реально дотянулся до начала года для всех.
"""

import os
import sys

# scripts/ лежит внутри backend/ — добавляем backend/ в sys.path для импорта database
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from database import engine
except ImportError:
    try:
        from app.database import engine  # на случай иной структуры пакета
    except ImportError as exc:
        print("!! Не удалось импортировать engine из database.")
        print("   Запускайте из C:\\AgroSat\\backend:  python scripts\\verify_backfill.py")
        print("   Ошибка импорта:", exc)
        sys.exit(1)

from sqlalchemy import inspect, text

# Граница: первая запись поля позже этой даты => январьская история отсутствует
JAN_CUTOFF = "2026-02-01"
EXPECTED_FIELDS = 275

DATE_CANDIDATES = (
    "captured_date", "acquisition_date", "observed_date", "captured_at", "date",
)


def resolve_date_column(insp):
    cols = [c["name"] for c in insp.get_columns("ndvi_records")]
    date_col = next((c for c in DATE_CANDIDATES if c in cols), None)
    return cols, date_col


def main():
    insp = inspect(engine)
    cols, date_col = resolve_date_column(insp)

    print("=" * 66)
    print("ПРОВЕРКА БЭКФИЛЛА NDVI")
    print("=" * 66)
    print("Колонки ndvi_records:", ", ".join(cols))

    if date_col is None:
        print("\n!! Не нашёл колонку с датой среди кандидатов:", DATE_CANDIDATES)
        print("   Добавьте фактическое имя в DATE_CANDIDATES и перезапустите.")
        return
    print("Колонка даты:", date_col)

    total_fields = 0
    total_with = 0
    no_ndvi_count = 0
    late_jan_count = 0

    with engine.connect() as conn:
        # ── [1] Итоги ────────────────────────────────────────────────
        r = conn.execute(text(
            f"SELECT COUNT(*) AS c, MIN({date_col}) AS lo, MAX({date_col}) AS hi "
            f"FROM ndvi_records"
        )).one()
        print(f"\n[1] Всего записей: {r.c}   диапазон: {r.lo} → {r.hi}")

        # ── [2] Покрытие полей по предприятиям ──────────────────────
        print("\n[2] Покрытие полей по предприятиям:")
        rows = conn.execute(text(
            "SELECT e.id, e.code, e.name, "
            "COUNT(DISTINCT f.id) AS total, "
            "COUNT(DISTINCT n.field_id) AS with_ndvi "
            "FROM enterprises e "
            "JOIN fields f ON f.enterprise_id = e.id "
            "LEFT JOIN ndvi_records n ON n.field_id = f.id "
            "GROUP BY e.id, e.code, e.name ORDER BY e.id"
        )).all()
        for row in rows:
            total_fields += row.total
            total_with += row.with_ndvi
            flag = "OK" if row.with_ndvi == row.total else "!! НЕ ВСЕ"
            name = (row.name or "")[:30]
            print(f"    {row.code:<7} {name:<30} {row.with_ndvi}/{row.total} полей  [{flag}]")
        print(f"    --- ИТОГО: {total_with}/{total_fields} полей имеют хотя бы одну запись")

        # ── [3] Поля без единой записи ──────────────────────────────
        rows = conn.execute(text(
            "SELECT f.id, f.code, f.enterprise_id "
            "FROM fields f "
            "LEFT JOIN ndvi_records n ON n.field_id = f.id "
            "WHERE n.field_id IS NULL "
            "ORDER BY f.enterprise_id, f.id"
        )).all()
        no_ndvi_count = len(rows)
        print(f"\n[3] Полей без NDVI вообще: {no_ndvi_count}")
        for row in rows[:40]:
            print(f"    field_id={row.id:<6} {row.code or '':<10} ent={row.enterprise_id}")
        if no_ndvi_count > 40:
            print(f"    ... и ещё {no_ndvi_count - 40}")

        # ── [4] Поля без январьской истории ─────────────────────────
        rows = conn.execute(text(
            f"SELECT field_id, MIN({date_col}) AS first_d, COUNT(*) AS c "
            "FROM ndvi_records GROUP BY field_id "
            f"HAVING MIN({date_col}) > :cutoff "
            f"ORDER BY first_d DESC"
        ), {"cutoff": JAN_CUTOFF}).all()
        late_jan_count = len(rows)
        print(f"\n[4] Полей с первой записью позже {JAN_CUTOFF} (нет январьской истории): {late_jan_count}")
        for row in rows[:40]:
            print(f"    field_id={row.field_id:<6} первая запись {row.first_d}  всего {row.c}")
        if late_jan_count > 40:
            print(f"    ... и ещё {late_jan_count - 40}")

        # ── [5] Помесячно: записей и УНИКАЛЬНЫХ полей в каждом месяце ─
        rows = conn.execute(text(
            f"SELECT to_char({date_col}, 'YYYY-MM') AS ym, "
            "COUNT(*) AS recs, COUNT(DISTINCT field_id) AS flds "
            "FROM ndvi_records GROUP BY ym ORDER BY ym"
        )).all()
        print("\n[5] По месяцам ('полей' = у скольких полей есть данные в этот месяц):")
        print(f"    {'Месяц':<9} {'записей':>10} {'полей':>10}")
        for row in rows:
            print(f"    {row.ym:<9} {row.recs:>10} {row.flds:>10}")

        # ── [6] Сколько месяцев покрыто у каждого поля (непрерывность) ─
        rows = conn.execute(text(
            f"WITH pf AS (SELECT field_id, "
            f"COUNT(DISTINCT to_char({date_col}, 'YYYY-MM')) AS m "
            "FROM ndvi_records GROUP BY field_id) "
            "SELECT m, COUNT(*) AS n FROM pf GROUP BY m ORDER BY m"
        )).all()
        print("\n[6] Распределение полей по числу покрытых месяцев:")
        print(f"    {'месяцев':<8} {'полей':>10}")
        for row in rows:
            print(f"    {row.m:<8} {row.n:>10}")

    # ── Вердикт ─────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("ВЕРДИКТ:")
    problems = []
    if total_with != total_fields:
        problems.append(f"{total_fields - total_with} полей без единой записи")
    if late_jan_count:
        problems.append(f"{late_jan_count} полей без январьской истории")
    if total_fields != EXPECTED_FIELDS:
        problems.append(f"всего полей {total_fields}, ожидалось {EXPECTED_FIELDS}")

    if not problems:
        print(f"  OK Бэкфилл полный: у всех {total_fields} полей есть история, январь на месте.")
        print("     FieldDetailPanel должен показывать сезонную кривую (см. [6]).")
    else:
        print("  ВНИМАНИЕ, найдены пробелы:")
        for p in problems:
            print("     -", p)
        print("     Эти поля нужно догнать повторным прогоном бэкфилла.")
    print("=" * 66)


if __name__ == "__main__":
    main()
