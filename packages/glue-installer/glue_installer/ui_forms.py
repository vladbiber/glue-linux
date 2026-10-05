"""
Pure form-field state for the Glue Linux installer wizard.

Text-entry screens (hostname, username, password + confirm, locale, timezone)
are modelled here as FormField objects with typed events: feed_char(),
backspace(), submit(). Validation errors are surfaced as plain strings.
Masking is a render concern - the model always stores the real value and
render.py displays '*' per character for secret fields.

This module MUST NOT import curses, subprocess, or os, and performs no I/O.
The validation patterns mirror identity.IdentitySpec exactly so that a value
accepted screen-by-screen is always accepted by the final IdentitySpec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

Validator = Callable[[str], Optional[str]]

# Hard cap on typed values; feed_char ignores input beyond this length.
MAX_VALUE_LEN = 255

PASSWORD_MISMATCH = "Passwords do not match — please re-enter both."

# Patterns mirror glue_installer.identity (kept dependency-free on purpose:
# importing identity would pull in the executor's subprocess machinery).
_RE_HOSTNAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")
_RE_USERNAME = re.compile(r"^[a-z_][a-z0-9_\-]{0,31}$")
_RE_LOCALE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}\.UTF-8$")
_RE_TIMEZONE = re.compile(r"^[A-Za-z_+\-]+(/[A-Za-z_+\-]+){0,2}$")


# ---------------------------------------------------------------------------
# Validators: return an error string, or None when the value is acceptable.
# ---------------------------------------------------------------------------

def validate_hostname(value: str) -> Optional[str]:
    if not value:
        return "Hostname must not be empty."
    if len(value) > 63:
        return "Hostname must be at most 63 characters."
    if not _RE_HOSTNAME.match(value) or value.endswith("-"):
        return ("Hostname may only contain lowercase letters, digits and "
                "hyphens, and must not start or end with a hyphen.")
    return None


def validate_username(value: str) -> Optional[str]:
    if not value:
        return "Username must not be empty."
    if not _RE_USERNAME.match(value):
        return ("Username must start with a lowercase letter or '_', contain "
                "only [a-z0-9_-], and be at most 32 characters.")
    return None


def validate_password(value: str) -> Optional[str]:
    if not value:
        return "Password must not be empty."
    return None


def validate_locale(value: str) -> Optional[str]:
    if not _RE_LOCALE.match(value):
        return "Locale must have the form ll_CC.UTF-8 (e.g. en_US.UTF-8)."
    return None


def validate_timezone(value: str) -> Optional[str]:
    if not value:
        return "Timezone must not be empty."
    if not _RE_TIMEZONE.match(value):
        return "Timezone must have Area/City form (e.g. Europe/London, UTC)."
    return None


# ---------------------------------------------------------------------------
# FormField
# ---------------------------------------------------------------------------

@dataclass
class FormField:
    """One text-entry field: real value + typed edit events + validation."""

    key: str
    label: str
    value: str = ""
    secret: bool = False
    validator: Optional[Validator] = None
    error: Optional[str] = None

    def feed_char(self, ch: str) -> bool:
        """Append one printable character; anything else is ignored.

        Returns True when the character was accepted. Editing clears any
        stale validation error.
        """
        if not isinstance(ch, str) or len(ch) != 1 or not ch.isprintable():
            return False
        if len(self.value) >= MAX_VALUE_LEN:
            return False
        self.value += ch
        self.error = None
        return True

    def backspace(self) -> None:
        """Delete the last character; no-op when the value is empty."""
        self.value = self.value[:-1]
        self.error = None

    def submit(self) -> Tuple[bool, Optional[str]]:
        """Validate the current value; returns (ok, error_message)."""
        err = self.validator(self.value) if self.validator else None
        self.error = err
        return (err is None, err)

    def clear(self) -> None:
        """Reset the value and any error (used on password mismatch)."""
        self.value = ""
        self.error = None


# ---------------------------------------------------------------------------
# Wizard integration helpers
# ---------------------------------------------------------------------------

def masked(field: FormField) -> str:
    """Display form of a field's value: '*' per char when secret."""
    return "*" * len(field.value) if field.secret else field.value


_IDENTITY_DEFAULTS = {
    "hostname": "glue",
    "locale": "en_US.UTF-8",
    "timezone": "UTC",
}


def build_identity_fields(
    defaults: Optional[Dict[str, str]] = None,
) -> Dict[str, FormField]:
    """Ordered identity form fields for the wizard, sensible defaults prefilled.

    `defaults` may override the prefill for 'hostname', 'username', 'locale'
    and 'timezone'; unknown keys raise ValueError (boundary validation).
    """
    merged = dict(_IDENTITY_DEFAULTS)
    if defaults:
        unknown = set(defaults) - {"hostname", "username", "locale", "timezone"}
        if unknown:
            raise ValueError(
                f"Unknown identity default(s): {', '.join(sorted(unknown))}"
            )
        merged.update({k: v for k, v in defaults.items() if v is not None})
    return {
        "hostname": FormField(
            key="hostname", label="Hostname",
            value=merged["hostname"], validator=validate_hostname,
        ),
        "username": FormField(
            key="username", label="Username",
            value=merged.get("username", ""), validator=validate_username,
        ),
        "password": FormField(
            key="password", label="Password",
            secret=True, validator=validate_password,
        ),
        "password_confirm": FormField(
            key="password_confirm", label="Confirm password",
            secret=True, validator=validate_password,
        ),
        "locale": FormField(
            key="locale", label="Locale",
            value=merged["locale"], validator=validate_locale,
        ),
        "timezone": FormField(
            key="timezone", label="Timezone",
            value=merged["timezone"], validator=validate_timezone,
        ),
    }


def build_identity_spec(forms):
    """IdentitySpec from completed identity forms; None when forms is None.

    Raises ValueError when a value is rejected - the wizard converts it to
    a ValidationError, same contract as build_identity_fields.
    """
    if forms is None:
        return None
    # Imported lazily: identity pulls in the executor's subprocess
    # machinery, which must never load at ui-layer import time.
    from glue_installer.identity import IdentityError, IdentitySpec
    try:
        return IdentitySpec(
            hostname=forms["hostname"].value,
            username=forms["username"].value,
            password=forms["password"].value,
            locale=forms["locale"].value,
            timezone=forms["timezone"].value,
        )
    except IdentityError as exc:
        raise ValueError(str(exc))


def prefill_timezone_field(forms, tz) -> bool:
    """Inject the GeoIP-detected timezone into an identity form, but only
    while the field still holds the 'UTC' placeholder - never clobbers what
    the user typed, and invalid values are rejected. Returns True on apply."""
    if not forms or not tz or not isinstance(tz, str):
        return False
    field = forms.get("timezone")
    if field is None or field.value != "UTC":
        return False
    if field.validator and field.validator(tz) is not None:
        return False
    field.value = tz
    field.error = None
    return True


def submit_form(forms: Dict[str, FormField], fkey: str) -> Tuple[Optional[str], bool]:
    """Submit field `fkey`; returns (error, reset_to_password).

    On a password-confirm mismatch both password fields are cleared and
    reset_to_password is True so the wizard can return to the password
    screen for re-entry.
    """
    if fkey not in forms:
        return (f"Unknown form field '{fkey}'", False)
    field = forms[fkey]
    ok, err = field.submit()
    if not ok:
        return (err, False)
    if fkey == "password_confirm" and field.value != forms["password"].value:
        forms["password"].clear()
        field.clear()
        return (PASSWORD_MISMATCH, True)
    return (None, False)


def disk_label(device) -> str:
    """Single-select row label for a disk: path, size in GiB, model."""
    size_gib = device.size_bytes / (1024 ** 3)
    model = (device.model or "").strip()
    return f"{device.path}  {size_gib:.1f} GiB  {model}".rstrip()
