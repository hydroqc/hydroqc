"""Capture Hydro-Québec API responses for test fixtures."""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from getpass import getpass
from pathlib import Path
from typing import Any

from hydroqc import contract as contract_module
from hydroqc.account import Account
from hydroqc.capture_fixtures import (
    anonymize_capture_to_fixtures,
    install_public_capture_to_fixtures,
    validate_fixture_name,
)
from hydroqc.consts import LoginStatus
from hydroqc.contract.common import Contract
from hydroqc.customer import Customer
from hydroqc.error import HydroQcError
from hydroqc.hydro_api.client import HydroClient
from hydroqc.hydro_api.consts import AUTH_CLIENT_MOBILE, AUTH_CLIENT_WEB
from hydroqc.public_client import PublicClient
from hydroqc.types.contract import ContractTyping
from hydroqc.utils import EST_TIMEZONE
from hydroqc.webuser import WebUser

SESSION_DIR = Path.home() / ".hydroqc" / "capture"


def create_contract_object(
    client: HydroClient,
    context: tuple[str, str, str],
    contract_id: str,
    contract_info: ContractTyping,
) -> Contract:
    """Instantiate the contract subclass selected by its live rate data."""
    rate = contract_info["tarifActuel"]
    rate_option = contract_info.get("optionTarifActuel") or ""
    class_name = f"Contract{rate}{rate_option}"
    contract_class = getattr(
        contract_module,
        class_name,
        contract_module.ContractFallBack,
    )
    return contract_class(
        context[0],
        context[1],
        account_id=context[2],
        contract_id=contract_id,
        hydro_client=client,
    )


def discover_contract_surface(value: Any, *, depth: int = 1) -> dict[str, Any]:
    """Discover public members and typed subobjects using ``dir()``."""
    surface: dict[str, Any] = {
        "methods": [],
        "properties": [],
        "attributes": [],
        "subobjects": {},
    }
    for name in dir(value):
        if name.startswith("_"):
            continue
        member = inspect.getattr_static(value, name)
        if isinstance(member, property):
            surface["properties"].append(name)
            if depth and member.fget is not None:
                child_type = inspect.signature(member.fget).return_annotation
                if isinstance(child_type, type) and child_type not in {
                    str,
                    int,
                    float,
                    bool,
                    bytes,
                }:
                    if (child := getattr(value, name)) is not None:
                        surface["subobjects"][name] = discover_contract_surface(
                            child, depth=depth - 1
                        )
        elif inspect.isroutine(member) or callable(member):
            surface["methods"].append(name)
        else:
            surface["attributes"].append(name)
    return surface


@dataclass
class ContractScenarioResult:
    """Results from the shared contract lookup and inspection scenario."""

    context: tuple[str, str, str]
    contract_info: ContractTyping
    contract: Contract
    surface: dict[str, Any]
    called_methods: list[str]
    reference_date: date


@dataclass
class AuthenticatedContract:
    """Selected contract and its complete authenticated object hierarchy."""

    webuser: WebUser
    customer: Customer
    account: Account
    contract: Contract

    @property
    def context(self) -> tuple[str, str, str]:
        """Return applicant, customer, and account identifiers."""
        return (
            self.customer.applicant_id,
            self.customer.customer_id,
            self.account.account_id,
        )


class ResponseCaptureWriter:
    """Write unmodified response bodies and request metadata to a temp folder."""

    def __init__(self, folder: Path, profile: str) -> None:
        """Initialize a response writer for one authentication profile."""
        self.folder = folder
        self.profile = profile
        self.sequence = 0
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    def capture(self, url: str, method: str, response: Any, body: bytes) -> None:
        """Persist one response body as bytes plus a JSON metadata sidecar."""
        if "/oauth2/v2.0/token" in url.casefold():
            return

        self.sequence += 1
        stem = f"{self.sequence:04d}"
        body_path = self.folder / f"{stem}-response.bin"
        metadata_path = self.folder / f"{stem}-metadata.json"
        body_path.write_bytes(body)
        metadata_path.write_text(
            json.dumps(
                {
                    "profile": self.profile,
                    "sequence": self.sequence,
                    "request_method": method,
                    "request_url": url,
                    "response_url": str(response.url),
                    "status": response.status,
                    "headers": [
                        [name.decode("latin-1"), value.decode("latin-1")]
                        for name, value in response.raw_headers
                    ],
                    "body_file": body_path.name,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if os.name == "posix":
            body_path.chmod(0o600)
            metadata_path.chmod(0o600)


def _session_path(profile: str, username: str) -> Path:
    """Return a profile-specific session file without exposing the username."""
    username_key = hashlib.sha256(
        username.strip().casefold().encode("utf-8")
    ).hexdigest()[:16]
    return SESSION_DIR / f"session-{profile}-{username_key}.json"


def _contract_ids_match(requested: str, candidate: Any) -> bool:
    """Compare contract IDs while tolerating numeric IDs without leading zeros."""
    requested_id = str(requested).strip()
    candidate_id = str(candidate).strip()
    if requested_id == candidate_id:
        return True
    if requested_id.isdecimal() and candidate_id.isdecimal():
        return (requested_id.lstrip("0") or "0") == (candidate_id.lstrip("0") or "0")
    return False


def _save_session(client: HydroClient, profile: str, username: str) -> None:
    """Persist a session state atomically with user-only file permissions."""
    SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        SESSION_DIR.chmod(0o700)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=SESSION_DIR,
            prefix=f".session-{profile}-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(client.export_session_state(), temporary_file, indent=2)
            temporary_file.write("\n")

        if os.name == "posix":
            temporary_path.chmod(0o600)
        temporary_path.replace(_session_path(profile, username))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def _restore_session(client: HydroClient, profile: str, username: str) -> bool:
    """Restore and validate a saved session, if one exists."""
    session_path = _session_path(profile, username)
    try:
        state: Any = json.loads(session_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError) as error:
        print(f"Session {profile} illisible ({error}); une reconnexion sera requise.")
        return False

    if not isinstance(state, dict):
        print(f"Session {profile} invalide; une reconnexion sera requise.")
        return False

    client.import_session_state(state)
    await client._get_httpsession()
    try:
        if client.is_session_expired():
            if await client.try_silent_login() is not LoginStatus.SUCCESS:
                return False
        else:
            # Refresh an expired access token using the persisted refresh token.
            await client._get_access_token(force_refresh=False)
    except HydroQcError:
        return False

    return (
        bool(client.access_token)
        and client.access_token_expiry > datetime.now()
        and not client.is_session_expired()
    )


def purge_sessions(username: str) -> list[Path]:
    """Delete the saved sessions (tokens and cookies) for both profiles."""
    removed: list[Path] = []
    for profile in (AUTH_CLIENT_WEB, AUTH_CLIENT_MOBILE):
        session_path = _session_path(profile, username)
        try:
            session_path.unlink()
        except FileNotFoundError:
            continue
        removed.append(session_path)
    return removed


def prompt_session_cleanup(username: str) -> None:
    """Ask whether to keep or purge the sessions written during the capture."""
    answer = (
        input(  # pylint: disable=bad-builtin
            "Conserver les sessions et cookies enregistrés pour les prochaines "
            "captures ? [O/n] : "
        )
        .strip()
        .casefold()
    )
    if answer in {"", "o", "oui", "y", "yes"}:
        print(f"Sessions et cookies conservés dans {SESSION_DIR}.")
        return

    if removed := purge_sessions(username):
        print(
            "Sessions et cookies supprimés : "
            + ", ".join(path.name for path in removed)
        )
    else:
        print("Aucune session enregistrée à supprimer.")


def prompt_fixture_set(
    fixture_root: Path,
    scenario_name: str,
    fixture_name: str | None = None,
    *,
    replace_fixture: bool = False,
) -> tuple[str, bool]:
    """Choose a fixture-set name and require an explicit replacement choice."""
    if replace_fixture and fixture_name is None:
        raise ValueError("--replace-fixture nécessite --fixture-name.")

    scenario_root = fixture_root / scenario_name
    existing_names = (
        sorted(
            path.name
            for path in scenario_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        if scenario_root.is_dir()
        else []
    )

    if fixture_name is not None:
        fixture_name = validate_fixture_name(fixture_name)
        exists = (
            fixture_name in existing_names or (scenario_root / fixture_name).exists()
        )
        if exists and not replace_fixture:
            raise FileExistsError(
                f"Le jeu {scenario_name}/{fixture_name} existe déjà. "
                "Utilisez --replace-fixture pour confirmer son remplacement, "
                "ou choisissez un autre --fixture-name."
            )
        if replace_fixture and not exists:
            raise FileNotFoundError(
                f"Le jeu {scenario_name}/{fixture_name} à remplacer n’existe pas."
            )
        return fixture_name, replace_fixture

    if existing_names:
        print(f"Jeux de fixtures déjà présents pour {scenario_name} :")
        for index, name in enumerate(existing_names, start=1):
            print(f"  {index}. Remplacer {name}")
        print("  n. Créer un nouveau jeu")
        while True:
            choice_input = input("Votre choix : ")  # pylint: disable=bad-builtin
            if (choice := choice_input.strip().casefold()) in {
                "n",
                "nouveau",
                "new",
            }:
                break
            if choice.isdecimal() and 1 <= int(choice) <= len(existing_names):
                selected_name = existing_names[int(choice) - 1]
                confirmation = (
                    input(  # pylint: disable=bad-builtin
                        f"Remplacer entièrement {scenario_name}/{selected_name} ? [o/N] : "
                    )
                    .strip()
                    .casefold()
                )
                if confirmation in {"o", "oui", "y", "yes"}:
                    return selected_name, True
                print("Remplacement annulé.")
                continue
            print("Choix invalide; sélectionnez un numéro ou « n ».")

    while True:
        entered_name = input(  # pylint: disable=bad-builtin
            "Nom neutre du nouveau jeu de fixtures (ex. jeu-02) : "
        ).strip()
        try:
            entered_name = validate_fixture_name(entered_name)
        except ValueError as error:
            print(error)
            continue
        if entered_name in existing_names or (scenario_root / entered_name).exists():
            print("Ce nom existe déjà; choisissez un autre nom ou sélectionnez-le.")
            continue
        return entered_name, False


async def find_contract_context(
    client: HydroClient, contract_id: str
) -> tuple[str, str, str]:
    """Find the applicant, customer, and account IDs for a contract."""
    matches: set[tuple[str, str, str]] = set()
    customers: set[tuple[str, str]] = set()
    relation_count = 0
    account_count = 0
    available_contract_count = 0

    for relation in await client.get_user_info():
        relation_count += 1
        applicant_id = relation["noPartenaireDemandeur"]
        customer_id = relation["noPartenaireTitulaire"]
        if (customer := (applicant_id, customer_id)) in customers:
            continue
        customers.add(customer)

        summary = await client.list_account_contract(applicant_id, customer_id)
        accounts = summary["comptesContrats"]
        account_count += len(accounts)
        for account in accounts:
            available_contract_count += len(account["listeNoContrat"])
            if any(
                _contract_ids_match(contract_id, candidate)
                for candidate in account["listeNoContrat"]
            ):
                matches.add((applicant_id, customer_id, account["noCompteContrat"]))

    if not matches:
        raise RuntimeError(
            f"Contrat {contract_id} introuvable dans les comptes accessibles. "
            f"L’API a retourné {relation_count} relation(s), {account_count} "
            f"compte(s) et {available_contract_count} contrat(s). "
            "Vérifiez que --contract contient bien le numéro de contrat "
            "(et non le numéro de compte) et qu’il est lié à ce compte Hydro-Québec."
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"Le contrat {contract_id} correspond à plusieurs comptes; "
            "la sélection est ambiguë."
        )
    return matches.pop()


async def run_contract_scenario(
    client: HydroClient,
    contract_id: str,
    reference_date: date | None = None,
) -> ContractScenarioResult:
    """Run the contract lookup and all discovered public contract operations."""
    context = await find_contract_context(client, contract_id)
    contract_info = await client.get_contract_info(*context, contract_id)
    contract = create_contract_object(client, context, contract_id, contract_info)
    return await run_contract_object_scenario(
        context, contract_info, contract, reference_date
    )


async def run_webuser_contract_scenario(
    client: HydroClient,
    contract_id: str,
    profile: str,
    reference_date: date | None = None,
) -> ContractScenarioResult:
    """Load the selected contract through WebUser → Customer → Account."""
    selected = await load_webuser_contract(client, contract_id, profile)
    return await run_contract_object_scenario(
        selected.context,
        selected.contract._raw_info_data,  # pylint: disable=protected-access
        selected.contract,
        reference_date,
    )


async def load_webuser_contract(
    client: HydroClient, contract_id: str, profile: str
) -> AuthenticatedContract:
    """Build the WebUser → Customer → Account → Contract hierarchy from APIs."""
    webuser = WebUser(
        "fixture@example.invalid",
        "",
        verify_ssl=True,
        auth_client=profile,
    )
    # Reuse the authenticated/captured client rather than opening a second session.
    webuser._hydro_client = client  # pylint: disable=protected-access
    await webuser.get_info()
    await webuser.fetch_customers_info()

    matches: list[AuthenticatedContract] = []
    for customer in webuser.customers:
        for account in customer.accounts:
            for contract in account.contracts:
                if _contract_ids_match(contract_id, contract.contract_id):
                    matches.append(
                        AuthenticatedContract(webuser, customer, account, contract)
                    )
    if not matches:
        raise RuntimeError(
            f"Contrat {contract_id} absent du parcours WebUser → Customer → Account."
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"Le contrat {contract_id} correspond à plusieurs comptes; "
            "la sélection est ambiguë."
        )

    return matches[0]


async def run_contract_object_scenario(
    context: tuple[str, str, str],
    contract_info: ContractTyping,
    contract: Contract,
    reference_date: date | None = None,
) -> ContractScenarioResult:
    """Exercise all discovered operations on an already initialized contract."""
    surface = discover_contract_surface(contract)
    reference_date = reference_date or datetime.now(EST_TIMEZONE).date()
    targets: list[tuple[str, Any, dict[str, Any]]] = [("contract", contract, surface)]
    for name, child_surface in surface["subobjects"].items():
        targets.append((f"contract.{name}", getattr(contract, name), child_surface))

    called_methods: list[str] = []

    async def call_method(target_name: str, target: Any, method_name: str) -> None:
        method = getattr(target, method_name)
        arguments = _method_arguments(method, reference_date)
        result = method(**arguments)
        if inspect.isawaitable(result):
            await result
        called_methods.append(f"{target_name}.{method_name}")

    # Initialize data required by later operations before the dir()-discovered pass.
    prerequisites = {
        "contract": ("get_info", "get_periods_info"),
        "contract.peak_handler": ("refresh_data", "refresh_open_data"),
    }
    called_by_target: dict[str, set[str]] = {name: set() for name, _, _ in targets}
    for target_name, target, target_surface in targets:
        for method_name in prerequisites.get(target_name, ()):
            if method_name in target_surface["methods"]:
                await call_method(target_name, target, method_name)
                called_by_target[target_name].add(method_name)

    for target_name, target, target_surface in targets:
        for method_name in target_surface["methods"]:
            if method_name not in called_by_target[target_name]:
                await call_method(target_name, target, method_name)

    return ContractScenarioResult(
        context, contract_info, contract, surface, called_methods, reference_date
    )


def _method_arguments(method: Any, reference_date: date) -> dict[str, Any]:
    """Build the small set of argument types used by public contract methods."""
    arguments: dict[str, Any] = {}
    for parameter in inspect.signature(method).parameters.values():
        if parameter.default is not inspect.Parameter.empty:
            continue
        if parameter.annotation is date:
            if parameter.name == "start_date":
                arguments[parameter.name] = reference_date - timedelta(days=3)
            else:
                arguments[parameter.name] = reference_date - timedelta(days=2)
        elif parameter.annotation is int:
            arguments[parameter.name] = 60
        else:
            raise TypeError(
                f"No capture argument rule for {parameter.name!r} "
                f"in {method.__qualname__}"
            )
    return arguments


@dataclass(frozen=True)
class CapturableContract:
    """Contract that can be captured with the authenticated account."""

    contract_id: str
    rate: str
    rate_option: str

    @property
    def label(self) -> str:
        """Return the human-readable contract label."""
        return f"Contrat {self.contract_id} - {self.rate}{self.rate_option}"


async def list_available_contracts(client: HydroClient) -> list[CapturableContract]:
    """Enumerate every contract reachable with an authenticated client."""
    contracts: list[CapturableContract] = []
    seen: set[str] = set()
    customers: set[tuple[str, str]] = set()
    for relation in await client.get_user_info():
        applicant_id = relation["noPartenaireDemandeur"]
        customer_id = relation["noPartenaireTitulaire"]
        if (applicant_id, customer_id) in customers:
            continue
        customers.add((applicant_id, customer_id))

        summary = await client.list_account_contract(applicant_id, customer_id)
        for account in summary["comptesContrats"]:
            for contract_id in account["listeNoContrat"]:
                if (key := contract_id.lstrip("0") or "0") in seen:
                    continue
                seen.add(key)
                contract_info = await client.get_contract_info(
                    applicant_id,
                    customer_id,
                    account["noCompteContrat"],
                    contract_id,
                )
                contracts.append(
                    CapturableContract(
                        contract_id=contract_id,
                        rate=contract_info["tarifActuel"],
                        rate_option=contract_info.get("optionTarifActuel") or "",
                    )
                )
    return contracts


async def prompt_contract_selection(client: HydroClient) -> str:
    """Prompt for the contract to capture once the login has succeeded."""
    if not (contracts := await list_available_contracts(client)):
        raise RuntimeError(
            "Aucun contrat accessible avec ce compte. Vérifiez que le compte "
            "Hydro-Québec est lié à au moins un contrat."
        )

    print("Contrats accessibles :")
    for index, contract in enumerate(contracts, start=1):
        print(f"  {index}. {contract.label}")

    selected: CapturableContract | None = None
    while selected is None:
        choice = input(  # pylint: disable=bad-builtin
            f"Contrat à capturer [1-{len(contracts)}] : "
        ).strip()
        if not choice and len(contracts) == 1:
            selected = contracts[0]
            break
        if choice.isdecimal() and 1 <= int(choice) <= len(contracts):
            selected = contracts[int(choice) - 1]
            break
        selected = next(
            (
                contract
                for contract in contracts
                if _contract_ids_match(choice, contract.contract_id)
            ),
            None,
        )
        if selected is None:
            print(f"Sélection invalide : {choice!r}. Réessayez.")

    print(f"Contrat sélectionné : {selected.label}")
    return selected.contract_id


async def authenticate_clients(username: str) -> dict[str, HydroClient]:
    """Restore or authenticate and return the web and mobile clients."""
    clients: list[tuple[str, HydroClient]] = []
    password: str | None = None
    try:
        for profile in (AUTH_CLIENT_WEB, AUTH_CLIENT_MOBILE):
            client = HydroClient(
                username,
                "",
                log_level="ERROR",
                auth_client=profile,
                enable_cache=False,
            )
            clients.append((profile, client))

            if await _restore_session(client, profile, username):
                print(f"Session restaurée avec le profil {profile}.")
                _save_session(client, profile, username)
                continue

            if password is None:
                password = getpass("Mot de passe Hydro-Québec : ")
            client.password = password
            status = await client.login()
            if status is LoginStatus.MFA_REQUIRED:
                code = input(  # pylint: disable=bad-builtin
                    f"Code MFA reçu pour le profil {profile} : "
                ).strip()
                status = await client.submit_mfa_code(code)

            if status is not LoginStatus.SUCCESS:
                raise RuntimeError(f"Échec de l’authentification du profil {profile}")

            _save_session(client, profile, username)
            print(f"Authentification réussie avec le profil {profile}.")
    except BaseException:
        for _, client in clients:
            await client.close_session()
        raise

    return dict(clients)


async def run_capture(
    username: str,
    contract_id: str | None = None,
    *,
    keep_raw: bool = False,
    winter_period: bool = False,
    fixture_name: str | None = None,
    replace_fixture: bool = False,
) -> None:
    """Capture, pseudonymize, and install fixture responses for both profiles.

    When ``contract_id`` is omitted, the available contracts are listed after
    login and the user picks one interactively, like the Home Assistant config
    flow does.
    """
    clients = await authenticate_clients(username)
    if contract_id is None:
        contract_id = await prompt_contract_selection(next(iter(clients.values())))
    capture_dir = Path(tempfile.mkdtemp(prefix="hydroqc-capture-"))
    reference_date = datetime.now(EST_TIMEZONE).date()
    try:
        for profile, client in clients.items():
            writer = ResponseCaptureWriter(capture_dir / profile, profile)
            client.response_capture_callback = writer.capture
            scenario = await run_webuser_contract_scenario(
                client, contract_id, profile, reference_date
            )
            fixture_type = (
                f"{scenario.contract.rate}_{scenario.contract.rate_option}"
                if scenario.contract.rate_option
                else scenario.contract.rate
            )
            client.response_capture_callback = None
            print(
                f"Contrat {contract_id} capturé avec le profil {profile} "
                f"(type {fixture_type}; "
                f"{len(scenario.surface['methods'])} méthodes, "
                f"{len(scenario.surface['properties'])} propriétés découvertes; "
                f"{len(scenario.called_methods)} méthodes appelées)."
            )

        fixture_root = Path(__file__).parents[1] / "tests" / "fixtures"
        fixture_type = (
            f"{scenario.contract.rate}_{scenario.contract.rate_option}"
            if scenario.contract.rate_option
            else scenario.contract.rate
        )
        fixture_scenario = f"{fixture_type}_{'WINTER' if winter_period else 'SUMMER'}"
        fixture_name, replace_fixture = prompt_fixture_set(
            fixture_root,
            fixture_scenario,
            fixture_name,
            replace_fixture=replace_fixture,
        )
        fixture_type = anonymize_capture_to_fixtures(
            capture_dir,
            contract_id,
            fixture_root,
            reference_date.isoformat(),
            winter_period=winter_period,
            fixture_name=fixture_name,
            replace_existing=replace_fixture,
        )
        print(
            "Réponses pseudonymisées copiées dans "
            f"{fixture_root / fixture_type / fixture_name} "
            f"(mesures et dates conservées; date de référence {reference_date}"
            f"; période hivernale : {'oui' if winter_period else 'non'})."
        )
        if keep_raw:
            print(f"Capture brute conservée dans : {capture_dir}")
        else:
            shutil.rmtree(capture_dir)
            print("Capture brute supprimée après installation des fixtures.")
    except BaseException:
        print(f"Capture brute conservée pour diagnostic dans : {capture_dir}")
        raise
    finally:
        for client in clients.values():
            client.response_capture_callback = None
        for client in clients.values():
            await client.close_session()

    prompt_session_cleanup(username)


async def run_public_capture(
    *,
    keep_raw: bool = False,
    winter_period: bool = False,
    fixture_name: str | None = None,
    replace_fixture: bool = False,
) -> None:
    """Capture public peak data and install replay fixtures without login."""
    fixture_root = Path(__file__).parents[1] / "tests" / "fixtures"
    fixture_name, replace_fixture = prompt_fixture_set(
        fixture_root,
        "PUBLIC_PEAKS_WINTER" if winter_period else "PUBLIC_PEAKS_SUMMER",
        fixture_name,
        replace_fixture=replace_fixture,
    )
    capture_dir = Path(tempfile.mkdtemp(prefix="hydroqc-public-capture-"))
    client = PublicClient(
        rate_code="DPC",
        log_level="ERROR",
        http_log_level="ERROR",
    )
    try:
        writer = ResponseCaptureWriter(capture_dir / "public", "public")
        client._hydro_client.response_capture_callback = (  # pylint: disable=protected-access
            writer.capture
        )
        try:
            portal_status = await client.check_hq_portal_status()
            await client.fetch_peak_data()
        finally:
            client._hydro_client.response_capture_callback = (
                None  # pylint: disable=protected-access
            )
            await client.close_session()

        reference_date = datetime.now(EST_TIMEZONE).date().isoformat()
        fixture_path = install_public_capture_to_fixtures(
            capture_dir,
            fixture_root,
            reference_date,
            fixture_name=fixture_name,
            winter_period=winter_period,
            replace_existing=replace_fixture,
        )
        print(
            "Données publiques capturées via PublicClient "
            f"(portail disponible : {'oui' if portal_status else 'non'})."
        )
        print(
            "Fixture publique installée dans : "
            f"{fixture_path} (réutilisable pour DPC et D_CPC; "
            f"période hivernale : {'oui' if winter_period else 'non'})."
        )
        if keep_raw:
            print(f"Capture brute conservée dans : {capture_dir}")
        else:
            shutil.rmtree(capture_dir)
            print("Capture brute supprimée après installation des fixtures.")
    except BaseException:
        print(f"Capture brute conservée pour diagnostic dans : {capture_dir}")
        raise


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Capture Hydro-Québec API data.")
    parser.add_argument(
        "--username",
        help="Hydro-Québec username (prompted if omitted).",
    )
    parser.add_argument(
        "--contract",
        help="Contract number to capture (prompted after login when omitted).",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Capture public portal and peak data without Hydro-Québec login.",
    )
    parser.add_argument(
        "--keep-raw",
        action="store_true",
        help="Keep the sensitive raw capture after fixture installation.",
    )
    parser.add_argument(
        "--winter-period",
        action="store_true",
        help=(
            "Mark the capture as a winter-period scenario and install it in "
            "a separate *_WINTER fixture directory."
        ),
    )
    parser.add_argument(
        "--fixture-name",
        help="Nom neutre du jeu de fixtures (sans donnée personnelle).",
    )
    parser.add_argument(
        "--replace-fixture",
        action="store_true",
        help="Confirmer le remplacement du jeu indiqué avec --fixture-name.",
    )
    return parser.parse_args()


def main() -> None:
    """Restore or authenticate each profile and persist its session state."""
    args = parse_args()
    if args.public:
        if args.username is not None:
            raise SystemExit("--public ne nécessite pas --username.")
        if args.contract is not None:
            raise SystemExit("--public ne peut pas être combiné avec --contract.")
        if args.replace_fixture and args.fixture_name is None:
            raise SystemExit("--replace-fixture nécessite --fixture-name.")
        if args.fixture_name is not None:
            try:
                validate_fixture_name(args.fixture_name)
            except ValueError as error:
                raise SystemExit(str(error)) from error
        asyncio.run(
            run_public_capture(
                keep_raw=args.keep_raw,
                winter_period=args.winter_period,
                fixture_name=args.fixture_name,
                replace_fixture=args.replace_fixture,
            )
        )
        return

    if not (
        username := (
            args.username
            or input("Nom d’utilisateur Hydro-Québec : ")  # pylint: disable=bad-builtin
        ).strip()
    ):
        raise SystemExit("Le nom d’utilisateur ne peut pas être vide.")
    if args.contract is not None and not args.contract.strip():
        raise SystemExit("Le numéro de contrat ne peut pas être vide.")
    if args.replace_fixture and args.fixture_name is None:
        raise SystemExit("--replace-fixture nécessite --fixture-name.")
    if args.fixture_name is not None:
        try:
            validate_fixture_name(args.fixture_name)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    contract_id = args.contract.strip() if args.contract is not None else None
    print(
        "Les sessions seront conservées localement sans chiffrement dans "
        f"{SESSION_DIR}. Elles contiennent des jetons et des cookies. "
        "Un choix de les conserver ou de les supprimer sera proposé à la fin."
    )
    asyncio.run(
        run_capture(
            username,
            contract_id,
            keep_raw=args.keep_raw,
            winter_period=args.winter_period,
            fixture_name=args.fixture_name,
            replace_fixture=args.replace_fixture,
        )
    )
