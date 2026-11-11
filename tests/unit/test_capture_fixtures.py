"""Tests for fixture pseudonymization and installation."""

import json
from pathlib import Path
from typing import Any

import pytest

from hydroqc.capture_fixtures import anonymize_capture_to_fixtures


def _write_response(
    root: Path,
    profile: str,
    sequence: int,
    path: str,
    body: Any,
    *,
    content_type: str = "application/json",
) -> None:
    """Write one raw response and metadata pair for fixture tests."""
    folder = root / profile
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{sequence:04}"
    body_path = folder / f"{stem}-response.bin"
    if isinstance(body, bytes):
        body_path.write_bytes(body)
    else:
        body_path.write_text(json.dumps(body), encoding="utf-8")
    metadata = {
        "profile": profile,
        "sequence": sequence,
        "request_method": "get",
        "request_url": f"https://api.example.invalid{path}?private=query",
        "response_url": f"https://api.example.invalid{path}",
        "status": 200,
        "headers": [
            ["Content-Type", content_type],
            ["Set-Cookie", "private-cookie=secret"],
        ],
        "body_file": body_path.name,
    }
    (folder / f"{stem}-metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )


def _write_profile(
    root: Path,
    profile: str,
    api_profile: str,
    *,
    rate: str = "D",
    option: str = "CPC",
) -> None:
    """Write the representative relation and contract API responses."""
    base = f"/wsapi/{api_profile}/prive/api/v3_0/partenaires"
    _write_response(
        root,
        profile,
        1,
        f"/wsapi/{api_profile}/prive/api/v1_0/relations",
        [
            {
                "noPartenaireDemandeur": "applicant-real",
                "noPartenaireTitulaire": "customer-other",
                "nom1Demandeur": "Real Applicant",
                "nom1Titulaire": "Other Holder",
            },
            {
                "noPartenaireDemandeur": "applicant-real",
                "noPartenaireTitulaire": "customer-real",
                "nom1Demandeur": "Real Applicant",
                "nom1Titulaire": "Real Holder",
            },
        ],
    )
    _write_response(
        root,
        profile,
        2,
        f"{base}/calculerSommaireContractuel",
        {"comptesContrats": [], "nbContrats": 0, "nbComptesContrats": 0},
    )
    _write_response(
        root,
        profile,
        3,
        f"{base}/calculerSommaireContractuel",
        {
            "comptesContrats": [
                {
                    "noCompteContrat": "account-real",
                    "listeNoContrat": ["contract-real"],
                    "titulaire": "customer-real",
                }
            ],
            "nbContrats": 1,
            "nbComptesContrats": 1,
        },
    )
    _write_response(
        root,
        profile,
        4,
        f"{base}/contrats",
        {
            "listeContrats": [
                {
                    "noContrat": "contract-real",
                    "noCompteContrat": "account-real",
                    "noPartenaireDemandeur": "applicant-real",
                    "noPartenaireTitulaire": "customer-real",
                    "nom1Titulaire": "Real Holder",
                    "adresseConsommation": "9 Real Street",
                    "adresseCourriel": "real@example.com",
                    "dateDebutContrat": "2024-01-02",
                    "tarifActuel": rate,
                    "optionTarifActuel": option,
                    "montantFacturePeriode": 82.4,
                }
            ]
        },
        content_type="application/json",
    )


def test_capture_installs_safe_fixtures(tmp_path: Path) -> None:
    """Install fixtures with fake identity while keeping measured values and dates."""
    capture_root = tmp_path / "raw"
    _write_profile(capture_root, "web", "web")
    _write_profile(capture_root, "mobile", "mobile")
    _write_response(
        capture_root,
        "web",
        5,
        "/tenant/oauth2/v2.0/token",
        {"access_token": "secret-access", "refresh_token": "secret-refresh"},
    )

    fixture_root = tmp_path / "fixtures"
    anonymize_capture_to_fixtures(
        capture_root,
        "contract-real",
        fixture_root,
        "2026-11-10",
    )

    common_scenario = json.loads(
        (fixture_root / "common" / "scenario.json").read_text(encoding="utf-8")
    )
    assert common_scenario == {
        "contract_id": "FIXTURE-CONTRACT-001",
        "context": [
            "FIXTURE-APPLICANT-001",
            "FIXTURE-CUSTOMER-002",
            "FIXTURE-ACCOUNT-001",
        ],
    }
    rate_scenario = json.loads(
        (fixture_root / "D_CPC_SUMMER" / "scenario.json").read_text(encoding="utf-8")
    )
    assert rate_scenario == {
        "rate": "D",
        "option": "CPC",
        "reference_date": "2026-11-10",
        "winter_period": False,
    }

    response_path = fixture_root / "D_CPC_SUMMER" / "web" / "0001-response.json"
    contract_response = json.loads(response_path.read_text(encoding="utf-8"))
    contract = contract_response["listeContrats"][0]
    assert contract["noContrat"] == "FIXTURE-CONTRACT-001"
    assert contract["noPartenaireTitulaire"] == "FIXTURE-CUSTOMER-002"
    assert contract["nom1Titulaire"] == "Fixture Holder"
    assert contract["adresseConsommation"] == "123 rue Exemple"
    assert contract["adresseCourriel"] == "fixture@example.invalid"
    assert contract["dateDebutContrat"] == "2024-01-02"
    assert contract["tarifActuel"] == "D"
    assert contract["optionTarifActuel"] == "CPC"
    assert contract["montantFacturePeriode"] == 0.01

    metadata = json.loads(
        (fixture_root / "common" / "web" / "0001-metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["request_path"] == "/wsapi/web/prive/api/v1_0/relations"
    assert metadata["headers"] == {"Content-Type": "application/json"}
    all_fixture_bytes = b"".join(
        path.read_bytes() for path in fixture_root.rglob("*") if path.is_file()
    )
    assert b"secret-access" not in all_fixture_bytes
    assert b"secret-refresh" not in all_fixture_bytes
    assert b"Real Applicant" not in all_fixture_bytes
    assert b"real@example.com" not in all_fixture_bytes


def test_capture_uses_rate_only_name(tmp_path: Path) -> None:
    """Install a rate-only contract under its own scenario directory."""
    capture_root = tmp_path / "raw"
    _write_profile(capture_root, "web", "web", rate="DT", option="")
    _write_profile(capture_root, "mobile", "mobile", rate="DT", option="")

    fixture_root = tmp_path / "fixtures"
    scenario_name = anonymize_capture_to_fixtures(
        capture_root,
        "contract-real",
        fixture_root,
        "2026-11-10",
    )

    assert scenario_name == "DT_SUMMER"
    scenario = json.loads(
        (fixture_root / "DT_SUMMER" / "scenario.json").read_text(encoding="utf-8")
    )
    assert scenario["rate"] == "DT"
    assert scenario["option"] == ""
    assert (fixture_root / "DT_SUMMER" / "web").is_dir()
    assert (fixture_root / "DT_SUMMER" / "mobile").is_dir()
    assert not (fixture_root / "D_CPC_SUMMER").exists()


def test_capture_marks_winter_scenario(tmp_path: Path) -> None:
    """Install winter-period contract data separately with explicit metadata."""
    capture_root = tmp_path / "raw"
    _write_profile(capture_root, "web", "web")
    _write_profile(capture_root, "mobile", "mobile")

    fixture_root = tmp_path / "fixtures"
    scenario_name = anonymize_capture_to_fixtures(
        capture_root,
        "contract-real",
        fixture_root,
        "2026-11-10",
        winter_period=True,
    )

    assert scenario_name == "D_CPC_WINTER"
    scenario = json.loads(
        (fixture_root / scenario_name / "scenario.json").read_text(encoding="utf-8")
    )
    assert scenario == {
        "rate": "D",
        "option": "CPC",
        "reference_date": "2026-11-10",
        "winter_period": True,
    }
    assert (fixture_root / scenario_name / "web").is_dir()
    assert (fixture_root / scenario_name / "mobile").is_dir()
    assert not (fixture_root / "D_CPC_SUMMER").exists()


def test_winter_rate_only_name(tmp_path: Path) -> None:
    """Add the winter marker after a rate-only scenario name."""
    capture_root = tmp_path / "raw"
    _write_profile(capture_root, "web", "web", rate="DT", option="")
    _write_profile(capture_root, "mobile", "mobile", rate="DT", option="")

    scenario_name = anonymize_capture_to_fixtures(
        capture_root,
        "contract-real",
        tmp_path / "fixtures",
        "2026-11-10",
        winter_period=True,
    )

    assert scenario_name == "DT_WINTER"


@pytest.mark.parametrize(
    ("rate", "option", "expected_name"),
    [("G", "", "G_WINTER"), ("M", "GDP", "M_GDP_WINTER")],
)
def test_winter_scenarios_support_other_rates(
    tmp_path: Path, rate: str, option: str, expected_name: str
) -> None:
    """Apply the winter marker to other rates and rate options as well."""
    capture_root = tmp_path / "raw"
    _write_profile(capture_root, "web", "web", rate=rate, option=option)
    _write_profile(capture_root, "mobile", "mobile", rate=rate, option=option)

    scenario_name = anonymize_capture_to_fixtures(
        capture_root,
        "contract-real",
        tmp_path / "fixtures",
        "2026-11-10",
        winter_period=True,
    )

    assert scenario_name == expected_name
