"""Hydroqc Consts."""

from enum import StrEnum

from dateutil import tz

# Always get the time using HydroQuebec Local Time
HQ_TIMEZONE = tz.gettz("America/Toronto")

LOGGING_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class LoginStatus(StrEnum):
    """Outcome of a login attempt.

    ``bool(status)`` is only true for :attr:`SUCCESS`, so the historic
    ``if await webuser.login():`` idiom keeps behaving as before while callers
    that need to react to the new e-mail verification step can inspect the
    value.
    """

    SUCCESS = "success"
    MFA_REQUIRED = "mfa_required"
    FAILED = "failed"

    def __bool__(self) -> bool:
        """Return True only when the login actually succeeded."""
        return self is LoginStatus.SUCCESS
