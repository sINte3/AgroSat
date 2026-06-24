from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from datetime import datetime
from database import Base


class Field(Base):
    """Поле (участок) агрокластера."""
    __tablename__ = "fields"

    id = Column(Integer, primary_key=True, index=True)
    enterprise_id = Column(Integer, ForeignKey("enterprises.id"), nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)           # Внутренний номер поля

    # Геометрия — полигон в WGS84 (EPSG:4326)
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=False)

    area_ha = Column(Float, nullable=True)              # Площадь (га), вычисляется автоматически
    centroid_lat = Column(Float, nullable=True)         # Центр поля (для запросов погоды)
    centroid_lon = Column(Float, nullable=True)

    # Агрономические параметры
    soil_type = Column(String(100), nullable=True)      # Тип почвы
    irrigation_type = Column(String(50), nullable=True) # Орошение: canal, drip, sprinkler, rainfed
    elevation_m = Column(Float, nullable=True)

    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    enterprise = relationship("Enterprise", back_populates="fields", lazy="raise_on_sql")
    seasons = relationship("CropSeason", back_populates="field", order_by="desc(CropSeason.season_year)", lazy="raise_on_sql")
    ndvi_records = relationship("NDVIRecord", back_populates="field", order_by="desc(NDVIRecord.captured_date)", lazy="raise_on_sql")
    alerts = relationship("Alert", back_populates="field", order_by="desc(Alert.triggered_at)", lazy="raise_on_sql")
    scouting_notes = relationship("ScoutingNote", back_populates="field", lazy="raise_on_sql")

    @property
    def current_season(self):
        """Текущий сезон (текущий год)."""
        from datetime import date
        current_year = date.today().year
        for s in self.seasons:
            if s.season_year == current_year:
                return s
        return None


class CropSeason(Base):
    """Сезон посева: что посеяно на поле в данном году."""
    __tablename__ = "crop_seasons"

    id = Column(Integer, primary_key=True, index=True)
    field_id = Column(Integer, ForeignKey("fields.id"), nullable=False)
    crop_type_id = Column(Integer, ForeignKey("crop_types.id"), nullable=False)

    season_year = Column(Integer, nullable=False)
    variety = Column(String(100), nullable=True)       # Сорт
    planting_date = Column(DateTime, nullable=True)
    expected_harvest_date = Column(DateTime, nullable=True)
    actual_harvest_date = Column(DateTime, nullable=True)

    planned_yield_tha = Column(Float, nullable=True)   # Плановая урожайность (т/га)
    actual_yield_tha = Column(Float, nullable=True)    # Фактическая урожайность

    # Нормы внесения (для будущей VRA функциональности)
    fertilizer_plan = Column(JSON, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    field = relationship("Field", back_populates="seasons", lazy="raise_on_sql")
    crop_type = relationship("CropType", back_populates="seasons", lazy="raise_on_sql")
