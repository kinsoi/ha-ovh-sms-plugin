"""OVH SMS notify entity with configurable rate limiting."""
from __future__ import annotations

import asyncio
from collections import deque
from functools import partial
import logging
import re
import time
from typing import Any

import ovh
import voluptuous as vol

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import is_out_of_credits
from .const import (
    ATTR_CODING,
    ATTR_NO_STOP_CLAUSE,
    ATTR_PRIORITY,
    ATTR_RECIPIENTS,
    ATTR_SENDER,
    CODINGS,
    DEFAULT_RATE_LIMIT_MAX,
    DEFAULT_RATE_LIMIT_QUEUE_SIZE,
    DEFAULT_RATE_LIMIT_STRATEGY,
    DEFAULT_RATE_LIMIT_WINDOW,
    DOMAIN,
    PRIORITIES,
    SERVICE_SEND_SMS,
    STRATEGY_DISABLED,
    STRATEGY_DROP,
    STRATEGY_QUEUE,
)

_LOGGER = logging.getLogger(__name__)
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_E164_NUMBER = vol.All(
    cv.string, vol.Match(_E164_RE, msg="must be in E.164 format, e.g. +33612345678")
)

SEND_SMS_SCHEMA: dict[vol.Marker, Any] = {
    vol.Required("message"): cv.string,
    vol.Optional(ATTR_RECIPIENTS): vol.All(cv.ensure_list, [_E164_NUMBER]),
    vol.Optional(ATTR_SENDER): vol.All(cv.string, vol.Length(min=1, max=11)),
    vol.Optional(ATTR_PRIORITY): vol.In(PRIORITIES),
    vol.Optional(ATTR_CODING): vol.In(CODINGS),
    vol.Optional(ATTR_NO_STOP_CLAUSE): cv.boolean,
}


# ──────────────────────────────────────────────
# Platform setup
# ──────────────────────────────────────────────
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OVH SMS notify entity from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OVHSMSNotifyEntity(hass, entry, entry_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SEND_SMS, SEND_SMS_SCHEMA, "async_send_sms"
    )


# ──────────────────────────────────────────────
# Sliding window rate limiter
# ──────────────────────────────────────────────
class SMSRateLimiter:
    """Sliding window rate limiter for SMS sending."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._timestamps: deque[float] = deque()

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._window
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def acquire(self) -> bool:
        self._evict()
        if len(self._timestamps) >= self._max_calls:
            return False
        self._timestamps.append(time.monotonic())
        return True

    @property
    def remaining(self) -> int:
        self._evict()
        return max(0, self._max_calls - len(self._timestamps))

    @property
    def seconds_until_available(self) -> float:
        self._evict()
        if len(self._timestamps) < self._max_calls:
            return 0.0
        return max(0.0, self._timestamps[0] + self._window - time.monotonic())


# ──────────────────────────────────────────────
# Queued message container
# ──────────────────────────────────────────────
class QueuedMessage:
    __slots__ = ("data", "message", "queued_at", "targets")

    def __init__(self, message: str, targets: list[str], data: dict[str, Any]) -> None:
        self.message = message
        self.targets = targets
        self.data = data
        self.queued_at = time.monotonic()


# ──────────────────────────────────────────────
# Notify entity
# ──────────────────────────────────────────────
class OVHSMSNotifyEntity(NotifyEntity):
    """OVH SMS notify entity with drop/queue/disabled rate limiting."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:message-text-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        entry_data: dict[str, Any],
    ) -> None:
        self._hass = hass
        self._client: ovh.Client = entry_data["client"]
        self._service_name: str = entry_data["service_name"]
        self._default_sender: str = entry_data["sender"]
        self._recipients: list[str] = entry_data.get("recipients", [])

        self._attr_unique_id = f"ovh_sms_notify_{self._service_name}"
        self._attr_name = f"OVH SMS ({self._service_name})"

        self._strategy: str = entry_data.get(
            "rate_limit_strategy", DEFAULT_RATE_LIMIT_STRATEGY
        )
        self._limiter: SMSRateLimiter | None = None
        self._queue: deque[QueuedMessage] | None = None
        self._queue_max: int = 0
        self._queue_task: asyncio.Task | None = None

        if self._strategy in (STRATEGY_DROP, STRATEGY_QUEUE):
            max_calls = entry_data.get("rate_limit_max", DEFAULT_RATE_LIMIT_MAX)
            window = entry_data.get("rate_limit_window", DEFAULT_RATE_LIMIT_WINDOW)
            self._limiter = SMSRateLimiter(max_calls, window)

            if self._strategy == STRATEGY_QUEUE:
                self._queue_max = entry_data.get(
                    "rate_limit_queue_size", DEFAULT_RATE_LIMIT_QUEUE_SIZE
                )
                self._queue = deque()

            _LOGGER.info(
                "OVH SMS: rate limiting [%s] — %d SMS per %d seconds%s",
                self._strategy,
                max_calls,
                window,
                f", queue size {self._queue_max}" if self._queue is not None else "",
            )
        else:
            _LOGGER.info("OVH SMS: rate limiting disabled")

    # ── NotifyEntity API ──────────────────────

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send an SMS to the default recipients (notify.send_message)."""
        await self._async_dispatch(message, self._recipients, {})

    async def async_send_sms(
        self, message: str, recipients: list[str] | None = None, **options: Any
    ) -> None:
        """Send an SMS with optional recipients and OVH options (ovh_sms.send_sms)."""
        await self._async_dispatch(message, recipients or self._recipients, options)

    async def _async_dispatch(
        self, message: str, targets: list[str], options: dict[str, Any]
    ) -> None:
        """Apply rate limiting, then send now, queue or drop."""
        if not targets:
            raise ServiceValidationError(
                "No recipients: configure default recipients in the integration "
                "options or pass `recipients` to the ovh_sms.send_sms action"
            )

        if self._strategy == STRATEGY_DISABLED or self._limiter is None:
            await self._async_send(message, list(targets), options)
            return

        if self._limiter.acquire():
            await self._async_send(message, list(targets), options)
            return

        wait = self._limiter.seconds_until_available

        if self._strategy == STRATEGY_DROP:
            _LOGGER.warning(
                "OVH SMS [drop]: message dropped — rate limit reached. "
                "Next slot in %.0fs.",
                wait,
            )
            return

        if self._queue is not None and len(self._queue) >= self._queue_max:
            _LOGGER.warning(
                "OVH SMS [queue]: queue full (%d/%d) — message dropped.",
                len(self._queue), self._queue_max,
            )
            return

        if self._queue is not None:
            self._queue.append(QueuedMessage(message, list(targets), options))
            _LOGGER.info(
                "OVH SMS [queue]: message queued (%d/%d). Next slot in %.0fs.",
                len(self._queue), self._queue_max, wait,
            )
            self._ensure_queue_processor()

    # ── Lifecycle ─────────────────────────────

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the queue processor task on unload."""
        if self._queue_task is not None and not self._queue_task.done():
            self._queue_task.cancel()
            _LOGGER.debug("OVH SMS: queue task cancelled on unload")

    # ── Queue processor ───────────────────────

    def _ensure_queue_processor(self) -> None:
        if self._queue_task is None or self._queue_task.done():
            self._queue_task = self._hass.async_create_task(self._process_queue())

    async def _process_queue(self) -> None:
        while self._queue:
            if self._limiter is None:
                break
            wait = self._limiter.seconds_until_available
            if wait > 0:
                await asyncio.sleep(wait + 0.1)
            if not self._limiter.acquire():
                continue
            msg = self._queue.popleft()
            age = time.monotonic() - msg.queued_at
            _LOGGER.info(
                "OVH SMS [queue]: sending queued message (waited %.0fs, %d remaining).",
                age, len(self._queue),
            )
            try:
                await self._async_send(msg.message, msg.targets, msg.data)
            except HomeAssistantError as err:
                _LOGGER.error("OVH SMS [queue]: queued message not sent: %s", err)

    # ── OVH API call ─────────────────────────

    async def _async_send(
        self, message: str, targets: list[str], options: dict[str, Any]
    ) -> None:
        payload: dict[str, Any] = {
            "message": message,
            "receivers": targets,
            "noStopClause": options.get(ATTR_NO_STOP_CLAUSE, True),
        }

        sender = options.get(ATTR_SENDER, self._default_sender)
        if sender:
            payload["sender"] = sender
        else:
            payload["senderForResponse"] = True

        if ATTR_PRIORITY in options:
            payload["priority"] = options[ATTR_PRIORITY]
        if ATTR_CODING in options:
            payload["coding"] = options[ATTR_CODING]

        try:
            result = await self._hass.async_add_executor_job(
                partial(self._client.post, f"/sms/{self._service_name}/jobs", **payload)
            )
        except ovh.exceptions.APIError as err:
            _LOGGER.debug("OVH SMS: send error detail: %s", err)
            if is_out_of_credits(err):
                raise HomeAssistantError(
                    "OVH SMS: Not enough SMS credits — top up your SMS account "
                    "in the OVHcloud Manager"
                ) from err
            raise HomeAssistantError(
                "OVH SMS: failed to send message — check your OVH account, "
                "credits and API permissions"
            ) from err

        remaining = f", {self._limiter.remaining} slot(s) remaining" if self._limiter else ""
        _LOGGER.info(
            "OVH SMS sent: %d credit(s) used, %d delivered, %d invalid%s",
            result.get("totalCreditsRemoved", 0),
            len(result.get("validReceivers", [])),
            len(result.get("invalidReceivers", [])),
            remaining,
        )
        _LOGGER.debug(
            "OVH SMS sent detail — IDs: %s, valid: %s, invalid: %s",
            result.get("ids", []),
            result.get("validReceivers", []),
            result.get("invalidReceivers", []),
        )
