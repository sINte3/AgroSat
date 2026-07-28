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
from services.logging_config import configure_logging
from services.metrics import MetricsMiddleware

# Fail-fast: reject insecure configuration before anything else.
validate_runtime_security()

# Настройка логирования
configure_logging(
    level=logging.INFO if settings.environment != "production" else logging.WARNING,
    release_revision=settings.release_revision,
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
app.add_middleware(MetricsMiddleware)

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
from api.field_inspections import router as field_inspections_router
from api.health import router as health_router
from api.operations import router as operations_router
from api.operational_closure import (
    action_router as operational_action_router,
    inspection_router as operational_inspection_router,
    verification_router as operational_verification_router,
)
from api.executive_accountability import router as executive_accountability_router

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
app.include_router(field_inspections_router)
app.include_router(health_router)
app.include_router(operations_router)
app.include_router(operational_inspection_router)
app.include_router(operational_action_router)
app.include_router(operational_verification_router)
app.include_router(executive_accountability_router)


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
