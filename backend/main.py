"""
AgroSat — Sistema intellektualnogo monitoringa polej
Bukhara Agroklaster, Uzbekistan

FastAPI backend — главная точка входа.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings, validate_runtime_security

# Fail-fast: reject insecure configuration before anything else.
validate_runtime_security()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO if settings.environment != "production" else logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifecycle ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте, очистка при остановке."""
    logger.info("🚀 AgroSat запускается...")

    logger.info(f"✅ AgroSat v{settings.app_version} запущен в режиме '{settings.environment}'")

    yield  # Приложение работает

    logger.info("👋 AgroSat останавливается...")


# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgroSat API",
    description="Система интеллектуального мониторинга полей — Бухоро Агрокластер",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    swagger_ui_parameters={
        "persistAuthorization": settings.environment != "production"
    },
)

# CORS — разрешаем фронтенд
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Роутеры ─────────────────────────────────────────────────────────────────

from api.fields import router as fields_router
from api.enterprises import router as enterprises_router
from api.ndvi import router as ndvi_router
from api.alerts import router as alerts_router
from api.dashboard import router as dashboard_router
from api.weather import router as weather_router
from api.ai import router as ai_router
from api.telegram import router as telegram_router
from api.reports import router as reports_router
from api.auth import router as auth_router
from api.satellite_indices import router as satellite_indices_router
from api.satellite_data_quality import router as satellite_data_quality_router
from api.agronomic_risk import router as agronomic_risk_router
from api.agronomic_interpretation import router as agronomic_interpretation_router
from api.ndvi_raster import router as ndvi_raster_router
from api.field_attention import router as field_attention_router

app.include_router(auth_router)
app.include_router(enterprises_router)
app.include_router(fields_router)
app.include_router(ndvi_router)
app.include_router(alerts_router)
app.include_router(dashboard_router)
app.include_router(weather_router)
app.include_router(ai_router)
app.include_router(telegram_router)
app.include_router(reports_router)
app.include_router(satellite_indices_router)
app.include_router(satellite_data_quality_router)
app.include_router(agronomic_risk_router)
app.include_router(agronomic_interpretation_router)
app.include_router(ndvi_raster_router)
app.include_router(field_attention_router)


# ─── Базовые эндпоинты ───────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "AgroSat",
        "version": settings.app_version,
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "docs": "/api/docs",
    }


@app.get("/health")
async def health():
    from database import engine
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status,
        "environment": settings.environment,
    }
