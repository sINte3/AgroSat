"""
Pydantic схемы для предприятий (Enterprise).
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class EnterpriseBase(BaseModel):
    name: str = Field(..., max_length=255, description="Название на русском")
    name_uz: Optional[str] = Field(None, max_length=255, description="Название на узбекском")
    code: Optional[str] = Field(None, max_length=50, description="Внутренний код")
    region: Optional[str] = Field(None, max_length=100, description="Район")
    contact_person: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    total_area_ha: Optional[float] = Field(None, description="Общая площадь (га)")
    is_active: bool = True
    notes: Optional[str] = None


class EnterpriseCreate(EnterpriseBase):
    """Создание предприятия."""
    pass


class EnterpriseUpdate(EnterpriseBase):
    """Редактирование предприятия. Все поля опциональны."""
    name: Optional[str] = Field(None, max_length=255)


class FieldBrief(BaseModel):
    """Краткая информация о поле для вложенного использования."""
    id: int
    name: str
    code: Optional[str] = None
    area_ha: Optional[float] = None
    is_active: bool
    current_crop: Optional[str] = None
    current_ndvi: Optional[float] = None
    active_alerts_count: int = 0

    class Config:
        from_attributes = True


class EnterpriseResponse(EnterpriseBase):
    """Полный ответ с предприятием."""
    id: int
    created_at: datetime
    updated_at: datetime
    fields_count: int = 0
    fields: list[FieldBrief] = []

    class Config:
        from_attributes = True


class EnterpriseListResponse(BaseModel):
    """Список предприятий."""
    id: int
    name: str
    name_uz: Optional[str] = None
    code: Optional[str] = None
    region: Optional[str] = None
    is_active: bool
    total_area_ha: Optional[float] = None
    fields_count: int = 0
    active_alerts_count: int = 0

    class Config:
        from_attributes = True
