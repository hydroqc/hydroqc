"""Contract D CPC tests module."""

import datetime

import pytest

from hydroqc.account import Account
from hydroqc.contract import ContractDCPC
from hydroqc.peak.cpc.consts import (
    DEFAULT_EVENING_PEAK_END,
    DEFAULT_EVENING_PEAK_START,
    DEFAULT_MORNING_PEAK_END,
    DEFAULT_MORNING_PEAK_START,
)

from .tools import SkipIfBadRate, today, tomorrow, yesterday


@pytest.mark.asyncio(loop_scope="session")
class SubTestContractDCPC:
    """Contract D CPCtests class."""

    # @typing.no_type_check
    # @pytest_asyncio.fixture(loop_scope="module", scope="module")
    # def event_loop(self):
    #    """Override asyncio event_loop to keep the loop running through the whole module."""
    #    print("FFFFFFFFFFFFFFFFFFFFF")
    #    try:
    #        loop = asyncio.get_running_loop()
    #    except RuntimeError:
    #        loop = asyncio.new_event_loop()
    #    print(loop)
    #    yield loop
    #    loop.close()

    @SkipIfBadRate(["D"], "CPC")
    async def test_contract_d_cpc_specific(  # pylint: disable=too-many-statements
        self, account: Account, contract: ContractDCPC
    ) -> None:
        """Test total hourly consumption stats."""
        assert contract.rate == "D"
        assert contract.rate_option == "CPC"

        assert (
            isinstance(contract.cp_projected_bill, float)
            and contract.cp_projected_bill >= 0
        )
        if contract.cp_current_bill is not None:
            # Data could be None if HQ is not able to return a negative value
            # Because of too much WC cumulated
            assert (
                isinstance(contract.cp_current_bill, float)
                and contract.cp_current_bill >= 0
            )
            # Data could be None if HQ is not able to return a negative value
            # Because of too much WC cumulated
            assert (
                isinstance(contract.cp_daily_bill_mean, float)
                and contract.cp_daily_bill_mean >= 0
            )
            assert (
                isinstance(contract.cp_kwh_cost_mean, float)
                and contract.cp_kwh_cost_mean >= 0
            )
        assert (
            isinstance(contract.cp_projected_total_consumption, int)
            and contract.cp_projected_total_consumption >= 0
        )

        contract.set_preheat_duration(60)
        peak_handler = contract.peak_handler
        assert peak_handler.applicant_id == contract.applicant_id
        assert peak_handler.customer_id == account.customer_id
        assert peak_handler.contract_id == contract.contract_id

        assert (
            len(peak_handler.raw_data) == 0
        ), "Raw data should be empty, data not fetched yet"

        await peak_handler.refresh_open_data()

        if peak_handler.raw_open_data:
            assert peak_handler.raw_open_data[0]["offre"] == peak_handler.offer_code

        await peak_handler.refresh_data()

        assert peak_handler.winter_start_date.month == 12
        assert peak_handler.winter_start_date.day == 1
        assert peak_handler.winter_end_date.month == 3
        assert peak_handler.winter_end_date.day == 31
        assert len(peak_handler.peaks) > 0
        assert len(peak_handler.sonic) > 0
        assert len(peak_handler.critical_peaks) >= 0
        assert len(peak_handler.critical_peaks) <= len(peak_handler.peaks)
        assert peak_handler.cumulated_credit >= 0
        assert peak_handler.projected_cumulated_credit >= 0
        assert peak_handler.projected_cumulated_credit >= peak_handler.cumulated_credit
        assert peak_handler.cumulated_critical_hours >= 0
        assert peak_handler.cumulated_curtailed_energy >= 0

        if (
            today <= peak_handler.winter_start_date.date()
            or today > peak_handler.winter_end_date.date()
        ):
            assert peak_handler.current_peak is None
        else:
            # We are in winter
            now = datetime.datetime.now().time()
            if (DEFAULT_MORNING_PEAK_START <= now <= DEFAULT_MORNING_PEAK_END) or (
                DEFAULT_EVENING_PEAK_START <= now <= DEFAULT_EVENING_PEAK_END
            ):
                assert (
                    peak_handler.current_peak is not None
                    and peak_handler.current_peak.start_date
                    in [p.start_date for p in peak_handler.peaks]
                )
            else:
                assert peak_handler.current_peak is None
        assert peak_handler.current_peak_is_critical in {None, True, False}
        assert peak_handler.current_state in {
            "critical_anchor",
            "anchor",
            "critical_peak",
            "peak",
            "normal",
        }

        assert isinstance(peak_handler.preheat_in_progress, bool)
        assert isinstance(peak_handler.is_any_critical_peak_coming, bool)
        assert isinstance(peak_handler.next_peak_is_critical, bool)
        if peak_handler.next_critical_peak is not None:
            assert peak_handler.next_critical_peak.start_date in [
                p.start_date for p in peak_handler.critical_peaks
            ]
        else:
            assert peak_handler.next_critical_peak is None

        if not peak_handler.is_winter:
            assert peak_handler.today_morning_peak is None
            assert peak_handler.today_evening_peak is None
            assert peak_handler.next_anchor is None
        else:
            # We are in winter
            assert (
                peak_handler.today_morning_peak is not None
                and peak_handler.today_morning_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
            )
            assert (
                peak_handler.today_evening_peak is not None
                and peak_handler.today_evening_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
            )
            if today != datetime.date(2023, 3, 31):
                assert (
                    peak_handler.next_anchor is not None
                    and peak_handler.next_anchor.start_date
                    in [p.anchor.start_date for p in peak_handler.peaks]
                )
        if (
            tomorrow < peak_handler.winter_start_date.date()
            or tomorrow > peak_handler.winter_end_date.date()
        ):
            assert peak_handler.tomorrow_morning_peak is None
            assert peak_handler.tomorrow_evening_peak is None
        else:
            # We are in winter
            assert (
                peak_handler.tomorrow_morning_peak is not None
                and peak_handler.tomorrow_morning_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
            )
            assert (
                peak_handler.tomorrow_evening_peak is not None
                and peak_handler.tomorrow_evening_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
            )
        if (
            yesterday < peak_handler.winter_start_date.date()
            or yesterday > peak_handler.winter_end_date.date()
        ):
            assert peak_handler.yesterday_morning_peak is None
            assert peak_handler.yesterday_evening_peak is None
        else:
            # We are in winter
            assert (
                peak_handler.yesterday_morning_peak is not None
                and peak_handler.yesterday_morning_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
            )
            assert (
                peak_handler.yesterday_evening_peak is not None
                and peak_handler.yesterday_evening_peak.start_date
                in [p.start_date for p in peak_handler.peaks]
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
