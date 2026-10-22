"""Acquisition des cartes, historique de nettoyage et photos d'obstacles (lecture seule).

Port de MapService.swift, HistoryService.swift et ObstaclePhotoService.swift.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, padding as sym_padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import mapdecoder
from .cloud import CloudClient, CloudError, Device, signed_url
from .mapdecoder import IncrementalFrame, MapError

DATA = Path(__file__).parent / "data"
# Métadonnées publiques par modèle, extraites de DEVICE_INFO en amont :
# IV AES de la carte, firmware minimal map-v3, firmware minimal map-v2, suffixes de modèles.
MAP_REGISTRY = json.loads((DATA / "map_registry.json").read_text())
# Seuil de firmware, IV base64, suffixes : modèles dont les photos sont stockées localement chiffrées.
PHOTO_REGISTRY = json.loads((DATA / "photo_registry.json").read_text())


class MapFailure(CloudError):
    pass


NO_MAP = "Impossible d'obtenir une carte complète depuis le compte."


def configuration(model: str, firmware: int = 1):
    suffix = model.rsplit(".", 1)[-1]
    for iv, v3, v2, models in MAP_REGISTRY:
        if suffix in models.split(","):
            version = 3 if v3 is not None and firmware >= v3 else 2 if v2 is not None and firmware >= v2 else 1
            return iv, version
    return None


def _first_string(value) -> str | None:
    if isinstance(value, list):
        return _str(value[0]) if value else None
    s = _str(value)
    if not s:
        return None
    if s.startswith("["):
        try:
            decoded = json.loads(s)
        except ValueError:
            return s
        return _str(decoded[0]) if isinstance(decoded, list) and decoded else None
    return s


def _str(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else str(value)
    return None


@dataclass
class Capture:
    raw: str
    iv: str | None
    key: str | None
    version: int
    source: str


def capture(cloud: CloudClient, device: Device) -> Capture:
    config = configuration(device.model, device.firmware_build)
    if config is None:
        raise MapFailure("Informations de chiffrement de carte non vérifiées pour ce modèle.")
    iv, version = config
    # La propriété OBJECT_NAME est préférable à un dernier paquet MAP_DATA partiel.
    props = cloud.properties(device, ["6.3"])
    object_name = None
    if isinstance(props, list):
        object_name = next((s for s in (_first_string(r.get("value")) for r in props if isinstance(r, dict)) if s), None)
    elif isinstance(props, dict):
        object_name = _first_string(props.get("6.3"))
    if not object_name:
        history = cloud.history(device, 6, 3, limit=1) or []
        object_name = next((s for s in (_first_string(r.get("value")) or _first_string(r.get("val"))
                                        for r in history if isinstance(r, dict)) if s), None)
    if object_name:
        parts = object_name.split(",", 1)
        url = signed_url(cloud.file_url(device, parts[0], interim=True))
        if not url:
            raise MapFailure("Adresse de téléchargement de la carte indisponible.")
        try:
            raw = cloud.download(url).decode()
        except UnicodeDecodeError as error:
            raise MapFailure("Le fichier de carte n'est pas au format texte attendu.") from error
        return Capture(raw, iv, parts[1] if len(parts) == 2 else None, version, "cloud_object")
    # Seule une trame complète est acceptée ; une trame P non fusionnée n'est jamais présentée comme carte.
    for record in cloud.history(device, 6, 1, limit=20) or []:
        raw = _first_string(record.get("value")) or _first_string(record.get("val"))
        if not raw:
            continue
        try:
            mapdecoder.decode(raw, version, iv)
            return Capture(raw, iv, None, version, "cloud_property_history")
        except IncrementalFrame:
            continue
    raise MapFailure(NO_MAP)


def _newer(candidate: dict, previous: dict | None) -> bool:
    if previous is None:
        return True
    time_new, time_old = candidate.get("timestamp_ms"), previous.get("timestamp_ms")
    if time_new is not None and time_old is not None and time_new != time_old:
        return time_new > time_old
    if time_new is None and time_old is not None:
        return False
    if candidate.get("map_id") != previous.get("map_id"):
        return False
    frame, old = candidate.get("frame_id"), previous.get("frame_id")
    if frame is None or old is None:
        return False
    distance = (frame - old) & 0xFFFF
    return 0 < distance < 32768


class MapService:
    def __init__(self, cloud: CloudClient):
        self.cloud = cloud
        self.stream = mapdecoder.MapStream()
        self.device_id: str | None = None
        self.payload: dict | None = None
        self.stream_iv: str | None = None
        self.stream_version = 1
        self.last_full_poll = 0.0
        self.last_capture: Capture | None = None
        self._lock = threading.Lock()

    def _acquire_full(self, device: Device):
        full = capture(self.cloud, device)
        self.last_capture = full
        candidate = mapdecoder.decode(full.raw, full.version, full.iv, full.key)
        if _newer(candidate, self.payload):
            self.payload = self.stream.accept(full.raw, full.version, full.iv, full.key)
            self.payload["acquisition_source"] = full.source
            self.stream_iv, self.stream_version = full.iv, full.version

    def current_capture(self, device: Device) -> Capture:
        with self._lock:
            if self.last_capture is None or self.device_id != device.id:
                return capture(self.cloud, device)
            return self.last_capture

    def current(self, device: Device) -> dict:
        with self._lock:
            if self.device_id != device.id:
                self.stream.reset()
                self.payload, self.device_id, self.last_full_poll = None, device.id, 0.0
                self.stream_iv, self.stream_version, self.last_capture = None, 1, None
            return self._refresh(device)

    def _refresh(self, device: Device) -> dict:
        warning = None
        if self.payload is None or time.time() - self.last_full_poll > 10:
            try:
                self._acquire_full(device)
            except CloudError as error:
                if self.payload is None:
                    raise
                warning = str(error)
            self.last_full_poll = time.time()
        timestamp = (self.payload or {}).get("timestamp_ms") or 0
        try:
            start = timestamp // 1000 if timestamp > 1_577_826_000_000 else int(time.time()) - 300
            updates = self.cloud.history(device, 6, 1, limit=20, start=start) or []
            for update in sorted(updates, key=lambda u: u.get("time") or 0):
                raw = _first_string(update.get("value")) or _first_string(update.get("val"))
                if not raw:
                    continue
                inflated = mapdecoder.inflate_envelope(raw, self.stream_iv)
                if len(inflated) < 27:
                    continue
                map_id, frame, width, height = mapdecoder.frame_header(inflated)
                offset = 27 + width * height
                if offset <= len(inflated):
                    try:
                        meta = json.loads(inflated[offset:])
                    except ValueError:
                        meta = None
                    if isinstance(meta, dict):
                        cover = meta.get("cover") if isinstance(meta.get("cover"), dict) else {}
                        incoming = cover.get("timestamp_ms", meta.get("timestamp_ms"))
                        previous = (self.payload or {}).get("timestamp_ms")
                        if isinstance(incoming, (int, float)) and previous is not None and incoming < previous:
                            continue
                if self._duplicate(map_id, frame):
                    continue
                # Décoder avant d'accepter : l'acceptation change la base des deltas.
                if inflated[4] == 73 and not _newer(mapdecoder.decode_inflated(inflated, self.stream_version), self.payload):
                    continue
                try:
                    self._accept(inflated)
                except IncrementalFrame:
                    # Pas de delta orphelin : on reprend la dernière carte complète, avec son âge réel.
                    self._acquire_full(device)
                    self.last_full_poll = time.time()
                    if self._duplicate(map_id, frame):
                        continue
                    try:
                        self._accept(inflated)
                    except IncrementalFrame:
                        warning = "Chaîne de trames incomplète ; dernière trame vérifiée affichée."
                        break
        except (CloudError, MapError) as error:
            warning = str(error)
        if self.payload is None:
            raise MapFailure(NO_MAP)
        payload = json.loads(json.dumps(self.payload))
        telemetry = payload.get("telemetry")
        if isinstance(telemetry, dict):
            now = int(time.time() * 1000)
            # L'heure du sondage n'est pas l'heure de capture : un vieux plan ne devient jamais « frais ».
            if payload.get("timestamp_ms") is not None:
                telemetry["freshness_ms"] = max(0, now - payload["timestamp_ms"])
            telemetry["last_poll_at"] = now
        if warning:
            payload["update_warning"] = warning
        return payload

    def _duplicate(self, map_id: int, frame: int) -> bool:
        if self.payload and map_id == self.payload.get("map_id") and self.payload.get("frame_id") is not None:
            distance = (frame - self.payload["frame_id"]) & 0xFFFF
            return distance == 0 or distance >= 32768
        return False

    def _accept(self, inflated: bytes):
        result = self.stream.accept_inflated(inflated, self.stream_version)
        result["acquisition_source"] = "cloud_property_history"
        self.payload = result


# -- Photos d'obstacles ---------------------------------------------------

def content_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _local_storage_iv(device: Device) -> bytes | None:
    suffix = device.model.rsplit(".", 1)[-1]
    for threshold, encoded, models in PHOTO_REGISTRY:
        if suffix in models.split(",") and device.firmware_build >= threshold:
            iv = base64.b64decode(encoded)
            if len(iv) == 16:
                return iv
    return None


def _decrypt(encrypted: bytes, key: bytes, iv: bytes | None) -> bytes:
    mode = modes.CBC(iv) if iv else modes.ECB()
    try:
        decryptor = Cipher(algorithms.AES(key), mode).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = sym_padding.PKCS7(128).unpadder()
        image = unpadder.update(padded) + unpadder.finalize()
    except ValueError as error:
        raise MapFailure("Impossible de déchiffrer la photo de l'obstacle.") from error
    if content_type(image) is None:
        raise MapFailure("Le fichier reçu n'est pas une photo valide.")
    return image


def _device_photo_key(cloud: CloudClient, device: Device) -> str:
    """Échange de clé en lecture seule (SIID 14 / AIID 1 / PIID 5). La clé privée reste en mémoire."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    spki = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    result = cloud.rpc(device, "action", {"did": device.id, "siid": 14, "aiid": 1,
                                          "in": [{"piid": 5, "value": base64.b64encode(spki).decode()}]})
    if not isinstance(result, dict) or result.get("code") not in (0, None):
        raise MapFailure("Cet objet n'a pas de photo ou elle n'est plus accessible.")
    out = result.get("out")
    encoded = out[0].get("value") if isinstance(out, list) and out and isinstance(out[0], dict) else None
    if not isinstance(encoded, str):
        raise MapFailure("Cet objet n'a pas de photo ou elle n'est plus accessible.")
    normalized = encoded.replace("-", "+").replace("_", "/")
    normalized += "=" * ((4 - len(normalized) % 4) % 4)
    try:
        key = private.decrypt(base64.b64decode(normalized),
                              padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)).decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise MapFailure("Impossible de déchiffrer la photo de l'obstacle.") from error
    if not key:
        raise MapFailure("Impossible de déchiffrer la photo de l'obstacle.")
    return key


def obstacle_image(cloud: CloudClient, device: Device, cap: Capture, index: str) -> bytes:
    unavailable = MapFailure("Cet objet n'a pas de photo ou elle n'est plus accessible.")
    decoded = mapdecoder.decode(cap.raw, cap.version, cap.iv, cap.key)
    chosen = next((o for o in decoded.get("obstacles", []) if str(o[0]) == index), None)
    if chosen is None or len(chosen) <= 7 or chosen[7] is None:
        raise unavailable
    object_id = mapdecoder._describe(chosen[7])
    data = mapdecoder.inflate_envelope(cap.raw, cap.iv, cap.key)
    if len(data) < 27:
        raise unavailable
    _, _, w, h = mapdecoder.frame_header(data)
    try:
        metadata = json.loads(data[27 + w * h:])
    except ValueError:
        raise unavailable
    rows = metadata.get("ai_obstacle") if isinstance(metadata, dict) else None
    row = next((r for r in rows or [] if isinstance(r, list) and len(r) > 4 and mapdecoder._describe(r[4]) == object_id), None)
    if row is None or len(row) < 7:
        raise unavailable
    file_index, key_index = (5, 6) if len(row) >= 8 else (6, 5)
    file = row[file_index]
    if not isinstance(file, str) or not file:
        raise unavailable
    if len(row) >= 13 and isinstance(row[11], int) and row[11] != 2:
        raise unavailable
    iv = _local_storage_iv(device)
    if iv:
        encrypted = cloud.device_file(device, file, "obstacle")
        key = _device_photo_key(cloud, device)
        return _decrypt(encrypted, hashlib.md5(key.encode()).hexdigest().encode(), iv)
    key = row[key_index]
    if not isinstance(key, str) or not key:
        raise unavailable
    url = signed_url(cloud.file_url(device, f"{file}-{object_id}", interim=False))
    if not url:
        raise unavailable
    return _decrypt(cloud.download(url), hashlib.md5(key.encode()).digest(), None)


# -- Historique de nettoyage ----------------------------------------------

class HistoryService:
    def __init__(self, cloud: CloudClient):
        self.cloud = cloud
        self.device_id: str | None = None
        self.records: list[dict] = []
        self.selected: Capture | None = None

    def list(self, device: Device) -> list[dict]:
        response = self.cloud.events(device, 4, 1, limit=50) or []
        result, seen = [], set()
        for record in response:
            blob = record.get("history") or record.get("value")
            try:
                properties = json.loads(blob) if isinstance(blob, str) else None
            except ValueError:
                continue
            if not isinstance(properties, list):
                continue
            values = {p["piid"]: p.get("value", p.get("val")) for p in properties
                      if isinstance(p, dict) and isinstance(p.get("piid"), int)}
            timestamp = values.get(8)
            if not isinstance(timestamp, int) or timestamp in seen:
                continue
            seen.add(timestamp)
            entry = {"index": len(result) + 1, "date": time.strftime("%d/%m %H:%M", time.localtime(timestamp)),
                     "timestamp": timestamp}
            if isinstance(values.get(2), int):
                entry["cleaning_time"] = f"{values[2]} min"
            if isinstance(values.get(3), int):
                entry["cleaned_area"] = f"{values[3]} m²"
            if isinstance(values.get(13), int):
                entry["completed"] = values[13] == 1
            if isinstance(values.get(9), str) and values[9]:
                entry["_object_name"] = values[9]
            result.append(entry)
            if len(result) == 30:
                break
        self.records, self.device_id = result, device.id
        # Les noms d'objets et clés de déchiffrement ne quittent jamais le service.
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in result]

    def map(self, device: Device, index: int) -> dict:
        if self.device_id != device.id or not self.records:
            self.list(device)
        config = configuration(device.model, device.firmware_build)
        if not 0 < index <= len(self.records) or "_object_name" not in self.records[index - 1] or config is None:
            raise MapFailure(NO_MAP)
        iv, version = config
        parts = self.records[index - 1]["_object_name"].split(",", 1)
        url = signed_url(self.cloud.file_url(device, parts[0], interim=True))
        if not url:
            raise MapFailure("Adresse de téléchargement de la carte indisponible.")
        raw = self.cloud.download(url).decode()
        cap = Capture(raw, iv, parts[1] if len(parts) > 1 else None, version, "cleaning_history")
        result = mapdecoder.decode(raw, version, iv, cap.key)
        result.update(history_map=True, history_index=index, acquisition_source="cleaning_history")
        self.selected = cap
        return result

    def photo(self, device: Device, obstacle_id: str) -> bytes:
        if self.device_id != device.id or self.selected is None:
            raise MapFailure(NO_MAP)
        return obstacle_image(self.cloud, device, self.selected, obstacle_id)
