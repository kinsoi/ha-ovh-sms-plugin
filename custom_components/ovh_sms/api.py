"""Helpers around the python-ovh client."""
from __future__ import annotations

import ovh

from .const import OVH_ENDPOINT, OVH_TIMEOUT


def create_client(
    application_key: str, application_secret: str, consumer_key: str
) -> ovh.Client:
    """Create an OVH API client (blocking: run it in the executor)."""
    return ovh.Client(
        endpoint=OVH_ENDPOINT,
        application_key=application_key,
        application_secret=application_secret,
        consumer_key=consumer_key,
        timeout=OVH_TIMEOUT,
    )


# OVH errors that retrying cannot fix (bad keys, revoked token, missing rights)
_AUTH_ERRORS = (
    ovh.exceptions.InvalidKey,
    ovh.exceptions.InvalidCredential,
    ovh.exceptions.NotCredential,
    ovh.exceptions.NotGrantedCall,
    ovh.exceptions.Forbidden,
)


def is_auth_error(err: ovh.exceptions.APIError) -> bool:
    """Return True if the error is a credential / permission problem.

    The OVH API does not always send an errorCode: the first call made with an
    invalid application key returns a bare 403, which python-ovh raises as a
    plain APIError. Rely on the HTTP status as well as the exception class.
    """
    if isinstance(err, _AUTH_ERRORS):
        return True
    response = getattr(err, "response", None)
    return getattr(response, "status_code", None) in (401, 403)


def is_out_of_credits(err: ovh.exceptions.APIError) -> bool:
    """Return True if OVH refused the job because the account has no credits left."""
    return "not enough credits" in str(err).lower()
