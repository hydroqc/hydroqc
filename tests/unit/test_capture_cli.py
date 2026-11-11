"""Tests for fixture-capture command-line options and contract selection."""

import sys
from pathlib import Path
from typing import Any, cast

import pytest

import hydroqc.capture
from hydroqc.capture import (
    CapturableContract,
    list_available_contracts,
    parse_args,
    prompt_contract_selection,
    prompt_session_cleanup,
    purge_sessions,
)
from hydroqc.hydro_api.client import HydroClient
from hydroqc.hydro_api.consts import AUTH_CLIENT_MOBILE, AUTH_CLIENT_WEB


def _make_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, username: str
) -> list[Path]:
    """Point the session directory at a temp folder and create both sessions."""
    monkeypatch.setattr(hydroqc.capture, "SESSION_DIR", tmp_path)
    paths = [
        hydroqc.capture._session_path(
            profile, username
        )  # pylint: disable=protected-access
        for profile in (AUTH_CLIENT_WEB, AUTH_CLIENT_MOBILE)
    ]
    for path in paths:
        path.write_text("{}", encoding="utf-8")
    return paths


def _available_contracts() -> list[CapturableContract]:
    """Return two contracts to choose from."""
    return [
        CapturableContract(contract_id="1001", rate="D", rate_option=""),
        CapturableContract(contract_id="1002", rate="D", rate_option="CPC"),
    ]


def _patch_available(
    monkeypatch: pytest.MonkeyPatch, contracts: list[CapturableContract]
) -> None:
    """Replace contract discovery with a fixed list."""

    async def fake_available(_client: HydroClient) -> list[CapturableContract]:
        return contracts

    monkeypatch.setattr(hydroqc.capture, "list_available_contracts", fake_available)


def _patch_inputs(monkeypatch: pytest.MonkeyPatch, *values: str) -> None:
    """Make ``input`` return the provided values in order."""
    pending = list(values)

    def fake_input(_prompt: str = "") -> str:
        return pending.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)


def test_winter_period_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing captures in the non-winter fixture directory by default."""
    monkeypatch.setattr(sys, "argv", ["hydroqc-capture", "--contract", "123"])

    assert parse_args().winter_period is False


def test_winter_period_flag_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recognize the explicit winter-period option."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["hydroqc-capture", "--contract", "123", "--winter-period"],
    )

    assert parse_args().winter_period is True


def test_contract_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow the contract to be prompted after login."""
    monkeypatch.setattr(sys, "argv", ["hydroqc-capture"])

    assert parse_args().contract is None


@pytest.mark.asyncio(loop_scope="module")
async def test_list_available_contracts_maps_rates() -> None:
    """Build capturable contracts from the account summaries and contract info."""
    relations = [
        {
            "noPartenaireDemandeur": "applicant",
            "noPartenaireTitulaire": "customer",
        }
    ]
    summary = {
        "comptesContrats": [
            {
                "noCompteContrat": "account",
                "listeNoContrat": ["1001", "1002"],
            }
        ]
    }
    infos: dict[str, dict[str, Any]] = {
        "1001": {"tarifActuel": "D", "optionTarifActuel": ""},
        "1002": {"tarifActuel": "D", "optionTarifActuel": "CPC"},
    }

    class FakeClient:
        """Expose just the read calls used by contract discovery."""

        async def get_user_info(self) -> list[dict[str, Any]]:
            """Return the account relations."""
            return relations

        async def list_account_contract(
            self, applicant_id: str, customer_id: str
        ) -> dict[str, Any]:
            """Return the account and contract summary."""
            assert (applicant_id, customer_id) == ("applicant", "customer")
            return summary

        async def get_contract_info(
            self,
            applicant_id: str,
            customer_id: str,
            account_id: str,
            contract_id: str,
        ) -> dict[str, Any]:
            """Return the rate data for one contract."""
            assert (applicant_id, customer_id, account_id) == (
                "applicant",
                "customer",
                "account",
            )
            return infos[contract_id]

    contracts = await list_available_contracts(cast(HydroClient, FakeClient()))

    assert [contract.contract_id for contract in contracts] == ["1001", "1002"]
    assert contracts[1].label == "Contrat 1002 - DCPC"


@pytest.mark.asyncio(loop_scope="module")
async def test_prompt_uses_numbered_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the contract matching the entered list number."""
    _patch_available(monkeypatch, _available_contracts())
    _patch_inputs(monkeypatch, "2")

    assert await prompt_contract_selection(cast(HydroClient, object())) == "1002"


@pytest.mark.asyncio(loop_scope="module")
async def test_prompt_accepts_a_contract_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept an exact contract number as well as a list position."""
    _patch_available(monkeypatch, _available_contracts())
    _patch_inputs(monkeypatch, "1001")

    assert await prompt_contract_selection(cast(HydroClient, object())) == "1001"


@pytest.mark.asyncio(loop_scope="module")
async def test_prompt_retries_after_invalid_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-ask until the selection matches a known contract."""
    _patch_available(monkeypatch, _available_contracts())
    _patch_inputs(monkeypatch, "9999", "1")

    assert await prompt_contract_selection(cast(HydroClient, object())) == "1001"


@pytest.mark.asyncio(loop_scope="module")
async def test_prompt_selects_only_contract_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid forcing a choice when the account has a single contract."""
    _patch_available(
        monkeypatch,
        [CapturableContract(contract_id="1001", rate="D", rate_option="")],
    )
    _patch_inputs(monkeypatch, "")

    assert await prompt_contract_selection(cast(HydroClient, object())) == "1001"


@pytest.mark.asyncio(loop_scope="module")
async def test_prompt_fails_without_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a clear error when the account has no reachable contract."""
    _patch_available(monkeypatch, [])
    _patch_inputs(monkeypatch)

    with pytest.raises(RuntimeError, match="Aucun contrat"):
        await prompt_contract_selection(cast(HydroClient, object()))


def test_purge_sessions_removes_both_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Delete the web and mobile session files of this account."""
    paths = _make_sessions(monkeypatch, tmp_path, "user")

    assert purge_sessions("user") == paths
    assert not any(path.exists() for path in paths)


def test_purge_sessions_ignores_missing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Report nothing to delete when no session was saved."""
    monkeypatch.setattr(hydroqc.capture, "SESSION_DIR", tmp_path)

    assert not purge_sessions("user")


def test_session_cleanup_purges_on_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Remove the sessions when the user answers no."""
    paths = _make_sessions(monkeypatch, tmp_path, "user")
    _patch_inputs(monkeypatch, "n")

    prompt_session_cleanup("user")

    assert not any(path.exists() for path in paths)


def test_session_cleanup_keeps_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep the sessions when the user just presses enter."""
    paths = _make_sessions(monkeypatch, tmp_path, "user")
    _patch_inputs(monkeypatch, "")

    prompt_session_cleanup("user")

    assert all(path.exists() for path in paths)
