from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Enterprise(Base):
    """Дочернее предприятие агрокластера."""
    __tablename__ = "enterprises"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)           # Название на русском
    name_uz = Column(String(255), nullable=True)         # Название на узбекском
    code = Column(String(50), unique=True, nullable=True) # Внутренний код
    region = Column(String(100), nullable=True)           # Район
    contact_person = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)
    total_area_ha = Column(Float, nullable=True)          # Общая площадь (га)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    fields = relationship("Field", back_populates="enterprise")
    users = relationship("User", back_populates="enterprise")
