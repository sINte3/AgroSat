# TASK_007: Implement NDVIChart and WeatherWidget Components

## Context

AgroSat field monitoring system. Frontend is React + Vite + TailwindCSS + Recharts + MapLibre.
Dark theme: background `#0f1b0d`, accent green `#4ade80`.
All UI text and comments in **Russian**.

Both components live inside the field detail panel (right sidebar), loaded when user clicks a field on the map.
The panel already has tabs — currently the NDVI and Weather tabs render empty components.

---

## TASK 1: NDVIChart component

### File: `frontend/src/components/Field/NDVIChart.jsx`

Replace the existing empty stub with a full implementation.

**What it does:**
- Fetches NDVI history from `GET /api/ndvi/{field_id}/history?days=90`
- Renders a Recharts line chart: date on X axis, NDVI value (0–1) on Y axis
- Shows colored reference bands / lines for NDVI zones
- Has a day-range selector: 30 / 60 / 90 days
- Loading skeleton and empty state

**API response shape:**
```json
{
  "field_id": 42,
  "field_name": "Поле А-12",
  "records": [
    {
      "id": 1,
      "captured_date": "2025-04-01",
      "mean_ndvi": 0.4823,
      "min_ndvi": 0.2100,
      "max_ndvi": 0.7200,
      "change_pct": -3.2,
      "satellite": "sentinel-2",
      "cloud_cover_pct": 5.0
    }
  ],
  "count": 24
}
```

**NDVI color zones (reference lines):**
```
< 0.20  → критически низкий   (красный   #ef4444)
0.20–0.35 → слабый            (оранжевый #f97316)
0.35–0.50 → умеренный         (жёлтый    #eab308)
0.50–0.65 → хороший           (светло-зелёный #84cc16)
> 0.65  → отличный            (зелёный   #22c55e)
```

**Component implementation:**

```jsx
import React, { useState, useEffect } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
  Area, AreaChart, Legend
} from 'recharts';
import apiClient from '../../api/client';

// Получить цвет по значению NDVI
function getNdviColor(value) {
  if (value < 0.20) return '#ef4444';
  if (value < 0.35) return '#f97316';
  if (value < 0.50) return '#eab308';
  if (value < 0.65) return '#84cc16';
  return '#22c55e';
}

// Метка NDVI зоны
function getNdviLabel(value) {
  if (value < 0.20) return 'Критически низкий';
  if (value < 0.35) return 'Слабый';
  if (value < 0.50) return 'Умеренный';
  if (value < 0.65) return 'Хороший';
  return 'Отличный';
}

// Форматировать дату для оси X
function formatDate(dateStr) {
  const d = new Date(dateStr);
  return `${d.getDate().toString().padStart(2,'0')}.${(d.getMonth()+1).toString().padStart(2,'0')}`;
}

// Кастомный тултип
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const ndvi = payload[0]?.value;
  if (ndvi === undefined) return null;
  const d = new Date(label);
  const dateStr = d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
  return (
    <div style={{
      background: '#1a2e1a',
      border: '1px solid #2d4a2d',
      borderRadius: '8px',
      padding: '10px 14px',
      fontSize: '13px',
      color: '#e2e8f0'
    }}>
      <div style={{ color: '#94a3b8', marginBottom: 4 }}>{dateStr}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          display: 'inline-block', width: 10, height: 10,
          borderRadius: '50%', background: getNdviColor(ndvi)
        }} />
        <span style={{ color: '#e2e8f0' }}>NDVI: </span>
        <span style={{ color: getNdviColor(ndvi), fontWeight: 700, fontSize: 15 }}>
          {ndvi.toFixed(4)}
        </span>
      </div>
      <div style={{ color: '#94a3b8', marginTop: 2, fontSize: 12 }}>
        {getNdviLabel(ndvi)}
      </div>
    </div>
  );
}

// Скелетон загрузки
function LoadingSkeleton() {
  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ background: '#1a2e1a', borderRadius: 6, height: 200, animation: 'pulse 1.5s infinite' }} />
    </div>
  );
}

export default function NDVIChart({ fieldId }) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(90);

  useEffect(() => {
    if (!fieldId) return;
    setLoading(true);
    setError(null);
    apiClient.get(`/api/ndvi/${fieldId}/history?days=${days}`)
      .then(res => {
        const records = (res.data.records || []).map(r => ({
          date: r.captured_date,
          ndvi: parseFloat(r.mean_ndvi),
          min: parseFloat(r.min_ndvi),
          max: parseFloat(r.max_ndvi),
          change: r.change_pct,
        }));
        // Сортируем по дате
        records.sort((a, b) => new Date(a.date) - new Date(b.date));
        setData(records);
      })
      .catch(err => {
        console.error('Ошибка загрузки NDVI:', err);
        setError('Не удалось загрузить историю NDVI');
      })
      .finally(() => setLoading(false));
  }, [fieldId, days]);

  if (loading) return <LoadingSkeleton />;

  if (error) return (
    <div style={{
      textAlign: 'center', padding: '32px 16px',
      color: '#ef4444', fontSize: 14
    }}>
      {error}
    </div>
  );

  if (!data.length) return (
    <div style={{
      textAlign: 'center', padding: '32px 16px',
      color: '#64748b', fontSize: 14
    }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>🛰️</div>
      Нет данных NDVI за выбранный период
    </div>
  );

  // Текущее NDVI (последняя запись)
  const latest = data[data.length - 1];
  const latestColor = getNdviColor(latest.ndvi);

  return (
    <div style={{ color: '#e2e8f0' }}>
      {/* Текущее значение NDVI */}
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', marginBottom: 16
      }}>
        <div>
          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 2 }}>Текущий NDVI</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 28, fontWeight: 700, color: latestColor }}>
              {latest.ndvi.toFixed(3)}
            </span>
            <span style={{
              fontSize: 12, color: latestColor,
              background: latestColor + '22',
              padding: '2px 8px', borderRadius: 12
            }}>
              {getNdviLabel(latest.ndvi)}
            </span>
          </div>
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
            {new Date(latest.date).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}
          </div>
        </div>

        {/* Изменение */}
        {latest.change !== null && latest.change !== undefined && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>Изменение</div>
            <div style={{
              fontSize: 18, fontWeight: 600,
              color: latest.change >= 0 ? '#4ade80' : '#ef4444'
            }}>
              {latest.change >= 0 ? '+' : ''}{latest.change?.toFixed(1)}%
            </div>
          </div>
        )}
      </div>

      {/* Селектор периода */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {[30, 60, 90].map(d => (
          <button
            key={d}
            onClick={() => setDays(d)}
            style={{
              padding: '4px 14px',
              borderRadius: 20,
              border: 'none',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: days === d ? 600 : 400,
              background: days === d ? '#4ade80' : '#1e3520',
              color: days === d ? '#0a1a0a' : '#94a3b8',
              transition: 'all 0.15s'
            }}
          >
            {d} дней
          </button>
        ))}
      </div>

      {/* График */}
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
          <defs>
            <linearGradient id="ndviGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#4ade80" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e3520" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={{ stroke: '#1e3520' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[0, 1]}
            ticks={[0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0]}
            tick={{ fill: '#64748b', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={v => v.toFixed(1)}
          />
          <Tooltip content={<CustomTooltip />} />

          {/* Зональные линии */}
          <ReferenceLine y={0.20} stroke="#ef4444" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.35} stroke="#f97316" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.50} stroke="#eab308" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />
          <ReferenceLine y={0.65} stroke="#84cc16" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.5} />

          <Area
            type="monotone"
            dataKey="ndvi"
            stroke="#4ade80"
            strokeWidth={2}
            fill="url(#ndviGradient)"
            dot={false}
            activeDot={{ r: 4, fill: '#4ade80', stroke: '#0f1b0d', strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>

      {/* Легенда зон */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '6px 12px',
        marginTop: 12, paddingTop: 12,
        borderTop: '1px solid #1e3520'
      }}>
        {[
          { color: '#ef4444', label: 'Критический (<0.2)' },
          { color: '#f97316', label: 'Слабый (0.2–0.35)' },
          { color: '#eab308', label: 'Умеренный (0.35–0.5)' },
          { color: '#22c55e', label: 'Хороший (>0.5)' },
        ].map(z => (
          <div key={z.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <div style={{ width: 24, height: 2, background: z.color, borderRadius: 1 }} />
            <span style={{ fontSize: 11, color: '#64748b' }}>{z.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

---

## TASK 2: WeatherWidget component

### File: `frontend/src/components/Field/WeatherWidget.jsx`

Replace the existing empty stub with a full implementation.

**What it does:**
- Fetches weather from `GET /api/weather/field/{field_id}`
- Shows current conditions + 5-day forecast
- Temperature in Celsius, wind in km/h, precipitation in mm

**API response shape (from Open-Meteo backend):**
```json
{
  "field_id": 42,
  "location": { "lat": 39.77, "lon": 64.43 },
  "current": {
    "temperature": 32.5,
    "feels_like": 35.0,
    "humidity": 28,
    "wind_speed": 12.5,
    "wind_direction": 225,
    "precipitation": 0.0,
    "weather_code": 1,
    "is_day": true
  },
  "forecast": [
    {
      "date": "2025-06-16",
      "temp_max": 37.0,
      "temp_min": 22.0,
      "precipitation": 0.0,
      "wind_speed_max": 18.0,
      "weather_code": 0,
      "sunrise": "05:20",
      "sunset": "20:45"
    }
  ]
}
```

**Weather code → icon + label mapping (WMO codes):**
```javascript
const WEATHER_CODES = {
  0: { icon: '☀️', label: 'Ясно' },
  1: { icon: '🌤️', label: 'Преимущественно ясно' },
  2: { icon: '⛅', label: 'Переменная облачность' },
  3: { icon: '☁️', label: 'Пасмурно' },
  45: { icon: '🌫️', label: 'Туман' },
  48: { icon: '🌫️', label: 'Изморозь' },
  51: { icon: '🌦️', label: 'Морось лёгкая' },
  53: { icon: '🌦️', label: 'Морось умеренная' },
  55: { icon: '🌧️', label: 'Морось сильная' },
  61: { icon: '🌧️', label: 'Дождь лёгкий' },
  63: { icon: '🌧️', label: 'Дождь умеренный' },
  65: { icon: '🌧️', label: 'Дождь сильный' },
  71: { icon: '🌨️', label: 'Снег лёгкий' },
  73: { icon: '🌨️', label: 'Снег умеренный' },
  75: { icon: '❄️', label: 'Снег сильный' },
  80: { icon: '🌦️', label: 'Ливень кратковременный' },
  81: { icon: '🌧️', label: 'Ливень умеренный' },
  82: { icon: '⛈️', label: 'Ливень сильный' },
  95: { icon: '⛈️', label: 'Гроза' },
  96: { icon: '⛈️', label: 'Гроза с градом' },
  99: { icon: '⛈️', label: 'Гроза с сильным градом' },
};

function getWeather(code) {
  return WEATHER_CODES[code] ?? { icon: '🌡️', label: 'Неизвестно' };
}
```

**Component implementation:**

```jsx
import React, { useState, useEffect } from 'react';
import apiClient from '../../api/client';

const WEATHER_CODES = { /* ...see above... */ };
function getWeather(code) { return WEATHER_CODES[code] ?? { icon: '🌡️', label: 'Неизвестно' }; }

// Направление ветра в текст
function windDir(deg) {
  const dirs = ['С', 'СВ', 'В', 'ЮВ', 'Ю', 'ЮЗ', 'З', 'СЗ'];
  return dirs[Math.round(deg / 45) % 8];
}

// Форматировать дату прогноза
function fmtDay(dateStr, idx) {
  if (idx === 0) return 'Сегодня';
  if (idx === 1) return 'Завтра';
  const d = new Date(dateStr);
  return d.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' });
}

function LoadingSkeleton() {
  return (
    <div style={{ padding: '16px 0' }}>
      {[...Array(3)].map((_, i) => (
        <div key={i} style={{
          height: 48, marginBottom: 8, borderRadius: 8,
          background: '#1a2e1a'
        }} />
      ))}
    </div>
  );
}

export default function WeatherWidget({ fieldId }) {
  const [weather, setWeather] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!fieldId) return;
    setLoading(true);
    setError(null);
    apiClient.get(`/api/weather/field/${fieldId}`)
      .then(res => setWeather(res.data))
      .catch(err => {
        console.error('Ошибка загрузки погоды:', err);
        setError('Не удалось загрузить данные погоды');
      })
      .finally(() => setLoading(false));
  }, [fieldId]);

  if (loading) return <LoadingSkeleton />;

  if (error) return (
    <div style={{ textAlign: 'center', padding: '24px 16px', color: '#ef4444', fontSize: 14 }}>
      {error}
    </div>
  );

  if (!weather) return null;

  const { current, forecast } = weather;
  const currentWeather = getWeather(current.weather_code);

  return (
    <div style={{ color: '#e2e8f0' }}>
      {/* Текущая погода */}
      <div style={{
        background: '#132913',
        border: '1px solid #1e3520',
        borderRadius: 12,
        padding: '16px',
        marginBottom: 12
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 42, lineHeight: 1, marginBottom: 4 }}>
              {currentWeather.icon}
            </div>
            <div style={{ fontSize: 13, color: '#94a3b8' }}>{currentWeather.label}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 44, fontWeight: 700, color: '#4ade80', lineHeight: 1 }}>
              {Math.round(current.temperature)}°
            </div>
            <div style={{ fontSize: 12, color: '#64748b' }}>
              Ощущается как {Math.round(current.feels_like)}°
            </div>
          </div>
        </div>

        {/* Детали текущей погоды */}
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
          gap: 8, marginTop: 12, paddingTop: 12,
          borderTop: '1px solid #1e3520'
        }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>💧</div>
            <div style={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>{current.humidity}%</div>
            <div style={{ fontSize: 11, color: '#64748b' }}>Влажность</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>💨</div>
            <div style={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>
              {Math.round(current.wind_speed)} км/ч
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>{windDir(current.wind_direction)}</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 18 }}>🌧️</div>
            <div style={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>
              {current.precipitation?.toFixed(1) ?? 0} мм
            </div>
            <div style={{ fontSize: 11, color: '#64748b' }}>Осадки</div>
          </div>
        </div>
      </div>

      {/* Прогноз на 5 дней */}
      <div>
        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Прогноз на 5 дней
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {(forecast || []).slice(0, 5).map((day, idx) => {
            const w = getWeather(day.weather_code);
            const hasPrecip = day.precipitation > 0.5;
            return (
              <div key={day.date} style={{
                display: 'flex', alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 10px',
                borderRadius: 8,
                background: idx === 0 ? '#132913' : 'transparent',
                border: `1px solid ${idx === 0 ? '#1e3520' : 'transparent'}`,
              }}>
                <div style={{ width: 80, fontSize: 13, color: idx === 0 ? '#e2e8f0' : '#94a3b8' }}>
                  {fmtDay(day.date, idx)}
                </div>
                <div style={{ fontSize: 20, width: 32, textAlign: 'center' }}>{w.icon}</div>
                {hasPrecip ? (
                  <div style={{ fontSize: 12, color: '#60a5fa', width: 50, textAlign: 'center' }}>
                    💧 {day.precipitation.toFixed(1)}мм
                  </div>
                ) : (
                  <div style={{ width: 50 }} />
                )}
                <div style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: '#e2e8f0' }}>
                    {Math.round(day.temp_max)}°
                  </span>
                  <span style={{ fontSize: 13, color: '#64748b', marginLeft: 6 }}>
                    {Math.round(day.temp_min)}°
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Агрономическая подсказка */}
      {weather.forecast && (() => {
        const next3 = weather.forecast.slice(0, 3);
        const totalRain = next3.reduce((s, d) => s + (d.precipitation || 0), 0);
        const maxTemp = Math.max(...next3.map(d => d.temp_max));
        if (totalRain > 15) return (
          <div style={{
            marginTop: 12, padding: '10px 12px',
            background: '#1e3563', border: '1px solid #3b5bdb',
            borderRadius: 8, fontSize: 12, color: '#a5b4fc'
          }}>
            🌧️ Ожидаются обильные осадки ({totalRain.toFixed(0)} мм за 3 дня). Возможен риск переувлажнения и болезней.
          </div>
        );
        if (maxTemp > 40) return (
          <div style={{
            marginTop: 12, padding: '10px 12px',
            background: '#3b1515', border: '1px solid #7f1d1d',
            borderRadius: 8, fontSize: 12, color: '#fca5a5'
          }}>
            🌡️ Экстремальная жара (до {maxTemp}°C). Рекомендуется увеличить норму полива.
          </div>
        );
        return null;
      })()}

      <div style={{ marginTop: 10, fontSize: 11, color: '#374151', textAlign: 'right' }}>
        Данные: Open-Meteo • {new Date().toLocaleDateString('ru-RU')}
      </div>
    </div>
  );
}
```

---

## TASK 3: Verify backend /api/weather/field/{field_id} endpoint

### File: `backend/api/weather.py`

Check that the endpoint `GET /api/weather/field/{field_id}` exists and returns the shape described above.

If it doesn't exist or returns a different shape, add/fix it.

The endpoint must:
1. Look up the field's `centroid_lat` and `centroid_lon` from the `fields` table
2. Call `weather_service.get_weather(lat, lon)` (the service already exists)
3. Return structured JSON with `current` and `forecast` keys

**Expected implementation pattern:**

```python
@router.get("/field/{field_id}")
async def get_weather_for_field(field_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import text
    
    # Получить центроид поля
    result = db.execute(
        text("SELECT centroid_lat, centroid_lon, name FROM fields WHERE id = :id"),
        {"id": field_id}
    ).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Поле {field_id} не найдено")
    
    lat, lon, field_name = result.centroid_lat, result.centroid_lon, result.name
    
    if not lat or not lon:
        raise HTTPException(status_code=422, detail="У поля не заданы координаты центроида")
    
    # Вызвать сервис погоды
    from services.weather import WeatherService
    weather_service = WeatherService()
    weather_data = await weather_service.get_weather(lat, lon)
    
    return {
        "field_id": field_id,
        "field_name": field_name,
        "location": {"lat": lat, "lon": lon},
        **weather_data
    }
```

Check `backend/services/weather.py` to see the actual return format — align the response schema with what the component expects.

---

## TASK 4: Fix FieldDetail tab rendering

### File: `frontend/src/components/Field/FieldDetail.jsx`

Verify that FieldDetail correctly passes `fieldId` (as a number) to both `NDVIChart` and `WeatherWidget`.

Find the tab rendering section and ensure it looks like this:

```jsx
import NDVIChart from './NDVIChart';
import WeatherWidget from './WeatherWidget';

// Inside FieldDetail, where tabs are rendered:
{activeTab === 'ndvi' && (
  <div style={{ padding: '12px 0' }}>
    <NDVIChart fieldId={field.id} />
  </div>
)}

{activeTab === 'weather' && (
  <div style={{ padding: '12px 0' }}>
    <WeatherWidget fieldId={field.id} />
  </div>
)}
```

Make sure `field.id` is a number (not a string). If `field.id` comes as a string from the URL params, convert it: `fieldId={parseInt(field.id)}`.

---

## TASK 5: Quick smoke-test checklist

After implementing, manually verify:

1. Click any field on the map → field detail panel opens
2. Click the "NDVI" tab → chart loads and renders (not blank)
3. The chart shows a green line over time with colored reference lines
4. The "30 дней / 60 дней / 90 дней" buttons filter data
5. Click the "Погода" tab → current weather + 5-day forecast renders
6. No console errors in browser dev tools

If any tab is blank: check browser Network tab for the API call and confirm it returns 200 with data.

---

## Notes for implementation

- Do NOT change Map components, routing, or any other files beyond what's specified
- Keep all UI text in Russian  
- All `console.error` messages can be in Russian or English
- The dark theme colors: background `#0f1b0d`, card `#132913`, border `#1e3520`, text `#e2e8f0`, muted `#64748b`, accent `#4ade80`
- Recharts is already in `package.json` — no new installs needed
- axios is already configured in `frontend/src/api/client.js` with base URL `http://localhost:8000`
