"""Fixtures for OVH SMS tests."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ovh_sms.const import (
    CONF_APPLICATION_KEY,
    CONF_APPLICATION_SECRET,
    CONF_CONSUMER_KEY,
    CONF_RATE_LIMIT_MAX,
    CONF_RATE_LIMIT_QUEUE_SIZE,
    CONF_RATE_LIMIT_STRATEGY,
    CONF_RATE_LIMIT_WINDOW,
    CONF_RECIPIENTS,
    CONF_SENDER,
    CONF_SERVICE_NAME,
    DOMAIN,
    STRATEGY_DISABLED,
)

SERVICE_NAME = "sms-ab12345-1"
NOTIFY_ENTITY = "notify.ovh_sms_sms_ab12345_1"
SENSOR_ENTITY = "sensor.ovh_sms_credits_sms_ab12345_1"

ENTRY_DATA = {
    CONF_APPLICATION_KEY: "ak",
    CONF_APPLICATION_SECRET: "as",
    CONF_CONSUMER_KEY: "ck",
    CONF_SERVICE_NAME: SERVICE_NAME,
    CONF_RECIPIENTS: ["+33600000001", "+33600000002"],
    CONF_SENDER: "",
    CONF_RATE_LIMIT_STRATEGY: STRATEGY_DISABLED,
    CONF_RATE_LIMIT_MAX: 10,
    CONF_RATE_LIMIT_WINDOW: 60,
    CONF_RATE_LIMIT_QUEUE_SIZE: 50,
    "config_validated": True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    return


def _default_get(path: str):
    if path == "/me":
        return {"firstname": "Jane", "name": "Doe"}
    if path == "/sms":
        return [SERVICE_NAME]
    if path == f"/sms/{SERVICE_NAME}":
        return {
            "creditsLeft": 42,
            "status": "enable",
            "smsResponse": {"responseType": "cgi"},
            "description": "",
        }
    raise AssertionError(f"unexpected GET {path}")


@pytest.fixture
def mock_ovh_client() -> Generator[MagicMock]:
    """Patch ovh.Client everywhere and return the client instance."""
    client = MagicMock()
    client.get.side_effect = _default_get
    client.post.return_value = {
        "totalCreditsRemoved": 1,
        "validReceivers": ["+33600000001"],
        "invalidReceivers": [],
        "ids": [1],
    }
    with patch("ovh.Client", return_value=client):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry with rate limiting disabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"OVH SMS - {SERVICE_NAME}",
        unique_id=SERVICE_NAME,
        data=dict(ENTRY_DATA),
    )
