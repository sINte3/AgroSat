# TASK_012B — Фикс роутинга EnterpriseDetailPage

## Skills to load before starting
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Проблема

Кнопка "📊 Подробнее" в EnterprisesPage вызывает `onNavigate('enterprise-detail', enterprise.id)`,
но в App.jsx этот кейс не обработан — вместо страницы деталей открывается страница Полей с фильтром.

## Что нужно сделать

### Шаг 1: Проверь `frontend/src/App.jsx`

Найди функцию `handleNavigate` (или `onNavigate`) — там должен быть switch/if по типу навигации.

Скорее всего выглядит так:
```js
function handleNavigate(page, id) {
  if (page === 'enterprise') {
    setCurrentPage('fields');
    setEnterpriseFilter(id);
  }
  // ... другие кейсы
}
```

### Шаг 2: Добавь кейс `enterprise-detail`

```js
function handleNavigate(page, id) {
  if (page === 'enterprise-detail') {
    setCurrentPage('enterprise-detail');
    setSelectedEnterpriseId(id);
    return;
  }
  if (page === 'enterprise') {
    setCurrentPage('fields');
    setEnterpriseFilter(id);
    return;
  }
  // ... остальные кейсы
}
```

### Шаг 3: Добавь state для selectedEnterpriseId

```js
const [selectedEnterpriseId, setSelectedEnterpriseId] = useState(null);
```

### Шаг 4: Добавь рендер EnterpriseDetailPage

В основном рендере App.jsx найди место где выбирается какую страницу показывать (if/switch по currentPage) и добавь:

```jsx
import EnterpriseDetailPage from './pages/EnterpriseDetailPage';

// В рендере:
{currentPage === 'enterprise-detail' && (
  <EnterpriseDetailPage
    enterpriseId={selectedEnterpriseId}
    onNavigate={handleNavigate}
    onBack={() => {
      setCurrentPage('enterprises');
      setSelectedEnterpriseId(null);
    }}
  />
)}
```

### Шаг 5: Проверь EnterpriseDetailPage.jsx принимает правильные пропсы

Убедись что `frontend/src/pages/EnterpriseDetailPage.jsx` принимает `enterpriseId` (не `id` из URL params, так как приложение использует кастомный роутинг через onNavigate, а не React Router):

```jsx
export default function EnterpriseDetailPage({ enterpriseId, onNavigate, onBack }) {
  const [enterprise, setEnterprise] = useState(null);
  const [fields, setFields] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!enterpriseId) return;

    Promise.all([
      apiClient.get(`/api/enterprises/${enterpriseId}`),
      apiClient.get(`/api/fields/?enterprise_id=${enterpriseId}`),
      apiClient.get(`/api/alerts/?enterprise_id=${enterpriseId}`),
    ]).then(([entRes, fieldsRes, alertsRes]) => {
      setEnterprise(entRes.data);
      setFields(fieldsRes.data?.items || fieldsRes.data || []);
      setAlerts(alertsRes.data?.items || alertsRes.data || []);
    }).catch(err => {
      console.error('EnterpriseDetail error:', err);
    }).finally(() => setLoading(false));
  }, [enterpriseId]);

  // ... рендер
}
```

### Шаг 6: Добавь кнопку "← Назад" в EnterpriseDetailPage

В начале страницы:
```jsx
<button onClick={onBack} style={{
  background: 'transparent',
  border: '1px solid #1e3520',
  borderRadius: 6,
  padding: '6px 14px',
  color: '#4ade80',
  cursor: 'pointer',
  marginBottom: 16,
  fontSize: 13,
}}>
  ← Назад к предприятиям
</button>
```

## Также добавь в Sidebar/меню

Если в Sidebar есть список предприятий (снизу), убедись что клик по ним тоже вызывает `onNavigate('enterprise-detail', id)`:

```jsx
// В Sidebar — список предприятий:
{enterprises.map(e => (
  <div key={e.id} onClick={() => onNavigate('enterprise-detail', e.id)}>
    {e.name}
  </div>
))}
```

## Проверка после исправления

1. Открой http://localhost:5173/enterprises
2. Кликни "📊 Подробнее" на любой карточке
3. Должна открыться страница деталей предприятия с:
   - KPI карточками (кол-во полей, NDVI, алерты)
   - Таблицей полей
   - Кнопкой "← Назад к предприятиям"
4. Кнопка "← Назад" должна возвращать на список предприятий

## Важно
- Приложение использует кастомный роутинг через onNavigate (не React Router)
- Не трогать другие страницы (Поля, Алерты, Главная)
- Всё на русском языке
