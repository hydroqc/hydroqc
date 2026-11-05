"""Basics tests for hydroqc."""

import datetime
import typing
from collections.abc import AsyncGenerator

import aiohttp
import pytest
import pytest_asyncio

from hydroqc.account import Account
from hydroqc.contract import ContractDCPC
from hydroqc.contract.common import (
    Contract,
    check_annual_data_present,
    check_info_data_present,
    check_outages_data_present,
)
from hydroqc.contract.contract_dpc import check_dpc_data_present
from hydroqc.consts import LoginStatus
from hydroqc.customer import Customer
from hydroqc.error import HydroQcError, HydroQcHTTPError
from hydroqc.peak.cpc.handler import CPCPeakHandler
from hydroqc.webuser import WebUser

from .tools import (
    lastweekday,
    lastweekday_str,
    today,
    today_str,
    yesterday,
    yesterday_str,
)


async def is_young_contract(contract: Contract) -> bool:
    """Determine if the contract is young than 1 year."""
    # Get one years ago plus few days
    one_year_ago = today - datetime.timedelta(days=367)
    contract_start_date = typing.cast(datetime.date, contract.start_date)
    # Get the youngest date between contract start date VS 1 years ago
    if contract_start_date is not None and contract_start_date > one_year_ago:
        return True
    return False


@pytest.mark.asyncio(loop_scope="session")
class SubTestCommon:
    """Base test class."""

    NAME: str
    HYDROQC_USERNAME: str
    HYDROQC_PASSWORD: str
    HYDROQC_CUSTOMER_NUMBER: str
    HYDROQC_ACCOUNT_NUMBER: str
    HYDROQC_CONTRACT_NUMBER: str
    HYDROQC_RATE: str
    HYDROQC_RATE_OPTION: str
    HYDROQC_EPP_ENABLED: str
    HYDROQC_WC_ENABLED: bool
    HYDROQC_ACCOUNT_ID: int
    session: aiohttp.ClientSession

    async def test_bad_credentials(self) -> None:
        """Test bad credentials management."""
        # Webuser
        webuser = WebUser("bad_username", "bad_password", verify_ssl=True)
        connected = await webuser.login()
        assert not connected, "Login should not work"

    @pytest_asyncio.fixture(loop_scope="session", scope="session")
    async def webuser(self) -> AsyncGenerator[WebUser]:
        """Fixture to keep the webuser instance through all tests."""
        webuser = WebUser(
            self.HYDROQC_USERNAME,
            self.HYDROQC_PASSWORD,
            verify_ssl=True,
            log_level="ERROR",
            http_log_level="ERROR",
        )
        yield webuser
        await webuser.close_session()

    async def test_error_cases(self) -> None:
        """Test some edge cases."""

        class FakeContract(Contract):
            """Fake contract class based on Contract just for tests."""

            _rate_option_code = "BAD"

            @property
            @check_info_data_present
            def fake_bad_property1(self) -> str:
                """Fake property 1 for test."""
                return "True"

            @property
            @check_annual_data_present
            def fake_bad_property2(self) -> str:
                """Fake property 2 for test."""
                return "True"

            @property
            @check_dpc_data_present
            def fake_bad_property3(self) -> str:
                """Fake property 3 for test."""
                return "True"

            @property
            @check_outages_data_present
            def fake_bad_property4(self) -> str:
                """Fake property 4 for test."""
                return "True"

        fake_contract = FakeContract(
            "aid",
            "cid",
            account_id="aid",
            contract_id="cid",
            hydro_client=None,  # type: ignore[arg-type]
        )
        fake_contract._all_period_data = [{"coutCentkWh": None}]  # type: ignore[typeddict-item]
        assert fake_contract.fake_bad_property1 is None
        assert fake_contract.fake_bad_property2 is None
        assert fake_contract.fake_bad_property3 is None
        assert fake_contract.fake_bad_property4 is None
        assert fake_contract.rate == "Unknown rate"
        assert fake_contract.cp_kwh_cost_mean is None

    async def test_good_credentials(self, webuser: WebUser) -> None:
        """Test credentials main."""
        connected = await webuser.login()
        assert connected is LoginStatus.SUCCESS, "Login should work"
        await webuser.get_info()

    async def test_session(self, webuser: WebUser) -> None:
        """Test session attributes."""
        assert webuser.first_name != "", "No webuser's firstname found"
        assert webuser.last_name != "", "No webuser's lastname found"
        assert repr(webuser).startswith("<Webuser - ")

    @pytest_asyncio.fixture(loop_scope="session", scope="session")
    async def customer(self, webuser: WebUser) -> Customer | None:
        """Fixture to keep the customer instance through all tests."""
        # New env var
        if self.HYDROQC_CUSTOMER_NUMBER is not None:
            customer_research = [
                c
                for c in webuser.customers
                if c.customer_id == self.HYDROQC_CUSTOMER_NUMBER
            ]
            if not customer_research:
                return None
            return customer_research[0]

        if self.HYDROQC_ACCOUNT_ID is not None:
            # TODO DEPRECATED env var
            if len(webuser.customers) <= self.HYDROQC_ACCOUNT_ID:
                return None
            return webuser.customers[self.HYDROQC_ACCOUNT_ID]
        return None

    @typing.no_type_check
    async def test_customer(self, webuser: WebUser, customer: Customer) -> None:
        """Test customer attributes."""
        assert len(webuser.customers) > 0, "No customer found"
        assert (
            webuser.get_customer(customer.customer_id) == customer
        ), "Can not retrieve the same customer with webuser.get_customer method"
        with pytest.raises(HydroQcError):
            webuser.get_customer("bad customer id")
        assert customer.names != "", "Bad customer name"
        assert repr(customer).startswith("<Customer - ")
        assert customer.customer_id is not None, "No customer id found"
        assert customer.infocompte_enabled is None

        await webuser.fetch_customers_info()
        await customer.get_info()
        assert customer.infocompte_enabled in set((True, False))
        if self.HYDROQC_RATE == "M":
            assert customer.infocompte_enabled is False
        else:
            assert customer.infocompte_enabled is True

    @pytest_asyncio.fixture(loop_scope="session", scope="session")
    async def account(self, customer: Customer) -> Account | None:
        """Fixture to keep the account instance through all tests."""
        if len(customer.accounts) <= 0:
            return None
        return customer.accounts[0]

    async def test_account(self, customer: Customer, account: Account) -> None:
        """Test account attributes."""
        assert len(customer.accounts) > 0, "No account found"
        assert account.account_id is not None, "No account id found"
        assert (
            customer.get_account(account.account_id) == account
        ), "Can not retrieve the same account with customer.get_account method"
        with pytest.raises(HydroQcError):
            customer.get_account("bad account id")
        if self.HYDROQC_RATE == "M":
            assert not hasattr(account, "_balance")
        else:
            assert isinstance(account.balance, float)
        assert repr(account).startswith("<Account - ")

    @pytest_asyncio.fixture(loop_scope="session", scope="session")
    async def contract(self, account: Account) -> Contract | None:
        """Fixture to keep the contract instance through all tests."""
        if len(account.contracts) <= 0:
            return None
        return account.contracts[0]

    async def test_contract(self, account: Account, contract: Contract) -> None:
        """Test contract attributes."""
        assert len(account.contracts) > 0, "No contract found"
        assert contract.contract_id is not None, "No contract id found"
        assert (
            account.get_contract(contract.contract_id) == contract
        ), "Can not retrieve the same contract with account.get_contract method"
        with pytest.raises(HydroQcError):
            account.get_contract("bad contract id")
        assert contract.account_id == account.account_id, "Bad account id in contract"
        assert repr(contract).startswith("<Contract - ")
        assert (
            contract.rate == self.HYDROQC_RATE
        ), "Bad rate status, please check the value of self.HYDROQC_RATE"
        if self.HYDROQC_RATE_OPTION != "":
            assert contract.rate_option == (
                self.HYDROQC_RATE_OPTION if self.HYDROQC_RATE_OPTION else ""
            ), "Bad Rate option, please check the value of self.HYDROQC_RATE_OPTION"
            # assert contract.peak_handler.is_enabled == (
            #    self.HYDROQC_RATE_OPTION == "CPC"
            # )

        assert isinstance(contract.start_date, datetime.date)
        assert contract.start_date.year > 1900
        assert isinstance(contract.address, str)
        assert contract.address != ""
        assert isinstance(contract.meter_id, str)
        assert contract.meter_id != ""

    async def test_hourly_consumption(self, contract: Contract) -> None:
        """Test hourly consumption stats."""
        try:
            today_hourly_consumption = await contract.get_today_hourly_consumption()
            assert today_hourly_consumption.get("success") is True
            assert today_hourly_consumption.get("results", {}).get("dateJour") in (
                today_str,
                yesterday_str,
            )
        except HydroQcHTTPError as exp:
            # Failing at the end of the period
            # So we are check if it's the case
            # If not, it's an actual issue
            await contract.get_periods_info()
            if (
                isinstance(contract.cp_current_day, int)
                and isinstance(contract.cp_duration, int)
                and (
                    contract.cp_current_day < 2
                    or contract.cp_duration - contract.cp_current_day < 2
                )
            ):
                # end of period or new period detected
                assert (
                    True
                ), "End of period or new period detected: no hourly consumption"
            else:
                assert (
                    False
                ), "NO End of period or new period detected: error in no hourly consumption"
                raise exp

    async def test_total_hourly_consumption(self, contract: Contract) -> None:
        """Test total hourly consumption stats."""
        hourly_consumption = await contract.get_hourly_consumption(yesterday)
        assert hourly_consumption.get("success") is True
        assert hourly_consumption.get("results", {}).get("dateJour") == yesterday_str

    async def test_daily_consumption(self, contract: Contract) -> None:
        """Test daily consumption stats."""
        daily_consumption = await contract.get_daily_consumption(lastweekday, yesterday)
        assert daily_consumption.get("success") is True
        assert len(daily_consumption.get("results", [])) == 7
        assert (
            daily_consumption["results"][-1]["courant"]["dateJourConso"]
            == lastweekday_str
        )

    async def test_total_daily_consumption(self, contract: Contract) -> None:
        """Test total hourly consumption stats."""
        today_daily_consumption = await contract.get_today_daily_consumption()
        assert today_daily_consumption.get("success") is True

        assert len(today_daily_consumption.get("results", [])) == 1
        assert (
            today_daily_consumption["results"][-1]["courant"]["dateJourConso"]
            == yesterday_str
        )

    @pytest.mark.skipif(
        "config.getoption('--new-contract')",
        reason="No history available on new contracts",
    )
    async def test_monthly_consumption(self, contract: Contract) -> None:
        """Test monthly consumption stats."""
        monthly_consumption = await contract.get_monthly_consumption()
        assert monthly_consumption.get("success") is True
        if await is_young_contract(contract):
            assert len(monthly_consumption.get("results", [])) > 0
            assert len(monthly_consumption.get("results", [])) < 12
        else:
            assert len(monthly_consumption.get("results", [])) == 12

    @pytest.mark.skipif(
        "config.getoption('--new-contract')",
        reason="No history available on new contracts",
    )
    async def test_annual_consumption(self, contract: Contract) -> None:
        """Test annual consumption stats."""
        annual_consumption = await contract.get_annual_consumption()
        assert annual_consumption.get("success") is True

        if await is_young_contract(contract):
            # If the contract is too young (less than 1 year) we don't have any data
            assert len(annual_consumption.get("results", [])) == 0
        else:
            assert len(annual_consumption.get("results", [])) == 1
            try:
                last_period_end_date = datetime.date.fromisoformat(
                    annual_consumption["results"][0]
                    .get("courant", {})
                    .get("dateFinAnnee", "")
                )
            except ValueError:
                assert False, "Can not parse dateFinAnnee in Annual consumption data"

            assert datetime.date.today() - last_period_end_date < datetime.timedelta(
                days=70
            ), "Annual data doesn't seems up-to-date"

    async def test_latest_period(self, contract: Contract) -> None:
        """Test latest_period."""
        if (
            isinstance(contract.cp_current_day, int)
            and isinstance(contract.cp_duration, int)
            and (
                contract.cp_current_day < 2
                or contract.cp_duration - contract.cp_current_day < 2
            )
        ):
            assert contract.cp_current_day is not None
            assert contract.latest_period_info
        else:
            assert (
                contract.cp_current_day is None
            ), "Method get_period_info is already called but it should not"
            assert (
                not contract.latest_period_info
            ), "Method get_period_info is already called but it should not"

    @typing.no_type_check
    async def test_contract_info(self, contract: Contract) -> None:
        """Test contract information validity."""
        await contract.get_periods_info()

        assert (
            contract._hydro_client.selected_customer == contract.customer_id
        ), "Bad selected customer"
        assert (
            contract._hydro_client.selected_contract == contract.contract_id
        ), "Bad selected contract"

        assert len(contract.latest_period_info) > 0, "No period info found"
        assert contract.cp_current_day >= 0
        assert contract.cp_duration >= 0
        assert contract.cp_daily_consumption_mean >= 0
        assert contract.cp_total_consumption >= 0
        assert contract.cp_average_temperature >= -30
        assert contract.cp_average_temperature <= 50
        assert (
            contract.cp_epp_enabled == self.HYDROQC_EPP_ENABLED
        ), "Bad EPP status, please check the value of self.HYDROQC_EPP_ENABLED"

    @pytest_asyncio.fixture(loop_scope="session", scope="session")
    async def cpc(self, contract: Contract) -> CPCPeakHandler | None:
        """Fixture to keep the cpc instance through all tests."""
        if self.HYDROQC_RATE == "D" and self.HYDROQC_RATE_OPTION == "CPC":
            contract_dcpc = typing.cast(ContractDCPC, contract)
            return contract_dcpc.peak_handler
        return None

    async def test_csv(
        self,
        contract: Contract,
    ) -> None:
        """Test CSV downloads."""
        # get_hourly_energy
        data_csv = await contract.get_hourly_energy(yesterday, today)
        first_line = next(data_csv)
        if contract.rate in set(("DT", "DPC")):
            assert first_line == [
                "Contrat",
                "Date et heure",
                "kWh bas",
                "kWh Haut",
                "Code de consommation",
                "Température moyenne (°C)",
                "Code de température",
            ], "Bad get_daily_energy CSV headers"
        else:
            assert first_line == [
                "Contrat",
                "Date et heure",
                "kWh",
                "Code de consommation",
                "Température moyenne (°C)",
                "Code de température",
            ], "Bad get_hourly_energy CSV headers"
        # get_daily_energy
        data_csv = await contract.get_daily_energy(yesterday, today)
        first_line = next(data_csv)
        if contract.rate in set(("DT", "DPC")):
            assert first_line == [
                "Contrat",
                "Tarif",
                "Date",
                "kWh bas",
                "kWh Haut",
                "Code de consommation",
                "Température moyenne (°C)",
                "Code de température",
            ], "Bad get_daily_energy CSV headers"
        else:
            assert first_line == [
                "Contrat",
                "Tarif",
                "Date",
                "kWh",
                "Code de consommation",
                "Température moyenne (°C)",
                "Code de température",
            ], "Bad get_hourly_energy CSV headers"

    async def test_outages(self, contract: Contract) -> None:
        """Test outages."""
        await contract.refresh_outages()

        assert isinstance(contract.outages, list)
        assert contract.next_outage in [None] + contract.outages
