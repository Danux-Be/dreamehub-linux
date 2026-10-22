"""Routes /api/* consommées par l'interface (port d'AppAPI.swift)."""
from __future__ import annotations

import base64
import json
import re
import threading
import time

from . import credentials
from .catalog import product_image
from .cloud import CloudError, NotAuthenticated
from .device import DeviceService, ServiceError, require_ack
from .maps import HistoryService, MapService, content_type, obstacle_image
from .voice import VoiceService

ROOM_TYPES = {0: "Pièce", 1: "Salon", 2: "Chambre principale", 3: "Bureau", 4: "Cuisine", 5: "Salle à manger",
              6: "Salle de bain", 7: "Balcon", 8: "Couloir", 9: "Buanderie", 10: "Dressing", 11: "Salle de réunion",
              12: "Bureau", 13: "Salle de sport", 14: "Salle de jeux", 15: "Chambre"}
SEGMENT_CLEANING, ZONE_CLEANING = 18, 19


class ApiError(Exception):
    pass


class Response:
    def __init__(self, body=None, status: int = 200, data: bytes | None = None, content_type: str = "application/json"):
        self.status, self.body, self.data, self.content_type = status, body, data, content_type


def _room_name(segment) -> str:
    sid, kind, name = segment[0], segment[3], segment[4]
    if isinstance(name, str) and name:
        try:
            return base64.b64decode(name).decode()
        except (ValueError, UnicodeDecodeError):
            return name
    label = ROOM_TYPES.get(kind, "Pièce")
    return f"{label} {sid}" if kind in (0, None) else label


class AppAPI:
    def __init__(self, read_only: bool = False):
        self.read_only = read_only
        self.service = DeviceService()
        self.boot = {"busy": True, "stage": "loading", "error": ""}
        self.credential_summary = self._empty_credentials()
        self.map_service: MapService | None = None
        self.history_service: HistoryService | None = None
        self.voice_service: VoiceService | None = None
        self.map_ready = False
        self.viewing_history = False
        self.last_map: dict | None = None
        self.status_error: str | None = None
        self.refreshed = 0.0
        self._refreshing = threading.Lock()
        self._session = threading.RLock()

    @staticmethod
    def _empty_credentials() -> dict:
        return {"available": credentials.available(), "remembered": False, "username": "", "region": "eu", "account": "dreame"}

    # -- cycle de vie -----------------------------------------------------
    def bootstrap(self):
        try:
            saved = credentials.load()
            if saved:
                self.credential_summary = {"available": True, "remembered": True, "username": saved.username,
                                           "region": saved.region, "account": saved.account}
                self.service.login(saved.username, saved.password, saved.region, saved.account)
                self.boot = {"busy": False, "stage": "ready", "error": ""}
                self._prefetch_map()
            else:
                self.boot = {"busy": False, "stage": "login_required", "error": ""}
        except Exception as error:  # noqa: BLE001 — toute erreur ramène à l'écran de connexion
            self.boot = {"busy": False, "stage": "login_required", "error": str(error)}

    def _reset_services(self):
        self.map_service = self.history_service = self.voice_service = None
        self.map_ready = self.viewing_history = False
        self.last_map = None

    def _prefetch_map(self):
        """Charge la carte en arrière-plan pour connaître les pièces dès l'ouverture."""
        def run():
            try:
                self._current_map()
            except Exception:  # noqa: BLE001 — l'onglet Carte affichera l'erreur au besoin
                pass
        threading.Thread(target=run, daemon=True).start()

    def _require_control(self):
        if self.read_only:
            raise ApiError("Mode lecture seule : aucun mouvement ni réglage n'a été envoyé.")

    def _device(self):
        if self.service.selected is None:
            raise NotAuthenticated()
        return self.service.selected

    def _maps(self) -> MapService:
        if self.map_service is None:
            self.map_service = MapService(self.service.cloud)
        return self.map_service

    def _history(self) -> HistoryService:
        if self.history_service is None:
            self.history_service = HistoryService(self.service.cloud)
        return self.history_service

    def _voice(self) -> VoiceService:
        device = self._device()
        if self.voice_service is None or self.voice_service.model != device.model:
            self.voice_service = VoiceService(device.model)
        return self.voice_service

    def _current_map(self) -> dict:
        result = self._maps().current(self._device())
        self.map_ready, self.viewing_history, self.last_map = True, False, result
        return result

    # -- état -------------------------------------------------------------
    def _tasks(self) -> dict:
        rooms = []
        segments = (self.last_map or {}).get("segments") or []
        for segment in segments:
            if isinstance(segment, list) and len(segment) >= 5 and isinstance(segment[0], int) and segment[0] < 64:
                rooms.append({"id": segment[0], "name": _room_name(segment)})
        return {"rooms": rooms, "zone_available": not self.read_only and self.last_map is not None}

    def state(self) -> dict:
        result = self.service.public_state()
        device = result.get("device")
        if isinstance(device, dict):
            device["image_url"] = product_image(device.get("model", ""))
            supports = dict(device.get("supports") or {})
            supports["map"] = True  # l'onglet Carte reste visible pour signaler les échecs
            supports["go_to"] = False
            device["supports"] = supports
            device["tasks"] = self._tasks()
        result["boot"] = self.boot
        result["credentials"] = self.credential_summary
        result["tailscale"] = {"installed": False, "connected": False, "enabled": False, "url": ""}
        result["read_only"] = self.read_only
        if self.read_only:
            result["warning"] = "Mode lecture seule : les commandes du robot sont désactivées."
        if self.status_error:
            result["warning"] = "Dernières données affichées : " + self.status_error
        if result.get("authenticated") and time.time() - self.refreshed > 3 and self._refreshing.acquire(blocking=False):
            threading.Thread(target=self._refresh, daemon=True).start()
        return result

    def _refresh(self):
        try:
            self.service.refresh()
            self.status_error = None
        except Exception as error:  # noqa: BLE001
            self.status_error = str(error)
        finally:
            self.refreshed = time.time()
            self._refreshing.release()

    # -- routage ----------------------------------------------------------
    def handle(self, method: str, path: str, body: dict, binary: bytes | None = None) -> Response:
        try:
            plain = path.split("?", 1)[0]
            match = re.fullmatch(r"/api/map/obstacle/([A-Za-z0-9_.:-]{1,128})/image", plain)
            if method == "GET" and match:
                device = self._device()
                if self.viewing_history and self.history_service:
                    data = self.history_service.photo(device, match.group(1))
                else:
                    data = obstacle_image(self.service.cloud, device, self._maps().current_capture(device), match.group(1))
                return Response(data=data, content_type=content_type(data) or "application/octet-stream")
            return Response(self._route(method, plain, body))
        except (ApiError, CloudError, credentials.CredentialError, ValueError) as error:
            return Response({"error": str(error)}, status=400)
        except Exception as error:  # noqa: BLE001 — remonter un message lisible plutôt qu'une page vide
            return Response({"error": f"Erreur interne : {error}"}, status=500)

    def _route(self, method: str, path: str, body: dict) -> dict:
        if method == "GET":
            match = re.fullmatch(r"/api/cleaning/history/(\d+)/map", path)
            if match:
                result = self._history().map(self._device(), int(match.group(1)))
                self.viewing_history = True
                return {"map": result}
            if path == "/api/state":
                return self.state()
            if path == "/api/credentials":
                return self.credential_summary
            if path == "/api/map/current":
                return {"map": self._current_map()}
            if path == "/api/cleaning/history":
                return {"history": self._history().list(self._device())}
            if path == "/api/tailscale/status":
                return {"installed": False, "connected": False, "enabled": False, "url": ""}
            if path == "/api/voice/packs":
                packs = sorted(self._voice().packs().values(), key=lambda p: p.id)
                return {"packs": [{"id": p.id, "name": p.name} for p in packs],
                        "current": self.service.cached.get("voice_language")}
            if path.startswith("/api/voice/"):
                raise ApiError("L'éditeur d'annonces n'est pas encore disponible dans la version Linux.")
            raise ApiError("Fonction non disponible dans cette version.")
        if method != "POST":
            raise ApiError("Méthode de requête invalide.")
        if path == "/api/login":
            return self._login(body)
        if path == "/api/logout":
            with self._session:
                self.service.logout()
                self._reset_services()
                self.boot = {"busy": False, "stage": "login_required", "error": ""}
            return {"ok": True}
        if path == "/api/credentials/forget":
            self._require_control()
            credentials.delete()
            self.credential_summary = self._empty_credentials()
            return {"ok": True}
        if path == "/api/device/select":
            device_id = body.get("device_id")
            if not isinstance(device_id, str):
                raise ApiError("Aucun appareil sélectionné.")
            with self._session:
                self.service.select(device_id)
                self._reset_services()
            self._prefetch_map()
            return self.state()
        if path == "/api/action":
            self._require_control()
            command = body.get("command")
            if not isinstance(command, str):
                raise ApiError("Commande manquante.")
            self.service.action(command)
            return {"ok": True}
        if path == "/api/setting":
            self._require_control()
            name, value = body.get("name"), body.get("value")
            if not isinstance(name, str) or not isinstance(value, int) or isinstance(value, bool):
                raise ApiError("Réglage invalide.")
            self.service.setting(name, value)
            return {"ok": True}
        if path == "/api/extra-setting":
            self._require_control()
            if body.get("name") == "dnd":
                self.service.set_dnd(body.get("enabled"), body.get("start"), body.get("end"))
            else:
                self.service.extra_setting(body.get("name"), body.get("value"))
            return {"ok": True}
        if path == "/api/consumable/reset":
            self._require_control()
            key = body.get("key")
            if not isinstance(key, str):
                raise ApiError("Consommable inconnu.")
            self.service.reset_consumable(key)
            return {"ok": True}
        if path == "/api/cleaning-task":
            self._require_control()
            return self._cleaning_task(body.get("kind"), body.get("payload") or {})
        if path.startswith("/api/tailscale/"):
            raise ApiError("Tailscale n'est pas pris en charge dans la version Linux.")
        if path == "/api/voice/install":
            self._require_control()
            language = body.get("language")
            if not isinstance(language, str):
                raise ApiError("Aucune langue sélectionnée.")
            device = self._device()
            pack = self._voice().pack(language)
            # Le robot télécharge lui-même le pack officiel depuis l'URL, après vérification MD5/taille.
            value = json.dumps(pack.install_value, sort_keys=True, separators=(",", ":"))
            require_ack(self.service.cloud.rpc(device, "set_properties",
                                               [{"did": device.id, "siid": 7, "piid": 4, "value": value}]))
            return {"ok": True}
        if path.startswith("/api/voice/"):
            raise ApiError("L'éditeur d'annonces n'est pas encore disponible dans la version Linux.")
        if path == "/api/go-to":
            raise ApiError("L'envoi vers un point n'est pas vérifié pour ce modèle.")
        raise ApiError("Fonction non disponible dans cette version.")

    def _login(self, body: dict) -> dict:
        user, password = body.get("username") or "", body.get("password") or ""
        region, account = body.get("region") or "eu", body.get("account") or "dreame"
        if account not in ("dreame", "mova"):
            raise ApiError("Fournisseur de compte invalide.")
        if body.get("use_saved") is True:
            saved = credentials.load()
            if saved is None:
                raise ApiError("Aucun compte enregistré.")
            if not saved.matches(user, region, account):
                raise ApiError("Le compte ou la région a changé. Saisis à nouveau ton mot de passe.")
            user, password = saved.username, saved.password
        if not user or not password:
            raise ApiError("Renseigne l'identifiant et le mot de passe.")
        with self._session:
            self._reset_services()
            self.service.login(user, password, region, account)
            if not self.read_only and body.get("remember"):
                credentials.save(credentials.StoredCredentials(user, password, region, account))
                self.credential_summary = {"available": True, "remembered": True, "username": user,
                                           "region": region, "account": account}
            self.boot = {"busy": False, "stage": "ready", "error": ""}
        self._prefetch_map()
        return self.state()

    def _cleaning_task(self, kind, payload: dict) -> dict:
        device = self._device()
        if kind == "all":
            self.service.action("start")
            return {"ok": True}
        cached = self.service.cached
        suction = cached.get("suction") if cached.get("suction") in (0, 1, 2, 3) else 1
        water = cached.get("water") if cached.get("water") in (1, 2, 3) else 2
        if kind == "rooms":
            ids = payload.get("room_ids")
            known = {room["id"] for room in self._tasks()["rooms"]}
            if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and i in known for i in ids):
                raise ApiError("Sélection de pièces invalide.")
            # [segment, passages, aspiration, eau, ordre] — format Tasshack/dreame-vacuum.
            mode, value = SEGMENT_CLEANING, {"selects": [[i, 1, suction, water, n + 1] for n, i in enumerate(ids)]}
        elif kind == "zone":
            zones = payload.get("zones")
            if not isinstance(zones, list) or not zones:
                raise ApiError("Zone invalide.")
            areas = []
            for zone in zones:
                if not (isinstance(zone, list) and len(zone) == 4 and all(isinstance(v, (int, float)) for v in zone)):
                    raise ApiError("Zone invalide.")
                x1, y1, x2, y2 = (int(round(v)) for v in zone)
                if abs(x2 - x1) < 300 or abs(y2 - y1) < 300:
                    raise ApiError("Zone trop petite (30 cm minimum).")
                areas.append([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), 1, suction, water])
            mode, value = ZONE_CLEANING, {"areas": areas}
        else:
            raise ApiError("Type de nettoyage inconnu.")
        reply = self.service.cloud.rpc(device, "action", {
            "did": device.id, "siid": 4, "aiid": 1,
            "in": [{"piid": 1, "value": mode}, {"piid": 10, "value": json.dumps(value, separators=(",", ":"))}]})
        require_ack(reply)
        return {"ok": True}

    def shutdown(self):
        try:
            self.service.logout()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["AppAPI", "Response", "ServiceError"]
