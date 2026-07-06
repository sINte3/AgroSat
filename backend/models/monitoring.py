from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, Date, Enum, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from database import Base


# ─── NDVI ────────────────────────────────────────────────────────────────────

class NDVIRecord(Base):
    """Снимок NDVI поля с конкретной даты."""
    __tablename__ = "ndvi_records"
    __table_args__ = (
        UniqueConstraint("field_id", "captured_date",
                         name="uq_ndvi_records_field_captured_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("fields.id"), nullable=False)

    captured_date = Column(Date, nullable=False)        # Дата снимка
    processed_at = Column(DateTime, default=datetime.utcnow)

    # NDVI статистика по полю
    mean_ndvi = Column(Float, nullable=True)            # Среднее NDVI по полю
    min_ndvi = Column(Float, nullable=True)
    max_ndvi = Column(Float, nullable=True)
    std_ndvi = Column(Float, nullable=True)             # Стандартное отклонение (неоднородность)
    p10_ndvi = Column(Float, nullable=True)             # 10-й перцентиль
    p90_ndvi = Column(Float, nullable=True)             # 90-й перцентиль

    cloud_cover_pct = Column(Float, nullable=True)      # % облачности
    valid_pixels_pct = Column(Float, nullable=True)     # % пикселей без облаков

    satellite = Column(String(50), default="Sentinel-2")
    image_url = Column(Text, nullable=True)             # URL превью

    # Отклонение от предыдущего снимка
    ndvi_change = Column(Float, nullable=True)          # mean_ndvi - предыдущий mean_ndvi
    ndvi_change_pct = Column(Float, nullable=True)      # изменение в %

    # Relationships
    field = relationship("Field", back_populates="ndvi_records", lazy="raise_on_sql")


# ─── ALERTS ──────────────────────────────────────────────────────────────────

class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    NDVI_DROP = "ndvi_drop"             # Резкое падение NDVI
    NDVI_LOW = "ndvi_low"               # NDVI ниже нормы для данной фазы
    NDVI_HIGH = "ndvi_high"             # NDVI выше нормы (редко — хорошо, но стоит проверить)
    NDVI_UNEVEN = "ndvi_uneven"         # Сильная неоднородность по полю
    DROUGHT_RISK = "drought_risk"       # Риск засухи (высокая температура + низкие осадки)
    FROST_RISK = "frost_risk"           # Риск заморозков
    HEAVY_RAIN = "heavy_rain"           # Сильные осадки (риск заболачивания)
    NO_DATA = "no_data"                 # Нет данных >14 дней (плотная облачность)
    MANUAL = "manual"                   # Ручная заметка агронома


class Alert(Base):
    """Алерт по полю."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("fields.id"), nullable=False)
    ndvi_record_id = Column(Integer, ForeignKey("ndvi_records.id"), nullable=True)

    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False, default=AlertSeverity.WARNING)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=True)       # Рекомендованное действие

    # Данные, вызвавшие алерт
    triggered_value = Column(Float, nullable=True)     # Значение, вызвавшее алерт
    threshold_value = Column(Float, nullable=True)     # Порог

    triggered_at = Column(DateTime, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_active = Column(Boolean, default=True)

    # Idempotency metadata for satellite-generated alerts (TASK_138)
    source = Column(String(64), nullable=True)
    source_key = Column(String(64), nullable=True)

    # Relationships
    field = relationship("Field", back_populates="alerts", lazy="raise_on_sql")


# ─── SATELLITE INDEX RECORDS (multi-index) ────────────────────────────────────

class SatelliteIndexRecord(Base):
    """Multi-index satellite vegetation record (NDVI, SAVI, EVI, NDMI, NDRE)."""
    __tablename__ = "satellite_index_records"
    __table_args__ = (
        UniqueConstraint("field_id", "captured_date", "index_code",
                          name="uq_satellite_index_records_field_date_code"),
        Index("ix_satellite_index_records_field_code_date",
              "field_id", "index_code", "captured_date"),
        Index("ix_satellite_index_records_captured_date", "captured_date"),
        Index("ix_satellite_index_records_index_code", "index_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("fields.id"), nullable=False)

    captured_date = Column(Date, nullable=False)
    index_code = Column(String(20), nullable=False)           # ndvi, savi, evi, ndmi, ndre

    mean_value = Column(Float, nullable=True)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)
    std_value = Column(Float, nullable=True)
    p10_value = Column(Float, nullable=True)
    p90_value = Column(Float, nullable=True)

    valid_pixels_pct = Column(Float, nullable=True)
    cloud_cover_pct = Column(Float, nullable=True)
    satellite = Column(String(50), default="Sentinel-2")

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    field = relationship("Field", back_populates="satellite_index_records", lazy="raise_on_sql")


# ─── SCOUTING ────────────────────────────────────────────────────────────────

class ScoutingNote(Base):
    """Заметка с выезда на поле (разведка)."""
    __tablename__ = "scouting_notes"

    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("fields.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    content = Column(Text, nullable=False)
    problem_type = Column(String(100), nullable=True)  # болезнь, вредитель, засуха, прочее
    severity_estimate = Column(String(20), nullable=True)  # low, medium, high

    # Геолокация точки на поле
    point_lat = Column(Float, nullable=True)
    point_lon = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    field = relationship("Field", back_populates="scouting_notes", lazy="raise_on_sql")


# ─── USERS ───────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    AGRONOMIST = "agronomist"
    MANAGER = "manager"
    VIEWER = "viewer"


class User(Base):
    """Пользователь системы."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    enterprise_id = Column(Integer, ForeignKey("enterprises.id"), nullable=True)

    email = Column(String(255), unique=True, nullable=False)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    role = Column(String(50), default=UserRole.VIEWER)
    hashed_password = Column(String(255), nullable=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    # Relationships
    enterprise = relationship("Enterprise", back_populates="users", lazy="raise_on_sql")
