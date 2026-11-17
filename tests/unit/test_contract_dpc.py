"""Offline tests for Flex D (DPC) contract payload handling."""

from __future__ import annotations

from typing import cast

from hydroqc.contract import ContractDPC
from hydroqc.hydro_api.client import HydroClient
from hydroqc.types import DPCDataTyping
from hydroqc.types.dpc import DPCPeriodeHiverTyping


def build_contract(dpc_data: DPCDataTyping) -> ContractDPC:
    """Build a DPC contract with a given payload, without touching the network."""
    contract = ContractDPC(
        "applicant",
        "customer",
        account_id="account",
        contract_id="contract",
        hydro_client=cast(HydroClient, None),
    )
    contract._dpc_data = dpc_data
    return contract


def winter_data(max_hours: str | None = None) -> DPCDataTyping:
    """Return a pre-season DPC payload, optionally with the max published."""
    period: DPCPeriodeHiverTyping = {
        "dateDebut": "2026-12-01T00:00:00Z",
        "dateFin": "2027-03-31T00:00:00Z",
        "dateMaj": "2026-11-16T00:00:00Z",
        "hrsCritiquesAppelees": "0",
    }
    if max_hours is not None:
        period["hrsCritiquesAppeleesMax"] = max_hours
    return {"periodesHiver": [period]}


def test_max_critical_called_hours_returns_published_value() -> None:
    """A published maximum is exposed as an int."""
    contract = build_contract(winter_data("30"))
    assert contract.max_critical_called_hours == 30


def test_max_critical_called_hours_missing_returns_none() -> None:
    """An unpublished maximum is None instead of raising KeyError (#173)."""
    contract = build_contract(winter_data())
    assert contract.max_critical_called_hours is None


def test_missing_max_keeps_other_dpc_properties_usable() -> None:
    """The absent optional field does not affect the other period properties."""
    contract = build_contract(winter_data())
    assert contract.critical_called_hours == 0
    assert contract.amount_saved_vs_base_rate is None
    assert contract.winter_total_days == 120


def test_max_critical_called_hours_without_period_data_returns_none() -> None:
    """No winter data at all still degrades to None."""
    contract = build_contract({})
    assert contract.max_critical_called_hours is None
