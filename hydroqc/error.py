"""HydroQc Error Module."""


class HydroQcError(Exception):
    """Base HydroQc Error."""


class HydroQcHTTPError(HydroQcError):
    """HTTP HydroQc Error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize."""
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class HydroQcMFARequiredError(HydroQcError):
    """The login needs a one-time code that cannot be requested here.

    Raised when a silent re-login (session expired in the background) lands on
    the e-mail verification step: there is nobody to ask for the code at that
    point, the caller has to start an interactive login again.
    """


class HydroQcMFAInvalidCodeError(HydroQcMFARequiredError):
    """The one-time code was refused by Hydro-Québec.

    The verification step is still valid, the caller can ask the user for
    another code and try again.
    """


class HydroQcMFAExpiredError(HydroQcMFARequiredError):
    """The verification step expired and the login flow has to be restarted."""


class HydroQcAnnualError(HydroQcError):
    """Annual HydroQc Error."""


class HydroQcCPCPeakError(HydroQcError):
    """CPC peak HydroQc Error."""


class HydroQcDPCPeakError(HydroQcError):
    """DPC peak HydroQc Error."""
