"""Client HTTP du cloud Dreame / MOVA.

Port Python de CloudClient.swift (dreamehub, MIT), lui-même dérivé de
Tasshack/dreame-vacuum (MIT). Ne retente jamais automatiquement une commande.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote, urlparse

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

REGIONS = ("cn", "sg", "us", "eu", "ru", "kr")


class CloudError(Exception):
    pass


class NotAuthenticated(CloudError):
    def __init__(self, message: str = "Connexion au compte Dreame requise."):
        super().__init__(message)


class AuthenticationRejected(CloudError):
    def __init__(self):
        super().__init__("Identifiants refusés par le cloud Dreame.")


@dataclass(frozen=True)
class Account:
    name: str
    domain: str
    tenant: str
    client_id: str
    authorization: str
    user_agent: str
    version: str


# Constantes publiques d'interopérabilité (Tasshack/dreame-vacuum), pas des secrets de compte.
ACCOUNTS = {
    "dreame": Account("dreame", "iot.dreame.tech", "000000", "EETjszu*XI5znHsI",
                      "Basic ZHJlYW1lX2FwcHYxOkFQXmR2QHpAU1FZVnhOODg=", "Dart/3.9 (dart:io)", "102060300"),
    "mova": Account("mova", "iot.mova-tech.com", "000002", "gigxlmqwZ]7oWZUF",
                    "Basic bW92YV9hcHA6VjdLb0NoTFc4dkhBQ3FHYg==", "Dart/3.10 (dart:io)", "10605530"),
}


@dataclass
class Device:
    id: str
    model: str
    name: str
    owner_id: str
    bind_domain: str
    raw: dict = field(repr=False)

    @classmethod
    def from_raw(cls, raw: dict) -> "Device":
        did, model = raw.get("did"), raw.get("model")
        if did is None or not model:
            raise CloudError("Réponse Dreame inattendue.")
        info = raw.get("deviceInfo") or {}
        name = raw.get("customName") or info.get("displayName") or model
        return cls(str(did), model, name, str(raw.get("masterUid") or ""), raw.get("bindDomain") or "", raw)

    @property
    def firmware_build(self) -> int:
        # La liste des appareils renvoie « ver » (ex. 4.3.9_1686) ; le build est le nombre après « _ ».
        version = str(self.raw.get("ver") or (self.raw.get("deviceInfo") or {}).get("version")
                      or self.raw.get("version") or "")
        match = re.search(r"_(\d+)", version)
        return int(match.group(1)) if match else 1

    @property
    def local_ip(self) -> str | None:
        return self.raw.get("localip") or self.raw.get("ip")


def md5(text: str | bytes) -> str:
    return hashlib.md5(text.encode() if isinstance(text, str) else text).hexdigest()


def _escaped(text: str) -> str:
    return quote(text, safe="-._~")


def _json(value) -> str:
    """Encodage compact à clés triées, identique au JSONEncoder Swift (.sortedKeys, .withoutEscapingSlashes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_signature_input(values: dict, top_level: bool = True) -> str:
    """Le serveur signe les champs triés, tableaux uniquement au premier niveau."""
    parts = []
    for key in sorted(values):
        v = values[key]
        if isinstance(v, dict):
            inner = canonical_signature_input(v, top_level=False)
            parts.append(f"{key}=]" if not inner else f"{key}=[{inner}]")
        elif isinstance(v, list):
            if top_level:
                parts.append(f"{key}={_json(v)}")
        elif isinstance(v, str):
            parts.append(f"{key}={v if top_level else _json(v)}")
        else:
            parts.append(f"{key}={_json(v)}")
    return "&".join(parts)


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padder.update(data) + padder.finalize()) + encryptor.finalize()


class CloudClient:
    def __init__(self):
        self.session = requests.Session()
        self._lock = threading.RLock()
        self.country = "sg"
        self.account = ACCOUNTS["dreame"]
        self.username = ""
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at = 0.0
        self.user_id = ""
        self.tenant_id = "000000"
        self.region = self.language = self.country_code = None
        self.request_id = random.randint(1, 100)
        self.auth_generation = 0
        self.visitor_id = uuid.uuid4().hex

    # -- authentification -------------------------------------------------
    def login(self, username: str, password: str, country: str, account: str = "dreame"):
        if country not in REGIONS:
            raise CloudError("Région de serveur invalide.")
        if account not in ACCOUNTS:
            raise CloudError("Fournisseur de compte invalide.")
        with self._lock:
            self.auth_generation += 1
            self.country, self.username, self.account = country, username, ACCOUNTS[account]
            self.user_id, self.expires_at = "", 0.0
            self.access_token = self.refresh_token = None
            self.tenant_id = self.account.tenant
            self.region = self.language = self.country_code = None
        fields = ("grant_type=password&scope=all&platform=ANDROID&type=account"
                  f"&username={_escaped(username)}&password={md5(password + 'RAylYC%fmSKp7%Tq')}")
        self._authenticate(fields)

    def logout(self):
        with self._lock:
            self.auth_generation += 1
            self.access_token = self.refresh_token = None
            self.username = self.user_id = ""
            self.expires_at = 0.0

    @property
    def base_url(self) -> str:
        return f"https://{self.country}.{self.account.domain}:13267"

    def _headers(self, content_type: str, authenticated: bool) -> dict:
        headers = {
            "User-Agent": self.account.user_agent,
            "authorization": self.account.authorization,
            "tenant-id": self.tenant_id,
            "content-type": content_type,
            "dreame-meta": f"cv=a_{self.account.version};canvasHash={md5(self.username + 'c')[:8]};"
                           f"webglHash={md5(self.username + 'w')[:8]};visitorIdHash={self.visitor_id}",
        }
        if self.account.name == "mova":
            headers["dreame-psd"] = "new"
        if authenticated and self.access_token:
            headers["dreame-auth"] = self.access_token
        if self.region and self.language and self.country_code:
            data = f"{self.region}|{self.language}|{self.country_code}".encode()
            headers["dreame-rlc"] = base64.b64encode(aes_ecb_encrypt(data, self.account.client_id.encode())).decode()
        return headers

    def _authenticate(self, body: str):
        epoch = self.auth_generation
        data = self._transport("/dreame-auth/oauth/token", body.encode(),
                               "application/x-www-form-urlencoded", authenticated=False)
        if epoch != self.auth_generation:
            raise NotAuthenticated()
        response = json.loads(data)
        token = response.get("access_token")
        if not token:
            raise AuthenticationRejected()
        with self._lock:
            self.access_token = token
            self.refresh_token = response.get("refresh_token")
            self.expires_at = time.time() + float(response.get("expires_in") or 3600)
            self.user_id = str(response.get("uid") or self.user_id)
            self.tenant_id = response.get("tenant_id") or self.tenant_id
            self.region = response.get("region") or self.region
            self.language = response.get("lang") or self.language
            self.country_code = response.get("country") or self.country_code

    def _ensure_session(self):
        if not self.access_token:
            raise NotAuthenticated()
        if self.expires_at - time.time() < 600:
            if not self.refresh_token:
                raise NotAuthenticated()
            self._authenticate("grant_type=refresh_token&scope=all&platform=ANDROID&type=account"
                               f"&refresh_token={_escaped(self.refresh_token)}")

    def _transport(self, path: str, body: bytes | None, content_type: str = "application/json",
                   authenticated: bool = True) -> bytes:
        epoch = self.auth_generation
        try:
            response = self.session.post(self.base_url + path, data=body, timeout=20,
                                         headers=self._headers(content_type, authenticated))
        except requests.RequestException as error:
            raise CloudError(f"Cloud Dreame injoignable : {error.__class__.__name__}") from error
        if not 200 <= response.status_code < 300:
            if response.status_code in (400, 401, 403) and not authenticated:
                raise AuthenticationRejected()
            if response.status_code == 401:
                if epoch == self.auth_generation:
                    self.access_token = None
                raise NotAuthenticated()
            raise CloudError(f"Le serveur Dreame a renvoyé HTTP {response.status_code}.")
        return response.content

    def _signed(self, params: dict) -> dict:
        timestamp = int(time.time() * 1000)
        params = dict(params)
        params["sign"] = md5(canonical_signature_input(params) + str(timestamp) + self.account.client_id)
        params["timestamp"] = timestamp
        return params

    def api(self, path: str, params: dict | None = None) -> dict:
        self._ensure_session()
        body = _json(self._signed(params)).encode() if params is not None else None
        result = json.loads(self._transport("/" + path, body))
        code = result.get("code")
        if code is not None and code != 0:
            raise CloudError(f"Requête Dreame refusée ({code}).")
        return result

    # -- points d'accès ---------------------------------------------------
    def devices(self) -> list[Device]:
        response = self.api("dreame-user-iot/iotuserbind/device/listV2")
        try:
            records = response["data"]["page"]["records"]
        except (KeyError, TypeError) as error:
            raise CloudError("Réponse Dreame inattendue.") from error
        return [Device.from_raw(r) for r in records if ".vacuum." in str(r.get("model", ""))]

    def rpc(self, device: Device, method: str, params):
        with self._lock:
            self.request_id += 1
            request_id = self.request_id
        host = device.bind_domain.split(".")[0] if device.bind_domain else ""
        if not all(c.isascii() and (c.isalnum() or c == "-") for c in host):
            raise CloudError("Domaine de l'appareil invalide.")
        suffix = f"-{host}" if host else ""
        response = self.api(f"dreame-iot-com{suffix}/device/sendCommand", {
            "did": device.id, "id": request_id,
            "data": {"did": device.id, "id": request_id, "method": method, "params": params},
        })
        data = response.get("data")
        if not isinstance(data, dict) or "result" not in data:
            raise CloudError("Résultat de la commande non vérifié ; pas de nouvel envoi automatique.")
        return data["result"]

    def properties(self, device: Device, keys: list[str]):
        return self.api("dreame-user-iot/iotstatus/props",
                        {"did": device.id, "keys": ",".join(keys)}).get("data")

    def history(self, device: Device, siid: int, piid: int, limit: int = 1, start: int = 1687019188):
        data = self.api("dreame-user-iot/iotstatus/history", {
            "uid": device.owner_id, "did": device.id, "from": start, "limit": limit,
            "siid": str(siid), "piid": str(piid), "region": self.country, "type": 3}).get("data")
        return (data or {}).get("list") if isinstance(data, dict) else None

    def events(self, device: Device, siid: int, eiid: int, limit: int = 20, start: int = 1687019188):
        data = self.api("dreame-user-iot/iotstatus/history", {
            "uid": device.owner_id, "did": device.id, "from": start, "limit": limit,
            "siid": str(siid), "eiid": str(eiid), "region": self.country, "type": 3}).get("data")
        return (data or {}).get("list") if isinstance(data, dict) else None

    def file_url(self, device: Device, object_name: str, interim: bool = False):
        params = {"did": device.id, "model": device.model,
                  "filename": object_name if interim else object_name[1:], "region": self.country}
        if not interim:
            params["uid"] = device.owner_id
        endpoint = "getDownloadUrl" if interim else "getOss1dDownloadUrl"
        return self.api("dreame-user-iot/iotfile/" + endpoint, params).get("data")

    def download(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CloudError("Adresse de fichier invalide ou non sûre.")
        try:
            response = self.session.get(url, timeout=20, headers={"User-Agent": "okhttp/4.12.0"})
        except requests.RequestException as error:
            raise CloudError("Téléchargement impossible.") from error
        if response.status_code != 200:
            raise CloudError("Téléchargement refusé.")
        return response.content

    def device_file(self, device: Device, filename: str, kind: str) -> bytes:
        self._ensure_session()
        info = _json({"filename": filename, "type": kind})
        params = self._signed({"did": device.id, "uid": device.owner_id, "fileinfo": info})
        return self._transport("/file-bridge/user/getDeiviceFile", _json(params).encode())


def signed_url(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("url"), str):
        return value["url"]
    return None
