from typing import Optional
from pydantic import BaseModel, Field, field_validator


class UserRegister(BaseModel):
    email: str
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None
    phone: Optional[str] = None
    enterprise_id: Optional[int] = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v):
        return v.strip().lower()


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    user_id: Optional[int] = None
    email: Optional[str] = None
    role: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: str
    enterprise_id: Optional[int] = None
    is_active: bool

    class Config:
        from_attributes = True
