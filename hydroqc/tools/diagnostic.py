"""Diagnostic module to help users to send debug to devs."""

import argparse
import asyncio
import copy
import datetime
import io
import logging
import os
import pathlib
import shutil
import traceback
import zipfile
from collections.abc import Iterator
from typing import cast

import pytz
from dateutil.relativedelta import relativedelta

from hydroqc.contract import ContractDCPC, ContractDPC, ContractM
from hydroqc.error import HydroQcError
from hydroqc.logger import get_logger
from hydroqc.public_client import PublicClient
from hydroqc.webuser import WebUser

LOG_LEVEL = (
    "CRITICAL",
    "ERROR",
    "WARNING",
    "INFO",
    "DEBUG",
)
TZ_EASTERN = pytz.timezone("US/Eastern")


class Diagnostic:
    """Diagnostic object."""

    def __init__(self, cli_settings: argparse.Namespace):
        """Contructor."""
        # Get base settings
        self.diagnostic_folder = cli_settings.output_folder
        self.diagnostic_folder_public = os.path.join(self.diagnostic_folder, "public")
        # Prepare output folder
        if os.path.exists(self.diagnostic_folder):
            shutil.rmtree(self.diagnostic_folder)
        os.makedirs(self.diagnostic_folder)
        os.makedirs(self.diagnostic_folder_public)
        # Prepare logger
        self._log_file = os.path.join(self.diagnostic_folder, "diagnostic.log")
        log_level = cli_settings.log_level
        self.logger = get_logger("diagnostic", log_level=log_level)
        file_handler = logging.FileHandler(self._log_file)
        file_handler.setFormatter(self.logger.handlers[0].formatter)
        file_handler.setLevel(self.logger.level)
        self.logger.addHandler(file_handler)
        self.logger_public = self.logger.getChild("public")
        http_log_level = cli_settings.http_log_level

        # Get settings
        username = cli_settings.username
        password = cli_settings.password
        self.customer_id = cli_settings.customer
        self.account_id = cli_settings.account
        self.contract_id = cli_settings.contract
        # Create webuser
        self.webuser = WebUser(
            username,
            password,
            verify_ssl=True,
            log_level=log_level,
            http_log_level=http_log_level,
            log_file=self._log_file,
            diag_folder=self.diagnostic_folder,
        )
        self.public_client = PublicClient(
            rate_code="DPC",
            # rate_option_code="CPC",
            verify_ssl=True,
            log_level=log_level,
            http_log_level=http_log_level,
            log_file=self._log_file,
            diag_folder=self.diagnostic_folder_public,
        )

    def zip(self) -> None:
        """Archive all generated files."""
        directory = pathlib.Path(self.diagnostic_folder)
        with zipfile.ZipFile("diagnostic.zip", mode="w") as archive:
            for file_path in directory.rglob("*"):
                archive.write(
                    file_path,
                    arcname=os.path.join(
                        "diagnostic", file_path.relative_to(directory)
                    ),
                )

    @property
    def diag_id(self) -> int:
        """Get diagnostic http ID."""
        return self.webuser._hydro_client._diag_id

    @property
    def public_diag_id(self) -> int:
        """Get diagnostic http ID."""
        return self.public_client._hydro_client._diag_id

    async def run(self) -> None:  # pylint: disable=too-many-statements
        """Run diagnostic."""
        today = datetime.date.today()
        day_1 = today - datetime.timedelta(days=1)
        day_2 = today - datetime.timedelta(days=2)

        try:
            self.logger_public.info(
                "%02d - Checking HQ portal status", self.public_diag_id
            )
            hq_status = await self.public_client.check_hq_portal_status()
            self.logger_public.info(
                "%02d - HQ portal available: %s", self.public_diag_id - 1, hq_status
            )

            self.logger_public.info(
                "%02d - Fetch peak using open data", self.public_diag_id
            )
            await self.public_client.fetch_peak_data()
            self.logger_public.info(
                "%02d - Peak found: %s",
                self.public_diag_id - 1,
                len(self.public_client.peaks),
            )

            self.logger.info("%02d - Checking HQ portal status", self.diag_id)
            hq_status = await self.webuser.check_hq_portal_status()
            self.logger.info(
                "%02d - HQ portal available: %s", self.diag_id - 1, hq_status
            )

            self.logger.info("%02d - Trying to login", self.diag_id)
            login_result = await self.webuser.login()
            self.logger.info("%02d - Login result: %s", self.diag_id - 1, login_result)

            # WebUser
            self.logger.info("%02d - Trying to get web user info", self.diag_id)
            await self.webuser.get_info()
            self.logger.info("%02d - Got Web User info", self.diag_id - 1)

            self.logger.info("%02d - Trying to get customers", self.diag_id)
            await self.webuser.fetch_customers_info()
            self.logger.info(
                "%02d - Found %d customers",
                self.diag_id - 1,
                len(self.webuser.customers),
            )

            # Customer
            self.logger.info("Checking if selected customer is present")
            try:
                self.customer = self.webuser.get_customer(self.customer_id)
            except HydroQcError:
                self.logger.error("Selected customer %s not found", self.customer_id)
                return
            self.logger.info("Selected customer is present")

            # Account
            self.logger.info("Checking if selected account is present")
            try:
                self.account = self.customer.get_account(self.account_id)
            except HydroQcError:
                self.logger.error("Selected account %s not found", self.account_id)
                return
            self.logger.info("Selected account is present")

            # Contract
            self.logger.info("Checking if selected contract is present")
            try:
                self.contract = self.account.get_contract(self.contract_id)
            except HydroQcError:
                self.logger.error("Selected contract %s not found", self.contract_id)
                return
            self.logger.info("Selected contract is present")

            # Outages
            self.logger.info(
                "%02d - Checking if fetching outages is working", self.diag_id
            )
            await self.contract.refresh_outages()
            self.logger.info("%02d - Fetching outages is working", self.diag_id - 1)
            if self.contract.next_outage:
                self.logger.info(
                    "Next outage start: %s", self.contract.next_outage.start_date
                )
                self.logger.info(
                    "Next outage end: %s", self.contract.next_outage.end_date
                )
                self.logger.info(
                    "Next outage cause: %s", self.contract.next_outage.cause
                )
                self.logger.info(
                    "Next outage status: %s", self.contract.next_outage.status
                )
                self.logger.info(
                    "Next outage emergency: %s",
                    self.contract.next_outage.emergency_level,
                )

            # Period
            self.logger.info("%02d - Trying to get period info", self.diag_id)
            await self.contract.get_periods_info()
            self.logger.info("%02d - Got period info", self.diag_id - 1)

            # Hourly yesterday consumption
            self.logger.info(
                "%02d - Trying to get yesterday hourly consumption", self.diag_id
            )
            await self.contract.get_hourly_consumption(day_1)
            self.logger.info(
                "%02d - Got yesterday hourly consumption", self.diag_id - 1
            )

            # Hourly today consumption
            self.logger.info(
                "%02d - Trying to get today hourly consumption", self.diag_id
            )
            await self.contract.get_today_hourly_consumption()
            self.logger.info("%02d - Got today hourly consumption", self.diag_id - 1)

            # Daily consumption
            self.logger.info("%02d - Trying to get daily consumption", self.diag_id)
            await self.contract.get_today_daily_consumption()
            self.logger.info("%02d - Got daily consumption", self.diag_id - 1)

            # get_monthly_consumption
            self.logger.info("%02d - Trying to get monthly consumption", self.diag_id)
            await self.contract.get_monthly_consumption()
            self.logger.info("%02d - Got monthly consumption", self.diag_id - 1)

            # get_annual_consumption
            self.logger.info("%02d - Trying to get annual consumption", self.diag_id)
            await self.contract.get_annual_consumption()
            self.logger.info("%02d - Got annual consumption", self.diag_id - 1)

            # get_daily_energy
            # TODO get data 2 years ago
            self.logger.info("%02d - Trying to get csv get_daily_energy", self.diag_id)
            await self.contract.get_daily_energy(day_2, day_1)
            self.logger.info("%02d - Got csv get_daily_energy", self.diag_id - 1)

            # get_hourly_energy
            today = datetime.date.today()
            oldest_data_date = today - relativedelta(days=731)
            if self.contract.start_date is not None:
                contract_start_date = datetime.date.fromisoformat(
                    str(self.contract.start_date)
                )
                # Get the youngest date between contract start date VS 2 years ago
                start_date = (
                    oldest_data_date
                    if contract_start_date < oldest_data_date
                    else contract_start_date
                )
            else:
                start_date = oldest_data_date
            data_date = copy.copy(start_date)
            while data_date < today:
                self.logger.info(
                    "%02d - Trying to get csv %s get_hourly_energy",
                    self.diag_id,
                    data_date,
                )
                raw_data = cast(
                    Iterator[list[str | int | float]],
                    await self.contract.get_hourly_energy(data_date, today),
                )
                self.logger.info(
                    "%02d - Got csv %s get_hourly_energy",
                    self.diag_id - 1,
                    data_date.isoformat(),
                )
                # Get date
                raw_data_sorted = list(raw_data)
                date_str = cast(str, raw_data_sorted[1][1])
                data_datetime = TZ_EASTERN.localize(
                    datetime.datetime.fromisoformat(date_str)
                )
                data_date = data_datetime.date() + datetime.timedelta(days=1)

            # get_consumption_overview_csv
            self.logger.info(
                "%02d - Trying to get csv get_consumption_overview_csv",
                self.diag_id,
            )
            await self.contract.get_consumption_overview_csv()
            self.logger.info(
                "%02d - Got csv get_consumption_overview_csv", self.diag_id - 1
            )

            # Contract Specific calls
            # CPC/Winter Credit
            if self.contract.rate == "D" and self.contract.rate_option == "CPC":
                self.logger.info("%02d - Trying to get cpc data", self.diag_id)
                contract_dcpc = cast(ContractDCPC, self.contract)
                await contract_dcpc.peak_handler.refresh_data()
                self.logger.info("%02d - Got cpc data", self.diag_id - 1)

                self.logger.info("%02d - Trying to get opn data", self.diag_id)
                await contract_dcpc.peak_handler.refresh_open_data()
                self.logger.info("%02d - Got open data", self.diag_id - 1)

            # DCP/FlexD
            elif self.contract.rate == "DPC":
                self.logger.info("%02d - Trying to get flexD data", self.diag_id)
                contract_dpc = cast(ContractDPC, self.contract)
                await contract_dpc.get_dpc_data()
                self.logger.info("%02d - Got flexD data", self.diag_id - 1)
                low_price = contract_dpc.cp_lower_price_consumption
                self.logger.info(
                    "%02d - Lower price consumption value: %s",
                    self.diag_id - 1,
                    low_price,
                )
                high_price = contract_dpc.cp_higher_price_consumption
                self.logger.info(
                    "%02d - Higher price consumption value: %s",
                    self.diag_id - 1,
                    high_price,
                )
                self.logger.info("%02d - Trying to get flexD data", self.diag_id)
                await contract_dpc.peak_handler.refresh_data()
                self.logger.info("%02d - Got flexD data", self.diag_id - 1)

                self.logger.info("%02d - Trying to get opn data", self.diag_id)
                await contract_dcpc.peak_handler.refresh_open_data()
                self.logger.info("%02d - Got open data", self.diag_id - 1)

            # DT
            if self.contract.rate == "DT":
                low_price = contract_dpc.cp_lower_price_consumption
                self.logger.info(
                    "%02d - Lower price consumption value: %s",
                    self.diag_id - 1,
                    low_price,
                )
                high_price = contract_dpc.cp_higher_price_consumption
                self.logger.info(
                    "%02d - Higher price consumption value: %s",
                    self.diag_id - 1,
                    high_price,
                )

            elif self.contract.rate == "M":
                contract_m = cast(ContractM, self.contract)
                # get_daily_energy_and_power
                self.logger.info(
                    "%02d - Trying to get csv daily_energy_and_power", self.diag_id
                )
                await contract_m.get_daily_energy_and_power(day_2, day_1)
                self.logger.info(
                    "%02d - Got csv daily_energy_and_power", self.diag_id - 1
                )
                # get_power_demand_per_15min
                self.logger.info(
                    "%02d - Trying to get csv get_power_demand_per_15min", self.diag_id
                )
                await contract_m.get_power_demand_per_15min(day_2, day_1)
                self.logger.info(
                    "%02d - Got csv get_power_demand_per_15min", self.diag_id - 1
                )

        except BaseException as exp:
            self.logger.error(exp)
            self.logger.error("Traceback will follow")
            fhtb = io.StringIO()
            traceback.print_exception(exp, file=fhtb)
            fhtb.seek(0)
            self.logger.error(fhtb.read())
        finally:
            await self.webuser.close_session()
            await self.public_client.close_session()


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    description = (
        """HydroQC lib diagnostic tool to help for debugging.\n"""
        """The command will run some http calls to the Hydro-Québec website, """
        """and store all of queries and results to the output folder and """
        """generate a zip file.\n"""
        """This tools is useful for helping HydroQC lib developers to debug issues"""
        """with your account.\n"""
        """WARNING: SOME PERSONAL INFORMATION ARE STORED in the files in the """
        """output folder.\n"""
        """         DO NOT SHARE it with people that you don't trust.\n"""
        """         The username and password are not stored in the zip file."""
    )
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=description,
        epilog="Hydroqc-diag is part of HydroQC lib - https://hydroqc.ca",
    )
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("-c", "--customer", required=True)
    parser.add_argument("-a", "--account", required=True)
    parser.add_argument("-C", "--contract", required=True)
    parser.add_argument(
        "-o",
        "--output-folder",
        default="diagnostic_output",
        help="Output folder path. Default: diagnostic_output",
    )
    parser.add_argument(
        "-l", "--log-level", default="INFO", choices=LOG_LEVEL, help="Default: INFO"
    )
    parser.add_argument(
        "-L",
        "--http-log-level",
        default="ERROR",
        choices=LOG_LEVEL,
        help="Default: ERROR",
    )

    return parser.parse_args()


def main() -> None:
    """Run diagnostic."""
    cli_settings = parse_args()

    diag = Diagnostic(cli_settings)

    # Fetch data
    asyncio.run(diag.run())
    # zip
    diag.zip()


if __name__ == "__main__":
    main()
