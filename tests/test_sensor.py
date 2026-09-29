"""Tests for the OVH SMS credit sensor."""
from __future__ import annotations

from unittest.mock import MagicMock

import ovh
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import async_update_entity

from .conftest import SENSOR_ENTITY


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
