"""Test tools."""

import datetime
import functools
import os
from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

import pytest

from hydroqc.contract.common import Contract
from hydroqc.utils import EST_TIMEZONE

PARAMS_PREFIX = "HYDROQC"
PARAMS_LIST = (
    ("USERNAME", lambda x: os.environ[x]),
    ("PASSWORD", lambda x: os.environ[x]),
    (
        "CUSTOMER_NUMBER",
        lambda x: os.environ.get(x),  # pylint: disable=unnecessary-lambda
    ),
    (
        "ACCOUNT_NUMBER",
        lambda x: os.environ.get(x),  # pylint: disable=unnecessary-lambda
    ),
    (
        "CONTRACT_NUMBER",
        lambda x: os.environ.get(x),  # pylint: disable=unnecessary-lambda
    ),
    ("RATE", lambda x: os.environ[x]),
    ("RATE_OPTION", lambda x: os.environ.get(x, "")),
    ("EPP_ENABLED", lambda x: os.environ[x].lower() == "true"),
    (
        "WC_ENABLED",
        lambda x: os.environ[x].lower() == "true" if x in os.environ else None,
    ),
    ("ACCOUNT_ID", lambda x: int(os.environ.get(x, 0))),
)


today = datetime.datetime.today().astimezone(EST_TIMEZONE).date()
today_str = today.strftime("%Y-%m-%d")
yesterday = today - datetime.timedelta(days=1)
yesterday_str = yesterday.strftime("%Y-%m-%d")
lastweekday = today - datetime.timedelta(days=7)
lastweekday_str = lastweekday.strftime("%Y-%m-%d")
tomorrow = today + datetime.timedelta(days=1)
tomorrow_str = tomorrow.strftime("%Y-%m-%d")


def get_env_vars() -> list[list[str | bool | int | None]]:
    """Fetch all env var available for test loop."""
    params_list = []
    # TODO DEPRECATED
    try:
        deprecated_params: list[str | bool | int | None] = [
            param[1](f"{PARAMS_PREFIX}_{param[0]}") for param in PARAMS_LIST
        ]
    except KeyError:
        pass
    else:
        params_list.append(deprecated_params)
    # END DEPRECATED

    # find the highest index in the env vars (HYDROQC_???_...)
    max_index = None
    for env_var in os.environ:
        if env_var.startswith(f"{PARAMS_PREFIX}_"):
            try:
                _, raw_index, _ = env_var.split("_", 3)
                index = int(raw_index)
            except ValueError:
                # ignoring
                continue
            if max_index is None:
                max_index = index
            elif max_index < index:
                max_index = index

    if max_index is not None:
        for index in range(max_index + 1):
            # Create empty list in the params list
            if len(params_list) < (index + 1):
                params_list.append([])

            params = []
            for param in PARAMS_LIST:
                env_var_name = f"{PARAMS_PREFIX}_{index}_{param[0]}"
                value: str | int | bool | None = param[1](env_var_name)
                if isinstance(value, str) and value == "NONE":
                    value = ""
                params.append(value)

            params_list[index] = params

    # CREATE test names
    for params in params_list:
        params.insert(0, f"{params[5]}_{params[6]}")

    return params_list


T = TypeVar("T")
P = ParamSpec("P")


class SkipIfBadRate:
    """Custom cache class based on aiocache."""

    def __init__(self, rates: list[str], rate_option: str) -> None:
        """Initialize custom cache decorator."""
        self.rates = rates
        self.rate_option = rate_option

    def __call__(self, func: Callable[P, T]) -> Callable[P, T]:
        """Call custom cache decorator."""

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            contract = cast(Contract, kwargs["contract"])
            if contract.rate in self.rates and self.rate_option == contract.rate_option:
                value = await func(*args, **kwargs)  # type: ignore
                return cast(T, value)
            return pytest.skip("RATE OR RATE OPTION NOT MATCHING")

        return cast(Callable[P, T], wrapper)
