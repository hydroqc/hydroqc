"""Contract D tests module."""

import pytest

from hydroqc.contract import ContractD

from .tools import SkipIfBadRate, today, yesterday


@pytest.mark.asyncio(loop_scope="session")
class SubTestContractD:
    """Contract D tests class."""

    @SkipIfBadRate(["D"], "")
    async def test_contract_d_specific(self, contract: ContractD) -> None:
        """Test total hourly consumption stats."""
        assert contract.rate == "D"
        assert contract.rate_option == ""

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

        # get_hourly_energy
        data_csv = await contract.get_hourly_energy(yesterday, today)
        first_line = next(data_csv)
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
        assert first_line == [
            "Contrat",
            "Tarif",
            "Date",
            "kWh",
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
