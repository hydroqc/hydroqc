"""Basics tests for hydroqc."""

import parameterized  # type: ignore
import pytest

from .subtest_common import SubTestCommon
from .subtest_contract_d import SubTestContractD
from .subtest_contract_d_cpc import SubTestContractDCPC
from .subtest_contract_dpc import SubTestContractDPC
from .subtest_contract_dt import SubTestContractDT
from .subtest_contract_m import SubTestContractM
from .subtest_contract_m_gdp import SubTestContractMGDP
from .tools import get_env_vars

if not (LIVE_ACCOUNT_CASES := get_env_vars()):
    pytest.skip(
        "Configure Hydro-Québec account variables to run live-account tests.",
        allow_module_level=True,
    )


def disable_non_parameterized_tests(
    cls: object,  # pylint: disable=unused-argument
) -> object:
    """Without that non parameterized tests are running.

    The empty decorator disabled them, but I don't know why
    This decorator behavior could stop at any Python update
    """

    class Wrapper:  # pylint: disable=too-few-public-methods
        """Empty class."""

    return Wrapper


@disable_non_parameterized_tests
@parameterized.parameterized_class(
    (
        "NAME",
        "HYDROQC_USERNAME",
        "HYDROQC_PASSWORD",
        "HYDROQC_CUSTOMER_NUMBER",
        "HYDROQC_ACCOUNT_NUMBER",
        "HYDROQC_CONTRACT_NUMBER",
        "HYDROQC_RATE",
        "HYDROQC_RATE_OPTION",
        "HYDROQC_EPP_ENABLED",
        "HYDROQC_WC_ENABLED",
        "HYDROQC_ACCOUNT_ID",
    ),
    LIVE_ACCOUNT_CASES,
)
class TestBase(
    SubTestContractMGDP,
    SubTestContractM,
    SubTestContractDPC,
    SubTestContractDT,
    SubTestContractDCPC,
    SubTestContractD,
    SubTestCommon,
):
    """Entry base class for tests."""

    NAME: str
    HYDROQC_USERNAME: str
    HYDROQC_PASSWORD: str
    HYDROQC_CUSTOMER_NUMBER: str
    HYDROQC_ACCOUNT_NUMBER: str
    HYDROQC_CONTRACT_NUMBER: str
    HYDROQC_RATE: str
    HYDROQC_RATE_OPTION: str
    HYDROQC_EPP_ENABLED: str
    HYDROQC_WC_ENABLED: bool
    HYDROQC_ACCOUNT_ID: int
