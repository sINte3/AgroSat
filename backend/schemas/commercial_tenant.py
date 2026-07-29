"""Validated commercial tenant administration contracts."""

from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,254}$")


class SubscriptionState(str, Enum):
    unsupported = "unsupported"
    trial = "trial"
    active = "active"
    suspended = "suspended"
    terminated = "terminated"


class CommercialProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str = Field(min_length=2, max_length=64)
    subscription_state: SubscriptionState
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    quota_limits: dict[str, int] = Field(default_factory=dict)
    retention_policy: dict[str, int] = Field(default_factory=dict)
    branding: dict[str, str] = Field(default_factory=dict)

    @field_validator("plan_code")
    @classmethod
    def normalize_plan(cls, value):
        value = value.strip().lower()
        if not CODE.fullmatch(value):
            raise ValueError("invalid plan code")
        return value

    @field_validator("feature_flags")
    @classmethod
    def validate_flags(cls, value):
        if len(value) > 100 or any(not CODE.fullmatch(key) for key in value):
            raise ValueError("invalid feature flags")
        return dict(sorted(value.items()))

    @field_validator("quota_limits", "retention_policy")
    @classmethod
    def validate_nonnegative_limits(cls, value):
        if len(value) > 100:
            raise ValueError("too many policy keys")
        for key, number in value.items():
            if not CODE.fullmatch(key) or isinstance(number, bool) or number < 0:
                raise ValueError("invalid policy limit")
        return dict(sorted(value.items()))

    @field_validator("branding")
    @classmethod
    def validate_branding(cls, value):
        allowed = {"display_name", "logo_asset_id", "primary_color"}
        if set(value) - allowed:
            raise ValueError("unsupported branding key")
        normalized = {key: item.strip() for key, item in value.items()}
        if any(not item or len(item) > 255 for item in normalized.values()):
            raise ValueError("invalid branding value")
        return normalized


class ProviderCredentialReferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_reference: str = Field(min_length=3, max_length=255)
    status: str = Field(pattern=r"^(configured|disabled)$")

    @field_validator("secret_reference")
    @classmethod
    def validate_reference(cls, value):
        value = value.strip()
        if not REFERENCE.fullmatch(value):
            raise ValueError("invalid opaque secret reference")
        return value


class LifecycleRequestType(str, Enum):
    export = "export"
    deletion = "deletion"


class LifecycleRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: LifecycleRequestType
    reason: str = Field(min_length=10, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value):
        return value.strip()


class LifecycleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(approved|rejected)$")
    confirm: bool
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def require_confirmation(self):
        if self.confirm is not True:
            raise ValueError("explicit confirmation is required")
        return self
