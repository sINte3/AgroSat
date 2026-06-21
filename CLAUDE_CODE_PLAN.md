# AgroSat — Мастер-план разработки

> Этот документ — инструкция для Claude Code. При каждой новой сессии
> начинай с чтения этого файла, затем файлов из раздела "Что читать".

## Контекст проекта

**AgroSat** — система интеллектуального мониторинга полей для Бухоро Агрокластера
(30 дочерних предприятий, Узбекистан). Использует спутниковые снимки Sentinel-2,
анализирует NDVI, генерирует агрономические алерты.

**Стек:**
- Backend: FastAPI + PostgreSQL/PostGIS + APScheduler
- Frontend: React + MapLibre GL JS + Recharts + TailwindCSS
- Спутники: Sentinel Hub API (бесплатный тариф)
- Погода: Open-Meteo API (бесплатно, без ключей)
- Деплой: Docker Compose

**Целевые пользователи:** агрономы и руководители агрокластера.
Язык интерфейса: русский (основной), узбекский (вторичный).

---

## Статус реализации

### ✅ Готово (Phase 0 Skeleton)

| Файл | Описание |
|------|----------|
| `docker-compose.yml` | PostgreSQL+PostGIS + Redis |
| `backend/config.py` | Настройки через .env |
| `backend/database.py` | SQLAlchemy + PostGIS init |
| `backend/models/enterprise.py` | Модель предприятия |
| `backend/models/crop.py` | Справочник культур (хлопок, пшеница, рис и др.) |
| `backend/models/field.py` | Поля + CropSeason |
| `backend/models/monitoring.py` | NDVIRecord, Alert, ScoutingNote, User |
| `backend/services/satellite.py` | Sentinel Hub + Mock режим |
| `backend/services/alert_engine.py` | Движок алертов (NDVI аномалии) |
| `backend/services/weather.py` | Open-Meteo погода |
| `backend/scheduler.py` | APScheduler (авто-фетчинг NDVI) |
| `backend/main.py` | FastAPI точка входа |
| `backend/scripts/seed_data.py` | Начальные данные |

### 🔲 Нужно сделать (в порядке приоритета)

---

## Задачи для Claude Code

### ЗАДАЧА 1: API роутеры (backend)

Создай следующие файлы в `backend/api/`:

#### `backend/api/enterprises.py`
```
GET  /api/enterprises/           — список предприятий
GET  /api/enterprises/{id}       — предприятие + его поля
POST /api/enterprises/           — создать
PUT  /api/enterprises/{id}       — редактировать
```

#### `backend/api/fields.py`
```
GET  /api/fields/                     — все поля (с фильтром по enterprise_id)
GET  /api/fields/{id}                 — поле + последнее NDVI + текущий алерты
POST /api/fields/                     — создать (принимает GeoJSON geometry)
PUT  /api/fields/{id}                 — редактировать
POST /api/fields/{id}/season          — добавить/обновить сезон (культуру)
GET  /api/fields/{id}/geojson         — вернуть поле как GeoJSON Feature
GET  /api/fields/geojson/all          — все поля как GeoJSON FeatureCollection (для карты)
```

#### `backend/api/ndvi.py`
```
GET  /api/ndvi/{field_id}/history     — история NDVI (параметр: days=90)
GET  /api/ndvi/{field_id}/latest      — последний снимок
POST /api/ndvi/{field_id}/refresh     — принудительно обновить NDVI сейчас
```

#### `backend/api/alerts.py`
```
GET  /api/alerts/                     — все активные алерты (фильтр: enterprise_id, severity)
GET  /api/alerts/{field_id}           — алерты по полю
PUT  /api/alerts/{id}/acknowledge     — отметить как просмотренный
```

#### `backend/api/dashboard.py`
```
GET  /api/dashboard/summary           — сводка по кластеру:
                                        total_fields, active_alerts, critical_alerts,
                                        avg_ndvi, fields_with_problems, last_updated
GET  /api/dashboard/enterprises/{id}  — сводка по предприятию
```

#### `backend/api/weather.py`
```
GET  /api/weather/field/{field_id}    — погода для конкретного поля
GET  /api/weather/location            — погода по ?lat=&lon=
```

**Важно для всех роутеров:**
- Используй `from database import get_db` и `Depends(get_db)` для сессий
- Возвращай Pydantic schemas (создай `schemas/` папку)
- Обрабатывай 404 через `HTTPException`
- Геометрию возвращай как GeoJSON (используй PostGIS функции ST_AsGeoJSON)

---

### ЗАДАЧА 2: Pydantic схемы

Создай `backend/schemas/`:

```python
# schemas/field.py
class FieldCreate(BaseModel):
    enterprise_id: int
    name: str
    code: Optional[str]
    geometry: dict  # GeoJSON geometry
    area_ha: Optional[float]
    irrigation_type: Optional[str]
    notes: Optional[str]

class FieldResponse(BaseModel):
    id: int
    name: str
    code: Optional[str]
    area_ha: Optional[float]
    enterprise_name: str
    current_ndvi: Optional[float]
    last_ndvi_date: Optional[str]
    active_alerts_count: int
    current_crop: Optional[str]
    geometry: dict  # GeoJSON
    # ... и т.д.
```

---

### ЗАДАЧА 3: Frontend React-приложение

Создай `frontend/` — React + Vite + TailwindCSS + MapLibre GL JS

**Структура:**
```
frontend/
├── package.json
├── vite.config.js
├── index.html
└── src/
    ├── main.jsx
    ├── App.jsx
    ├── api/
    │   └── client.js          ← axios с базовым URL
    ├── components/
    │   ├── Map/
    │   │   ├── FieldMap.jsx   ← главная карта (MapLibre)
    │   │   └── FieldPopup.jsx ← попап поля
    │   ├── Dashboard/
    │   │   ├── SummaryCards.jsx
    │   │   └── AlertsList.jsx
    │   ├── Field/
    │   │   ├── FieldDetail.jsx
    │   │   ├── NDVIChart.jsx  ← Recharts линейный график
    │   │   └── WeatherWidget.jsx
    │   └── Layout/
    │       ├── Sidebar.jsx
    │       └── Header.jsx
    └── pages/
        ├── DashboardPage.jsx
        ├── FieldsPage.jsx
        └── FieldDetailPage.jsx
```

**Дизайн:**
- Тёмная тема (#0f1b0d фон, акцент #4ade80 зелёный)
- Карта занимает 65% экрана
- Справа — панель с деталями выбранного поля
- NDVI colorscale: красный (0.0) → жёлтый (0.3) → зелёный (0.7+)

**Цветовая схема NDVI для карты:**
```javascript
const NDVI_COLORS = [
  [0.0, '#8B0000'],  // тёмно-красный — нет растительности
  [0.2, '#FF4500'],  // оранжевый — скудная
  [0.35, '#FFD700'], // жёлтый — умеренная
  [0.5, '#9ACD32'],  // жёлто-зелёный — хорошая
  [0.65, '#228B22'], // зелёный — отличная
  [0.8, '#006400'],  // тёмно-зелёный — максимальная
];
```

---

### ЗАДАЧА 4: Добавление полей на карту

Реализуй функцию рисования полигонов на карте:

- Кнопка "Добавить поле" → режим рисования
- Клики на карте → строим полигон вершина за вершиной
- Двойной клик → завершаем полигон
- После завершения → форма: название, предприятие, культура, площадь (авторасчёт)
- Сохранение → POST /api/fields/

Используй `@maplibre-gl/maplibre-gl-draw` или реализуй вручную через события map.

---

### ЗАДАЧА 5: Аутентификация

Простая JWT аутентификация:

```
POST /api/auth/login    — email + password → JWT token
GET  /api/auth/me       — текущий пользователь
```

- `python-jose` для JWT
- `passlib[bcrypt]` для хешей паролей
- Bearer token в Authorization header
- React: храни токен в localStorage, добавляй в axios

---

### ЗАДАЧА 6: Страница отчёта предприятия

`/enterprises/{id}/report` — распечатываемый отчёт:
- Сводка по всем полям
- NDVI за последние 30 дней
- Активные алерты
- Рекомендации

Используй `window.print()` для печати, @media print CSS.

---

## Правила кодирования

1. **Геометрия:** всегда EPSG:4326 (WGS84). PostGIS: `ST_AsGeoJSON()`, `ST_GeomFromGeoJSON()`
2. **Даты:** ISO формат (`2024-06-15`), timezone: `Asia/Tashkent`
3. **NDVI:** хранить с 4 знаками после запятой (DECIMAL(5,4))
4. **Язык:** все комментарии и UI на русском
5. **Ошибки:** логировать через `logger = logging.getLogger(__name__)`
6. **Никаких внешних платных API** кроме Sentinel Hub

## Как проверить работу

```bash
# 1. Запустить инфраструктуру
docker-compose up -d db redis

# 2. Установить зависимости
cd backend && pip install -r requirements.txt

# 3. Заполнить БД
python scripts/seed_data.py

# 4. Запустить сервер
uvicorn main:app --reload

# 5. Проверить API
open http://localhost:8000/api/docs

# 6. Запустить фронтенд
cd ../frontend && npm install && npm run dev
```

## Важные технические детали

### PostGIS запросы
```python
# Получить геометрию поля как GeoJSON
from sqlalchemy import text
result = db.execute(
    text("SELECT ST_AsGeoJSON(geometry)::json FROM fields WHERE id = :id"),
    {"id": field_id}
).fetchone()
geojson = result[0]

# Вычислить площадь в гектарах
result = db.execute(
    text("SELECT ST_Area(ST_Transform(geometry, 32639)) / 10000 FROM fields WHERE id = :id"),
    {"id": field_id}
).fetchone()  # EPSG:32639 = UTM Zone 39N (Узбекистан)
area_ha = result[0]
```

### Sentinel Hub без ключей
Система автоматически переключается в Mock-режим если ключи не заданы.
Это позволяет разрабатывать и демонстрировать без регистрации.

### Координаты Бухарской области
- Примерный bbox: lon 63.0–65.5, lat 38.5–40.5
- Центр Бухары: lat=39.7747, lon=64.4286
- Часовой пояс: Asia/Tashkent (UTC+5)
