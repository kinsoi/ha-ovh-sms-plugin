"""OVH SMS credit sensor."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import ovh

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OVH SMS credit sensor from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [OVHSMSCreditSensor(hass, entry, entry_data)],
        update_before_add=True,
    )


class OVHSMSCreditSensor(SensorEntity):
    """Sensor showing remaining OVH SMS credits."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "credits"
    _attr_icon = "mdi:message-text-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        entry_data: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        self._hass = hass
        self._client: ovh.Client = entry_data["client"]
        self._service_name: str = entry_data["service_name"]

        self._attr_name = f"OVH SMS Credits ({self._service_name})"
        self._attr_unique_id = f"ovh_sms_credits_{self._service_name}"

        self._entry = entry
        self._extra_attrs: dict[str, Any] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return self._extra_attrs

    async def async_update(self) -> None:
        """Fetch the latest credit info from OVH API."""
        try:
            info = await self._hass.async_add_executor_job(
                self._client.get, f"/sms/{self._service_name}"
            )

            self._attr_native_value = info.get("creditsLeft", 0)
            self._attr_available = True

            self._extra_attrs = {
                "service_name": self._service_name,
                "status": info.get("status"),
                "credits_left": info.get("creditsLeft"),
                "sms_response": info.get("smsResponse", {}).get(
                    "responseType", "unknown"
                ),
                "description": info.get("description", ""),
            }

            _LOGGER.debug(
                "OVH SMS credits updated: %s credit(s) remaining",
                self._attr_native_value,
            )

        except ovh.exceptions.APIError as err:
            _LOGGER.error(
                "OVH SMS: error fetching credit info: %s", err
            )
            self._attr_available = False
