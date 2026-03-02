"""Config flow for OVH SMS integration."""
from __future__ import annotations

import logging
from typing import Any

import ovh
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

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
    OVH_ENDPOINT,
    STRATEGY_DISABLED,
    STRATEGY_DROP,
    STRATEGY_QUEUE,
)

_LOGGER = logging.getLogger(__name__)

# ── Schemas ───────────────────────────────────

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_APPLICATION_KEY): str,
        vol.Required(CONF_APPLICATION_SECRET): str,
        vol.Required(CONF_CONSUMER_KEY): str,
        vol.Required(CONF_SERVICE_NAME): str,
        vol.Required(CONF_RECIPIENTS): str,
        vol.Optional(CONF_SENDER, default=DEFAULT_SENDER): str,
    }
)

STEP_RATE_LIMIT_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_RATE_LIMIT_STRATEGY, default=DEFAULT_RATE_LIMIT_STRATEGY
        ): SelectSelector(SelectSelectorConfig(
            options=[STRATEGY_DROP, STRATEGY_QUEUE, STRATEGY_DISABLED],
            translation_key="rate_limit_strategy",
        )),
        vol.Optional(
            CONF_RATE_LIMIT_MAX, default=DEFAULT_RATE_LIMIT_MAX
        ): vol.All(int, vol.Range(min=1, max=1000)),
        vol.Optional(
            CONF_RATE_LIMIT_WINDOW, default=DEFAULT_RATE_LIMIT_WINDOW
        ): vol.All(int, vol.Range(min=10, max=86400)),
        vol.Optional(
            CONF_RATE_LIMIT_QUEUE_SIZE, default=DEFAULT_RATE_LIMIT_QUEUE_SIZE
        ): vol.All(int, vol.Range(min=1, max=500)),
    }
)


# ── Helpers ───────────────────────────────────

def parse_recipients(raw: str) -> list[str]:
    """Parse a comma-separated string of phone numbers into a clean list."""
    return [n.strip() for n in raw.split(",") if n.strip()]


# ── Validation ────────────────────────────────

async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate user input by connecting to the OVH API."""
    def _create_client() -> ovh.Client:
        return ovh.Client(
            endpoint=OVH_ENDPOINT,
            application_key=data[CONF_APPLICATION_KEY],
            application_secret=data[CONF_APPLICATION_SECRET],
            consumer_key=data[CONF_CONSUMER_KEY],
        )

    client = await hass.async_add_executor_job(_create_client)

    try:
        me = await hass.async_add_executor_job(client.get, "/me")
    except ovh.exceptions.InvalidKey:
        raise InvalidAuth("Invalid application key or secret")
    except ovh.exceptions.InvalidCredential:
        raise InvalidAuth("Invalid or expired consumer key")
    except ovh.exceptions.APIError as err:
        _LOGGER.error("OVH validate_input: APIError: %s", err)
        raise CannotConnect(f"OVH API error: {err}")
    except Exception as err:
        _LOGGER.exception("OVH validate_input: unexpected error: %s", err)
        raise CannotConnect(f"Connection error: {err}")

    try:
        sms_accounts = await hass.async_add_executor_job(client.get, "/sms")
    except ovh.exceptions.APIError as err:
        raise CannotConnect(f"Unable to list SMS services: {err}")

    if data[CONF_SERVICE_NAME] not in sms_accounts:
        raise ServiceNotFound(
            f"Service '{data[CONF_SERVICE_NAME]}' not found. "
            f"Available: {sms_accounts}"
        )

    account_name = me.get("firstname", "")
    return {"title": f"OVH SMS - {data[CONF_SERVICE_NAME]} ({account_name})"}


# ══════════════════════════════════════════════
# MAIN CONFIG FLOW (initial setup)
# ══════════════════════════════════════════════

class OVHSMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OVH SMS."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}
        self._validation_error: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OVHSMSOptionsFlow:
        """Get the options flow handler."""
        return OVHSMSOptionsFlow(config_entry)

    # ── Step 1: Credentials ───────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: API credentials and service name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_SERVICE_NAME])
            self._abort_if_unique_id_configured()

            try:
                await validate_input(self.hass, user_input)
            except (CannotConnect, InvalidAuth, ServiceNotFound) as err:
                self._validation_error = str(err)
                self._user_data = user_input
                return await self.async_step_validation_failed()
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self._user_data = user_input
                return await self.async_step_rate_limit()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    # ── Step: Validation failed ───────────────

    async def async_step_validation_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show validation error with retry / save anyway / abort options."""
        if user_input is not None:
            choice = user_input.get("action", "abort")

            if choice == "save_anyway":
                self._user_data["config_validated"] = False
                return await self.async_step_rate_limit()
            if choice == "retry":
                return await self.async_step_user()
            return self.async_abort(reason="setup_cancelled")

        return self.async_show_form(
            step_id="validation_failed",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="retry"): SelectSelector(
                        SelectSelectorConfig(
                            options=["retry", "save_anyway", "abort"],
                            translation_key="validation_action",
                        )
                    ),
                }
            ),
            description_placeholders={"error_detail": self._validation_error},
        )

    # ── Step 2: Rate limiting ─────────────────

    async def async_step_rate_limit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Rate limiting configuration."""
        if user_input is not None:
            final_data = {**self._user_data, **user_input}
            final_data.setdefault("config_validated", True)
            final_data[CONF_RECIPIENTS] = parse_recipients(
                final_data.get(CONF_RECIPIENTS, "")
            )

            title = f"OVH SMS - {final_data[CONF_SERVICE_NAME]}"
            if not final_data.get("config_validated"):
                title += " (⚠ unverified)"

            return self.async_create_entry(title=title, data=final_data)

        return self.async_show_form(
            step_id="rate_limit",
            data_schema=STEP_RATE_LIMIT_SCHEMA,
        )

    # ── YAML import ───────────────────────────

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle import from configuration.yaml."""
        await self.async_set_unique_id(import_data[CONF_SERVICE_NAME])
        self._abort_if_unique_id_configured()

        import_data.setdefault(CONF_RATE_LIMIT_STRATEGY, DEFAULT_RATE_LIMIT_STRATEGY)
        import_data.setdefault(CONF_RATE_LIMIT_MAX, DEFAULT_RATE_LIMIT_MAX)
        import_data.setdefault(CONF_RATE_LIMIT_WINDOW, DEFAULT_RATE_LIMIT_WINDOW)
        import_data.setdefault(CONF_RATE_LIMIT_QUEUE_SIZE, DEFAULT_RATE_LIMIT_QUEUE_SIZE)

        try:
            await validate_input(self.hass, import_data)
            import_data["config_validated"] = True
        except (CannotConnect, InvalidAuth, ServiceNotFound) as err:
            _LOGGER.warning(
                "OVH SMS: YAML import validation failed (%s). "
                "Saving anyway — please verify your configuration.",
                err,
            )
            import_data["config_validated"] = False

        title = f"OVH SMS - {import_data[CONF_SERVICE_NAME]}"
        if not import_data.get("config_validated"):
            title += " (⚠ unverified)"

        return self.async_create_entry(title=title, data=import_data)


# ══════════════════════════════════════════════
# OPTIONS FLOW (modify config after install)
# ══════════════════════════════════════════════

class OVHSMSOptionsFlow(OptionsFlow):
    """Handle options for OVH SMS (reconfigure after install)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step: choose what to modify."""
        if user_input is not None:
            section = user_input.get("section", "credentials")
            if section == "credentials":
                return await self.async_step_credentials()
            if section == "test_sms":
                return await self.async_step_test_sms()
            if section == "documentation":
                return await self.async_step_help()
            return await self.async_step_rate_limit()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("section", default="credentials"): SelectSelector(
                        SelectSelectorConfig(
                            options=["credentials", "rate_limit", "test_sms", "documentation"],
                            mode=SelectSelectorMode.LIST,
                            translation_key="init_section",
                        )
                    ),
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Modify API credentials."""
        errors: dict[str, str] = {}
        current = self._config_entry.data

        if user_input is not None:
            # Validate new credentials
            try:
                await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ServiceNotFound:
                errors[CONF_SERVICE_NAME] = "service_not_found"
            except Exception:
                errors["base"] = "unknown"
            else:
                # Merge with existing data, update validated flag
                new_data = {**current, **user_input, "config_validated": True}
                new_data[CONF_RECIPIENTS] = parse_recipients(
                    new_data.get(CONF_RECIPIENTS, "")
                )
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data,
                    title=f"OVH SMS - {user_input[CONF_SERVICE_NAME]}",
                )
                return self.async_create_entry(data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_APPLICATION_KEY,
                    default=current.get(CONF_APPLICATION_KEY, ""),
                ): str,
                vol.Required(
                    CONF_APPLICATION_SECRET,
                    default=current.get(CONF_APPLICATION_SECRET, ""),
                ): str,
                vol.Required(
                    CONF_CONSUMER_KEY,
                    default=current.get(CONF_CONSUMER_KEY, ""),
                ): str,
                vol.Required(
                    CONF_SERVICE_NAME,
                    default=current.get(CONF_SERVICE_NAME, ""),
                ): str,
                vol.Required(
                    CONF_RECIPIENTS,
                    default=", ".join(current.get(CONF_RECIPIENTS, [])),
                ): str,
                vol.Optional(
                    CONF_SENDER,
                    default=current.get(CONF_SENDER, DEFAULT_SENDER),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_rate_limit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Modify rate limiting settings."""
        current = self._config_entry.data

        if user_input is not None:
            new_data = {**current, **user_input}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data,
            )
            return self.async_create_entry(data={})

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RATE_LIMIT_STRATEGY,
                    default=current.get(
                        CONF_RATE_LIMIT_STRATEGY, DEFAULT_RATE_LIMIT_STRATEGY
                    ),
                ): SelectSelector(SelectSelectorConfig(
                    options=[STRATEGY_DROP, STRATEGY_QUEUE, STRATEGY_DISABLED],
                    translation_key="rate_limit_strategy",
                )),
                vol.Optional(
                    CONF_RATE_LIMIT_MAX,
                    default=current.get(CONF_RATE_LIMIT_MAX, DEFAULT_RATE_LIMIT_MAX),
                ): vol.All(int, vol.Range(min=1, max=1000)),
                vol.Optional(
                    CONF_RATE_LIMIT_WINDOW,
                    default=current.get(
                        CONF_RATE_LIMIT_WINDOW, DEFAULT_RATE_LIMIT_WINDOW
                    ),
                ): vol.All(int, vol.Range(min=10, max=86400)),
                vol.Optional(
                    CONF_RATE_LIMIT_QUEUE_SIZE,
                    default=current.get(
                        CONF_RATE_LIMIT_QUEUE_SIZE, DEFAULT_RATE_LIMIT_QUEUE_SIZE
                    ),
                ): vol.All(int, vol.Range(min=1, max=500)),
            }
        )

        return self.async_show_form(
            step_id="rate_limit",
            data_schema=schema,
        )

    async def async_step_help(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show usage documentation."""
        if user_input is not None:
            return await self.async_step_init()

        service_name = self._config_entry.data.get(CONF_SERVICE_NAME, "")
        entity_id = "notify.ovh_sms_" + service_name.lower().replace("-", "_")

        return self.async_show_form(
            step_id="help",
            data_schema=vol.Schema({}),
            description_placeholders={
                "service_name": service_name,
                "entity_id": entity_id,
            },
        )

    async def async_step_test_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send a test SMS to configured recipients."""
        current = self._config_entry.data
        recipients = current.get(CONF_RECIPIENTS, [])
        errors: dict[str, str] = {}

        if not recipients:
            return self.async_show_form(
                step_id="test_sms",
                data_schema=vol.Schema({}),
                errors={"base": "no_recipients"},
            )

        if user_input is not None:
            message = user_input.get("message", "Test SMS from Home Assistant")

            def _send() -> dict:
                def _make_client() -> ovh.Client:
                    return ovh.Client(
                        endpoint=OVH_ENDPOINT,
                        application_key=current[CONF_APPLICATION_KEY],
                        application_secret=current[CONF_APPLICATION_SECRET],
                        consumer_key=current[CONF_CONSUMER_KEY],
                    )
                client = _make_client()
                payload: dict[str, Any] = {
                    "message": message,
                    "receivers": recipients,
                    "noStopClause": True,
                }
                sender = current.get(CONF_SENDER, "")
                if sender:
                    payload["sender"] = sender
                else:
                    payload["senderForResponse"] = True
                return client.post(
                    f"/sms/{current[CONF_SERVICE_NAME]}/jobs", **payload
                )

            try:
                result = await self.hass.async_add_executor_job(_send)
                valid = result.get("validReceivers", [])
                invalid = result.get("invalidReceivers", [])
                _LOGGER.info(
                    "OVH SMS test: sent to %s, invalid: %s", valid, invalid
                )
                notif_msg = f"✅ SMS envoyé à : {', '.join(valid)}"
                if invalid:
                    notif_msg += f"\n❌ Numéros invalides : {', '.join(invalid)}"
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": notif_msg,
                        "title": "OVH SMS — Test",
                        "notification_id": "ovh_sms_test_result",
                    },
                )
                return self.async_create_entry(data={})
            except ovh.exceptions.APIError as err:
                _LOGGER.error("OVH SMS test failed: %s", err)
                errors["base"] = "test_failed"

        return self.async_show_form(
            step_id="test_sms",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "message",
                        default="Test SMS from Home Assistant",
                    ): str,
                }
            ),
            description_placeholders={"recipients": ", ".join(recipients)},
            errors=errors,
        )


# ── Exceptions ────────────────────────────────

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class ServiceNotFound(HomeAssistantError):
    """Error to indicate the SMS service was not found."""
