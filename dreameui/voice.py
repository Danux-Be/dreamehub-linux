"""Packs vocaux : catalogue officiel, éditeur d'annonces et transfert local (port de VoiceService / VoiceTransferHost)."""
from __future__ import annotations

import gzip
import hashlib
import io
import ipaddress
import json
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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


# -- Éditeur d'annonces ------------------------------------------------------

LABELS = Path(__file__).parent / "data" / "voice_labels_fr.json"
MAX_AUDIO_BYTES = 12 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024
_SOUND = re.compile(r"\d+\.ogg")


def ffmpeg_available() -> bool:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    try:
        encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "libvorbis" in encoders


def parse_pack(compressed: bytes) -> dict[str, bytes]:
    """Lecteur tar strict : fichiers ordinaires à la racine uniquement, jamais d'extraction sur disque."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            data = stream.read(MAX_UNPACKED + 1)
    except (OSError, EOFError) as error:
        raise CloudError("Archive du pack vocal illisible.") from error
    if len(data) > MAX_UNPACKED:
        raise CloudError("Pack vocal décompressé trop volumineux.")
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                name = member.name
                if (not member.isfile() or not name or "/" in name or "\\" in name or name.startswith(".")
                        or name in files or member.size > MAX_AUDIO_BYTES or len(files) >= 4096):
                    raise CloudError("Entrée non sûre ou dupliquée dans le pack vocal.")
                files[name] = archive.extractfile(member).read()
    except tarfile.TarError as error:
        raise CloudError("Archive du pack vocal invalide.") from error
    if "tts.json" not in files or not any(_SOUND.fullmatch(n) for n in files):
        raise CloudError("Contenu du pack vocal inattendu.")
    return files


def build_pack(files: dict[str, bytes]) -> bytes:
    """Archive tar.gz déterministe (dates à zéro, propriétaire neutre)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(files[name]), 0o644, 0
            archive.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as stream:
        stream.write(raw.getvalue())
    return out.getvalue()


def convert_audio(payload: bytes) -> bytes:
    """N'importe quel format lu par ffmpeg → OGG Vorbis mono 16 kHz, crêtes limitées."""
    if not payload or len(payload) > MAX_AUDIO_BYTES:
        raise CloudError("Fichier audio vide ou supérieur à 12 Mo.")
    if not ffmpeg_available():
        raise CloudError("ffmpeg (avec libvorbis) est requis pour convertir l'audio.")
    with tempfile.TemporaryDirectory(prefix="dreameui-voice-") as folder:
        source, target = Path(folder) / "input", Path(folder) / "output.ogg"
        source.write_bytes(payload)
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(source)], capture_output=True, text=True, timeout=30)
            duration = float(probe.stdout.strip())
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            raise CloudError("Fichier audio illisible.") from error
        if not 0.15 <= duration <= 45:
            raise CloudError("L'annonce doit durer entre 0,15 et 45 secondes.")
        try:
            result = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                                     "-vn", "-map_metadata", "-1", "-ac", "1", "-ar", "16000",
                                     "-af", "alimiter=limit=0.95", "-c:a", "libvorbis", "-q:a", "4", str(target)],
                                    capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CloudError("Conversion audio interrompue.") from error
        if result.returncode != 0 or not target.exists():
            raise CloudError("Conversion audio impossible ; vérifie le format du fichier.")
        converted = target.read_bytes()
    if not converted.startswith(b"OggS") or len(converted) > MAX_AUDIO_BYTES:
        raise CloudError("Conversion audio invalide.")
    return converted


class VoiceEditor:
    """Pack de base officiel + remplacements en mémoire. Rien n'est écrit sur disque."""

    def __init__(self, service: VoiceService):
        self.service = service
        self.base: VoicePack | None = None
        self.files: dict[str, bytes] | None = None
        self.replacements: dict[str, bytes] = {}
        self.labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
        self._lock = threading.Lock()

    def load(self, language: str | None):
        with self._lock:
            if self.files is not None:
                return
            packs = self.service.packs()
            pack = packs.get((language or "").upper()) or packs.get("FR") or packs.get("EN") or next(iter(packs.values()))
            try:
                response = requests.get(pack.url, timeout=60, headers={"User-Agent": "Dreamehome/2.4.7"})
            except requests.RequestException as error:
                raise CloudError("Téléchargement du pack vocal officiel impossible.") from error
            data = response.content
            if response.status_code != 200 or len(data) != pack.size or hashlib.md5(data).hexdigest() != pack.md5:
                raise CloudError("Contrôle d'intégrité du pack vocal officiel échoué.")
            self.files, self.base = parse_pack(data), pack

    def catalog(self, language: str | None) -> dict:
        self.load(language)
        ids = sorted((n[:-4] for n in self.files if _SOUND.fullmatch(n)), key=int)
        sounds = [{"id": i, "label": self.labels.get(i, {}).get("name", f"Annonce {i}"),
                   "transcript": self.labels.get(i, {}).get("transcript", "Texte non vérifié."),
                   "confidence": "translation" if i in self.labels else "unknown",
                   "changed": i in self.replacements} for i in ids]
        packs = sorted(self.service.packs().values(), key=lambda p: p.id)
        return {"sounds": sounds, "changed_count": len(self.replacements), "base": self.base.id,
                "packs": [{"id": p.id, "name": p.name} for p in packs], "conversion_available": ffmpeg_available()}

    def audio(self, sound_id: str) -> bytes:
        if self.files is None or not sound_id.isdigit() or f"{sound_id}.ogg" not in self.files:
            raise CloudError("Annonce introuvable.")
        return self.replacements.get(sound_id) or self.files[f"{sound_id}.ogg"]

    def replace(self, sound_id: str, payload: bytes) -> dict:
        self.audio(sound_id)
        self.replacements[sound_id] = convert_audio(payload)
        return {"id": sound_id, "changed_count": len(self.replacements), "bytes": len(self.replacements[sound_id])}

    def reset(self, sound_id: str) -> int:
        self.audio(sound_id)
        self.replacements.pop(sound_id, None)
        return len(self.replacements)

    def build(self) -> tuple[bytes, str, str]:
        if self.files is None or not self.replacements:
            raise CloudError("Modifie d'abord au moins une annonce.")
        edited = dict(self.files)
        for sound_id, payload in self.replacements.items():
            edited[f"{sound_id}.ogg"] = payload
        data = build_pack(edited)
        if len(data) > MAX_PACK_BYTES:
            raise CloudError("Pack personnalisé trop volumineux.")
        return data, hashlib.md5(data).hexdigest(), self.base.id


# -- Transfert local vers le robot -------------------------------------------

TRANSFER_PORT = 47824


def _private_ipv4(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.version == 4 and (address.is_private or address.is_link_local)


def _local_address(peer: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((peer, 9))  # UDP : aucun paquet envoyé, choisit juste l'interface
        return probe.getsockname()[0]


def _neighbor_ip(mac: str) -> str | None:
    try:
        table = subprocess.run(["ip", "-4", "neigh"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in table.splitlines():
        if mac in line.lower() and "FAILED" not in line:
            return line.split()[0]
    return None


def find_robot_ip(device) -> str:
    """IP locale du robot : champ cloud, sinon table ARP (par MAC), après sollicitation du /24 local."""
    for key in ("localip", "ip"):
        ip = device.raw.get(key)
        if isinstance(ip, str) and _private_ipv4(ip):
            return ip
    mac = str(device.raw.get("mac") or "").lower()
    if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
        raise CloudError("Adresse locale du robot inconnue.")
    found = _neighbor_ip(mac)
    if found:
        return found
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            local = probe.getsockname()[0]
        except OSError as error:
            raise CloudError("Aucun réseau local détecté.") from error
    network = ipaddress.ip_network(local + "/24", strict=False)
    if not network.is_private:
        raise CloudError("Le PC n'est pas sur un réseau local privé.")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        # Datagramme vide vers le port « discard » : suffit à déclencher la résolution ARP.
        for host in network.hosts():
            if str(host) != local:
                try:
                    probe.sendto(b"", (str(host), 9))
                except OSError:
                    pass
    for _ in range(6):
        time.sleep(0.5)
        found = _neighbor_ip(mac)
        if found:
            return found
    raise CloudError("Robot introuvable sur le réseau local : vérifie qu'il est allumé et sur le même réseau que ce PC.")


def firewall_hint(robot_ip: str) -> str | None:
    """Si ufw est actif et bloque l'entrant sans règle pour le port, renvoie la commande à exécuter."""
    try:
        enabled = "ENABLED=yes" in Path("/etc/ufw/ufw.conf").read_text()
        policy = Path("/etc/default/ufw").read_text()
    except OSError:
        return None
    if not enabled or 'DEFAULT_INPUT_POLICY="ACCEPT"' in policy:
        return None
    try:
        rules = Path("/etc/ufw/user.rules").read_text()
    except OSError:
        rules = ""
    if re.search(rf"--dport {TRANSFER_PORT}\b", rules):
        return None
    return f"sudo ufw allow from {robot_ip} to any port {TRANSFER_PORT} proto tcp"


class TransferHost:
    """Serveur HTTP éphémère : un seul fichier, une URL à jeton, accessible uniquement depuis l'IP du robot."""

    def __init__(self, payload: bytes, robot_ip: str):
        if not _private_ipv4(robot_ip):
            raise CloudError("Adresse locale du robot invalide.")
        self.payload, self.robot_ip = payload, robot_ip
        self.local_ip = _local_address(robot_ip)
        self.path = f"/voice/{secrets.token_hex(16)}/voice_pack.tar.gz"
        self.contacted = self.downloaded = False
        self.bytes_sent = 0
        self.httpd = None
        self._timer = None

    def start(self) -> str:
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self, head: bool):
                if self.client_address[0] != host.robot_ip or self.path != host.path:
                    self.send_error(404)
                    return
                host.contacted = True
                size = len(host.payload)
                start, end = 0, size - 1
                ranged = self.headers.get("Range")
                if ranged:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranged.strip())
                    if not match or (not match.group(1) and not match.group(2)):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    if match.group(1):
                        start = int(match.group(1))
                        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
                    else:
                        start = max(0, size - int(match.group(2)))
                    if start >= size or end < start:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                body = host.payload[start:end + 1]
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                if ranged:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head:
                    return
                self.wfile.write(body)
                host.bytes_sent += len(body)
                if start == 0 and end == size - 1 or host.bytes_sent >= size:
                    host.downloaded = True

            def do_GET(self):
                self._serve(False)

            def do_HEAD(self):
                self._serve(True)

        try:
            self.httpd = ThreadingHTTPServer((self.local_ip, TRANSFER_PORT), Handler)
        except OSError as error:
            raise CloudError(f"Port {TRANSFER_PORT} indisponible sur ce PC.") from error
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dreameui-voice-transfer").start()
        self._timer = threading.Timer(900, self.close)  # 15 min maximum
        self._timer.daemon = True
        self._timer.start()
        return f"http://{self.local_ip}:{TRANSFER_PORT}{self.path}"

    def status(self) -> dict:
        return {"active": self.httpd is not None, "contacted": self.contacted, "downloaded": self.downloaded,
                "bytes_sent": self.bytes_sent, "port": TRANSFER_PORT if self.httpd else None}

    def close(self):
        if self._timer:
            self._timer.cancel()
        httpd, self.httpd = self.httpd, None
        if httpd:
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()
