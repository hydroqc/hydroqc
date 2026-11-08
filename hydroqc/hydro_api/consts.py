"""Hydroqc API Consts.

.. todo::

    avoid all the /portail/ URLs
"""

from dataclasses import dataclass

# Always get the time using HydroQuebec Local Time
REQUESTS_TIMEOUT = 30
REQUESTS_TTL = 1

HOST_LOGIN = "https://connexion.solutions.hydroquebec.com"
HOST_SESSION = "https://session.hydroquebec.com"
HOST_SERVICES = "https://services-cl.solutions.hydroquebec.com"
# HOST_SPRING = "https://cl-ec-lsw.solutions.hydroquebec.com"
HOST_RB_SOL = "https://rb.solutions.hydroquebec.com"
HOST_OUTAGES = "https://services-bs.solutions.hydroquebec.com"
HOST_OPEN_DATA = "https://donnees.solutions.hydroquebec.com"

# Azure B2C
AZB2C_TENANT_ID = "32bf9b91-0a36-4385-b231-d9a8fa3b05ab"
AZB2C_POLICY = "B2C_1A_PRD_signup_signin"
AZB2C_CLIENT_ID_WEB = "09b0ae72-6db8-4ecc-a1be-041b67afc1cd"
AZB2C_CLIENT_ID_MOBILE = "70cd7b23-de9a-4d74-8592-d378afbfb863"
AZB2C_RESPONSE_TYPE = "code"
AZB2C_SCOPE_WEB = (
    "openid https://connexionhq.onmicrosoft.com/hq-clientele/Espace.Client"
)
AZB2C_POLICY_MOBILE = "B2C_1A_PRD_signup_signin_mobile"
AZB2C_SCOPE_MOBILE = (
    "openid offline_access "
    "https://connexionhq.onmicrosoft.com/hq-clientele-mobile/Espace.Client"
)
AZB2C_CODE_CHALLENGE_METHOD = "S256"

# Time to remove from the token expiration time to avoid calls to fail
AZB2C_TIMEOUT_SKEW_SECS = 60

# Outages
OUTAGES = f"{HOST_OUTAGES}/pan/web/api/v1/lieux-conso/etats/"
# OAUTH PATHS
AUTHORIZE_URL = (
    f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{AZB2C_POLICY.lower()}/oauth2/v2.0/authorize"
)
AUTH_URL = f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{AZB2C_POLICY}/SelfAsserted"
AUTH_CALLBACK_URL = f"{HOST_SESSION}/oauth2/callback"
AUTH_CALLBACK_URL_MOBILE = (
    "msauth://com.hydroquebec.mf_android/2TD6UgHUYzHyQirztabBgWTrJNs%3D"
)
TOKEN_URL = f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{AZB2C_POLICY.lower()}/oauth2/v2.0/token"

CHECK_SESSION_URL = f"{HOST_LOGIN}/hqam/oauth2/connect/checkSession"


SECURITY_URL = f"{HOST_SESSION}/config/security.json"
SESSION_REFRESH_URL = f"{HOST_SESSION}/oauth2/callback/silent-refresh"
LOGIN_URL_6 = f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/conversion/codeAcces"

# OPEN DATA PEAK URL
OPEN_DATA_PEAK_URL = (
    f"{HOST_OPEN_DATA}/donnees-ouvertes/data/json/pointeshivernales.json"
)

# Initialization uri
RELATION_URL = f"{HOST_SERVICES}/wsapi/web/prive/api/v1_0/relations"

FLEXD_DATA_URL = (
    f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/"
    "tarificationDynamique/tarifPointeCritique"
)
FLEXD_PEAK_URL = f"{HOST_SERVICES}/conso/portraitweb/api/v3_0/conso"
CONSO_CSV_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceTelechargerDonneesConsommation"
)
CONSO_OVERVIEW_CSV_URL = (
    f"{HOST_SERVICES}/lsw/portail/en/group/clientele/portrait-de-consommation/"
    "resourceTelechargerPeriodesFacturation"
)

# TODO avoid all the /portail/ URLs
SESSION_URL = f"{HOST_SERVICES}/lsw/portail/prive/maj-session/"
# TODO avoid all the /portail/ URLs
CONTRACT_HTML_URL = f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/gerer-mon-compte"
#
INFOBASE_URL = f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/partenaires/infoBase"
CONTRACT_SUMMARY_URL = (
    f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/partenaires/"
    "calculerSommaireContractuel?indMAJNombres=true"
)
CONTRACT_LIST_URL = f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/partenaires/contrats"

CUSTOMER_INFO_URL = f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/partenaires/infoCompte"

# TODO avoid all the /portail/ URLs
PORTRAIT_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation"
)
# TODO avoid all the /portail/ URLs
PERIOD_DATA_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesPeriodesConsommation"
)

# TODO avoid all the /portail/ URLs
ANNUAL_DATA_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesConsommationAnnuelles"
)

# TODO avoid all the /portail/ URLs
MONTHLY_DATA_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesConsommationMensuelles"
)

# TODO avoid all the /portail/ URLs
DAILY_CONSUMPTION_API_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesQuotidiennesConsommation"
)

# TODO avoid all the /portail/ URLs
HOURLY_CONSUMPTION_API_URL = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesConsommationHoraires"
)
# TODO avoid all the /portail/ URLs
HOURLY_DATA_URL_2 = (
    f"{HOST_SERVICES}/lsw/portail/fr/group/clientele/portrait-de-consommation/"
    "resourceObtenirDonneesMeteoHoraires"  # not used
)

# CPC
GET_CPC_API_URL = (
    f"{HOST_SERVICES}/wsapi/web/prive/api/v3_0/tarificationDynamique/"
    "creditPointeCritique"
)
# IS PORTAL RUNNING
IS_HYDRO_PORTAL_UP_URL = f"{HOST_SESSION}/portail/fr/group/clientele/gerer-mon-compte"


@dataclass(frozen=True)
class AuthClient:
    """Azure B2C client used to log in, with the private API it can call."""

    client_id: str
    policy: str
    scope: str
    redirect_uri: str
    # Path segment of the private API: /wsapi/<api_channel>/prive/...
    api_channel: str

    @property
    def authorize_url(self) -> str:
        """Return the authorize endpoint of the policy."""
        return (
            f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{self.policy.lower()}"
            "/oauth2/v2.0/authorize"
        )

    @property
    def auth_url(self) -> str:
        """Return the SelfAsserted endpoint of the policy."""
        return f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{self.policy}/SelfAsserted"

    @property
    def token_url(self) -> str:
        """Return the token endpoint of the policy."""
        return f"{HOST_LOGIN}/{AZB2C_TENANT_ID}/{self.policy.lower()}/oauth2/v2.0/token"

    def api_url(self, url: str) -> str:
        """Return a private API URL (declared on /wsapi/web/) for this client."""
        return url.replace("/wsapi/web/", f"/wsapi/{self.api_channel}/", 1)


AUTH_CLIENT_WEB = "web"
AUTH_CLIENT_MOBILE = "mobile"
AUTH_CLIENTS = {
    AUTH_CLIENT_WEB: AuthClient(
        client_id=AZB2C_CLIENT_ID_WEB,
        policy=AZB2C_POLICY,
        scope=AZB2C_SCOPE_WEB,
        redirect_uri=AUTH_CALLBACK_URL,
        api_channel="web",
    ),
    # Client of the official Android app: offline_access gives it a 90-day
    # sliding refresh token, so the e-mail code is not asked every day.
    AUTH_CLIENT_MOBILE: AuthClient(
        client_id=AZB2C_CLIENT_ID_MOBILE,
        policy=AZB2C_POLICY_MOBILE,
        scope=AZB2C_SCOPE_MOBILE,
        redirect_uri=AUTH_CALLBACK_URL_MOBILE,
        api_channel="mobile",
    ),
}
