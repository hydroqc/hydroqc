"""Hydroqc DPC types."""

# pylint: disable=invalid-name
from typing import TypedDict


class DPCPeriodeHiverTyping(TypedDict, total=False):
    """FlexD winter period data (API v3_0 format).

    Note: montantEconPerteVSTarifBase may not be available in the first year of Flex D
    as it requires comparison data from previous Rate D billing.

    Note: hrsCritiquesAppeleesMax may be absent for the upcoming winter period
    until Hydro-Québec publishes it, so it must be read with ``.get()``.
    """

    dateDebut: str
    dateFin: str
    dateMaj: str
    hrsCritiquesAppelees: str
    hrsCritiquesAppeleesMax: str
    montantEconPerteVSTarifBase: float


class DPCEvenementPointeTyping(TypedDict, total=True):
    """FlexD peak event data (API v3_0 format)."""

    dateEvenement: str
    heureDebut: str
    heureFin: str
    consoReelle: float
    codeConso: str
    indFacture: bool


class DPCEvenementsPointeWrapperTyping(TypedDict, total=True):
    """FlexD peak events wrapper (API v3_0 format)."""

    dateDebutPeriodeHiver: str
    dateFinPeriodeHiver: str
    evenementsPointe: list[DPCEvenementPointeTyping]


class DPCDataTyping(TypedDict, total=False):
    """FlexD data json output format (API v3_0)."""

    dateTestAffichageWeb: str
    periodesHiver: list[DPCPeriodeHiverTyping]
    evenementsPointe: list[DPCEvenementsPointeWrapperTyping]


class DPCPeakDataTyping(TypedDict, total=True):
    """DPC Peak data json output format."""

    dateDebut: str
    dateFin: str


class DPCPeakListDataTyping(TypedDict, total=True):
    """DPC Peak list data json output format."""

    codeEtatPeriodeCourante: str
    dateDebutPeriode: str
    dateFinPeriode: str
    dateDerniereLecturePeriode: str
    nbJourLecturePeriode: int
    nbJourPrevuPeriode: int
    montantFacturePeriode: float
    montantProjetePeriode: float
    indContratPuissance: bool
    adresseLieuConsoPartie1: str
    adresseLieuConsoPartie2: str
    codeTarif: str
    codeOptionTarif: str | None
    listePeriodePointeCritiqueAujourdhui: None | list[DPCPeakDataTyping]
    listePeriodePointeCritiqueDemain: None | list[DPCPeakDataTyping]
