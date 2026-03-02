"""OVH SMS notify entity with configurable rate limiting."""
from __future__ import annotations

import asyncio
from collections import deque
import logging
import re
import time
from typing import Any

import ovh

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_CODING,
    ATTR_NO_STOP_CLAUSE,
    ATTR_PRIORITY,
    ATTR_SENDER,
    DEFAULT_RATE_LIMIT_MAX,
    DEFAULT_RATE_LIMIT_QUEUE_SIZE,
    DEFAULT_RATE_LIMIT_STRATEGY,
    DEFAULT_RATE_LIMIT_WINDOW,
    DOMAIN,
    STRATEGY_DISABLED,
    STRATEGY_DROP,
    STRATEGY_QUEUE,
)

_LOGGER = logging.getLogger(__name__)
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")


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
    __slots__ = ("message", "targets", "data", "queued_at")

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

    async def async_send_message(
        self, message: str, title: str | None = None, data: dict[str, Any] | None = None
    ) -> None:
        """Send an SMS via OVH API."""
        data = data or {}
        targets = data.get("target") or data.get("targets") or self._recipients

        if not targets:
            _LOGGER.error("OVH SMS: no recipients configured. Add phone numbers in the integration settings.")
            return

        if isinstance(targets, str):
            targets = [targets]

        valid_targets = [t for t in targets if _E164_RE.match(str(t))]
        invalid_targets = [t for t in targets if not _E164_RE.match(str(t))]
        if invalid_targets:
            _LOGGER.warning("OVH SMS: %d recipient(s) ignored — not valid E.164 format", len(invalid_targets))
        if not valid_targets:
            _LOGGER.error("OVH SMS: no valid recipients after E.164 validation")
            return
        targets = valid_targets

        if self._strategy == STRATEGY_DISABLED or self._limiter is None:
            await self._hass.async_add_executor_job(
                self._do_send, message, list(targets), data
            )
            return

        if self._limiter.acquire():
            await self._hass.async_add_executor_job(
                self._do_send, message, list(targets), data
            )
            return

        wait = self._limiter.seconds_until_available

        if self._strategy == STRATEGY_DROP:
            _LOGGER.warning(
                "OVH SMS [drop]: message dropped — rate limit reached. "
                "Next slot in %.0fs. Recipients: %s | Message: %.80s",
                wait, targets, message,
            )
            return

        if self._queue is not None and len(self._queue) >= self._queue_max:
            _LOGGER.warning(
                "OVH SMS [queue]: queue full (%d/%d) — message dropped.",
                len(self._queue), self._queue_max,
            )
            return

        if self._queue is not None:
            self._queue.append(QueuedMessage(message, list(targets), data))
            _LOGGER.info(
                "OVH SMS [queue]: message queued (%d/%d). Next slot in %.0fs.",
                len(self._queue), self._queue_max, wait,
            )
            self._ensure_queue_processor()

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
            await self._hass.async_add_executor_job(
                self._do_send, msg.message, msg.targets, msg.data
            )

    # ── OVH API call ─────────────────────────

    def _do_send(self, message: str, targets: list[str], data: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "message": message,
            "receivers": targets,
            "noStopClause": data.get(ATTR_NO_STOP_CLAUSE, True),
        }

        sender = data.get(ATTR_SENDER, self._default_sender)
        if sender:
            payload["sender"] = sender
        else:
            payload["senderForResponse"] = True

        if ATTR_PRIORITY in data:
            payload["priority"] = data[ATTR_PRIORITY]
        if ATTR_CODING in data:
            payload["coding"] = data[ATTR_CODING]

        try:
            result = self._client.post(f"/sms/{self._service_name}/jobs", **payload)
            remaining = f", {self._limiter.remaining} slot(s) remaining" if self._limiter else ""
            _LOGGER.info(
                "OVH SMS sent: %d credit(s) used, IDs: %s, valid: %s, invalid: %s%s",
                result.get("totalCreditsRemoved", 0),
                result.get("ids", []),
                result.get("validReceivers", []),
                result.get("invalidReceivers", []),
                remaining,
            )
        except ovh.exceptions.APIError as err:
            _LOGGER.debug("OVH SMS: send error detail: %s", err)
            _LOGGER.error("OVH SMS: failed to send message — check your OVH account and API permissions")
        except ovh.exceptions.InvalidResponse as err:
            _LOGGER.debug("OVH SMS: invalid API response detail: %s", err)
            _LOGGER.error("OVH SMS: invalid response from OVH API")
