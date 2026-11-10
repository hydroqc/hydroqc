"""Contract DT tests module."""

from types import NoneType

import pytest

from hydroqc.contract import ContractDT

from .tools import SkipIfBadRate, today, yesterday


@pytest.mark.asyncio(loop_scope="session")
class SubTestContractDT:
    """Contract DT tests class."""

    @SkipIfBadRate(["DT"], "")
    async def test_contract_dt_specific(self, contract: ContractDT) -> None:
        """Test total hourly consumption stats."""
        assert contract.rate == "DT"
        assert contract.rate_option == ""

        if (
            isinstance(contract.cp_current_day, int)
            and isinstance(contract.cp_duration, int)
            and (
                contract.cp_current_day < 2
                or contract.cp_duration - contract.cp_current_day < 2
            )
        ):
            assert isinstance(contract.cp_current_bill, (float, NoneType))
            assert isinstance(contract.cp_projected_bill, (float, NoneType))
            assert isinstance(contract.cp_daily_bill_mean, (float, NoneType))
            assert isinstance(contract.cp_projected_total_consumption, (int, NoneType))
            assert isinstance(contract.cp_kwh_cost_mean, (float, NoneType))
            assert isinstance(contract.cp_lower_price_consumption, (int, NoneType))
            assert isinstance(contract.cp_higher_price_consumption, (int, NoneType))
        else:
            assert (
                isinstance(contract.cp_current_bill, float)
                and contract.cp_current_bill >= 0
            )
            assert (
                isinstance(contract.cp_projected_bill, float)
                and contract.cp_projected_bill >= 0
            )
            assert (
                isinstance(contract.cp_daily_bill_mean, float)
                and contract.cp_daily_bill_mean >= 0
            )
            assert (
                isinstance(contract.cp_projected_total_consumption, int)
                and contract.cp_projected_total_consumption >= 0
            )
            assert (
                isinstance(contract.cp_kwh_cost_mean, float)
                and contract.cp_kwh_cost_mean >= 0
            )

            assert (
                isinstance(contract.cp_lower_price_consumption, int)
                and contract.cp_lower_price_consumption >= 0
            )
            assert (
                isinstance(contract.cp_higher_price_consumption, int)
                and contract.cp_higher_price_consumption >= 0
            )

        assert isinstance(contract.amount_saved_vs_base_rate, float)

        # get_hourly_energy
        data_csv = await contract.get_hourly_energy(yesterday, today)
        first_line = next(data_csv)
        assert first_line == [
            "Contrat",
            "Date et heure",
            "kWh bas",
            "kWh Haut",
            "Code de consommation",
            "Température moyenne (°C)",
            "Code de température",
        ], "Bad get_daily_energy CSV headers"

        # get_daily_energy
        data_csv = await contract.get_daily_energy(yesterday, today)
        first_line = next(data_csv)
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

        # get_consumption_overview_csv
        data_csv = await contract.get_consumption_overview_csv()
        first_line = next(data_csv)
        assert first_line == [
            "Contract",
            "Rate",
            "Starting date",
            "Ending date",
            "Day",
            "Date and time of last reading",
            "kWh",
            "Amount ($)",
            "Meter-reading code",
            "Average $/day",
            "Average kWh/day",
            "kWh anticipated",
            "Amount anticipated ($)",
            "Average temperature (°C)",
        ], "Bad get_consumption_overview_csv CSV headers"
