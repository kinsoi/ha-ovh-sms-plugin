"""Tests for the OVH SMS config and options flows."""
from __future__ import annotations

from unittest.mock import MagicMock

import ovh
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import SERVICE_NAME
from custom_components.ovh_sms.const import (
    CONF_RATE_LIMIT_STRATEGY,
    CONF_RECIPIENTS,
    CONF_SERVICE_NAME,
    DOMAIN,
    STRATEGY_QUEUE,
)

USER_INPUT = {
    "application_key": "ak",
    "application_secret": "as",
    "consumer_key": "ck",
    "service_name": SERVICE_NAME,
    "recipients": "+33600000001, +33600000002",
    "sender": "",
}
RATE_INPUT = {
    "rate_limit_strategy": "drop",
    "rate_limit_max": 5,
    "rate_limit_window": 60,
    "rate_limit_queue_size": 10,
}


async def test_user_flow_success(
    hass: HomeAssistant, mock_ovh_client: MagicMock
) -> None:
    """Happy path: credentials, then rate limit, creates the entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "rate_limit"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], RATE_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERVICE_NAME
    assert result["data"][CONF_RECIPIENTS] == ["+33600000001", "+33600000002"]
    assert result["data"]["config_validated"] is True


async def test_user_flow_invalid_recipients(
    hass: HomeAssistant, mock_ovh_client: MagicMock
) -> None:
    """Non E.164 numbers are rejected on the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "recipients": "0612345678"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_RECIPIENTS: "invalid_recipients"}


@pytest.mark.parametrize(
    "exc",
    [
        ovh.exceptions.InvalidKey("bad"),
        ovh.exceptions.NotGrantedCall("no rights"),
        ovh.exceptions.HTTPError("down"),
    ],
)
async def test_user_flow_validation_failed_save_anyway(
    hass: HomeAssistant, mock_ovh_client: MagicMock, exc: Exception
) -> None:
    """A validation error offers to save anyway (entry flagged unverified)."""
    mock_ovh_client.get.side_effect = exc
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "validation_failed"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"action": "save_anyway"}
    )
    assert result["step_id"] == "rate_limit"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], RATE_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["config_validated"] is False


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """The same SMS service cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _options_init(hass: HomeAssistant, entry: MockConfigEntry, section: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"section": section}
    )


async def test_options_rate_limit(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Rate limit options are saved into the entry data."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await _options_init(hass, config_entry, "rate_limit")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**RATE_INPUT, "rate_limit_strategy": STRATEGY_QUEUE}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.data[CONF_RATE_LIMIT_STRATEGY] == STRATEGY_QUEUE


async def test_options_credentials_change_service(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Changing the service name also updates the entry unique_id."""
    new_service = "sms-zz99999-1"
    mock_ovh_client.get.side_effect = lambda path: (
        {"firstname": "J"}
        if path == "/me"
        else [SERVICE_NAME, new_service]
        if path == "/sms"
        else {"creditsLeft": 1}
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await _options_init(hass, config_entry, "credentials")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "application_key": "ak",
            "application_secret": "",
            "consumer_key": "",
            "service_name": new_service,
            "recipients": "+33600000009",
            "sender": "",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.data[CONF_SERVICE_NAME] == new_service
    assert config_entry.data["application_secret"] == "as"  # kept when blank
    assert config_entry.data[CONF_RECIPIENTS] == ["+33600000009"]
    assert config_entry.unique_id == new_service


async def test_options_credentials_service_already_used(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Switching to a service already configured in another entry is refused."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="sms-other-1",
        data={},
        disabled_by=ConfigEntryDisabler.USER,
    )
    other.add_to_hass(hass)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await _options_init(hass, config_entry, "credentials")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "application_key": "ak",
            "application_secret": "",
            "consumer_key": "",
            "service_name": "sms-other-1",
            "recipients": "+33600000009",
            "sender": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SERVICE_NAME: "already_configured"}


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (None, FlowResultType.ABORT),
        (ovh.exceptions.APIError("boom"), FlowResultType.FORM),
        (ovh.exceptions.InvalidResponse("garbage"), FlowResultType.FORM),
        (
            ovh.exceptions.APIError("Not enough credits (left: -12.00)"),
            FlowResultType.FORM,
        ),
    ],
)
async def test_options_test_sms(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    config_entry: MockConfigEntry,
    side_effect: Exception | None,
    expected: FlowResultType,
) -> None:
    """The test SMS step sends to default recipients and reports errors."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_ovh_client.post.side_effect = side_effect
    result = await _options_init(hass, config_entry, "test_sms")
    assert result["step_id"] == "test_sms"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"message": "ping"}
    )
    assert result["type"] is expected
    if expected is FlowResultType.ABORT:
        assert result["reason"] == "test_sent"
        assert result["description_placeholders"] == {"sent": "1", "invalid": "0"}
    if expected is FlowResultType.FORM:
        expected_error = (
            "not_enough_credits" if "credits" in str(side_effect) else "test_failed"
        )
        assert result["errors"] == {"base": expected_error}
    assert mock_ovh_client.post.call_args.kwargs["receivers"] == [
        "+33600000001",
        "+33600000002",
    ]


async def test_options_help(
    hass: HomeAssistant, mock_ovh_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """The help page renders with the entity id placeholder."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await _options_init(hass, config_entry, "documentation")
    assert result["step_id"] == "help"
    assert result["description_placeholders"]["entity_id"] == (
        "notify.ovh_sms_sms_ab12345_1"
    )


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (ovh.exceptions.InvalidKey("bad"), "invalid_auth"),
        (
            ovh.exceptions.APIError("invalid key", response=MagicMock(status_code=403)),
            "invalid_auth",
        ),
        (ovh.exceptions.HTTPError("down"), "cannot_connect"),
    ],
)
async def test_options_credentials_errors(
    hass: HomeAssistant,
    mock_ovh_client: MagicMock,
    config_entry: MockConfigEntry,
    exc: Exception,
    error: str,
) -> None:
    """Credential errors are mapped to the right form error."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_ovh_client.get.side_effect = exc
    result = await _options_init(hass, config_entry, "credentials")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "application_key": "ak2",
            "application_secret": "",
            "consumer_key": "",
            "service_name": SERVICE_NAME,
            "recipients": "+33600000001",
            "sender": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
