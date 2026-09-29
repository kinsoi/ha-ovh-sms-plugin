"""Tests for the OVH SMS credit sensor."""
from __future__ import annotations

from unittest.mock import MagicMock

import ovh
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_component import async_update_entity

from .conftest import SENSOR_ENTITY, SERVICE_NAME
from custom_components.ovh_sms.const import DOMAIN


async def test_credit_sensor(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Sensor reports credits and becomes unavailable on API errors."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(SENSOR_ENTITY)
    assert state.state == "42"
    assert state.attributes["unit_of_measurement"] == "credits"
    assert state.attributes["status"] == "enable"

    mock_ovh_client.get.side_effect = ovh.exceptions.HTTPError("down")
    await async_update_entity(hass, SENSOR_ENTITY)
    assert hass.states.get(SENSOR_ENTITY).state == STATE_UNAVAILABLE


async def test_out_of_credits_repair_issue(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A repair issue is raised at 0 credits or less and cleared after a top-up."""
    credits = {"value": -12}

    def _get(path: str):
        if path == "/me":
            return {}
        if path == "/sms":
            return [SERVICE_NAME]
        return {"creditsLeft": credits["value"], "status": "enable"}

    mock_ovh_client.get.side_effect = _get
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    issue_id = f"out_of_credits_{SERVICE_NAME}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"service_name": SERVICE_NAME}

    credits["value"] = 100
    await async_update_entity(hass, SENSOR_ENTITY)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_sensor_handles_null_sms_response(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """A null smsResponse from OVH does not break the sensor."""
    mock_ovh_client.get.side_effect = lambda path: (
        [SERVICE_NAME]
        if path == "/sms"
        else {}
        if path == "/me"
        else {"creditsLeft": 5, "status": "enable", "smsResponse": None}
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(SENSOR_ENTITY)
    assert state.state == "5"
    assert state.attributes["sms_response"] == "unknown"
