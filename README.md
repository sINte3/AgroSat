# 🌾 AgroSat

**Система интеллектуального мониторинга полей**
Бухоро Агрокластер, Узбекистан

---

## Что это

AgroSat — внутренняя платформа для:
- Спутникового мониторинга полей (NDVI из Sentinel-2)
- Раннего выявления проблем на посевах
- Агрономических рекомендаций на основе AI
- Управления полями 30 дочерних предприятий кластера

## Быстрый старт

### 1. Требования
- Docker + Docker Compose
- Python 3.11+
- Node.js 18+

### 2. Настройка

```bash
# Клонировать / распаковать проект
cd agrosat

# Создать .env файл
cp .env.example .env
# Отредактируйте .env — добавьте Sentinel Hub ключи (опционально)

# Запустить БД
docker-compose up -d db redis
```

### 3. Backend

```bash
cd backend
pip install -r requirements.txt

# Инициализация БД + тестовые данные
python scripts/seed_data.py

# Запуск сервера
uvicorn main:app --reload --port 8000
```

Откройте http://localhost:8000/api/docs — интерактивная документация API.

### 4. Frontend (после создания)

```bash
cd frontend
npm install
npm run dev
```

Откройте http://localhost:5173

## Sentinel Hub (спутниковые данные)

Без ключей система работает в **Mock режиме** — генерирует реалистичные тестовые данные.
Для реальных данных:

1. Зарегистрируйтесь на https://www.sentinel-hub.com/ (бесплатно)
2. Dashboard → User Settings → OAuth Clients → New OAuth Client
3. Добавьте Client ID и Client Secret в `.env`

**Бесплатный план:** 30,000 processing units/месяц
При 100 полях ~50 га: ~5,000 PU/месяц ✅

## Структура проекта

```
agrosat/
├── backend/
│   ├── models/          ← Модели БД (SQLAlchemy + PostGIS)
│   ├── api/             ← REST эндпоинты (FastAPI)
│   ├── services/        ← Бизнес-логика (спутники, алерты, погода)
│   ├── scripts/         ← Утилиты (seed, импорт)
│   ├── main.py          ← Точка входа
│   └── scheduler.py     ← Авто-обновление NDVI
├── frontend/            ← React интерфейс (создаётся в следующем шаге)
├── docker-compose.yml
└── CLAUDE_CODE_PLAN.md  ← Подробный план для продолжения разработки
```

## Культуры в справочнике

| Код | Название | Сезон |
|-----|----------|-------|
| cotton | Хлопок | Апр–Окт |
| wheat | Пшеница озимая | Окт–Июн |
| rice | Рис | Май–Сен |
| maize | Кукуруза | Апр–Сен |
| sunflower | Подсолнечник | Апр–Сен |
| vegetables | Овощи | Мар–Окт |
| alfalfa | Люцерна | Мар–Окт |
| fallow | Пар | — |

## Дорожная карта

- **Phase 0** (текущая): Ядро системы, NDVI мониторинг для кластера
- **Phase 1**: Мобильная версия (PWA), первые внешние клиенты
- **Phase 2**: Масштабирование на все вилояты Узбекистана
- **Phase 3**: Международный рынок

---

*Разработка: Умид, Бухоро Агрокластер*
*Технологии: FastAPI, PostgreSQL/PostGIS, Sentinel-2, React, MapLibre GL*
