# TASK_019 — Fix NDVI data quality and display

## Skills to load before starting
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Critical context

The user (an enterprise developer) showed concerns:
1. NDVI values look implausibly low on cotton fields (negative or near zero in growing season)
2. Alerts show change percentages like "-185%", "-500%" — which is nonsense and makes the user look incompetent to agronomists
3. There is no indication of cloud cover or snapshot date — agronomists can't judge if data is reliable

We must fix these BEFORE the user shows data to management. Wrong data destroys trust.

---

## Part A — Backend fixes

### A1. Database check (read-only — DO NOT modify)

Run a quick sanity check before changing anything:
```bash
cd C:\AgroSat\backend
venv\Scripts\activate
python -c "from database import engine; from sqlalchemy import text; \
  res = engine.connect().execute(text('SELECT MIN(cloud_cover_pct), MAX(cloud_cover_pct), AVG(cloud_cover_pct), COUNT(*) FROM ndvi_records')).fetchone(); \
  print('cloud_cover_pct stats:', res)"
```
Just to confirm cloud_cover_pct exists and has values. Then proceed.

---

### A2. Add cloud filtering in NDVI history endpoint

File: `backend/api/ndvi.py`

Find the function that returns NDVI history for a field. It currently returns ALL records.
Modify it to:
- ADD a query param `include_cloudy: bool = False` (default false)
- Filter `cloud_cover_pct <= 30` when `include_cloudy = false`
- ALWAYS include `cloud_cover_pct` and `captured_date` in response

Also expose `ndvi_change` (absolute delta) alongside `ndvi_change_pct` in the response.
Frontend will prefer the absolute number.

If the endpoint already returns these fields just check the cloud filter is applied.

---

### A3. Fix the alert engine — sane percent calculation

File: `backend/services/alert_engine.py`

Find where alerts compute `triggered_value` / change percent.

The current bug: percent change is calculated as `(new - old) / old * 100`. When `old` is near 0 or negative (early season, bare soil, clouds), this produces -185%, -500%, etc.

Replace the percent calculation logic with this safer version:

```python
def safe_pct_change(new_val, old_val):
    """
    Returns percent change ONLY when it is meaningful.
    Returns None if old_val is too small or negative — caller must show absolute change instead.
    """
    if old_val is None or new_val is None:
        return None
    if old_val < 0.15:           # too close to zero / soil — percent is misleading
        return None
    if old_val < 0:              # negative NDVI = water/clouds — not a base
        return None
    return round((new_val - old_val) / old_val * 100, 1)
```

Use this helper everywhere a percent change is computed for alerts. When it returns None, the alert description should use **absolute change** in NDVI (e.g. "NDVI снизился с 0.42 до 0.18 (−0.24)") instead of percent.

Also when generating new alerts, REJECT/skip cases where:
- Either `mean_ndvi < -0.5` (sensor noise, clouds)
- OR `cloud_cover_pct > 30` (cloudy snapshot — not reliable)

Add at the top of alert generation:
```python
# Skip unreliable readings — clouds or sensor noise
if record.cloud_cover_pct is not None and record.cloud_cover_pct > 30:
    return None  # do not create alert from cloudy snapshot
if record.mean_ndvi is None or record.mean_ndvi < -0.5:
    return None  # sensor noise / water — not a real reading
```

---

### A4. Add a cleanup script — recompute alerts and drop bad ones

Create `backend/scripts/clean_bad_alerts.py`:

```python
"""
Удаляет (помечает is_active=false) алерты, основанные на ненадёжных данных:
  - снимки с облачностью > 30%
  - NDVI < -0.5 (шум сенсора / вода)
  - проценты падения, рассчитанные от очень низкой базы

Запуск: python scripts/clean_bad_alerts.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

# 1. Деактивировать алерты по облачным снимкам
r1 = db.execute(text("""
    UPDATE alerts a
    SET is_active = false
    FROM ndvi_records n
    WHERE a.ndvi_record_id = n.id
      AND a.is_active = true
      AND n.cloud_cover_pct > 30
"""))

# 2. Деактивировать алерты по шумным снимкам (NDVI < -0.5)
r2 = db.execute(text("""
    UPDATE alerts a
    SET is_active = false
    FROM ndvi_records n
    WHERE a.ndvi_record_id = n.id
      AND a.is_active = true
      AND n.mean_ndvi < -0.5
"""))

# 3. Деактивировать алерты с нереалистичным процентом (< -200% или > 500%)
r3 = db.execute(text("""
    UPDATE alerts
    SET is_active = false
    WHERE is_active = true
      AND triggered_value IS NOT NULL
      AND threshold_value IS NOT NULL
      AND ABS(triggered_value) < 0.05
"""))

db.commit()

# Подсчёт оставшихся
remaining = db.execute(text("SELECT COUNT(*) FROM alerts WHERE is_active = true")).scalar()
print(f"✅ Очистка завершена. Активных алертов осталось: {remaining}")
db.close()
```

Run it ONCE after fixing the alert engine:
```bash
cd C:\AgroSat\backend
python scripts/clean_bad_alerts.py
```

---

## Part B — Frontend fixes

### B1. Alert card description — absolute values instead of broken percentages

File: `frontend/src/pages/EnterpriseDetailPage.jsx`

Find the `RecommendationsTab` component, specifically where each alert description is rendered.

Currently alerts show titles like "Критическое падение NDVI (-185.3%)" — this is misleading.

Replace the alert title/description rendering with a function that detects and formats reasonable values:

```jsx
// Add this helper at the top of EnterpriseDetailPage.jsx (after imports):
function formatAlertTitle(alert) {
  let title = alert.title || 'Алерт';

  // Strip out crazy percentages like (-185%) or (-500.3%) from the title
  // Replace them with absolute NDVI values when available
  const pctMatch = title.match(/\(([-+]?\d+(?:\.\d+)?)%\)/);
  if (pctMatch) {
    const pct = parseFloat(pctMatch[1]);
    // If percentage is sane (between -100 and +200), keep it
    // Otherwise strip it from the title
    if (pct < -100 || pct > 200) {
      title = title.replace(/\s*\([-+]?\d+(?:\.\d+)?%\)/, '');
    }
  }
  return title;
}

function formatAlertDescription(alert) {
  let desc = alert.description || '';

  // Detect ratios of the form "с X до Y" and ensure they show absolute NDVI
  // If the description contains an obviously broken percent, strip it
  desc = desc.replace(/\(\s*снижение на [-+]?\d{3,}(?:\.\d+)?%\s*\)/g, '');
  desc = desc.replace(/\(\s*[-+]?\d{3,}(?:\.\d+)?%\s*\)/g, '');

  // If we have triggered_value and threshold_value, append a clean absolute summary
  if (alert.triggered_value !== null && alert.triggered_value !== undefined &&
      alert.threshold_value !== null && alert.threshold_value !== undefined) {
    const cur = Number(alert.triggered_value).toFixed(3);
    const thr = Number(alert.threshold_value).toFixed(3);
    const delta = (Number(alert.triggered_value) - Number(alert.threshold_value)).toFixed(3);
    const deltaStr = delta >= 0 ? `+${delta}` : delta;
    if (!desc.includes('NDVI:')) {
      desc += desc ? ` ` : '';
      desc += `Текущий NDVI: ${cur}, порог: ${thr} (${deltaStr}).`;
    }
  }
  return desc.trim();
}
```

Then in the alert card JSX, replace:
```jsx
{alertItem.title}
```
with:
```jsx
{formatAlertTitle(alertItem)}
```

And replace:
```jsx
{alertItem.description}
```
with:
```jsx
{formatAlertDescription(alertItem)}
```

### B2. Show snapshot date and cloud cover on alert cards

Add a small line below the alert description showing data quality info:

```jsx
{(alertItem.captured_date || alertItem.cloud_cover_pct !== undefined) && (
  <div style={{
    fontSize: 10,
    color: '#94a3b8',
    marginTop: 6,
    display: 'flex',
    gap: 10,
    flexWrap: 'wrap',
  }}>
    {alertItem.captured_date && (
      <span>📅 Снимок: {new Date(alertItem.captured_date).toLocaleDateString('ru-RU')}</span>
    )}
    {alertItem.cloud_cover_pct !== undefined && alertItem.cloud_cover_pct !== null && (
      <span style={{ color: alertItem.cloud_cover_pct > 20 ? '#fbbf24' : '#94a3b8' }}>
        ☁️ Облачность: {Math.round(alertItem.cloud_cover_pct)}%
      </span>
    )}
  </div>
)}
```

If `captured_date` and `cloud_cover_pct` are not currently in the alert response from backend, that's fine — the conditional won't render anything until backend includes them.

### B3. Update backend alerts endpoint to include snapshot metadata

File: `backend/api/alerts.py`

Find the SELECT query for the alerts list. It should JOIN ndvi_records and include:
- `n.captured_date as captured_date`
- `n.cloud_cover_pct as cloud_cover_pct`
- `n.mean_ndvi as snapshot_ndvi`

Example pattern (adapt to existing query):
```python
SELECT a.id, a.field_id, a.alert_type, a.severity, a.title, a.description,
       a.recommendation, a.triggered_value, a.threshold_value, a.triggered_at,
       n.captured_date, n.cloud_cover_pct, n.mean_ndvi as snapshot_ndvi
FROM alerts a
LEFT JOIN ndvi_records n ON n.id = a.ndvi_record_id
WHERE ...
```

Include these new fields in the response dict.

### B4. Show snapshot date on EnterpriseFieldsTable too

File: `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

If each field row has `last_ndvi_date` (it already does — used in lastUpdated calc), show it as a small tooltip or subtitle under the NDVI value. Optional but nice.

---

## Verification checklist

After all changes:

1. Restart backend (`taskkill /F /IM python.exe` then `start.bat`)
2. Run cleanup script: `python scripts/clean_bad_alerts.py`
3. Open Предприятия → Бухара Сервис → Рекомендации
4. Verify:
   - [ ] NO alert title contains numbers like "-185%" or "-500%"
   - [ ] Each alert shows current and threshold NDVI as absolute numbers
   - [ ] Each alert shows snapshot date and cloud cover (when backend provides them)
   - [ ] Total number of active alerts has decreased (cloudy snapshots filtered out)
5. Open one field — check NDVI history graph excludes cloudy snapshots

## Files touched
- `backend/api/ndvi.py` (filter cloudy snapshots)
- `backend/api/alerts.py` (return captured_date, cloud_cover_pct)
- `backend/services/alert_engine.py` (safe_pct_change + skip cloudy)
- `backend/scripts/clean_bad_alerts.py` (new file, one-time cleanup)
- `frontend/src/pages/EnterpriseDetailPage.jsx` (formatAlertTitle, formatAlertDescription, snapshot info)
- `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx` (snapshot date subtitle — optional)

## Important
- DO NOT delete any NDVI records or alerts — only mark `is_active=false`
- All UI text in Russian
- Test that the cleanup script reports a sensible number of remaining alerts
- The percent fix is the MOST IMPORTANT thing — users will lose trust if they see -500%

## After this task
The next task (TASK_020) will add visible cloud-cover and snapshot-date columns to the fields table,
plus a "data quality" indicator. But first we must stop showing nonsense numbers.
