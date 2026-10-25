"""Brand settings of a project (self-unlock email and page): validation shared by the PATCH body.

Values are text the customer controls and the engine later puts in an email and a public page,
so each one is checked by shape here and escaped again where it is rendered.
"""

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BeforeValidator

from .notifications.webhook import UnsafeUrlError, check_url

BRAND_TEXT_FIELDS = ("brand_name", "brand_logo_url", "brand_color", "brand_support_url", "brand_support_email")
BRAND_FIELDS = (*BRAND_TEXT_FIELDS, "self_unlock_enabled")

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")


def blank_to_none(value):
    """Null or a blank string clears a brand field; any other string is trimmed."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _color(value: str | None) -> str | None:
    if value is not None and not _COLOR.match(value):
        raise ValueError("brand_color must be a #RRGGBB hex color")
    return value


def _email(value: str | None) -> str | None:
    if value is not None and (len(value) > 254 or not _EMAIL.match(value)):
        raise ValueError("brand_support_email must be an email address")
    return value


def _https_url(value: str | None) -> str | None:
    """https with a host and no credentials. The logo goes through `webhook.check_url` as well."""
    if value is None:
        return None
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError:
        raise ValueError("not a valid url") from None
    if (parts.scheme.lower() != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or re.search(r"[\s<>\"']", value)):
        raise ValueError("must be an https url")
    return value


def check_logo_url(url: str, resolver) -> None:
    """The logo is validated like the webhook URL: https to a public address. Raises UnsafeUrlError."""
    try:
        _https_url(url)
    except ValueError as exc:
        raise UnsafeUrlError(f"unsafe logo url: {exc}") from None
    check_url(url, resolver)


# Used as the PATCH field types: a blank value arrives as None, which clears the field.
BrandColor = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_color)]
BrandEmail = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_email)]
BrandUrl = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_https_url)]
BrandName = Annotated[str | None, BeforeValidator(blank_to_none)]
