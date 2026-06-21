# TASK_012_FIX — Исправление производительности страниц предприятий

## Skills to load before starting
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Проблема

1. `EnterprisesPage.jsx` делает отдельный запрос `fields/` для КАЖДОГО предприятия → 8+ тяжёлых запросов по 70 kB одновременно
2. `EnterpriseDetailPage.jsx` загружает NDVI историю для ВСЕХ полей сразу → 187 параллельных запросов → timeout

## Исправление 1: `frontend/src/pages/EnterprisesPage.jsx`

**Убери** загрузку полей внутри страницы списка предприятий.
Карточка предприятия должна показывать только данные из `/api/dashboard/enterprises/{id}` (или из самого объекта enterprise — там уже есть `field_count`).

**Было (неправильно):**
```js
// Не делать этого — загрузка fields для каждого предприятия на странице списка
const fields = await api.get(`/api/fields/?enterprise_id=${enterprise.id}`)
```

**Должно быть:**
```js
// Загружать только список предприятий — один запрос
const enterprises = await api.get('/api/enterprises/')
// Всё. Поля НЕ грузить на этой странице.
// field_count, avg_ndvi и active_alerts_count уже есть в ответе enterprises
```

Карточка предприятия показывает:
- `enterprise.name`
- `enterprise.field_count` (уже есть в API)
- `enterprise.total_area_ha` (уже есть в API)
- Кнопка "Подробнее" → ссылка на `/enterprises/{id}`

Если `field_count` нет в ответе `/api/enterprises/` — добавь его в `backend/api/enterprises.py`:

```python
# В GET /api/enterprises/ добавить field_count через subquery
from sqlalchemy import func

enterprises = db.query(
    Enterprise,
    func.count(Field.id).label('field_count')
).outerjoin(Field, Field.enterprise_id == Enterprise.id)\
 .group_by(Enterprise.id)\
 .all()
```

---

## Исправление 2: `frontend/src/pages/EnterpriseDetailPage.jsx`

**Убери** массовую загрузку NDVI истории для всех полей при открытии страницы.

**Было (неправильно):**
```js
// Загружать NDVI для КАЖДОГО поля при загрузке страницы — убийца производительности
const ndviPromises = fields.map(f => api.get(`/api/ndvi/${f.id}/history?days=30`))
const allNdvi = await Promise.all(ndviPromises)  // 187 параллельных запросов!
```

**Должно быть:**
```js
// На странице детали предприятия загружать только:
// 1. Данные предприятия (1 запрос)
const enterprise = await api.get(`/api/enterprises/${id}`)

// 2. Поля предприятия (1 запрос)
const fields = await api.get(`/api/fields/?enterprise_id=${id}`)

// 3. Алерты предприятия (1 запрос)
const alerts = await api.get(`/api/alerts/?enterprise_id=${id}`)

// NDVI историю НЕ грузить здесь — грузить только по клику на "📊 График"
```

---

## Исправление 3: `frontend/src/components/Enterprise/NDVIHistoryModal.jsx`

NDVI история должна загружаться **только когда пользователь открывает модал** для конкретного поля.

```jsx
const NDVIHistoryModal = ({ field, onClose }) => {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Загружать при открытии модала — только для одного поля
    api.get(`/api/ndvi/${field.id}/history?days=30`)
      .then(res => setHistory(res.data))
      .finally(() => setLoading(false))
  }, [field.id])

  // Показывать spinner пока грузится
  if (loading) return <div className="modal-loading">Загрузка...</div>

  return (
    // ... график Recharts с history данными
  )
}
```

---

## Исправление 4: `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

Кнопка "📊 График" должна открывать модал и передавать только `field` объект (без pre-loaded ndvi):

```jsx
const [selectedField, setSelectedField] = useState(null)

// В таблице:
<button onClick={() => setSelectedField(field)}>📊 График</button>

// Рядом с таблицей:
{selectedField && (
  <NDVIHistoryModal
    field={selectedField}
    onClose={() => setSelectedField(null)}
  />
)}
```

---

## Итог: сколько запросов должно быть

| Страница | Было | Должно быть |
|----------|------|-------------|
| `/enterprises` (список) | 8+ запросов по 70 kB | **1 запрос** |
| `/enterprises/{id}` (детали) | 3 + 187 запросов | **3 запроса** |
| Клик "📊 График" | уже загружено | **1 запрос** |

---

## Проверка после исправления

1. Открой http://localhost:5173/enterprises
2. DevTools → Network tab
3. Убедись что на странице списка только **1-2 запроса** (не 40+)
4. Кликни на предприятие → страница детали открывается за **< 2 секунды**
5. Кликни "📊 График" на любом поле → модал появляется через 1-2 сек

## Важно
- Всё на русском языке
- Не использовать Docker
- API базовый URL: `/api/`
