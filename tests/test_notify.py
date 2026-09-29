"""Tests for the OVH SMS notify entity and send_sms action."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import ovh
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import NOTIFY_ENTITY, SERVICE_NAME
from custom_components.ovh_sms.const import (
    CONF_RATE_LIMIT_MAX,
    CONF_RATE_LIMIT_STRATEGY,
    CONF_RATE_LIMIT_WINDOW,
    CONF_RECIPIENTS,
    DOMAIN,
    STRATEGY_DROP,
    STRATEGY_QUEUE,
)

JOBS_PATH = f"/sms/{SERVICE_NAME}/jobs"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **data) -> None:
    hass.config_entries.async_update_entry(entry, data={**entry.data, **data})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture
def entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> MockConfigEntry:
    config_entry.add_to_hass(hass)
    return config_entry


async def test_send_message_default_recipients(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """notify.send_message sends to the configured recipients."""
    await _setup(hass, entry)
    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": NOTIFY_ENTITY, "message": "Hello"},
        blocking=True,
    )
    mock_ovh_client.post.assert_called_once_with(
        JOBS_PATH,
        message="Hello",
        receivers=["+33600000001", "+33600000002"],
        noStopClause=True,
        senderForResponse=True,
    )


async def test_send_message_without_recipients_raises(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """notify.send_message with no default recipients reports an error."""
    await _setup(hass, entry, **{CONF_RECIPIENTS: []})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": NOTIFY_ENTITY, "message": "Hello"},
            blocking=True,
        )
    mock_ovh_client.post.assert_not_called()


async def test_send_sms_action_with_options(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """ovh_sms.send_sms supports recipients and advanced OVH options."""
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "send_sms",
        {
            "entity_id": NOTIFY_ENTITY,
            "message": "Alarm!",
            "recipients": ["+33611111111"],
            "sender": "MyHome",
            "priority": "high",
            "coding": "8bit",
            "no_stop_clause": False,
        },
        blocking=True,
    )
    mock_ovh_client.post.assert_called_once_with(
        JOBS_PATH,
        message="Alarm!",
        receivers=["+33611111111"],
        noStopClause=False,
        sender="MyHome",
        priority="high",
        coding="8bit",
    )


async def test_send_sms_action_defaults(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """ovh_sms.send_sms without recipients falls back to the defaults."""
    await _setup(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "send_sms",
        {"entity_id": NOTIFY_ENTITY, "message": "Hi"},
        blocking=True,
    )
    assert mock_ovh_client.post.call_args.kwargs["receivers"] == [
        "+33600000001",
        "+33600000002",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"recipients": ["0612345678"]},
        {"sender": "WayTooLongSender"},
        {"coding": "unicode"},
        {"priority": "urgent"},
    ],
)
async def test_send_sms_action_rejects_invalid_input(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    entry: MockConfigEntry,
    bad: dict,
) -> None:
    """Invalid parameters are rejected before calling OVH."""
    await _setup(hass, entry)
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(
            DOMAIN,
            "send_sms",
            {"entity_id": NOTIFY_ENTITY, "message": "Hi", **bad},
            blocking=True,
        )
    mock_ovh_client.post.assert_not_called()


async def test_send_api_error_raises(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """An OVH API failure surfaces as a HomeAssistantError to the caller."""
    await _setup(hass, entry)
    mock_ovh_client.post.side_effect = ovh.exceptions.APIError("boom")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": NOTIFY_ENTITY, "message": "Hello"},
            blocking=True,
        )


async def test_rate_limit_drop_does_not_log_pii(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropped messages are not sent and their content never reaches INFO+ logs."""
    await _setup(
        hass,
        entry,
        **{
            CONF_RATE_LIMIT_STRATEGY: STRATEGY_DROP,
            CONF_RATE_LIMIT_MAX: 1,
            CONF_RATE_LIMIT_WINDOW: 60,
        },
    )
    for text in ("first", "secret second"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": NOTIFY_ENTITY, "message": text},
            blocking=True,
        )
    assert mock_ovh_client.post.call_count == 1
    visible = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "dropped" in visible
    assert "secret second" not in visible
    assert "+33600000001" not in visible


async def test_rate_limit_queue_sends_later(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """Queued messages are sent once the window frees up."""
    await _setup(
        hass,
        entry,
        **{
            CONF_RATE_LIMIT_STRATEGY: STRATEGY_QUEUE,
            CONF_RATE_LIMIT_MAX: 1,
            CONF_RATE_LIMIT_WINDOW: 1,
        },
    )
    for text in ("one", "two"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": NOTIFY_ENTITY, "message": text},
            blocking=True,
        )
    assert mock_ovh_client.post.call_count == 1
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_ovh_client.post.call_count == 2
    assert mock_ovh_client.post.call_args.kwargs["message"] == "two"


async def test_unload_cancels_queue(
    hass: HomeAssistant, mock_ovh_client: MagicMock, entry: MockConfigEntry
) -> None:
    """Unloading the entry does not leave the queue task running."""
    await _setup(
        hass,
        entry,
        **{
            CONF_RATE_LIMIT_STRATEGY: STRATEGY_QUEUE,
            CONF_RATE_LIMIT_MAX: 1,
            CONF_RATE_LIMIT_WINDOW: 3600,
        },
    )
    for text in ("one", "two"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": NOTIFY_ENTITY, "message": text},
            blocking=True,
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert mock_ovh_client.post.call_count == 1
