# HydroQC - Hydro Quebec API wrapper


[![CI](https://github.com/hydroqc/hydroqc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hydroqc/hydroqc/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/Hydro-Quebec-API-Wrapper.svg)](https://pypi.org/project/Hydro-Quebec-API-Wrapper/)



**This is a library to use if you want to build your own solution. If you want a user-friendly implementation for your home automation system please use [hydroqc2mqtt](https://gitlab.com/hydroqc/hydroqc2mqtt) which provide a MQTT publisher, docker image and HASS addon.**

This is a package to access some functionalities of Hydro Quebec API that are not documented.

We started a discord server for the project where you can come to discuss and find help with the project on our [#development](https://discord.gg/NWnfdfRZ7T) channel

## Donations

We put a lot of heart and effort in this project, any contribution is greatly appreciated!

[![Donate](https://img.shields.io/badge/Donate-Hydroqc-green)](https://hydroqc.ca/en/donations)

## Tested Rates

Each account, contracts and rates are different and we do our best to integrate all available options. Currently the account combinations listed below are supported and tested automaticaly by our CI/CD pipeline. If your account scenario is not currently tested and listed as **Needed** feel free to reach out to us on our discord [#development](https://discord.gg/NWnfdfRZ7T) channel if you want to volunter for testing. We provide a way to setup CI/CD tests in your own account in a way that keep your account info private to you (more details [here](https://gitlab.com/hydroqc/hydroqc-test-template)). Keep in mind that this process is a bit involved technicaly so it is best if you have some working knowledge with Gitlab.

### Rate D

| Base Rate | Rate Option | Tester | Test result | Last run | Comment |
| - | - | - | - | - | - |
| D | - | @weimdall | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39038345-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39038345-job-end-date.svg?job=private_tests_done) | |
| D | - | @mdallaire1 | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/52728916-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/52728916-job-end-date.svg?job=private_tests_done) | |
| D | - | @devzwf | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39310934-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39310934-job-end-date.svg?job=private_tests_done) | Hilo |
| D | CPC | @titilambert |![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/35085986-badge.svg?job=private_tests_done)  | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/35085986-job-end-date.svg?job=private_tests_done) | |

#### Flex D (DPC)

| Base Rate | Rate Option  | Tester | Test result | Last run | Comment |
| - | - | - | - | - | - |
| DPC | - | @mdallaire1 | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39503160-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39503160-job-end-date.svg?job=private_tests_done) | |

#### Dual Energy (DT)

| Base Rate | Rate Option | Tester | Test result | Last run | Comment |
| - | - | - | - | - | - |
| DT | - | @mdallaire1 | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/52729124-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/52729124-job-end-date.svg?job=private_tests_done) |

#### Rate M

| Base Rate | Rate Option | Tester | Test result | Last run | Comment |
| - | - | - | - | - | - |
| M | - | @hdenergy | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/41155894-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39503160-job-end-date.svg?job=private_tests_done) | [HD Énergie](https://hd.energy) |
| M | GDP | @hdenergy | ![Test Result](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/41155894-badge.svg?job=private_tests_done) | ![Timestamp](https://gitlab.com/api/v4/projects/hydroqc%2Fhydroqc/jobs/artifacts/main/raw/39503160-job-end-date.svg?job=private_tests_done) | [HD Énergie](https://hd.energy) |

Thanks to [HD Énergie](https://hd.energy) for sponsoring developpement of the Rate M support.

### Other plans
We don't have anyone to test with. Please reach out to us if you have this plan and want to help!

## Documentation

### Code documentation

[https://hydroqc.readthedocs.io/](https://hydroqc.readthedocs.io/)

### Architecture / concepts

If you need more information about the usage and current integrations of the library please see our website [https://hydroqc.ca](https://hydroqc.ca)

## E-mail verification (MFA)

Hydro-Québec can ask for a one-time code sent by e-mail after the password round
trip. `login()` returns a `LoginStatus` instead of a plain boolean:

```python
from hydroqc.consts import LoginStatus
from hydroqc.error import HydroQcMFAInvalidCodeError

status = await webuser.login()
while status is LoginStatus.MFA_REQUIRED:
    code = input("Verification code (empty = send a new one): ")
    if not code.strip():
        await webuser.request_mfa_code()
        continue
    try:
        status = await webuser.submit_mfa_code(code)
    except HydroQcMFAInvalidCodeError:
        print("Code refused, try again.")

if status is not LoginStatus.SUCCESS:
    raise RuntimeError("Login failed")
```

`bool(status)` is only true for `LoginStatus.SUCCESS`, so existing code using
`if await webuser.login():` keeps working. The code is requested automatically
when the step is detected; `request_mfa_code()` sends a new one. The
verification step expires after a few minutes (3 minutes on the current
portal): `HydroQcMFAExpiredError` means the login has to be restarted.

A silent background login cannot ask for a code and raises
`HydroQcMFARequiredError` instead of failing quietly.

### Reusing a session

`export_session_state()` returns the OAuth tokens plus the Azure AD B2C cookies;
`import_session_state()` restores them. Persisting that state lets a later run
skip the password (and a new verification code) while the refresh token or the
single sign-on cookie is still valid. The current portal issues 15-minute access
tokens and a 24-hour refresh token, so the stored session is normally reusable
for about a day; once the refresh token and the single sign-on cookie are both
refused, a new interactive login (and a new code) is required:

```python
state = webuser.export_session_state()   # store it somewhere safe
# ... next run ...
webuser = WebUser(username, password, verify_ssl=True)
webuser.import_session_state(state)
if await webuser.try_silent_login() is not LoginStatus.SUCCESS:
    await webuser.login()
```

## Goal

Make it easy to fetch and manipulate data from Hydro-Quebec, especially the winter credit periods

## Example folder

An example script that extracts the data available from Hydro-Quebec is available in the examples folder.

### Basic setup

This uses python 3.10

1. Clone the repo

   ```bash
   git clone https://github.com/hydroqc/hydroqc.git
   ```

2. Create a virtual-env

   ```bash
   python -m venv env
   . env/bin/activate
   pip install --editable .
   ```

3. Enter your hydro account credentials in the examples/hydro.py file (line 6)

4. Run

   ```bash
   examples/hydro.py
   ```

## TODO

[https://gitlab.com/groups/hydroqc/-/issues](https://gitlab.com/groups/hydroqc/-/issues)

## Targeted architecture (might change in the future)

```mermaid
classDiagram
    WebUser <-- Customer
    WebUser <-- HydroClient

    Customer <-- Account

    Account <-- Contract

    Contract <-- HydroClient
    Contract <-- Period
    Contract <-- Event
    Contract <-- WinterCreditHelper

    WinterCreditHelper <-- HydroClient


    HydroClient <-- Authenticator

    class HydroClient{

    }
    class WinterCreditHelper{
    }

    class WebUser{
        -HydroClient _hydro_client
        +List~Customer~ customers
        +get_contracts()
    }

    class Customer{
        -HydroClient _hydro_client
        +List~Account~ accounts
    }

    class Account{
        -HydroClient _hydro_client
        +List~Contract~ contracts
    }

    class Config{
        -string config_file_path
    }

    class Contract{
        -HydroClient _hydro_client
        -int customer_id
        -int webuser_id
        +string subscription_type
        +float balance
        +list~Period~ periods
        +list~Event~ events
        +get_winter_credit()
        +fetch_periods()
        +fetch_summary()
    }

    class Period{
    }

    class Event{
    }
```
