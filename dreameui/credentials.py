"""Identifiants enregistrés dans le trousseau du bureau (Secret Service : KWallet, GNOME Keyring…)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

try:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ImportError, ValueError):  # libsecret absent : pas d'enregistrement possible
    Secret = None

_ATTRIBUTES = {"application": "dreameui", "account": "dreame-login"}


@dataclass
class StoredCredentials:
    username: str
    password: str
    region: str
    account: str = "dreame"

    def matches(self, username: str, region: str, account: str) -> bool:
        return self.account == account and self.region == region and (not username or self.username == username)


class CredentialError(Exception):
    pass


def _schema():
    return Secret.Schema.new("io.github.dreameui", Secret.SchemaFlags.NONE,
                             {"application": Secret.SchemaAttributeType.STRING,
                              "account": Secret.SchemaAttributeType.STRING})


def available() -> bool:
    return Secret is not None


def load() -> StoredCredentials | None:
    if Secret is None:
        return None
    try:
        secret = Secret.password_lookup_sync(_schema(), _ATTRIBUTES, None)
    except Exception as error:  # GLib.Error : trousseau verrouillé ou indisponible
        raise CredentialError("Trousseau du système inaccessible.") from error
    if not secret:
        return None
    data = json.loads(secret)
    return StoredCredentials(data["username"], data["password"], data["region"], data.get("account", "dreame"))


def save(credentials: StoredCredentials):
    if Secret is None:
        raise CredentialError("libsecret n'est pas disponible : impossible d'enregistrer le compte.")
    try:
        Secret.password_store_sync(_schema(), _ATTRIBUTES, Secret.COLLECTION_DEFAULT, "Dreame UI",
                                   json.dumps(asdict(credentials)), None)
    except Exception as error:
        raise CredentialError("Enregistrement dans le trousseau impossible.") from error


def delete():
    if Secret is None:
        return
    try:
        Secret.password_clear_sync(_schema(), _ATTRIBUTES, None)
    except Exception as error:
        raise CredentialError("Suppression dans le trousseau impossible.") from error
