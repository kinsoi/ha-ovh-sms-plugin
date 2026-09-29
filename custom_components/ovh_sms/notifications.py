"""Translated persistent notifications for the OVH SMS integration."""
from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN


async def async_notify(
    hass: HomeAssistant,
    notification_id: str,
    key: str,
    placeholders: dict[str, str],
) -> None:
    """Create a persistent notification from an `exceptions` translation.

    Notifications are plain text, so the message is resolved here in the
    language configured in Home Assistant (English as fallback).
    """
    message = key
    for language in (hass.config.language, "en"):
        translations = await async_get_translations(
            hass, language, "exceptions", {DOMAIN}
        )
        template = translations.get(f"component.{DOMAIN}.exceptions.{key}.message")
        if template:
            message = template.format(**placeholders)
            break
    persistent_notification.async_create(
        hass,
        message,
        title=f"OVH SMS ({placeholders.get('service_name', '')})",
        notification_id=notification_id,
    )
