# TASK_012 — Страница Предприятия с Dashboard и таблицей полей

## Skills to load before starting
Read BEFORE writing any code:
- `/mnt/skills/user/full-output-enforcement/SKILL.md` — полный вывод без truncation
- `/mnt/skills/user/interface-design/SKILL.md` — dashboard дизайн
- `/mnt/skills/user/impeccable/SKILL.md` — полировка UI

## Цель

Создать страницу `/enterprises/{id}` с:
1. **Dashboard KPI** — сводка по предприятию (всего полей, средний NDVI, активные алерты, последний обновление)
2. **Таблица полей** — все поля предприятия с NDVI, статусом, культурой
3. **Мини-графики NDVI** — 30-дневная история каждого поля (по клику → развернуть)
4. **Кнопка "Скачать отчёт"** — скачивание HTML-отчёта (print-friendly)
5. **Навигация** — ссылка из EnterprisesPage на детали предприятия

---

## Компоненты для создания

### 1. `frontend/src/pages/EnterpriseDetailPage.jsx`

```jsx
// EnterpriseDetailPage.jsx — главная страница предприятия
// Роут: /enterprises/{id}
// Props: id из URL params

// 1. Загрузить данные предприятия (GET /api/enterprises/{id})
// 2. Загрузить все поля предприятия (GET /api/fields/?enterprise_id={id})
// 3. Загрузить NDVI историю для каждого поля (GET /api/ndvi/{field_id}/history?days=30)
// 4. Загрузить алерты по предприятию (GET /api/alerts/?enterprise_id={id})

// Макет:
// [Header с названием предприятия]
// [KPI Cards Row]
// [Tab Bar: Поля | История | Рекомендации]
// [Таблица полей OR График истории]
// [Кнопка "Скачать отчёт"]

// Loading state: skeleton loaders для каждого компонента
// Error handling: если предприятие не найдено → 404
```

### 2. `frontend/src/components/Enterprise/EnterpriseDashboard.jsx`

```jsx
// Dashboard KPI карточки для предприятия
// Props: enterprise, fields, alerts, ndviData

// Карточки:
// 1. "Всего полей" — count(fields)
// 2. "Средний NDVI" — average(field.current_ndvi) с цветовой индикацией
// 3. "Критических алертов" — count(alerts where severity='critical')
// 4. "Последний апдейт" — max(ndvi_record.captured_date) форматировано
// 5. "Средняя площадь" — average(field.area_ha) + единица "га"

// Дизайн:
// - Карточки 5 в ряд (на большом экране)
// - На мобиле — 2 в ряд или скролл
// - Фон: #1a2818 (тёмно-зелёный)
// - Текст числа: белый, крупный (28px)
// - Иконка слева (🌾, 📊, ⚠️, 🕐, 📐)
// - На hover — лёгкое увеличение (#2a3828)
```

### 3. `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

```jsx
// Таблица всех полей предприятия
// Props: fields, alerts (indexed by field_id)

// Колонки:
// 1. "Код поля" — field.code (левый align)
// 2. "Название" — field.name
// 3. "Культура" — field.current_crop OR "—"
// 4. "Площадь (га)" — field.area_ha.toFixed(1)
// 5. "NDVI" — field.current_ndvi с цветовой ячейкой (NDVI colorscale)
// 6. "Алерты" — count(alerts for this field) + severity badge (красный/жёлтый)
// 7. "Дата NDVI" — field.last_ndvi_date (коротко, "15 июня")
// 8. "Действия" — кнопка "📊 График" (открывает мини-модал с 30-дневной историей)

// Сортировка:
// - По умолчанию: по NDVI (низкие первые — проблемные вверху)
// - Клик на заголовок колонки → сортировка

// Поиск:
// - Input "Поиск по коду или названию"

// Фильтры (выпадающие):
// - По культуре (dropdown)
// - По статусу NDVI (хорошо/средне/плохо)
// - По наличию алертов (все/только с алертами)

// Стиль:
// - Чередующиеся строки: #0f1b0d и #131f0e
// - Hover: #1a2818
// - NDVI ячейка: цветная заливка текста (красный→жёлтый→зелёный) или background
// - Алерты: иконка 🔴 (critical) или 🟡 (warning)
// - Максимум 50 полей на странице (пагинация или virtual scroll)
```

### 4. `frontend/src/components/Enterprise/NDVIHistoryModal.jsx`

```jsx
// Мини-модал с 30-дневной историей NDVI поля
// Props: field, ndviHistory (array of {captured_date, mean_ndvi})
// Triggered by: клик кнопки "📊 График" в таблице

// Содержимое:
// [Закрыть кнопка × вверху справа]
// [Заголовок "NDVI История — {field.name}"]
// [Recharts LineChart]
//   X axis: дата (формат "15 июня")
//   Y axis: NDVI (0.0 до 1.0)
//   Line: зелёная, толщина 3px
//   Area under line: зелёный gradient (прозрачность)
//   Dots: кружочки на каждой точке (hover → tooltip "0.45 от 15 июня")
//
// [Таблица под графиком с данными]
//   Дата | NDVI | Изменение | Статус
//   ...

// На мобиле: развернуть на весь экран
// На десктопе: модал 600x400px, centered
```

### 5. `frontend/src/components/Enterprise/EnterpriseReport.jsx`

```jsx
// HTML-отчёт для печати (Print-friendly)
// Props: enterprise, fields, alerts, ndviData

// Секции:
// 1. [Обложка]
//    - Логотип AgroSat
//    - Название предприятия (крупный заголовок)
//    - Дата отчёта
//    - "Составлено: {date.toLocaleString('ru-RU')}"
//
// 2. [Оглавление]
//    - Структурированное оглавление
//
// 3. [Executive Summary]
//    - KPI: всего полей, средний NDVI, критических алертов
//    - Ключевые выводы
//
// 4. [Детали по полям]
//    - Для каждого поля:
//      * Название + код
//      * Культура, площадь
//      * Текущий NDVI (статус)
//      * График NDVI (SVG на 200x120px)
//      * Активные алерты (если есть)
//      * Рекомендация (текст из alert.recommendation)
//
// 5. [Сводная таблица алертов]
//    - Все критические алерты по предприятию
//    - Поле | Тип алерта | Рекомендация
//
// 6. [Контактная информация]
//    - "Для вопросов: support@agrosat.uz"
//    - QR-код на страницу предприятия в веб-приложении (опционально)

// Стиль печати:
// - @media print CSS с белым фоном, чёрным текстом
// - Разрывы страниц: page-break-after между секциями
// - Без меню, только контент
// - Шрифты: Arial, sans-serif
// - На печать можно вывести через window.print()
```

---

## API Endpoints (уточнения)

Убедись что эти endpoints работают (проверь backend/api/enterprises.py):

```python
# GET /api/enterprises/{id}
# Response:
{
  "id": 2,
  "name": "Бухара Сервис Агрокластер",
  "code": "SVC",
  "region": "Бухара",
  "field_count": 187,
  "total_area_ha": 15234.5
}

# GET /api/fields/?enterprise_id={id}
# Response:
{
  "items": [
    {
      "id": 100,
      "enterprise_id": 2,
      "code": "8563-2",
      "name": "8563-2 пахта томчи 2026",
      "area_ha": 42.5,
      "current_crop": "пахта томчи (хлопок)",
      "current_ndvi": 0.3251,
      "last_ndvi_date": "2026-06-16",
      "irrigation_type": "капельное"
    },
    ...
  ],
  "total": 187
}

# GET /api/ndvi/{field_id}/history?days=30
# Response:
[
  {
    "id": 1500,
    "field_id": 100,
    "captured_date": "2026-05-17",
    "mean_ndvi": 0.1234,
    "min_ndvi": 0.0800,
    "max_ndvi": 0.2100,
    "std_dev": 0.0450,
    "satellite": "Sentinel-2"
  },
  ...
]

# GET /api/alerts/?enterprise_id={id}&severity=critical
# Response:
{
  "items": [
    {
      "id": 50,
      "field_id": 100,
      "alert_type": "NDVI_DROP",
      "severity": "critical",
      "title": "Резкое падение NDVI",
      "description": "NDVI упал с 0.45 до 0.32 за сутки",
      "recommendation": "Проверить наличие вредителей, оценить состояние полива",
      "triggered_value": 0.32,
      "threshold_value": 0.40,
      "created_at": "2026-06-16T08:00:00"
    },
    ...
  ]
}
```

Если endpoint не возвращает что-то — добавь поле в backend/api/enterprises.py.

---

## Роутинг в React

Добавь в `frontend/src/App.jsx`:

```jsx
import EnterpriseDetailPage from './pages/EnterpriseDetailPage';

// В <Routes>:
<Route path="/enterprises/:id" element={<EnterpriseDetailPage />} />
```

И в `frontend/src/pages/EnterprisesPage.jsx` (карточка предприятия):

```jsx
<Link to={`/enterprises/${enterprise.id}`} className="enterprise-card">
  {enterprise.name}
  <ArrowRight size={20} />
</Link>
```

---

## NDVI Colorscale для таблицы

```javascript
const getNDVIColor = (ndvi) => {
  if (ndvi < 0.2) return '#8B0000';      // тёмно-красный
  if (ndvi < 0.35) return '#FF4500';     // оранжевый
  if (ndvi < 0.5) return '#FFD700';      // жёлтый
  if (ndvi < 0.65) return '#9ACD32';     // жёлто-зелёный
  if (ndvi < 0.8) return '#228B22';      // зелёный
  return '#006400';                       // тёмно-зелёный
};

// В таблице:
<td style={{ background: getNDVIColor(field.current_ndvi), color: 'white' }}>
  {field.current_ndvi.toFixed(3)}
</td>
```

---

## Дизайн (тёмная тема)

- **Фон страницы:** `#0f1b0d` (очень тёмный зелёный)
- **Карточки/блоки:** `#131f0e` (чуть светлее)
- **Текст:** `#e8f0e5` (почти белый, слегка зеленоватый)
- **Акцент:** `#4ade80` (яркий зелёный для кнопок и активных элементов)
- **Алерт (critical):** `#ef4444` (красный)
- **Алерт (warning):** `#f59e0b` (оранжевый)
- **Заголовки:** размер 32px, weight 700, цвет `#ffffff`

---

## Чек-лист

- [ ] `EnterpriseDetailPage.jsx` — загрузка данных, управление state
- [ ] `EnterpriseDashboard.jsx` — KPI карточки (5 шт)
- [ ] `EnterpriseFieldsTable.jsx` — таблица с сортировкой и поиском
- [ ] `NDVIHistoryModal.jsx` — мини-модал с графиком Recharts
- [ ] `EnterpriseReport.jsx` — HTML отчёт print-friendly
- [ ] Кнопка "Скачать отчёт" в header (вызывает `window.print()`)
- [ ] Роутинг в App.jsx
- [ ] Ссылка из EnterprisesPage → EnterpriseDetailPage
- [ ] Проверка API endpoints (убедись что все работают)
- [ ] Тестирование на обоих предприятиях (Garden и Servis)
- [ ] Responsive на мобиле (таблица → горизонтальный scroll)

---

## Когда готово

1. Запусти фронтенд: `npm run dev`
2. Открой http://localhost:5173/enterprises
3. Кликни на одно из предприятий
4. Проверь:
   - KPI карточки загружаются
   - Таблица полей видна
   - Клик на "📊 График" → открывает модал с NDVI графиком
   - Кнопка "Скачать отчёт" → `window.print()` работает
   - На мобиле таблица scrollable

Если что-то не работает — скинь скриншот ошибки или код из консоли (F12).

---

## Важно

- **Всё на русском языке**
- **Не используй Docker** — прямой запуск Python и npm
- **API базовый URL:** `/api/` (Vite proxy + axios interceptor)
- **Никаких платных сервисов** — всё открытые данные (Sentinel-2, Open-Meteo)
