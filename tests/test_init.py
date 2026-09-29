"""Tests for OVH SMS setup and unload."""
from __future__ import annotations

from unittest.mock import MagicMock

import ovh
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import NOTIFY_ENTITY, SENSOR_ENTITY, SERVICE_NAME
from custom_components.ovh_sms.const import CONF_RECIPIENTS, DOMAIN


async def test_setup_and_unload(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Entry loads, creates both entities and unloads cleanly."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(NOTIFY_ENTITY) is not None
    assert hass.states.get(SENSOR_ENTITY).state == "42"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


def _generic_403() -> ovh.exceptions.APIError:
    """What the real OVH API returns on the first call with a bad key."""
    return ovh.exceptions.APIError(
        "This application key is invalid", response=MagicMock(status_code=403)
    )


@pytest.mark.parametrize(
    "exc",
    [
        ovh.exceptions.InvalidKey("bad"),
        ovh.exceptions.InvalidCredential("bad"),
        ovh.exceptions.NotGrantedCall("no rights"),
        _generic_403(),
    ],
)
async def test_setup_auth_error_is_permanent(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    config_entry: MockConfigEntry,
    exc: Exception,
) -> None:
    """Invalid credentials fail the setup without retrying."""
    mock_ovh_client.get.side_effect = exc
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.parametrize(
    "exc",
    [
        ovh.exceptions.HTTPError("network down"),
        ovh.exceptions.InvalidResponse("bad"),
        ovh.exceptions.APIError("server error", response=MagicMock(status_code=503)),
    ],
)
async def test_setup_transient_error_retries(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    config_entry: MockConfigEntry,
    exc: Exception,
) -> None:
    """Network / API outages at startup schedule a retry."""
    mock_ovh_client.get.side_effect = exc
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_unknown_service(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A service name absent from the account fails the setup."""
    mock_ovh_client.get.side_effect = lambda path: (
        {"firstname": "J"} if path == "/me" else ["sms-other-1"]
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_yaml_import_with_recipients(
    hass: HomeAssistant, mock_ovh_client: MagicMock
) -> None:
    """YAML config (including recipients) is imported as a config entry."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "application_key": "ak",
                "application_secret": "as",
                "consumer_key": "ck",
                "service_name": SERVICE_NAME,
                "recipients": ["+33600000001", "+33600000002"],
            }
        },
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data[CONF_RECIPIENTS] == ["+33600000001", "+33600000002"]
    assert entries[0].state is ConfigEntryState.LOADED
