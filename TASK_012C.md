# TASK_012C — Два критических фикса

## Skills to load before starting
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

---

## Фикс 1: EnterpriseFieldsTable.jsx — onFieldClick is not defined

**Файл:** `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx`

Открой файл и найди строку ~432 где используется `onFieldClick`.

**Проблема:** функция `onFieldClick` используется внутри компонента, но не передаётся как prop и не определена.

**Решение A:** Добавь в деструктуризацию props с дефолтным значением:
```jsx
export default function EnterpriseFieldsTable({ fields, alerts, onFieldClick = () => {} }) {
```

**Решение B:** Если `onFieldClick` не нужен — замени все вызовы `onFieldClick(field)` на `() => {}` или удали их.

Найди ВСЕ места где используется `onFieldClick` в файле и убедись что функция либо передаётся как prop, либо имеет дефолтное значение `() => {}`.

---

## Фикс 2: DashboardPage.jsx — timeout 60000ms

**Файл:** `frontend/src/pages/DashboardPage.jsx`

**Проблема:** Dashboard делает тяжёлые запросы которые таймаутят.

Открой файл и найди строку ~24 где происходит запрос.

Скорее всего там что-то типа `Promise.all([...много запросов...])`.

**Решение:** Замени тяжёлые запросы на лёгкие:

```jsx
useEffect(() => {
  // Только эти два лёгких запроса — не грузить fields/ndvi для всех предприятий
  Promise.all([
    apiClient.get('/api/dashboard/summary'),
    apiClient.get('/api/alerts/?severity=critical&limit=10'),
  ]).then(([summaryRes, alertsRes]) => {
    setSummary(summaryRes.data);
    setAlerts(alertsRes.data?.items || alertsRes.data || []);
  }).catch(err => {
    console.error('Dashboard error:', err.message);
    // Не крашить приложение — показать пустые данные
    setSummary({ total_fields: 0, active_alerts: 0, critical_alerts: 0, avg_ndvi: null });
    setAlerts([]);
  }).finally(() => setLoading(false));
}, []);
```

**Убери** из DashboardPage любые запросы к:
- `/api/fields/` (70 kB данных)
- `/api/ndvi/.../history` (много запросов)
- `/api/enterprises/{id}` в цикле

Dashboard должен использовать ТОЛЬКО:
- `GET /api/dashboard/summary` — сводка по кластеру
- `GET /api/alerts/?severity=critical&limit=10` — топ критических алертов

---

## Проверка

После исправления:

1. Открой http://localhost:5173/ → Главная должна загрузиться без ошибок
2. Открой Предприятия → кликни "Подробнее" → страница деталей должна открыться
3. F12 Console — не должно быть красных ошибок `onFieldClick` или `timeout`

## Важно
- Не трогать другие файлы
- Всё на русском языке
- API базовый URL: `/api/`
