"""Pseudonymize captured responses and install them as replay fixtures."""

import csv
import json
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from hydroqc.hydro_api.consts import IS_HYDRO_PORTAL_UP_URL, OPEN_DATA_PEAK_URL
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
        "modeEncaissement": "FIXTURE",
        "payeurDivergent": "FIXTURE",
        "langueCorrespondance": "fr",
        "segmentation": "FIXTURE",
        "categorie": "FIXTURE",
        "categorieTitulaire": "FIXTURE",
        "regroupement": "FIXTURE",
        "typeEntenteSansProjection": "FIXTURE",
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
    _PERSONAL_KEY_MARKERS = (
        "nom",
        "prenom",
        "fullname",
        "courriel",
        "email",
        "mail",
        "telephone",
        "phone",
        "adresse",
        "address",
        "rue",
        "ville",
        "postal",
        "civique",
        "appartement",
        "banque",
        "bancaire",
        "institution",
        "succursale",
        "folio",
        "payeur",
        "paiement",
    )
    _FINANCIAL_KEY_MARKERS = (
        "montant",
        "amount",
        "solde",
        "balance",
        "mnt",
        "cout",
        "cost",
        "dollar",
        "price",
        "derniermontant",
        "moyennedollars",
    )
    _EMAIL_PATTERN = re.compile(
        r"\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b",
        re.IGNORECASE,
    )
    _PHONE_PATTERN = re.compile(
        r"(?<!\d)(?:\+?1[\s.-]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})"
        r"[\s.-]?[2-9]\d{2}[\s.-]?\d{4}(?!\d)"
    )
    _PRESIDIO_REPLACEMENTS = {
        "PERSON": "Fixture",
        "EMAIL": "fixture@example.invalid",
        "EMAIL_ADDRESS": "fixture@example.invalid",
        "PHONE_NUMBER": "514-555-0100",
        "URL": "https://example.invalid",
        "IBAN_CODE": "000000000",
        "CREDIT_CARD": "0000000000000000",
        "IP_ADDRESS": "192.0.2.1",
    }
    _PRESIDIO_TEXT_ENTITIES = {
        "PERSON",
        "EMAIL",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "URL",
        "IBAN_CODE",
        "CREDIT_CARD",
        "IP_ADDRESS",
    }
    _PRESERVE_NER_KEY_MARKERS = (
        "code",
        "date",
        "time",
        "label",
        "tooltip",
        "url",
        "tarif",
        "option",
        "offre",
        "uuid",
        "temperature",
        "consommation",
        "puissance",
        "mesure",
        "unite",
        "jour",
        "periode",
    )

    def __init__(self, contract_id: str, target_context: tuple[str, str, str]):
        """Initialize pseudonymization maps for the selected contract context."""
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_analyzer.predefined_recognizers import (
            CreditCardRecognizer,
            EmailRecognizer,
            IbanRecognizer,
            IpRecognizer,
            PhoneRecognizer,
            UrlRecognizer,
        )
        from presidio_analyzer.predefined_recognizers.nlp_engine_recognizers import (
            spacy_recognizer,
        )

        SpacyRecognizer = spacy_recognizer.SpacyRecognizer
        from presidio_analyzer.recognizer_registry import RecognizerRegistry
        from presidio_anonymizer import AnonymizerEngine

        self.contract_id = contract_id
        self.target_context = target_context
        self.id_maps: dict[str, dict[str, str]] = {role: {} for role in self._PREFIXES}
        self.sensitive_originals: set[str] = set()
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "fr", "model_name": "fr_core_news_sm"}],
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["fr"])
        for recognizer in (
            SpacyRecognizer(supported_language="fr"),
            EmailRecognizer(supported_language="fr"),
            PhoneRecognizer(supported_language="fr", supported_regions=("CA",)),
            IbanRecognizer(supported_language="fr"),
            CreditCardRecognizer(supported_language="fr"),
            IpRecognizer(supported_language="fr"),
            UrlRecognizer(supported_language="fr"),
        ):
            registry.add_recognizer(recognizer)
        self._presidio_analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp_engine,
            supported_languages=["fr"],
        )
        self._presidio_anonymizer = AnonymizerEngine()  # type: ignore[no-untyped-call]
        self._seed_target_ids()

    def _presidio_replace(self, value: Any, entity_type: str, replacement: str) -> Any:
        """Apply a deterministic replacement through Presidio's anonymizer."""
        from presidio_anonymizer.entities import (
            OperatorConfig,
        )
        from presidio_anonymizer.entities import (
            RecognizerResult as AnonymizerRecognizerResult,
        )

        if value is None:
            return None
        source = str(value)
        if source:
            self._remember_sensitive_value(source)
        result = AnonymizerRecognizerResult(
            entity_type=entity_type, start=0, end=len(source), score=1.0
        )
        anonymized = self._presidio_anonymizer.anonymize(
            text=source,
            analyzer_results=[result],
            operators={
                entity_type: OperatorConfig("replace", {"new_value": replacement})
            },
        ).text
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and replacement == "0.01":
            return 0
        if isinstance(value, float) and replacement == "0.01":
            return 0.01
        return anonymized

    def _anonymize_detected_text(self, value: str, key: str) -> str:
        """Detect free-text PII and anonymize its spans with Presidio."""
        from presidio_anonymizer.entities import (
            OperatorConfig,
        )
        from presidio_anonymizer.entities import (
            RecognizerResult as AnonymizerRecognizerResult,
        )

        if not value.strip():
            return value
        normalized_key = self._normalized_key(key)
        preserve_ner = any(
            marker in normalized_key for marker in self._PRESERVE_NER_KEY_MARKERS
        )
        detected_results = self._presidio_analyzer.analyze(
            text=value,
            language="fr",
            score_threshold=0.65,
        )
        results = [
            result
            for result in detected_results
            if result.entity_type in self._PRESIDIO_TEXT_ENTITIES
            and not (preserve_ner and result.entity_type in {"PERSON", "URL"})
        ]
        for result in results:
            self._remember_sensitive_value(
                value[result.start : result.end]  # noqa: E203
            )
        if not results:
            return value
        anonymizer_results = [
            AnonymizerRecognizerResult(
                entity_type=result.entity_type,
                start=result.start,
                end=result.end,
                score=result.score,
            )
            for result in results
        ]
        operators = {
            entity_type: OperatorConfig("replace", {"new_value": replacement})
            for entity_type, replacement in self._PRESIDIO_REPLACEMENTS.items()
        }
        anonymized = self._presidio_anonymizer.anonymize(
            text=value,
            analyzer_results=anonymizer_results,
            operators=operators,
        )
        return str(anonymized.text)

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
        self._remember_sensitive_value(raw_value)
        mapping = self.id_maps[role]
        if raw_value not in mapping:
            used = set(mapping.values())
            index = 1
            while f"{self._PREFIXES[role]}-{index:03}" in used:
                index += 1
            mapping[raw_value] = f"{self._PREFIXES[role]}-{index:03}"
        return mapping[raw_value]

    def _remember_sensitive_value(self, value: Any) -> None:
        """Remember meaningful originals so the final audit can detect leaks."""
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            raw_value = str(value).strip()
            placeholder_values = {
                str(placeholder).casefold()
                for placeholder in self._FAKE_FIELDS.values()
            }
            placeholder_values.update(
                {
                    "fixture",
                    "fixture@example.invalid",
                    "514-555-0100",
                    "0.01",
                    "true",
                    "false",
                    "none",
                    "null",
                }
            )
            # Tiny values commonly occur in dates and measurements; checking them
            # globally would produce false positives in otherwise clean fixtures.
            if (
                len(raw_value) >= 5
                and raw_value.casefold() not in placeholder_values
                and not raw_value.startswith("FIXTURE-")
                and not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", raw_value)
            ):
                self.sensitive_originals.add(raw_value)

    @classmethod
    def _normalized_key(cls, key: str) -> str:
        """Normalize API field names across casing and punctuation variants."""
        decomposed = unicodedata.normalize("NFKD", key.casefold())
        unaccented = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return re.sub(r"[^a-z0-9]", "", unaccented)

    def _is_personal_field(self, key: str) -> bool:
        normalized = self._normalized_key(key)
        if normalized.startswith(("nombre", "number")):
            return False
        return any(marker in normalized for marker in self._PERSONAL_KEY_MARKERS)

    def _is_financial_field(self, key: str) -> bool:
        if "$" in key:
            return True
        normalized = self._normalized_key(key)
        return any(marker in normalized for marker in self._FINANCIAL_KEY_MARKERS)

    def sanitize(  # pylint: disable=too-many-return-statements,too-many-branches
        self, value: Any, key: str = ""
    ) -> Any:
        """Sanitize a decoded JSON value, keeping its schema and value types."""
        normalized_key = self._normalized_key(key)
        if normalized_key in {
            self._normalized_key(item) for item in self._TOKEN_FIELDS
        }:
            return None
        id_role = next(
            (
                role
                for field, role in self._ID_FIELDS.items()
                if self._normalized_key(field) == normalized_key
            ),
            None,
        )
        if id_role is not None and value is not None:
            return self._presidio_replace(
                value,
                f"HYDRO_{id_role.upper()}",
                self._mapped_id(id_role, value),
            )
        if normalized_key == "listenocontrat" and isinstance(value, list):
            return [
                self._presidio_replace(
                    item,
                    "HYDRO_CONTRACT",
                    self._mapped_id("contract", item),
                )
                for item in value
            ]
        if normalized_key == "listecomptecontrat" and isinstance(value, list):
            return [
                self._presidio_replace(
                    item,
                    "HYDRO_ACCOUNT",
                    self._mapped_id("account", item),
                )
                for item in value
            ]
        fake_field = next(
            (
                field
                for field in self._FAKE_FIELDS
                if self._normalized_key(field) == normalized_key
            ),
            None,
        )
        if fake_field is not None:
            return self._presidio_replace(
                value, "HYDRO_PERSONAL_FIELD", str(self._FAKE_FIELDS[fake_field])
            )
        if self._is_personal_field(key):
            if value is None:
                return None
            replacement = (
                "fixture@example.invalid" if "mail" in normalized_key else "Fixture"
            )
            return self._presidio_replace(value, "HYDRO_PERSONAL_FIELD", replacement)
        if key.startswith(("zoneMessageHTML", "zoneMsgHTML")):
            return None if value is None else ""
        if key.startswith("tooltipId"):
            return "fixture-tooltip"
        if key in self._RATE_FIELDS:
            return value
        if self._is_financial_field(key):
            if isinstance(value, bool) or value is None:
                return value
            return self._presidio_replace(value, "HYDRO_FINANCIAL", "0.01")
        if isinstance(value, bool):
            return value
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
                if self._normalized_key(child_key) in {
                    self._normalized_key(item) for item in self._TOKEN_FIELDS
                }:
                    continue
                clean_key = child_key
                if key == "listeComptesContrats":
                    clean_key = child_key
                clean[clean_key] = self.sanitize(child_value, child_key)
            return clean
        if isinstance(value, list):
            return [self.sanitize(item, key) for item in value]
        if isinstance(value, str):
            return self._anonymize_detected_text(value, key)
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

    def filter_target_payload(self, path: str, payload: Any) -> Any:
        """Keep only the selected customer/account/contract in fixture responses."""
        applicant_id, customer_id, account_id = self.target_context
        if path.endswith("/relations") and isinstance(payload, list):
            return [
                item
                for item in payload
                if isinstance(item, dict)
                and str(item.get("noPartenaireDemandeur")) == applicant_id
                and str(item.get("noPartenaireTitulaire")) == customer_id
            ]

        if "calculerSommaireContractuel" in path and isinstance(payload, dict):
            accounts = payload.get("comptesContrats")
            if isinstance(accounts, list):
                payload["comptesContrats"] = [
                    account
                    for account in accounts
                    if isinstance(account, dict)
                    and _numeric_ids_equal(account.get("noCompteContrat"), account_id)
                ]
                for account in payload["comptesContrats"]:
                    contracts = account.get("listeNoContrat", [])
                    account["listeNoContrat"] = [
                        contract
                        for contract in contracts
                        if _numeric_ids_equal(self.contract_id, contract)
                    ]
                payload["nbComptesContrats"] = len(payload["comptesContrats"])
                payload["nbContrats"] = sum(
                    len(account.get("listeNoContrat", []))
                    for account in payload["comptesContrats"]
                )
            account_map = payload.get("listeComptesContrats")
            if isinstance(account_map, dict):
                for key, value in account_map.items():
                    if isinstance(value, list):
                        account_map[key] = [
                            item
                            for item in value
                            if _numeric_ids_equal(item, account_id)
                        ]
            return payload

        if path.endswith("/infoCompte") and isinstance(payload, dict):
            cockpit = payload.get("infoCockpitPourPartenaireModel")
            if isinstance(cockpit, dict):
                accounts = cockpit.get("listeComptesContrats")
                if isinstance(accounts, list):
                    cockpit["listeComptesContrats"] = [
                        account
                        for account in accounts
                        if isinstance(account, dict)
                        and _numeric_ids_equal(
                            account.get("noCompteContrat"), account_id
                        )
                    ]
                    for account in cockpit["listeComptesContrats"]:
                        contracts = account.get("listeNoContrat", [])
                        account["listeNoContrat"] = [
                            contract
                            for contract in contracts
                            if _numeric_ids_equal(self.contract_id, contract)
                        ]
            contracts = payload.get("listeContratModel")
            if isinstance(contracts, list):
                payload["listeContratModel"] = [
                    contract
                    for contract in contracts
                    if isinstance(contract, dict)
                    and _numeric_ids_equal(
                        self.contract_id, contract.get("noContrat", "")
                    )
                ]
            return payload

        if path.endswith("/contrats") and isinstance(payload, dict):
            contracts = payload.get("listeContrats")
            if isinstance(contracts, list):
                payload["listeContrats"] = [
                    contract
                    for contract in contracts
                    if isinstance(contract, dict)
                    and _numeric_ids_equal(
                        self.contract_id, contract.get("noContrat", "")
                    )
                ]
            return payload
        return payload

    def is_replay_relevant(self, path: str, payload: Any) -> bool:
        """Drop account-selection responses unrelated to the requested contract."""
        applicant_id, customer_id, account_id = self.target_context
        if path.endswith("/relations") and isinstance(payload, list):
            return any(
                isinstance(item, dict)
                and str(item.get("noPartenaireDemandeur")) == applicant_id
                and str(item.get("noPartenaireTitulaire")) == customer_id
                for item in payload
            )

        if "calculerSommaireContractuel" in path and isinstance(payload, dict):
            return any(
                isinstance(account, dict)
                and _numeric_ids_equal(account.get("noCompteContrat"), account_id)
                and any(
                    _numeric_ids_equal(self.contract_id, contract_id)
                    for contract_id in account.get("listeNoContrat", [])
                )
                for account in payload.get("comptesContrats", [])
            )

        if path.endswith("/infoCompte") and isinstance(payload, dict):
            cockpit = payload.get("infoCockpitPourPartenaireModel")
            if not isinstance(cockpit, dict):
                return False
            return any(
                isinstance(account, dict)
                and _numeric_ids_equal(account.get("noCompteContrat"), account_id)
                for account in cockpit.get("listeComptesContrats", [])
            )

        if path.endswith("/contrats") and isinstance(payload, dict):
            return any(
                isinstance(contract, dict)
                and _numeric_ids_equal(self.contract_id, contract.get("noContrat", ""))
                for contract in payload.get("listeContrats", [])
            )
        return True


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
    if (
        path.endswith("/relations")
        or "calculerSommaireContractuel" in path
        or path.endswith("/infoCompte")
    ):
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
        payload = pseudonymizer.filter_target_payload(response.path, payload)
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
                else:
                    clean_value = pseudonymizer.sanitize(row[column_index], column_name)
                    row[column_index] = "" if clean_value is None else str(clean_value)
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
        if (payload := _json_body(response)) is not None and not (
            pseudonymizer.is_replay_relevant(response.path, payload)
        ):
            continue
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


def _audit_staging_tree(
    staging_root: Path, pseudonymizer: CapturePseudonymizer | None = None
) -> None:
    """Reject output containing secrets or known unsanitized personal values."""
    forbidden = (
        "access_token",
        "refresh_token",
        "id_token",
        "set-cookie",
        "request_url",
    )
    email_pattern = CapturePseudonymizer._EMAIL_PATTERN
    phone_pattern = CapturePseudonymizer._PHONE_PATTERN
    for path in staging_root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore").casefold()
        if any(marker in text for marker in forbidden):
            raise ValueError(f"Audit d’anonymisation échoué dans {path.name}.")
        if pseudonymizer is not None:
            emails = email_pattern.findall(text)
            if any(email != "fixture@example.invalid" for email in emails):
                raise ValueError(f"Audit courriel échoué dans {path.name}.")
            phones = phone_pattern.findall(text)
            if any(phone != "514-555-0100" for phone in phones):
                raise ValueError(f"Audit téléphone échoué dans {path.name}.")
        if pseudonymizer is not None and any(
            original.casefold() in text
            for original in pseudonymizer.sensitive_originals
        ):
            raise ValueError(
                f"Audit d’anonymisation : une valeur sensible originale subsiste "
                f"dans {path.name}."
            )


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
        _audit_staging_tree(staging_root, pseudonymizer)
        destination = fixture_root / scenario_name / fixture_name
        _install_fixture_bundle(
            staging_root,
            destination,
            replace_existing=replace_existing,
        )
    return scenario_name


def install_public_capture_to_fixtures(
    capture_root: Path,
    fixture_root: Path,
    reference_date: str,
    *,
    fixture_name: str,
    winter_period: bool = False,
    replace_existing: bool = False,
) -> Path:
    """Install sanitized replay fixtures for public peak-data requests."""
    fixture_name = validate_fixture_name(fixture_name)
    response_folder = capture_root / "public"
    responses: list[CapturedResponse] = []
    for metadata_path in sorted(response_folder.glob("*-metadata.json")):
        metadata: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        request_url = str(metadata.get("request_url", ""))
        if "/oauth2/v2.0/token" in request_url.casefold():
            continue
        responses.append(
            CapturedResponse(
                "public",
                metadata,
                urlsplit(request_url).path,
                (response_folder / metadata["body_file"]).read_bytes(),
            )
        )

    expected_paths = {
        urlsplit(IS_HYDRO_PORTAL_UP_URL).path,
        urlsplit(OPEN_DATA_PEAK_URL).path,
    }
    captured_paths = {response.path for response in responses}
    if missing_paths := expected_paths - captured_paths:
        raise ValueError(
            "La capture publique est incomplète; réponse absente pour : "
            + ", ".join(sorted(missing_paths))
        )

    with tempfile.TemporaryDirectory(prefix="hydroqc-public-fixtures-") as temporary:
        staging_root = Path(temporary)
        public_folder = staging_root / "public"
        public_folder.mkdir()
        for sequence, response in enumerate(responses, start=1):
            content_type = next(
                (
                    value
                    for name, value in response.metadata.get("headers", [])
                    if name.casefold() == "content-type"
                ),
                "",
            )
            extension = (
                "json"
                if "json" in content_type.casefold()
                else "txt" if content_type.casefold().startswith("text/") else "bin"
            )
            stem = f"{sequence:04}"
            body_file = f"{stem}-response.{extension}"
            (public_folder / body_file).write_bytes(response.body)
            metadata = {
                "profile": "public",
                "sequence": sequence,
                "request_method": str(response.metadata["request_method"]).lower(),
                "request_path": response.path,
                "status": response.metadata["status"],
                "headers": _safe_headers(response.metadata),
                "body_file": body_file,
            }
            (public_folder / f"{stem}-metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        scenario = {
            "fixture_name": fixture_name,
            "reference_date": reference_date,
            "winter_period": winter_period,
            "clients": [
                {"rate": "DPC", "option": ""},
                {"rate": "D", "option": "CPC"},
            ],
        }
        (staging_root / "scenario.json").write_text(
            json.dumps(scenario, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _audit_staging_tree(staging_root)
        scenario_name = (
            "PUBLIC_PEAKS_WINTER" if winter_period else "PUBLIC_PEAKS_SUMMER"
        )
        destination = fixture_root / scenario_name / fixture_name
        _install_fixture_bundle(
            staging_root,
            destination,
            replace_existing=replace_existing,
        )

    return destination
