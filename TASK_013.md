# TASK_013 — Layout fix: страница деталей предприятия

## Skills to load before starting
Read BEFORE writing any code:
- `/mnt/skills/user/full-output-enforcement/SKILL.md`
- `/mnt/skills/user/impeccable/SKILL.md` — затем выполни команду: `/impeccable layout EnterpriseDetailPage`

---

## Проблема

На странице `/enterprises/{id}` (EnterpriseDetailPage) KPI-карточки сверху нечитаемы:
- Текст обрезается ("Крити-", "алерто", "Последн.")
- Карточки слишком узкие — 5-6 штук в одну строку не помещаются
- Значения и подписи не читаются с первого взгляда

---

## Что исправить

### 1. KPI карточки — `EnterpriseDashboard.jsx`

Замени текущую сетку из 5-6 узких карточек в одну строку на **2 ряда по 3 карточки**:

```jsx
// БЫЛО: 5-6 карточек в 1 строку — текст обрезается
// СТАЛО: 2 строки × 3 карточки — всё читаемо

<div style={{
  display: 'grid',
  gridTemplateColumns: 'repeat(3, 1fr)',  // ровно 3 колонки
  gap: 10,
  marginBottom: 20,
}}>
```

Каждая карточка:
```jsx
// Структура карточки:
<div style={{
  background: '#0f1b0d',
  borderRadius: 10,
  padding: '14px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
}}>
  {/* Иконка + большое число */}
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
    <span style={{ fontSize: 20 }}>{icon}</span>
    <span style={{
      fontSize: 26,
      fontWeight: 700,
      color: valueColor,
      lineHeight: 1,
    }}>
      {value}
    </span>
  </div>
  {/* Подпись — всегда в одну строку, без переноса */}
  <div style={{
    fontSize: 12,
    color: '#64748b',
    whiteSpace: 'nowrap',  // запрет переноса!
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  }}>
    {label}
  </div>
</div>
```

Карточки (6 штук, 2 ряда × 3):
| Строка | Карточка 1 | Карточка 2 | Карточка 3 |
|--------|-----------|-----------|-----------|
| 1 | 🌾 Всего полей | 📊 Средний NDVI | ⚠️ Критических |
| 2 | ⏱ Обновлено | 📐 Площадь (га) | ✅ Норма NDVI |

---

### 2. Шапка страницы — `EnterpriseDetailPage.jsx`

Сделай шапку компактнее и читаемее:

```jsx
{/* Шапка */}
<div style={{
  display: 'flex',
  alignItems: 'flex-start',
  justifyContent: 'space-between',
  marginBottom: 16,
  gap: 12,
}}>
  <div>
    {/* Кнопка назад */}
    <button onClick={onBack} style={{
      background: 'none',
      border: 'none',
      color: '#4ade80',
      cursor: 'pointer',
      fontSize: 12,
      padding: 0,
      marginBottom: 6,
      display: 'flex',
      alignItems: 'center',
      gap: 4,
    }}>
      ← Предприятия
    </button>

    {/* Название */}
    <h2 style={{
      fontSize: 20,
      fontWeight: 700,
      color: '#fff',
      margin: 0,
      lineHeight: 1.2,
    }}>
      {enterprise?.name}
    </h2>

    {/* Мета: код, регион, дата */}
    <div style={{
      fontSize: 12,
      color: '#64748b',
      marginTop: 4,
      display: 'flex',
      gap: 8,
      flexWrap: 'wrap',
    }}>
      <span>Код: {enterprise?.code}</span>
      <span>•</span>
      <span>{enterprise?.region}</span>
      <span>•</span>
      <span>Обновлено: {lastUpdated}</span>
    </div>
  </div>

  {/* Кнопка отчёта */}
  <button onClick={() => window.print()} style={{
    background: '#1a2818',
    border: '1px solid #2d4a2d',
    borderRadius: 8,
    padding: '8px 14px',
    color: '#4ade80',
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
    flexShrink: 0,
  }}>
    📄 Скачать отчёт
  </button>
</div>
```

---

### 3. Табы "Поля" / "Рекомендации"

Сделай табы четче — активный таб должен быть очевидно выбран:

```jsx
{/* Tab Bar */}
<div style={{
  display: 'flex',
  gap: 0,
  borderBottom: '1px solid #1e3520',
  marginBottom: 16,
}}>
  {['fields', 'recommendations'].map(tab => (
    <button
      key={tab}
      onClick={() => setActiveTab(tab)}
      style={{
        background: 'none',
        border: 'none',
        borderBottom: activeTab === tab ? '2px solid #4ade80' : '2px solid transparent',
        color: activeTab === tab ? '#4ade80' : '#64748b',
        fontWeight: activeTab === tab ? 600 : 400,
        padding: '8px 16px',
        cursor: 'pointer',
        fontSize: 14,
        transition: 'all 0.15s',
        marginBottom: -1,
      }}
    >
      {tab === 'fields' ? 'Поля' : 'Рекомендации'}
    </button>
  ))}
</div>
```

---

### 4. Таблица полей — `EnterpriseFieldsTable.jsx`

Добавь фиксированную высоту с вертикальным скроллом, чтобы таблица не выходила за экран:

```jsx
{/* Обёртка таблицы */}
<div style={{
  overflowX: 'auto',
  overflowY: 'auto',
  maxHeight: 'calc(100vh - 420px)',  // адаптируется к высоте экрана
  borderRadius: 8,
  border: '1px solid #1e3520',
}}>
  <table style={{
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 13,
  }}>
    <thead style={{
      position: 'sticky',
      top: 0,
      background: '#0f1b0d',
      zIndex: 1,
    }}>
      <tr>
        {/* заголовки колонок */}
      </tr>
    </thead>
    <tbody>
      {/* строки */}
    </tbody>
  </table>
</div>
```

Колонки таблицы — сократи до самых нужных:
| Колонка | Ширина | Выравнивание |
|---------|--------|-------------|
| Код поля | 90px | left |
| Название | auto | left |
| Культура | 120px | left |
| Площадь (га) | 90px | right |
| NDVI | 80px | center |
| Алерты | 60px | center |

Убери колонку "Дата NDVI" — не критично, экономит место.
Убери колонку "Действия" (График) из таблицы — клик по строке открывает модал.

---

### 5. Секция "Рекомендации"

Карточки рекомендаций уже выглядят неплохо, но добавь пагинацию:

```jsx
// Показывать по 10 рекомендаций, кнопка "Показать ещё"
const [visibleCount, setVisibleCount] = useState(10);
const visibleAlerts = alerts.slice(0, visibleCount);

// Внизу списка:
{alerts.length > visibleCount && (
  <button
    onClick={() => setVisibleCount(v => v + 10)}
    style={{
      width: '100%',
      padding: '10px',
      background: '#132913',
      border: '1px solid #1e3520',
      borderRadius: 8,
      color: '#4ade80',
      cursor: 'pointer',
      fontSize: 13,
      marginTop: 8,
    }}
  >
    Показать ещё ({alerts.length - visibleCount} из {alerts.length})
  </button>
)}
```

---

## Итоговый результат

После исправлений страница должна выглядеть так:

```
┌─────────────────────────────────────────────┐
│ ← Предприятия                  [📄 Отчёт]  │
│ Бухара Сервис Агрокластер                   │
│ Код: BAK-08 • Бухарский район • 16.06.2026  │
├──────────────┬──────────────┬───────────────┤
│ 🌾 184       │ 📊 0.323     │ ⚠️ 39         │
│    Всего     │    Ср. NDVI  │    Критических│
├──────────────┼──────────────┼───────────────┤
│ ⏱ 16 июня   │ 📐 42.3 га   │ ✅ 92 в норме │
│    Обновлено │    Ср. площадь│   Без алертов │
├─────────────────────────────────────────────┤
│ [Поля ──] [Рекомендации]                    │
├─────────────────────────────────────────────┤
│ Код   Название           Культура  Га   NDVI│
│ 8691  8691 пахта очик    Хлопок   3.4 -0.23 │
│ 8567  8567 пахта очик    Хлопок   7.9 -0.06 │
│ ...                                    ↕скролл│
└─────────────────────────────────────────────┘
```

---

## Файлы для изменения

1. `frontend/src/pages/EnterpriseDetailPage.jsx` — шапка, табы
2. `frontend/src/components/Enterprise/EnterpriseDashboard.jsx` — KPI карточки (2×3)
3. `frontend/src/components/Enterprise/EnterpriseFieldsTable.jsx` — таблица с скроллом, пагинация
4. `frontend/src/pages/EnterpriseDetailPage.jsx` — Рекомендации: пагинация "показать ещё"

## Важно
- Не трогать другие страницы (Главная, Поля, Алерты)
- Всё на русском языке
- Тёмная тема: фон #0f1b0d, акцент #4ade80
