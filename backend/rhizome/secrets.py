# SPDX-License-Identifier: Apache-2.0
"""Secrets (API keys) never go into settings.json or the database.

Resolution order: the environment variable, then the operating system's credential store through
``keyring`` (Windows Credential Manager, macOS Keychain, Secret Service on Linux). A machine without
a usable store can still set the variable; the UI and CLI say which source is in effect.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

log = logging.getLogger(__name__)

SERVICE = "rhizome"
# secret name -> the environment variable that overrides the stored value
ENV_VARS: dict[str, str] = {"anthropic_api_key": "ANTHROPIC_API_KEY"}
Source = Literal["env", "keyring"]


class SecretStoreUnavailable(RuntimeError):
    """No credential store on this machine (``keyring`` missing or without a backend)."""


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as e:
        raise SecretStoreUnavailable("keyring is not installed") from e
    return keyring, KeyringError


def _stored(name: str) -> str | None:
    try:
        keyring, KeyringError = _keyring()
    except SecretStoreUnavailable:
        return None
    try:
        return keyring.get_password(SERVICE, name) or None
    except KeyringError as e:  # no backend on this machine: behave as "not set"
        log.debug("credential store unavailable: %s", e)
        return None


def secret_source(name: str) -> Source | None:
    """Where the secret comes from right now, or None when it is not set anywhere."""
    if os.environ.get(ENV_VARS.get(name, "")):
        return "env"
    return "keyring" if _stored(name) else None


def get_secret(name: str) -> str | None:
    env = os.environ.get(ENV_VARS.get(name, ""))
    return env.strip() if env and env.strip() else _stored(name)


def set_secret(name: str, value: str | None) -> Source:
    """Store (or with None, delete) a secret in the credential store. Returns "keyring" on success
    and raises SecretStoreUnavailable when there is no store to write to."""
    if name not in ENV_VARS:
        raise ValueError(f"unknown secret {name!r}")
    keyring, KeyringError = _keyring()
    try:
        if value is None or not value.strip():
            try:
                keyring.delete_password(SERVICE, name)
            except KeyringError:  # PasswordDeleteError when nothing was stored: already the goal
                pass
        else:
            keyring.set_password(SERVICE, name, value.strip())
    except KeyringError as e:
        raise SecretStoreUnavailable(str(e)) from e
    return "keyring"
