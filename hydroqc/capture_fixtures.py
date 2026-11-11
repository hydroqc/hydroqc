"""Pseudonymize captured responses and install them as replay fixtures."""

import csv
import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from hydroqc.types.contract import ContractTyping, ListContractsTyping


@dataclass
class CapturedResponse:
    """One raw HTTP response from a capture run."""

    profile: str
    metadata: dict[str, Any]
    path: str
    body: bytes


class CapturePseudonymizer:
    """Replace direct identifiers while retaining dates and consumption values."""

    _ID_FIELDS = {
        "noPartenaireDemandeur": "applicant",
        "noPartenaireTitulaire": "customer",
        "noPartenaire": "customer",
        "noPartenaireMandataire": "customer",
        "titulaire": "customer",
        "noCompteContrat": "account",
        "noContrat": "contract",
        "numeroContrat": "contract",
        "idLieuConso": "location",
        "idLieuConsommation": "location",
        "noInstallation": "installation",
        "noCompteur": "meter",
        "documentArchivelinkID": "document",
        "factureIXOSID": "document",
        "idTechnique": "technical",
    }
    _PREFIXES = {
        "applicant": "FIXTURE-APPLICANT",
        "customer": "FIXTURE-CUSTOMER",
        "account": "FIXTURE-ACCOUNT",
        "contract": "FIXTURE-CONTRACT",
        "location": "FIXTURE-LOCATION",
        "installation": "FIXTURE-INSTALLATION",
        "meter": "FIXTURE-METER",
        "document": "FIXTURE-DOCUMENT",
        "technical": "FIXTURE-TECHNICAL",
    }
    _FAKE_FIELDS: dict[str, Any] = {
        "nom1Demandeur": "Fixture Applicant",
        "nom2Demandeur": "",
        "nom1Titulaire": "Fixture Holder",
        "nom2Titulaire": "",
        "nom": "Fixture",
        "prenom": "Camille",
        "nomTitulaire": "Fixture Holder",
        "prenomTitulaire": "Camille",
        "raisonSocialeTitulaire1": "Société Exemple",
        "raisonSocialeTitulaire2": "",
        "raisonSociale1": "Société Exemple",
        "raisonSociale2": "",
        "nomGroupeTitulaire1": "Société Exemple",
        "nomGroupeTitulaire2": "",
        "nickname": "Fixture",
        "libelle": "Compte fictif",
        "adresse": "123 rue Exemple",
        "adresseConsommation": "123 rue Exemple",
        "adresseFacturation": "123 rue Exemple",
        "adresseLieuConso1": "123 rue Exemple",
        "adresseLieuConso2": "Appartement 1",
        "adresseLieuConsoPartie1": "123 rue Exemple",
        "adresseLieuConsoPartie2": "Appartement 1",
        "adresseCourriel": "fixture@example.invalid",
        "courriel": "fixture@example.invalid",
        "email": "fixture@example.invalid",
        "mail": "fixture@example.invalid",
        "noCivique": "123",
        "rue": "Rue Exemple",
        "appartement": "1",
        "ville": "Montréal",
        "codePostal": "H0H 0H0",
        "numeroTelephone": "514-555-0100",
        "posteTelephone": "000",
        "codeMunicipal": "0000",
        "compteBancaire": "000000000",
        "idBanque": "000",
        "institution": "Banque fictive",
        "succursale": "00000",
        "folio": "0000000",
        "typeRelation": "FIXTURE",
    }
    _TOKEN_FIELDS = {
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "set-cookie",
        "cookie",
    }
    _RATE_FIELDS = {
        "tarifActuel",
        "tarifBase",
        "tarifCPC",
        "optionTarifActuel",
        "optionTarifBase",
        "optionTarifCPC",
        "offre",
    }

    def __init__(self, contract_id: str, target_context: tuple[str, str, str]):
        """Initialize pseudonymization maps for the selected contract context."""
        self.contract_id = contract_id
        self.target_context = target_context
        self.id_maps: dict[str, dict[str, str]] = {role: {} for role in self._PREFIXES}
        self._seed_target_ids()

    def _seed_target_ids(self) -> None:
        """Assign stable fixture identifiers to the target account context."""
        applicant_id, customer_id, account_id = self.target_context
        self.id_maps["applicant"][applicant_id] = "FIXTURE-APPLICANT-001"
        self.id_maps["customer"][customer_id] = "FIXTURE-CUSTOMER-002"
        self.id_maps["account"][account_id] = "FIXTURE-ACCOUNT-001"
        self.id_maps["contract"][self.contract_id] = "FIXTURE-CONTRACT-001"

    def _mapped_id(self, role: str, value: Any) -> str:
        """Return the deterministic pseudonym for an identifier and its role."""
        raw_value = str(value)
        mapping = self.id_maps[role]
        if raw_value not in mapping:
            used = set(mapping.values())
            index = 1
            while f"{self._PREFIXES[role]}-{index:03}" in used:
                index += 1
            mapping[raw_value] = f"{self._PREFIXES[role]}-{index:03}"
        return mapping[raw_value]

    def sanitize(  # pylint: disable=too-many-return-statements,too-many-branches
        self, value: Any, key: str = ""
    ) -> Any:
        """Sanitize a decoded JSON value, keeping its schema and value types."""
        if key in self._TOKEN_FIELDS:
            return None
        if key in self._ID_FIELDS and value is not None:
            return self._mapped_id(self._ID_FIELDS[key], value)
        if key == "listeNoContrat" and isinstance(value, list):
            return [self._mapped_id("contract", item) for item in value]
        if key == "listeCompteContrat" and isinstance(value, list):
            return [self._mapped_id("account", item) for item in value]
        if key in self._FAKE_FIELDS:
            return self._FAKE_FIELDS[key]
        if key.startswith(("zoneMessageHTML", "zoneMsgHTML")):
            return None if value is None else ""
        if key.startswith("tooltipId"):
            return "fixture-tooltip"
        if key in self._RATE_FIELDS:
            return value
        if key.lower().startswith("montant") or key.lower().startswith(
            ("solde", "mnt", "cout", "derniermontant", "moyennedollars")
        ):
            if isinstance(value, bool) or value is None:
                return value
            if isinstance(value, str):
                return "0.01"
            return 0.01 if isinstance(value, float) else 0
        if key == "success" and isinstance(value, bool):
            return True
        if isinstance(value, bool):
            return key in {"actif", "indEcActif"}
        if key.startswith("nb") and isinstance(value, int):
            if key in {
                "nbJourPrevuPeriode",
                "nbJourCalendrierMois",
                "nbJourCalendrierAnnee",
            }:
                return 30
            if key == "nbJourLecturePeriode":
                return 10
            return 1
        if key == "noInterruption" and isinstance(value, int):
            return 1
        if key.startswith("tempMoyenne") and isinstance(value, (int, float)):
            return 0
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            for child_key, child_value in value.items():
                if child_key.casefold() in self._TOKEN_FIELDS:
                    continue
                clean_key = child_key
                if key == "listeComptesContrats":
                    clean_key = child_key
                clean[clean_key] = self.sanitize(child_value, child_key)
            return clean
        if isinstance(value, list):
            return [self.sanitize(item, key) for item in value]
        return value

    def replace_ids_in_text(self, text: str) -> str:
        """Replace known IDs in CSV and URL paths without changing measurements."""
        replacements = [
            (raw, fake)
            for role_map in self.id_maps.values()
            for raw, fake in role_map.items()
            if raw and raw != fake
        ]
        for raw, fake in sorted(
            replacements, key=lambda pair: len(pair[0]), reverse=True
        ):
            text = text.replace(raw, fake)
        return text

    def fake_request_path(self, path: str) -> str:
        """Remove query parameters and pseudonymize identifiers in the path."""
        return self.replace_ids_in_text(path)


def _numeric_ids_equal(left: Any, right: Any) -> bool:
    """Compare identifier strings while ignoring leading zeroes on numbers."""
    left_text = str(left).strip()
    right_text = str(right).strip()
    if left_text == right_text:
        return True
    if left_text.isdecimal() and right_text.isdecimal():
        return (left_text.lstrip("0") or "0") == (right_text.lstrip("0") or "0")
    return False


def _load_responses(capture_root: Path) -> list[CapturedResponse]:
    """Load raw responses but discard OAuth token responses immediately."""
    responses: list[CapturedResponse] = []
    for profile in ("web", "mobile"):
        for metadata_path in sorted((capture_root / profile).glob("*-metadata.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            request_url = str(metadata.get("request_url", ""))
            if "/oauth2/v2.0/token" in request_url.casefold():
                continue
            body_path = capture_root / profile / metadata["body_file"]
            path = urlsplit(request_url).path
            responses.append(
                CapturedResponse(
                    profile,
                    metadata,
                    path,
                    body_path.read_bytes(),
                )
            )
    return responses


def _json_body(response: CapturedResponse) -> Any | None:
    """Decode a response as JSON when possible."""
    try:
        return json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _find_target_context(
    responses: list[CapturedResponse], contract_id: str
) -> tuple[tuple[str, str, str], str]:
    """Find the account context represented in the raw relation/summary responses."""
    relation_response = next(
        (
            response
            for response in responses
            if response.profile == "web" and response.path.endswith("/relations")
        ),
        None,
    )
    summaries = [
        response
        for response in responses
        if response.profile == "web" and "calculerSommaireContractuel" in response.path
    ]
    if relation_response is None:
        raise ValueError("La capture ne contient pas la réponse des relations.")
    relations = _json_body(relation_response)
    if not isinstance(relations, list):
        raise ValueError("La réponse des relations n’est pas une liste JSON.")

    seen: set[tuple[str, str]] = set()
    summary_index = 0
    for relation in relations:
        applicant_id = str(relation["noPartenaireDemandeur"])
        customer_id = str(relation["noPartenaireTitulaire"])
        if (pair := (applicant_id, customer_id)) in seen:
            continue
        seen.add(pair)
        if summary_index >= len(summaries):
            break
        summary = _json_body(summaries[summary_index])
        summary_index += 1
        if not isinstance(summary, dict):
            continue
        for account in summary.get("comptesContrats", []):
            contract_ids = account.get("listeNoContrat", [])
            for item in contract_ids:
                if _numeric_ids_equal(contract_id, item):
                    return (
                        (
                            applicant_id,
                            customer_id,
                            str(account["noCompteContrat"]),
                        ),
                        str(item),
                    )
    raise ValueError("Le contrat cible n’a pas été retrouvé dans la capture brute.")


def _fixture_group(path: str) -> str:
    """Split contract selection responses from contract-specific responses."""
    if path.endswith("/relations") or "calculerSommaireContractuel" in path:
        return "common"
    return "contract"


def _find_target_contract_info(
    responses: list[CapturedResponse], contract_id: str
) -> ContractTyping:
    """Find the captured contract record matching the requested contract number."""
    for response in responses:
        if response.profile != "web" or not response.path.endswith("/contrats"):
            continue
        raw_payload = _json_body(response)
        if not isinstance(raw_payload, dict):
            continue
        payload = cast(ListContractsTyping, raw_payload)
        for contract_info in payload["listeContrats"]:
            if _numeric_ids_equal(contract_id, contract_info["noContrat"]):
                return contract_info
    raise ValueError("La capture ne contient pas les détails du contrat cible.")


def _scenario_name(contract_info: Mapping[str, Any], winter_period: bool) -> str:
    """Build a safe fixture directory name from the rate, option, and season."""
    rate = str(contract_info["tarifActuel"])
    option = str(contract_info.get("optionTarifActuel") or "")
    name = f"{rate}_{option}" if option else rate
    name += "_WINTER" if winter_period else "_SUMMER"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"Type de contrat invalide pour un scénario: {name!r}.")
    return name


def _safe_headers(metadata: dict[str, Any]) -> dict[str, str]:
    """Keep only the non-sensitive content type response header."""
    for name, value in metadata.get("headers", []):
        if name.casefold() == "content-type":
            return {"Content-Type": value}
    return {}


def _sanitize_body(
    response: CapturedResponse, pseudonymizer: CapturePseudonymizer
) -> tuple[bytes, str]:
    """Sanitize JSON and text responses while preserving replayable response types."""
    content_type = next(
        (
            value
            for name, value in response.metadata.get("headers", [])
            if name.casefold() == "content-type"
        ),
        "",
    )
    if "text/html" in content_type.casefold():
        return b"", "bin"

    if (payload := _json_body(response)) is not None:
        clean_payload = pseudonymizer.sanitize(payload)
        return (
            (json.dumps(clean_payload, indent=2, ensure_ascii=False) + "\n").encode(
                "utf-8"
            ),
            "json",
        )

    if "csv" in content_type.casefold():
        charset = "iso-8859-1" if "iso-8859-1" in content_type.casefold() else "utf-8"
        text = response.body.decode(charset, errors="replace")
        rows = list(csv.reader(StringIO(text), delimiter=";"))
        for row_index, row in enumerate(rows):
            if not row_index:
                continue
            for column_index, column_name in enumerate(rows[0]):
                normalized_column = column_name.casefold()
                if column_index >= len(row):
                    continue
                if normalized_column in {"contrat", "contract"}:
                    row[column_index] = "FIXTURE-CONTRACT-001"
                elif any(
                    marker in normalized_column
                    for marker in ("amount", "montant", "$", "dollar", "cost", "coût")
                ):
                    row[column_index] = "0.01"
        buffer = StringIO()
        csv.writer(buffer, delimiter=";", lineterminator="\n").writerows(rows)
        return buffer.getvalue().encode(charset), "csv"

    return (
        pseudonymizer.replace_ids_in_text(
            response.body.decode("utf-8", errors="replace")
        ).encode("utf-8"),
        "txt",
    )


def _write_fixture_set(
    staging_root: Path,
    responses: list[CapturedResponse],
    pseudonymizer: CapturePseudonymizer,
    contract_id: str,
    reference_date: str,
    *,
    fixture_name: str,
    winter_period: bool,
) -> str:
    """Write sanitized replay responses and scenario metadata into a staging tree."""
    counters: dict[tuple[str, str], int] = {}
    target_contract_info = _find_target_contract_info(responses, contract_id)
    scenario_name = _scenario_name(target_contract_info, winter_period)

    for response in responses:
        group = _fixture_group(response.path)
        key = (group, response.profile)
        counters[key] = counters.get(key, 0) + 1
        sequence = counters[key]
        folder = staging_root / group / response.profile
        folder.mkdir(parents=True, exist_ok=True)
        body, extension = _sanitize_body(response, pseudonymizer)
        stem = f"{sequence:04}"
        body_file = f"{stem}-response.{extension}"
        (folder / body_file).write_bytes(body)
        metadata = {
            "profile": response.profile,
            "sequence": sequence,
            "request_method": str(response.metadata["request_method"]).lower(),
            "request_path": pseudonymizer.fake_request_path(response.path),
            "status": response.metadata["status"],
            "headers": _safe_headers(response.metadata),
            "body_file": body_file,
        }
        (folder / f"{stem}-metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    common_scenario = {
        "contract_id": "FIXTURE-CONTRACT-001",
        "context": [
            pseudonymizer.id_maps["applicant"][pseudonymizer.target_context[0]],
            pseudonymizer.id_maps["customer"][pseudonymizer.target_context[1]],
            pseudonymizer.id_maps["account"][pseudonymizer.target_context[2]],
        ],
    }
    rate_scenario = {
        "fixture_name": fixture_name,
        **common_scenario,
        "rate": target_contract_info["tarifActuel"],
        "option": target_contract_info.get("optionTarifActuel") or "",
        "reference_date": reference_date,
        "winter_period": winter_period,
    }
    (staging_root / "scenario.json").write_text(
        json.dumps(rate_scenario, indent=2) + "\n", encoding="utf-8"
    )
    return scenario_name


def _audit_staging_tree(staging_root: Path) -> None:
    """Reject output containing OAuth fields, cookies, or unstripped URLs."""
    forbidden = (
        "access_token",
        "refresh_token",
        "id_token",
        "set-cookie",
        "request_url",
    )
    for path in staging_root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore").casefold()
        if any(marker in text for marker in forbidden):
            raise ValueError(f"Audit d’anonymisation échoué dans {path.name}.")


def validate_fixture_name(fixture_name: str) -> str:
    """Validate a neutral fixture-set name safe for use as a directory."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", fixture_name):
        raise ValueError(
            "Le nom du jeu de fixtures doit commencer par une lettre ou un chiffre "
            "et ne contenir que des lettres, chiffres, tirets ou tirets bas (64 "
            "caractères maximum)."
        )
    return fixture_name


def _install_fixture_bundle(
    source: Path,
    destination: Path,
    *,
    replace_existing: bool,
) -> None:
    """Atomically install a complete fixture set, only replacing by request."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    exists = destination.exists()
    if exists and not replace_existing:
        raise FileExistsError(
            f"Le jeu de fixtures existe déjà : {destination}. "
            "Choisissez un autre nom ou confirmez explicitement son remplacement."
        )
    if replace_existing and not exists:
        raise FileNotFoundError(
            f"Impossible de remplacer le jeu de fixtures absent : {destination}."
        )
    if exists and not destination.is_dir():
        raise NotADirectoryError(destination)

    with tempfile.TemporaryDirectory(
        prefix=".hydroqc-fixture-", dir=destination.parent
    ) as temporary:
        temporary_root = Path(temporary)
        staged_bundle = temporary_root / "new"
        shutil.copytree(source, staged_bundle)
        backup = temporary_root / "previous"
        if exists:
            destination.rename(backup)
        try:
            staged_bundle.rename(destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise


def anonymize_capture_to_fixtures(
    capture_root: Path,
    contract_id: str,
    fixture_root: Path,
    reference_date: str,
    *,
    winter_period: bool = False,
    fixture_name: str,
    replace_existing: bool = False,
) -> str:
    """Pseudonymize a capture and install its web/mobile replay fixtures."""
    responses = _load_responses(capture_root)
    target_context, matched_contract_id = _find_target_context(responses, contract_id)
    pseudonymizer = CapturePseudonymizer(matched_contract_id, target_context)
    fixture_name = validate_fixture_name(fixture_name)

    with tempfile.TemporaryDirectory(prefix="hydroqc-fixtures-") as temporary:
        staging_root = Path(temporary)
        scenario_name = _write_fixture_set(
            staging_root,
            responses,
            pseudonymizer,
            contract_id,
            reference_date,
            fixture_name=fixture_name,
            winter_period=winter_period,
        )
        _audit_staging_tree(staging_root)
        destination = fixture_root / scenario_name / fixture_name
        _install_fixture_bundle(
            staging_root,
            destination,
            replace_existing=replace_existing,
        )
    return scenario_name
