"""Hydroquebec webuser module."""

import logging
import socket
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import aiohttp

from hydroqc.consts import LoginStatus
from hydroqc.customer import Customer
from hydroqc.error import HydroQcError
from hydroqc.hydro_api.client import HydroClient
from hydroqc.hydro_api.consts import AUTH_CLIENT_WEB
from hydroqc.logger import get_logger


class WebUser:
    """Hydroquebec webuser.

    Represents a login/password web account (Demandeur)
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        verify_ssl: bool,
        log_level: str | None = None,
        http_log_level: str | None = None,
        log_file: str | None = None,
        diag_folder: str | None = None,
        family: socket.AddressFamily = socket.AF_UNSPEC,  # pylint: disable=no-member
        cookie_jar: aiohttp.CookieJar | None = None,
        refresh_token: str = "",
        refresh_token_expiry: datetime | None = None,
        auth_client: str = AUTH_CLIENT_WEB,
    ):
        """Create a new Hydroquebec webuser.

        ``auth_client`` selects the B2C client, ``"web"`` (default) or
        ``"mobile"``, see :class:`HydroClient`.
        """
        self._hydro_client = HydroClient(
            username,
            password,
            verify_ssl=verify_ssl,
            log_level=http_log_level,
            diag_folder=diag_folder,
            family=family,
            cookie_jar=cookie_jar,
            refresh_token=refresh_token,
            refresh_token_expiry=refresh_token_expiry,
            auth_client=auth_client,
        )
        self._username: str = username
        self._log_level: str | None = log_level
        self.customers: list[Customer] = []
        self._logger: None | logging.Logger = None
        self._log_file: str | None = log_file

    async def check_hq_portal_status(self) -> bool:
        """Check if the Hydro Quebec website/portal is available."""
        return await self._hydro_client.check_portal_status()

    async def get_hq_portal_unavailability(self) -> dict[str, str] | None:
        """Return the planned Hydro Quebec unavailability in effect, if any."""
        return await self._hydro_client.get_portal_unavailability()

    async def login(self) -> LoginStatus:
        """Login to Hydroquebec website.

        Returns :attr:`LoginStatus.MFA_REQUIRED` when Hydro-Quebec asks for an
        e-mail verification code. The login is then incomplete until
        :meth:`submit_mfa_code` is called with the received code.
        """
        return await self._hydro_client.login()

    async def try_silent_login(self) -> LoginStatus:
        """Try to log in with the stored session, without any user interaction.

        Useful when a previous session was persisted: the B2C single sign-on
        cookie may still be valid and skip both the password and the e-mail
        verification code.
        """
        return await self._hydro_client.try_silent_login()

    async def submit_mfa_code(self, code: str) -> LoginStatus:
        """Submit the e-mail verification code and finish the login.

        Raises:
            hydroqc.error.HydroQcMFAInvalidCodeError: the code was refused, the
                verification step is still valid.
            hydroqc.error.HydroQcMFAExpiredError: the verification step expired
                and the login has to be restarted.

        """
        return await self._hydro_client.submit_mfa_code(code)

    async def request_mfa_code(self) -> bool:
        """Ask Hydro-Quebec to send (or resend) an e-mail verification code."""
        return await self._hydro_client.request_mfa_code()

    @property
    def mfa_required(self) -> bool:
        """Return True while a verification code has to be submitted."""
        return self._hydro_client.mfa_required

    def export_session_state(self) -> dict[str, Any]:
        """Return the session state worth persisting between runs.

        Holds the OAuth tokens and the Azure AD B2C cookies. Persisting it lets
        the next run skip the login, and therefore skip a new e-mail
        verification code, while the refresh token or the SSO cookie is valid.
        """
        return self._hydro_client.export_session_state()

    def import_session_state(self, state: Mapping[str, Any]) -> None:
        """Restore a session state produced by :meth:`export_session_state`."""
        self._hydro_client.import_session_state(state)

    @property
    def session_expired(self) -> bool:
        """Check if the session is expired."""
        return self._hydro_client.is_session_expired()

    async def get_info(self) -> None:
        """Fetch data about this webuser.

        Retrieve customers
        """
        user_info = await self._hydro_client.get_user_info()
        # We can only create the logger after getting user info at least on time
        if self._logger is None:
            self._logger = get_logger(
                f"w-{self.webuser_id}", self._log_level, log_file=self._log_file
            )

        # is the same accross contracts
        for customer_data in user_info:
            customer_names = [
                str(v)
                for k, v in customer_data.items()
                if k.startswith("nom") and k.endswith("Titulaire")
            ]
            customer_id = customer_data["noPartenaireTitulaire"]
            # Create new customer if it's not there
            if customer_id not in set(c.customer_id for c in self.customers):
                self._logger.info("Creating new customer %s", customer_id)
                customer = Customer(
                    customer_data["noPartenaireDemandeur"],
                    customer_data["noPartenaireTitulaire"],
                    customer_names=customer_names,
                    hydro_client=self._hydro_client,
                    log_level=self._log_level,
                )
                self.customers.append(customer)
        self._logger.debug("Got webuser info")

    @property
    def webuser_id(self) -> str:
        """Get webuser id."""
        if not (data := self._hydro_client.get_token_data()):
            return ""
        return data.get("sub", "")

    @property
    def first_name(self) -> str:
        """Get webuser firstname."""
        if not (data := self._hydro_client.get_token_data()):
            return ""
        return data["given_name"]

    @property
    def last_name(self) -> str:
        """Get webuser lastname."""
        if not (data := self._hydro_client.get_token_data()):
            return ""
        return data["family_name"]

    def get_customer(self, customer_id: str) -> Customer:
        """Find customer by id."""
        if not (
            customers := [c for c in self.customers if c.customer_id == customer_id]
        ):
            raise HydroQcError(f"Customer {customer_id} not found")
        return customers[0]

    async def fetch_customers_info(self) -> None:
        """Fetch all customers info.

        It could be long if you have more than 5 customers.
        """
        if self._logger:
            self._logger.debug("Getting customers info")
        for customer in self.customers:
            await customer.get_info()
        if self._logger:
            self._logger.debug("Got customers info")

    async def close_session(self) -> None:
        """Close http sessions."""
        await self._hydro_client.close_session()

    def __repr__(self) -> str:
        """Represent object."""
        if not self.webuser_id:
            return f"""<Webuser - {self._username}>"""
        return f"""<Webuser - {self.webuser_id}>"""
