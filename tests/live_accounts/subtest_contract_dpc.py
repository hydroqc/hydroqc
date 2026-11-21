"""Contract DPC tests module."""

import datetime

import pytest

from hydroqc.contract import ContractDPC

from .tools import SkipIfBadRate, today, yesterday


@pytest.mark.asyncio(loop_scope="session")
class SubTestContractDPC:  # pylint: disable=too-many-statements
    """Contract DPC tests class."""

    @SkipIfBadRate(["DPC"], "")
    async def test_contract_dpc_specific(self, contract: ContractDPC) -> None:
        """Test total hourly consumption stats."""
        assert contract.rate == "DPC"
        assert contract.rate_option == ""

        contract.set_preheat_duration(60)

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
            isinstance(contract.cp_lower_price_consumption, float)
            and contract.cp_lower_price_consumption >= 0
        )
        assert (
            isinstance(contract.cp_higher_price_consumption, float)
            and contract.cp_higher_price_consumption >= 0
        )

        await contract.get_dpc_data()

        contract.set_preheat_duration(10)
        assert isinstance(contract.peak_handler.winter_start_date, datetime.datetime)

        assert isinstance(contract.peak_handler.winter_end_date, datetime.datetime)

        assert isinstance(contract.last_update_date, datetime.date)

        assert isinstance(contract.critical_called_hours, int)
        assert contract.critical_called_hours >= 0

        assert isinstance(contract.max_critical_called_hours, int)
        assert contract.max_critical_called_hours >= 0

        assert contract.max_critical_called_hours >= contract.critical_called_hours

        if contract.amount_saved_vs_base_rate is not None:
            assert isinstance(contract.amount_saved_vs_base_rate, float)

        assert isinstance(contract.winter_total_days, int)
        assert contract.winter_total_days >= 0
        assert isinstance(contract.winter_total_days_last_update, int)
        assert contract.winter_total_days_last_update >= 0
        assert contract.winter_total_days_last_update <= contract.winter_total_days

        assert isinstance(contract.winter_state, str)

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
        ], "Bad get_hourly_energy CSV headers"

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

        # Get dpc specific data
        await contract.peak_handler.refresh_open_data()
        if contract.peak_handler.raw_open_data:
            assert (
                contract.peak_handler.raw_open_data[0]["offre"]
                == contract.peak_handler.offer_code
            )

        await contract.peak_handler.refresh_data()

        assert isinstance(contract.peak_handler.raw_data, list)
        assert contract.peak_handler.winter_start_date.day == 1
        assert contract.peak_handler.winter_start_date.month == 12
        assert contract.peak_handler.winter_end_date.day == 31
        assert contract.peak_handler.winter_end_date.month == 3
        assert isinstance(contract.peak_handler.peaks, list)
        # DPC can have many peaks throughout the winter season
        assert len(contract.peak_handler.peaks) >= 0
        assert (
            contract.peak_handler.current_peak in [None] + contract.peak_handler.peaks
        )
        assert (
            contract.peak_handler.current_state == "peak"
            if contract.peak_handler.current_peak
            else "normal"
        )
        assert contract.peak_handler.preheat_in_progress in {True, False}

        if contract.peak_handler.next_peak is not None:
            assert contract.peak_handler.next_peak.start_date in [
                p.start_date for p in contract.peak_handler.peaks
            ]
        else:
            assert contract.peak_handler.next_peak is None

        if contract.peak_handler.today_morning_peak is not None:
            assert contract.peak_handler.today_morning_peak.start_date in [
                p.start_date for p in contract.peak_handler.peaks
            ]
        else:
            assert contract.peak_handler.today_morning_peak is None

        if contract.peak_handler.today_evening_peak is not None:
            assert contract.peak_handler.today_evening_peak.start_date in [
                p.start_date for p in contract.peak_handler.peaks
            ]
        else:
            assert contract.peak_handler.today_evening_peak is None

        if contract.peak_handler.tomorrow_morning_peak is not None:
            assert contract.peak_handler.tomorrow_morning_peak.start_date in [
                p.start_date for p in contract.peak_handler.peaks
            ]
        else:
            assert contract.peak_handler.tomorrow_morning_peak is None

        if contract.peak_handler.tomorrow_evening_peak is not None:
            assert contract.peak_handler.tomorrow_evening_peak.start_date in [
                p.start_date for p in contract.peak_handler.peaks
            ]
        else:
            assert contract.peak_handler.tomorrow_evening_peak is None
