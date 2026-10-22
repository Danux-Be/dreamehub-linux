"""Packs vocaux officiels (port partiel de VoiceService.swift : catalogue et installation)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from .cloud import CloudError

MAX_PACK_BYTES = 20 * 1024 * 1024
MANIFEST = "https://awsde0.fds.api.xiaomi.com/dreame-product/{model}/voices/soundpackage.json"


@dataclass(frozen=True)
class VoicePack:
    id: str
    name: str
    url: str
    md5: str
    size: int

    @property
    def install_value(self) -> dict:
        return {"id": self.id, "url": self.url, "md5": self.md5, "size": self.size}


class VoiceService:
    def __init__(self, model: str):
        if not re.fullmatch(r"[a-z]+\.vacuum\.[a-z0-9_-]+", model):
            raise CloudError("Code de modèle invalide.")
        self.model = model
        self._packs: dict[str, VoicePack] | None = None
        self._lock = threading.Lock()

    def packs(self) -> dict[str, VoicePack]:
        with self._lock:
            if self._packs is None:
                self._packs = self._fetch()
            return self._packs

    def _fetch(self) -> dict[str, VoicePack]:
        try:
            response = requests.get(MANIFEST.format(model=self.model), timeout=30,
                                    headers={"User-Agent": "Dreamehome/2.4.7",
                                             "Referer": "https://awsde0.fds.api.xiaomi.com/"})
        except requests.RequestException as error:
            raise CloudError("Catalogue officiel des voix injoignable.") from error
        if response.status_code != 200 or len(response.content) > 2 * 1024 * 1024:
            raise CloudError("Aucun catalogue de voix officiel pour ce modèle.")
        try:
            voices = response.json()["data"]["voices"]
        except (ValueError, KeyError, TypeError) as error:
            raise CloudError("Catalogue de voix illisible.") from error
        result = {}
        for voice in voices if isinstance(voices, list) else []:
            if not isinstance(voice, dict):
                continue
            vid, url, digest = voice.get("id"), voice.get("download"), voice.get("md5sum")
            try:
                size = int(voice.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if not (isinstance(vid, str) and vid and isinstance(url, str) and urlparse(url).scheme == "https"
                    and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{32}", digest) and 0 < size <= MAX_PACK_BYTES):
                continue
            name = (voice.get("name") or {}).get("default") if isinstance(voice.get("name"), dict) else None
            result[vid.upper()] = VoicePack(vid.upper(), name or vid, url, digest, size)
        if not result:
            raise CloudError("Aucun pack vocal officiel valide pour ce modèle.")
        return result

    def pack(self, language: str) -> VoicePack:
        pack = self.packs().get(language.upper())
        if pack is None:
            raise CloudError("Cette langue n'est pas proposée pour ce modèle.")
        return pack
