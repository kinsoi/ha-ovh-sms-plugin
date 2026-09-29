"""Repair issues raised by the OVH SMS integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN


def _out_of_credits_issue_id(service_name: str) -> str:
    return f"out_of_credits_{service_name}"


@callback
def async_update_out_of_credits_issue(
    hass: HomeAssistant, service_name: str, out_of_credits: bool
) -> None:
    """Raise or clear the 'no SMS credits left' repair issue for a service."""
    issue_id = _out_of_credits_issue_id(service_name)
    if not out_of_credits:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="out_of_credits",
        translation_placeholders={"service_name": service_name},
        learn_more_url="https://www.ovhcloud.com/en/sms/",
    )
