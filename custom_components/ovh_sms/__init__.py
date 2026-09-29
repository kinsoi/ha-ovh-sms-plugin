"""The OVH SMS integration."""
from __future__ import annotations

import logging
import re

import ovh
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import create_client, is_auth_error
from .const import (
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
    DEFAULT_RATE_LIMIT_MAX,
    DEFAULT_RATE_LIMIT_QUEUE_SIZE,
    DEFAULT_RATE_LIMIT_STRATEGY,
    DEFAULT_RATE_LIMIT_WINDOW,
    DEFAULT_SENDER,
    DOMAIN,
    SERVICE_NAME_PATTERN,
    STRATEGY_DISABLED,
    STRATEGY_DROP,
    STRATEGY_QUEUE,
)
from .issues import async_update_out_of_credits_issue

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NOTIFY]

# YAML schema (configuration.yaml)
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_APPLICATION_KEY): cv.string,
                vol.Required(CONF_APPLICATION_SECRET): cv.string,
                vol.Required(CONF_CONSUMER_KEY): cv.string,
                vol.Required(CONF_SERVICE_NAME): vol.All(
                    cv.string, vol.Match(SERVICE_NAME_PATTERN)
                ),
                vol.Optional(CONF_RECIPIENTS, default=[]): vol.All(
                    cv.ensure_list_csv, [cv.string]
                ),
                vol.Optional(CONF_SENDER, default=DEFAULT_SENDER): cv.string,
                vol.Optional(
                    CONF_RATE_LIMIT_STRATEGY, default=DEFAULT_RATE_LIMIT_STRATEGY
                ): vol.In([STRATEGY_DROP, STRATEGY_QUEUE, STRATEGY_DISABLED]),
                vol.Optional(
                    CONF_RATE_LIMIT_MAX, default=DEFAULT_RATE_LIMIT_MAX
                ): cv.positive_int,
                vol.Optional(
                    CONF_RATE_LIMIT_WINDOW, default=DEFAULT_RATE_LIMIT_WINDOW
                ): cv.positive_int,
                vol.Optional(
                    CONF_RATE_LIMIT_QUEUE_SIZE, default=DEFAULT_RATE_LIMIT_QUEUE_SIZE
                ): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


def _parse_recipients(value: list | str) -> list[str]:
    """Normalize and E.164-validate recipients (list or legacy comma-separated string)."""
    if isinstance(value, list):
        numbers = [r.strip() for r in value if str(r).strip()]
    else:
        numbers = [r.strip() for r in str(value).split(",") if r.strip()]
    return [n for n in numbers if _E164_RE.match(n)]


# ──────────────────────────────────────────────
# YAML mode: detect config and create a config entry via import
# ──────────────────────────────────────────────
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up OVH SMS from configuration.yaml (YAML import)."""
    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        _LOGGER.info(
            "OVH SMS: YAML configuration detected, importing as config entry"
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=config[DOMAIN],
            )
        )

    return True


# ──────────────────────────────────────────────
# Config entry mode (UI or imported from YAML)
# ──────────────────────────────────────────────
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OVH SMS from a config entry."""
    conf = entry.data

    client = await hass.async_add_executor_job(
        create_client,
        conf[CONF_APPLICATION_KEY],
        conf[CONF_APPLICATION_SECRET],
        conf[CONF_CONSUMER_KEY],
    )

    # Check if config was saved with validation skipped
    config_valid = conf.get("config_validated", True)

    if config_valid:
        # Verify API connection and that the SMS service exists
        try:
            await hass.async_add_executor_job(client.get, "/me")
            sms_accounts = await hass.async_add_executor_job(client.get, "/sms")
        except ovh.exceptions.APIError as err:
            _LOGGER.debug("OVH SMS: API error detail: %s", err)
            if is_auth_error(err):
                raise ConfigEntryError(
                    translation_domain=DOMAIN, translation_key="auth_failed"
                ) from err
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from err

        if conf[CONF_SERVICE_NAME] not in sms_accounts:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="service_not_found",
                translation_placeholders={"service_name": conf[CONF_SERVICE_NAME]},
            )
    else:
        _LOGGER.warning(
            "OVH SMS: configuration was saved without validation. "
            "SMS sending may fail — please verify your credentials "
            "in Settings → Devices & Services → OVH SMS → Configure."
        )

    # Store data for platforms
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "service_name": conf[CONF_SERVICE_NAME],
        "recipients": _parse_recipients(conf.get(CONF_RECIPIENTS, [])),
        "sender": conf.get(CONF_SENDER, DEFAULT_SENDER),
        "rate_limit_strategy": conf.get(
            CONF_RATE_LIMIT_STRATEGY, DEFAULT_RATE_LIMIT_STRATEGY
        ),
        "rate_limit_max": conf.get(CONF_RATE_LIMIT_MAX, DEFAULT_RATE_LIMIT_MAX),
        "rate_limit_window": conf.get(
            CONF_RATE_LIMIT_WINDOW, DEFAULT_RATE_LIMIT_WINDOW
        ),
        "rate_limit_queue_size": conf.get(
            CONF_RATE_LIMIT_QUEUE_SIZE, DEFAULT_RATE_LIMIT_QUEUE_SIZE
        ),
    }

    # Load platforms via config entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up repair issues when the entry is deleted."""
    async_update_out_of_credits_issue(hass, entry.data[CONF_SERVICE_NAME], False)
