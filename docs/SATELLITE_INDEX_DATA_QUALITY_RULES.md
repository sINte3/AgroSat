# Satellite Index Data Quality Rules

## 1. Purpose

This document defines valid data-quality predicates for `satellite_index_records`
and prevents future audits from incorrectly treating negative vegetation index
values as invalid.

It serves as the canonical reference for audit SQL, data validation logic, and
operational rules governing satellite index storage in the AgroSat system.

## 2. Tables and scope

| Table | Role |
|---|---|
| `satellite_index_records` | Stores additional spectral indices |
| `ndvi_records` | Stores NDVI only |

`savellite_index_records` stores the following index types only:

- `savi`
- `evi`
- `ndmi`
- `ndre`

`ndvi` must **not** be inserted into `satellite_index_records`. Legacy NDVI
remains in the `ndvi_records` table and is served via `/api/ndvi/*` endpoints.

## 3. Current local replay baseline

The following values were confirmed after TASK_095, TASK_097, and TASK_099:

- Runtime DB target: local `localhost:5432/agrosat`
- `satellite_index_records = 1104`
- `ndvi_records = 5818`
- Covered field IDs: `4..278`
- Distinct covered fields: `275`
- Each of `savi`, `evi`, `ndmi`, `ndre` has `275` rows
- Duplicate groups by `(field_id, captured_date, index_code)` = `0`
- Unsupported `ndvi` rows in `satellite_index_records` = `0`
- Invalid values under correct predicates = `0`

## 4. Valid `mean_value` predicate

A `mean_value` is valid if:

- it is not `NULL`
- it is finite
- it is not `NaN`
- it is not `Infinity`
- it is not `-Infinity`
- it is within the application-defined broad range `[-1.0, 1.0]`

**`mean_value < 0` is not an invalid-value predicate**

A `mean_value` must not be treated as invalid merely because it is negative.

## 5. Valid `valid_pixels_pct` predicate

A `valid_pixels_pct` value is valid if:

- it is not `NULL`
- it is finite
- it is not `NaN`
- it is not `Infinity`
- it is not `-Infinity`
- it is between `0` and `100`, inclusive

## 6. Negative value interpretation

Negative spectral-index values are valid and carry meaningful agronomic
information:

- `ndmi < 0`: dry vegetation, low moisture, bare/dry soil, or moisture stress
  signal
- `ndre < 0`: sparse vegetation / low red-edge response can be legitimate
- `savi < 0`: bare soil or very low vegetation can be legitimate
- `evi < 0`: low vegetation or reflectance conditions can be legitimate

Negative values require agronomic interpretation, not automatic deletion or
correction.

## 7. Correct audit predicates

The following safe read-only SQL examples target the local database
`localhost:5432/agrosat`.

```sql
-- Count total rows
SELECT count(*) AS total FROM satellite_index_records;

-- Grouped count by index code
SELECT index_code, count(*) AS cnt
  FROM satellite_index_records
 GROUP BY index_code
 ORDER BY index_code;

-- Detect unsupported index codes
SELECT DISTINCT index_code
  FROM satellite_index_records
 WHERE index_code NOT IN ('savi', 'evi', 'ndmi', 'ndre');

-- Detect ndvi rows inside satellite_index_records
SELECT count(*) AS ndvi_in_satellite_index
  FROM satellite_index_records
 WHERE index_code = 'ndvi';

-- Detect duplicates by (field_id, captured_date, index_code)
SELECT field_id, captured_date, index_code, count(*) AS cnt
  FROM satellite_index_records
 GROUP BY field_id, captured_date, index_code
HAVING count(*) > 1;

-- Detect invalid mean_value
SELECT count(*) AS invalid_mean_value
  FROM satellite_index_records
 WHERE mean_value IS NULL
    OR mean_value <> mean_value  -- NaN check
    OR mean_value::numeric = 'Infinity'::numeric
    OR mean_value::numeric = '-Infinity'::numeric
    OR mean_value < -1.0
    OR mean_value > 1.0;

-- Detect invalid valid_pixels_pct
SELECT count(*) AS invalid_valid_pixels_pct
  FROM satellite_index_records
 WHERE valid_pixels_pct IS NULL
    OR valid_pixels_pct <> valid_pixels_pct
    OR valid_pixels_pct::numeric = 'Infinity'::numeric
    OR valid_pixels_pct::numeric = '-Infinity'::numeric
    OR valid_pixels_pct < 0
    OR valid_pixels_pct > 100;

-- Detect incomplete field coverage (fields missing from one or more index types)
WITH all_fields AS (
  SELECT DISTINCT field_id FROM satellite_index_records
), all_codes AS (
  SELECT unnest(ARRAY['savi', 'evi', 'ndmi', 'ndre']) AS index_code
), expected AS (
  SELECT field_id, index_code
    FROM all_fields CROSS JOIN all_codes
)
SELECT e.field_id, e.index_code
  FROM expected e
  LEFT JOIN satellite_index_records r
         ON r.field_id = e.field_id
        AND r.index_code = e.index_code
 WHERE r.field_id IS NULL
 ORDER BY e.field_id, e.index_code;

-- Spot-check representative fields
SELECT field_id, index_code,
       count(*) AS rows,
       min(mean_value) AS min_val,
       max(mean_value) AS max_val,
       min(captured_date) AS earliest,
       max(captured_date) AS latest
  FROM satellite_index_records
 WHERE field_id IN (SELECT field_id FROM fields ORDER BY field_id LIMIT 5)
 GROUP BY field_id, index_code
 ORDER BY field_id, index_code;
```

## 8. Incorrect predicates to avoid

The following anti-patterns must not be used:

- **Do not use `mean_value < 0` as invalid**. Negative values are valid for
  spectral indices (see §6).
- **Do not assume NDMI/NDRE/SAVI/EVI must be non-negative**. All four indices
  legitimately produce negative values in certain agronomic conditions.
- **Do not rerun write-mode idempotency replay**. Sentinel Hub values may drift
  over time, so a replay can overwrite previously correct data with slightly
  different values, making it appear as though data was corrupted when it was
  not.
- **Do not mix NDVI legacy checks with satellite-index checks**. The two tables
  have different schemas, value ranges, and quality rules.
- **Do not check root `.env` as runtime source of truth**. Runtime DB
  configuration is loaded through `backend/config.py`, not from the repository
  root `.env` file.

**Do not rerun write-mode idempotency replay**

## 9. Operational rules

- **DB target must be verified as local `localhost/agrosat`** before any local
  replay or audit task.
- **APScheduler must not be run inside FastAPI**. Satellite collection and
  backfill must remain isolated CLI execution.
- Satellite collection/backfill must remain isolated CLI execution.
- **SQLAlchemy lazy loading is forbidden** in runtime code; use explicit joins
  and queries.
- **Database schema changes require migrations**. Direct ALTER TABLE in the
  database is not allowed.
- **Frontend MapLibre hooks** must clean up layers, sources, and event
  listeners on unmount.

**APScheduler must not be run inside FastAPI**

## 10. TASK_099 conclusion

TASK_098 reported 68 rows with negative `mean_value` values as potentially
invalid. TASK_099 investigated and concluded:

- The 68 rows were negative but **valid**.
- They were **not data corruption**.
- **No repair was needed**.
- The false positive was caused by using `mean_value < 0` as an invalid-value
  predicate, which is incorrect for vegetation/water indices.
- Future audits must use the documented predicates in §7 instead.
