from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from config import Settings
import api.telematics as telematics_api


def test_wialon_setting_is_disabled_by_default():
    assert Settings.model_fields["wialon_enabled"].default is False


def test_disabled_wialon_route_fails_before_field_lookup(monkeypatch):
    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("disabled Wialon route must not inspect a field")

    monkeypatch.setattr(
        telematics_api,
        "settings",
        SimpleNamespace(wialon_enabled=False),
    )
    monkeypatch.setattr(
        telematics_api,
        "get_authorized_field_row",
        unexpected_lookup,
    )

    with pytest.raises(HTTPException) as raised:
        telematics_api.get_field_telematics(
            field_id=17,
            hours=24,
            limit=50,
            db=object(),
            current_user=object(),
        )

    assert raised.value.status_code == 404
    assert raised.value.detail == "Not found"
