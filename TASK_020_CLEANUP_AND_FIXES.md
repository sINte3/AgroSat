# TASK_020: Cleanup Invalid Alerts + Frontend Fix + start.bat Encoding

## Context
Some alerts have `triggered_value` storing percentage changes (e.g. -201.506) instead of actual NDVI values (which are always between -1.0 and 1.0). These need to be deactivated. The frontend also needs to handle these gracefully, and start.bat has encoding issues.

---

## Step 1: Read relevant files first

**MANDATORY** — before making ANY changes, read these files:
```
backend/services/alert_engine.py
frontend/src/components/Dashboard/AlertsList.jsx
frontend/src/pages/AlertsPage.jsx
start.bat
```

Also check if there's a `formatAlertDescription` function — search for it across the frontend:
```bash
grep -r "formatAlertDescription\|triggered_value\|Текущий NDVI" frontend/src/ --include="*.jsx" --include="*.js" -l
```

---

## Step 2: Database cleanup — deactivate invalid alerts

Create and run a Python script `backend/scripts/cleanup_invalid_alerts.py`:

```python
"""
Deactivate alerts where ABS(triggered_value) > 1.0
These are percentage values incorrectly stored as NDVI values.
NDVI is always between -1.0 and 1.0.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import SessionLocal

def cleanup():
    db = SessionLocal()
    try:
        # First, count how many invalid alerts exist
        count_result = db.execute(text("""
            SELECT COUNT(*) FROM alerts 
            WHERE is_active = true 
            AND ABS(triggered_value) > 1.0
        """)).scalar()
        print(f"Found {count_result} active alerts with |triggered_value| > 1.0")
        
        if count_result == 0:
            print("Nothing to clean up.")
            return
        
        # Show some examples before deactivating
        examples = db.execute(text("""
            SELECT id, alert_type, triggered_value, threshold_value
            FROM alerts 
            WHERE is_active = true 
            AND ABS(triggered_value) > 1.0
            LIMIT 5
        """)).fetchall()
        
        print("\nExamples of invalid alerts:")
        for row in examples:
            print(f"  ID={row[0]}, type={row[1]}, triggered={row[2]}, threshold={row[3]}")
        
        # Deactivate them
        result = db.execute(text("""
            UPDATE alerts 
            SET is_active = false 
            WHERE is_active = true 
            AND ABS(triggered_value) > 1.0
        """))
        db.commit()
        
        print(f"\nDeactivated {result.rowcount} invalid alerts.")
        
        # Count remaining active alerts
        remaining = db.execute(text("""
            SELECT COUNT(*) FROM alerts WHERE is_active = true
        """)).scalar()
        print(f"Remaining active alerts: {remaining}")
        
    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
```

Run this script:
```bash
cd C:\AgroSat\backend
python scripts/cleanup_invalid_alerts.py
```

---

## Step 3: Frontend — hide invalid triggered_value in alert descriptions

Find ALL places in the frontend that display `triggered_value` or format alert descriptions. Common locations:
- AlertsList.jsx
- AlertsPage.jsx
- EnterpriseDetailPage.jsx (alerts tab)

**Rule:** If `Math.abs(triggered_value) > 1.0`, do NOT display the value. NDVI is always between -1.0 and 1.0.

Find lines that look like:
```
Текущий NDVI: {alert.triggered_value}
```
or similar patterns.

Replace with a safe version. Example:

```javascript
// FIND any function or inline code that formats triggered_value
// ADD this helper if it doesn't exist, or modify the existing one:

const formatTriggeredValue = (value) => {
  if (value === null || value === undefined) return null;
  if (Math.abs(value) > 1.0) return null; // Invalid — percentage stored as NDVI
  return value.toFixed(3);
};
```

Then in JSX, where triggered_value is displayed:
```jsx
{/* BEFORE (unsafe): */}
<span>NDVI: {alert.triggered_value?.toFixed(3)}</span>

{/* AFTER (safe): */}
{alert.triggered_value !== null && Math.abs(alert.triggered_value) <= 1.0 && (
  <span>NDVI: {alert.triggered_value.toFixed(3)}</span>
)}
```

Apply this pattern to EVERY place triggered_value is rendered. Search thoroughly:
```bash
grep -rn "triggered_value\|triggeredValue" frontend/src/ --include="*.jsx" --include="*.js"
```

---

## Step 4: Fix start.bat encoding

Find `start.bat` in `C:\AgroSat\` (or `C:\AgroSat\backend\`).

Add `chcp 65001` at the very beginning of the file, right after `@echo off` (if present):

```batch
@echo off
chcp 65001 >nul
REM ... rest of the file stays the same
```

The `>nul` suppresses the "Active code page: 65001" message.

If `@echo off` is NOT present, add both lines at the top:
```batch
@echo off
chcp 65001 >nul
```

---

## Verification

After all changes:

1. Run the cleanup script — should report deactivated alerts count
2. Start the frontend: `cd C:\AgroSat\frontend && npm run dev`
3. Open AlertsPage — no alerts should show values like "-201.506"
4. Open EnterpriseDetailPage alerts tab — same check
5. Run `start.bat` — should display Russian text without garbled characters
