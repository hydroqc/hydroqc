"""Contract M tests module."""

import pytest

from hydroqc.contract import ContractM

from .tools import SkipIfBadRate, today, yesterday


@pytest.mark.asyncio(loop_scope="session")
class SubTestContractM:
    """Contract M tests module."""

    @SkipIfBadRate(["M"], "")
    async def test_contract_m_specific(self, contract: ContractM) -> None:
        """Test total hourly consumption stats."""
        assert contract.rate == "M"
        assert contract.rate_option == ""

        assert (
            isinstance(contract.cp_current_bill, float)
            and contract.cp_current_bill >= 0
        )
        assert (
            isinstance(contract.cp_daily_bill_mean, float)
            and contract.cp_daily_bill_mean >= 0
        )
        assert (
            isinstance(contract.cp_kwh_cost_mean, float)
            and contract.cp_kwh_cost_mean >= 0
        )
        assert contract.cp_projected_bill is None
        assert contract.cp_projected_total_consumption is None

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

        # get_power_demand_per_15min
        data_csv = await contract.get_power_demand_per_15min(yesterday, today)
        first_line = next(data_csv)
        assert first_line == [
            "Contrat",
            "Date et heure",
            "Puissance réelle (kW)",
            "Code de puissance réelle",
            "90 % de la puissance apparente (kVA)",
            "Code de puissance apparente",
        ], "Bad get_power_demand_per_15min headers"

        # get_daily_energy_and_power
        data_csv = await contract.get_daily_energy_and_power(yesterday, today)
        first_line = next(data_csv)
        assert first_line == [
            "Contrat",
            "Tarif",
            "Date de consommation",
            "kWh",
            "Code de consommation",
            "Puissance réelle (kW)",
            "Code de puissance réelle",
            "Heure ? Puissance maximale réelle",
            "90 % de la puissance apparente (kVA)",
            "Code de puissance apparente",
            "Heure ? Puissance apparente maximale ",
            "Température moyenne (°C)",
            "Code de température",
        ], "Bad get_daily_energy_and_power CSV headers"

        # get_consumption_overview_csv
        data_csv = await contract.get_consumption_overview_csv()
        first_line = next(data_csv)
        assert first_line == [
            "Contract",
            "Rate",
            "Starting date",
            "Ending date",
            "Day(s)",
            "Date and time of last reading",
            "kWh",
            "Amount ($)",
            "Meter-reading code",
            "Average $/day",
            "Average ¢/kWh",
            "Billing demand",
            "Real power (kW)",
            "Date and time of maximum real power ",
            "Apparent (90%) (kVA)",
            "Date and time of maximum apparent power demand",
            "Minimum billing demand  (MBD)",
            "Minimum billing demand (MBD) period ",
            "Power factor (or PF (%)",
            "Load factor (or LF (%)",
            "Mean temperature (°C)",
        ]
