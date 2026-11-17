"""Hydroquebec contract module."""

from collections.abc import Callable
from datetime import date, datetime
from enum import Enum

from hydroqc.contract.common import check_period_data_present
from hydroqc.contract.contract_residential import ContractResidential
from hydroqc.hydro_api.client import HydroClient
from hydroqc.peak.dpc.handler import DPCPeakHandler
from hydroqc.types import DPCDataTyping


class WinterState(str, Enum):
    """Winter period state."""

    CURRENT = "C"  # Current/Active (within winter period)
    FINISHED = "F"  # Finished (past winter period)


def check_dpc_data_present(
    method: Callable[..., None | str | bool | float | int | date | WinterState],
) -> Callable[..., None | str | bool | float | int | date | WinterState]:
    """Check if contractDPC's data are present.

    Returns None if data is not available or incomplete.
    """

    def wrapper(
        contract: "ContractDPC",
    ) -> None | str | bool | float | int | date | WinterState:
        if not hasattr(contract, "_dpc_data"):
            contract._logger.warning("You need to call get_dpc_data method first")
            return None

        if not contract._dpc_data:
            contract._logger.info(
                "It seems Hydro-Québec didn't provided some data. "
                "Maybe you did a rate change recently. "
                "This message should disappear at the beginning of the next bill period."
            )
            return None

        # Check if winter period data is available
        if (
            "periodesHiver" not in contract._dpc_data
            or not contract._dpc_data.get("periodesHiver")
            or len(contract._dpc_data["periodesHiver"]) == 0
        ):
            contract._logger.info(
                "DPC data is empty or incomplete. "
                "This may occur outside the winter period or during a rate transition."
            )
            return None

        return method(contract)

    return wrapper


class ContractDPC(ContractResidential):
    """Hydroquebec contract.

    Represents a FlexD contract (contrat)
    """

    _rate_code = "DPC"
    _rate_option_code = ""
    _dpc_data: DPCDataTyping

    def __init__(
        self,
        applicant_id: str,
        customer_id: str,
        *,
        account_id: str,
        contract_id: str,
        hydro_client: HydroClient,
        log_level: str | None = None,
    ):
        """Create a new Hydroquebec contract."""
        ContractResidential.__init__(
            self,
            applicant_id,
            customer_id,
            account_id=account_id,
            contract_id=contract_id,
            hydro_client=hydro_client,
            log_level=log_level,
        )
        peaks_logger = self._logger.getChild("peaks")
        self._peakh: DPCPeakHandler = DPCPeakHandler(
            self.applicant_id,
            self.customer_id,
            contract_id=self.contract_id,
            hydro_client=hydro_client,
            logger=peaks_logger,
        )

    @property
    def peak_handler(self) -> DPCPeakHandler:
        """Get peak handler object."""
        return self._peakh

    def set_preheat_duration(self, duration: int) -> None:
        """Set preheat duration in minutes."""
        self._peakh.set_preheat_duration(duration)

    async def get_dpc_data(self) -> DPCDataTyping:
        """Fetch FlexD data."""
        self._dpc_data = await self._hydro_client.get_dpc_data(
            self.applicant_id, self.customer_id, self.contract_id
        )
        return self._dpc_data

    @property
    @check_period_data_present
    def cp_lower_price_consumption(self) -> float:
        """Total lower priced consumption since the current period started."""
        return float(
            self._all_period_data[0]["consoTotalPeriode"]
            - self._all_period_data[0]["nbKwhConsoHautTarifFlexPeriode"]
        )

    @property
    @check_period_data_present
    def cp_higher_price_consumption_cost(self) -> float:
        """Total cost of critical peak consumption since the current period started."""
        return self._all_period_data[0]["montantVentePointeCritique"]

    @property
    @check_period_data_present
    def cp_higher_price_consumption(self) -> float:
        """Total higher priced consumption since the current period started."""
        return float(self._all_period_data[0]["nbKwhConsoHautTarifFlexPeriode"])

    @property
    @check_dpc_data_present
    def last_update_date(self) -> date:
        """DPC data last update."""
        date_str = str(self._dpc_data["periodesHiver"][0]["dateMaj"])
        # Handle ISO format with timezone
        if "T" in date_str:
            return date.fromisoformat(date_str.split("T", maxsplit=1)[0])
        return date.fromisoformat(date_str)

    @property
    @check_dpc_data_present
    def critical_called_hours(self) -> int:
        """Get number of critical hours.

        Returns 0 for current ongoing winter period where hours haven't been called yet.
        """
        return int(self._dpc_data["periodesHiver"][0].get("hrsCritiquesAppelees", 0))

    @property
    @check_dpc_data_present
    def max_critical_called_hours(self) -> int | None:
        """Get max number of critical hours.

        Returns None when Hydro-Québec has not published the field yet, which
        happens for the upcoming winter period before it starts: the key is
        optional in the API payload and must not raise KeyError.
        """
        winter = self._dpc_data["periodesHiver"][0]
        max_hours = winter.get("hrsCritiquesAppeleesMax")
        if max_hours is None:
            self._logger.debug(
                "No maximum critical hours in DPC data; it is probably not "
                "published yet for the current winter period"
            )
            return None
        return int(max_hours)

    @property
    @check_dpc_data_present
    def amount_saved_vs_base_rate(self) -> float | None:
        """Amount saved or not versus the base rate.

        Returns None if comparison data is not yet available (e.g., first year on Flex D).
        """
        if (
            self._dpc_data["periodesHiver"][0].get("montantEconPerteVSTarifBase")
            is None
        ):
            return None
        return float(self._dpc_data["periodesHiver"][0]["montantEconPerteVSTarifBase"])

    @property
    @check_dpc_data_present
    def winter_total_days(self) -> int:
        """Get number of winter days (calculated from period dates)."""
        winter = self._dpc_data["periodesHiver"][0]
        start = datetime.fromisoformat(winter["dateDebut"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(winter["dateFin"].replace("Z", "+00:00"))
        return (end - start).days

    @property
    @check_dpc_data_present
    def winter_total_days_last_update(self) -> int:
        """Get number of days since winter starts (calculated from dates).

        Returns 0 if last update is before winter start (pre-season setup).
        """
        winter = self._dpc_data["periodesHiver"][0]
        start = datetime.fromisoformat(winter["dateDebut"].replace("Z", "+00:00"))
        update = datetime.fromisoformat(winter["dateMaj"].replace("Z", "+00:00"))
        return max(0, (update - start).days)

    @property
    @check_dpc_data_present
    def winter_state(self) -> WinterState:
        """Get winter state (computed from dates).

        Returns:
            WinterState.CURRENT: Within winter period
            WinterState.FINISHED: Past winter period
        """
        winter = self._dpc_data["periodesHiver"][0]
        now = datetime.now(
            datetime.fromisoformat(winter["dateDebut"].replace("Z", "+00:00")).tzinfo
        )
        start = datetime.fromisoformat(winter["dateDebut"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(winter["dateFin"].replace("Z", "+00:00"))
        if start <= now <= end:
            return WinterState.CURRENT
        return WinterState.FINISHED
