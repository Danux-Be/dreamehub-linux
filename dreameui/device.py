"""État et commandes de l'aspirateur (port de DeviceContract.swift / DeviceService.swift)."""
from __future__ import annotations

import json
import threading
import time

from . import extras
from .catalog import product_image, product_name
from .cloud import CloudClient, CloudError, Device, NotAuthenticated

PROPERTY_PAIRS = [(2, 1), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2), (4, 3), (4, 4), (4, 5), (4, 23), (4, 25),
                  (4, 63), (4, 64), (15, 3), (15, 5), (7, 2), (7, 3), (27, 1), (27, 2), (27, 3), (27, 4),
                  (9, 1), (10, 1), (11, 1), (16, 1)]
GROUPED_MODELS = {"dreame.vacuum.r2338a", "dreame.vacuum.r2377", "dreame.vacuum.r2338h"}

STATE_NAMES = {1: "Aspiration", 2: "Inactif", 3: "En pause", 4: "Erreur", 5: "Retour à la base", 6: "En charge",
               7: "Lavage du sol", 8: "Séchage des serpillières", 9: "Lavage des serpillières",
               10: "Retour pour lavage", 11: "Cartographie", 12: "Aspiration et lavage", 13: "Charge terminée",
               14: "Mise à jour", 20: "Remplissage d'eau", 21: "Lavage des serpillières en pause",
               22: "Vidage du bac à poussière", 24: "Charge intelligente", 28: "Retour pour vidage",
               29: "Tâche en attente", 30: "Nettoyage de la station"}
SUCTION_NAMES = {0: "Silencieux", 1: "Standard", 2: "Turbo", 3: "Max"}
MODE_NAMES = {0: "Aspiration seule", 1: "Lavage seul", 2: "Aspiration + lavage", 3: "Aspiration puis lavage"}
WATER_NAMES = {1: "Faible", 2: "Moyen", 3: "Élevé"}
BASE_NAMES = {0: "Prête", 1: "Lavage des serpillières", 2: "Séchage des serpillières", 3: "En attente du retour",
              4: "En pause", 5: "Remplissage d'eau"}
VOICE_STATUS = {0: "Prêt", 1: "Téléchargement du pack", 2: "Installation du pack", 3: "Terminé", 4: "Échec de l'installation"}
DOCK_NAMES = {
    "clean_water": {0: "OK", 1: "Absent", 2: "Faible / vide", 3: "Raccordé au réseau"},
    "dirty_water": {0: "OK", 1: "Plein ou absent"},
    "dust_bag": {0: "OK", 1: "Absent", 2: "Plein"},
    "detergent": {0: "OK", 1: "Absent", 2: "Vide"},
}
ACTIONS = {"start": (2, 1, None), "pause": (2, 2, None), "dock": (3, 1, None), "stop": (4, 2, None),
           "locate": (7, 1, None), "auto_empty": (15, 1, None), "wash": (4, 4, "2,1"),
           "wash_pause": (4, 4, "1,0"), "dry_start": (4, 4, "3,1"), "dry_stop": (4, 4, "3,0")}
# Modèles MOVA dont l'encodage groupé (mode + humidité dans 4.23) a été vérifié en amont.
VERIFIED_GROUPED = set(json.loads("""["dreame.vacuum.r2427","dreame.vacuum.r2427a","mova.vacuum.r5732a",
"mova.vacuum.r9504a","mova.vacuum.r6710a","mova.vacuum.r6710g","mova.vacuum.r6710h","mova.vacuum.r6710t",
"mova.vacuum.r67095","mova.vacuum.r6709a","mova.vacuum.r6709g","mova.vacuum.r6709h","mova.vacuum.r2599h",
"mova.vacuum.r2599k","mova.vacuum.r2599u","mova.vacuum.r2475a","mova.vacuum.r2475h","mova.vacuum.r2475t",
"mova.vacuum.r2587a","mova.vacuum.r9416d","mova.vacuum.r2519a","mova.vacuum.r5770","mova.vacuum.r590q",
"mova.vacuum.r5977a","mova.vacuum.r5977c","mova.vacuum.r5977f","mova.vacuum.r5977g","mova.vacuum.r5977h",
"mova.vacuum.r5977k","mova.vacuum.r5977t","mova.vacuum.r5769a","mova.vacuum.r5769f","mova.vacuum.r5769g",
"mova.vacuum.r5769h","mova.vacuum.r5769q","mova.vacuum.r5769t","mova.vacuum.r5770a","mova.vacuum.r5770g",
"mova.vacuum.r5770h","mova.vacuum.r5770q","mova.vacuum.r5770t","mova.vacuum.r590qf","mova.vacuum.r2525a",
"mova.vacuum.r2525e","mova.vacuum.r2525h","mova.vacuum.r2582a","mova.vacuum.r2582h","mova.vacuum.r2582c",
"mova.vacuum.r2582k","mova.vacuum.r5979a","mova.vacuum.r5979f","mova.vacuum.r5979g","mova.vacuum.r5979h",
"mova.vacuum.r5979t","mova.vacuum.r59795","mova.vacuum.r59796","mova.vacuum.r59797","mova.vacuum.r59799",
"mova.vacuum.r5979u","mova.vacuum.r5978a","mova.vacuum.r5978h","mova.vacuum.r5978q","mova.vacuum.r2430a",
"mova.vacuum.r2430u","mova.vacuum.r9540a","mova.vacuum.r9540h","mova.vacuum.r9540k","mova.vacuum.r9540n",
"mova.vacuum.r9540u","mova.vacuum.r9544a","mova.vacuum.r5765h","mova.vacuum.r5765k","mova.vacuum.r5765p",
"mova.vacuum.r5765q","mova.vacuum.r5765u","mova.vacuum.r5766h","mova.vacuum.r5766k","mova.vacuum.r5766q"]"""))


class ServiceError(CloudError):
    pass


def unsupported() -> ServiceError:
    return ServiceError("Cette fonction n'a pas pu être vérifiée sur l'appareil.")


def action_payload(name: str, device_id: str) -> dict:
    if name not in ACTIONS:
        raise ServiceError("Commande inconnue.")
    siid, aiid, value = ACTIONS[name]
    return {"did": device_id, "siid": siid, "aiid": aiid, "in": [{"piid": 10, "value": value}] if value else []}


def require_ack(response):
    if isinstance(response, list):
        if response and all(isinstance(r, dict) and r.get("code") == 0 for r in response):
            return
    elif isinstance(response, dict) and response.get("code") == 0:
        return
    raise ServiceError("L'appareil n'a pas accepté la commande ou la réponse n'a pas pu être vérifiée.")


def prop(rows, siid: int, piid: int):
    for r in rows or []:
        if isinstance(r, dict) and r.get("siid") == siid and r.get("piid") == piid and r.get("code") == 0:
            return r.get("value")
    return None


def iprop(rows, siid: int, piid: int) -> int | None:
    value = prop(rows, siid, piid)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def updated_grouped(raw: int, mode: int | None = None, water: int | None = None) -> int:
    fields = [raw & 3, (raw >> 8) & -769, raw >> 16]
    if mode is not None:
        if mode not in (0, 1, 2, 3):
            raise ServiceError("Valeur de réglage invalide.")
        fields[0] = {0: 2, 1: 1, 2: 0, 3: 3}[mode]
    if water is not None:
        if water not in (1, 2, 3):
            raise ServiceError("Valeur de réglage invalide.")
        fields[2] = water
    return (fields[2] << 16) | (fields[1] << 8) | fields[0]


class DeviceService:
    def __init__(self, cloud: CloudClient | None = None):
        self.cloud = cloud or CloudClient()
        self.selected: Device | None = None
        self.devices: list[Device] = []
        self.cached: dict = {}
        self.properties: list = []
        self.switches: dict[str, int] = {}
        self.extra_rows: list = []
        self.generation = 0
        self._command_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def login(self, username: str, password: str, region: str, account: str = "dreame"):
        self.generation += 1
        epoch = self.generation
        self.selected, self.devices, self.cached, self.properties, self.switches = None, [], {}, [], {}
        self.cloud.login(username, password, region, account)
        found = self.cloud.devices()
        if epoch != self.generation:
            raise NotAuthenticated()
        if not found:
            raise ServiceError("Aucun aspirateur Dreame sélectionnable sur ce compte.")
        self.devices = found
        self.select(found[0].id)

    def select(self, device_id: str):
        target = next((d for d in self.devices if d.id == device_id), None)
        if target is None:
            raise ServiceError("Appareil introuvable.")
        self.generation += 1
        self.selected, self.cached, self.properties, self.switches = target, self.describe(target), [], {}
        self.refresh()

    def logout(self):
        self.generation += 1
        self.selected, self.devices, self.cached, self.properties, self.switches = None, [], {}, [], {}
        self.cloud.logout()

    @staticmethod
    def describe(device: Device) -> dict:
        online = device.raw.get("online")
        if isinstance(online, int) and not isinstance(online, bool):
            online = online == 1
        name = product_name(device.model) if device.name == device.model else device.name
        return {"id": device.id, "name": name or device.name, "model": device.model,
                "online": online if isinstance(online, bool) else None, "image_url": product_image(device.model)}

    def public_state(self) -> dict:
        result = {"authenticated": self.selected is not None,
                  "devices": [{**self.describe(d), "selected": self.selected is not None and d.id == self.selected.id}
                              for d in self.devices],
                  "selected_device_id": self.selected.id if self.selected else None}
        if self.selected:
            result["device"] = dict(self.cached)
        return result

    def read(self, pairs) -> list:
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        reply = self.cloud.rpc(device, "get_properties", [{"did": device.id, "siid": s, "piid": p} for s, p in pairs])
        if not isinstance(reply, list):
            raise CloudError("Réponse Dreame inattendue.")
        return reply

    def refresh(self):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            epoch = self.generation
            rows = self.read(PROPERTY_PAIRS + [(4, 34), (4, 46), (4, 50)])
            if not any(isinstance(r, dict) and r.get("code") == 0 and "value" in r for r in rows):
                raise CloudError("Aucune propriété lisible renvoyée par l'appareil.")
            if epoch != self.generation:
                return
            try:
                # Lecture séparée : un échec ne doit pas masquer l'état principal.
                extra_rows = self.read(extras.PAIRS)
            except CloudError:
                extra_rows = self.extra_rows
            if epoch != self.generation:
                return
            self.properties, self.switches, self.extra_rows = rows, {}, extra_rows
            raw = prop(rows, 4, 50)
            if isinstance(raw, str):
                try:
                    decoded = json.loads(raw)
                except ValueError:
                    decoded = None
                for item in decoded if isinstance(decoded, list) else [decoded] if isinstance(decoded, dict) else []:
                    if isinstance(item, dict) and isinstance(item.get("k"), str) and isinstance(item.get("v"), int):
                        self.switches[item["k"]] = item["v"]
            self.cached = self._status(device, rows)
        finally:
            self._refresh_lock.release()

    def uses_grouped(self, model: str, rows) -> bool:
        if model in GROUPED_MODELS:
            return True
        if model in VERIFIED_GROUPED and prop(rows, 4, 25) is not None:
            return True
        # Une valeur 4.23 > 3 ne peut pas être un mode simple : c'est l'encodage groupé
        # (mode dans les 2 bits faibles, humidité dans les bits 16+), utilisé par les L40s.
        raw = iprop(rows, 4, 23)
        return raw is not None and raw > 3

    def _status(self, device: Device, rows) -> dict:
        n = lambda s, i: iprop(rows, s, i)
        result = self.describe(device)
        mode, water = n(4, 23), n(4, 5)
        if self.uses_grouped(device.model, rows) and mode is not None:
            raw = mode
            mode = {0: 2, 1: 1, 2: 0, 3: 3}[raw & 3]
            water = raw >> 16 if 1 <= raw >> 16 <= 3 else None
        suction = 4 if self.switches.get("SuctionMax") == 1 else n(4, 4)
        for key, s, i in (("state", 2, 1), ("error", 2, 2), ("battery", 3, 1), ("charging", 3, 2), ("status", 4, 1),
                          ("cleaning_time", 4, 2), ("cleaned_area", 4, 3), ("cleaning_progress", 4, 63),
                          ("drying_progress", 4, 64), ("base_status", 4, 25), ("voice_language", 7, 2),
                          ("voice_status", 7, 3)):
            result[key] = prop(rows, s, i)
        state = n(2, 1)
        result["state_text"] = STATE_NAMES.get(state, f"État {state}") if state is not None else "Inconnu"
        result["base_status_text"] = BASE_NAMES.get(n(4, 25), "Inconnu")
        result["voice_status_text"] = VOICE_STATUS.get(n(7, 3), "En attente d'état")
        result["suction"] = suction
        result["suction_text"] = "Max+" if suction == 4 else SUCTION_NAMES.get(suction)
        result["water"], result["water_text"] = water, WATER_NAMES.get(water)
        result["cleaning_mode"], result["cleaning_mode_text"] = mode, MODE_NAMES.get(mode)
        # Heures restantes (l'interface affiche « X h ») : 9.1/10.1 sont des heures, mais 11.1/16.1 des %.
        hours = lambda s, i, fallback: prop(self.extra_rows, s, i) if prop(self.extra_rows, s, i) is not None else prop(rows, *fallback)
        result["consumables"] = {"main_brush": prop(rows, 9, 1), "side_brush": prop(rows, 10, 1),
                                 "filter": hours(11, 2, (11, 1)), "sensor": hours(16, 2, (16, 1))}
        result["extras"] = extras.describe(self.extra_rows)
        dock = {}
        for key, index in (("clean_water", 1), ("dirty_water", 2), ("dust_bag", 3), ("detergent", 4)):
            value = n(27, index)
            dock[key] = {"value": prop(rows, 27, index),
                         "text": DOCK_NAMES[key].get(value, f"État {value}") if value is not None else None,
                         "attention": (value not in (0, 3) if key == "clean_water" else value != 0) if value is not None else False}
        result["dock_health"] = dock
        result["sensor_profile"] = {}
        result["supports"] = {"suction": n(4, 4) is not None, "water": water is not None,
                              "cleaning_mode": mode is not None, "base": n(4, 25) is not None,
                              "auto_empty": n(15, 3) is not None or n(15, 5) is not None,
                              "voice": prop(rows, 7, 2) is not None or prop(rows, 7, 3) is not None,
                              "map": False, "go_to": False}
        result["updated_at"] = time.strftime("%H:%M")
        result["controls"] = self._controls(suction)
        return result

    def _controls(self, suction) -> dict:
        controls = {}

        def control(name, label, value, choices):
            if value is None:
                return
            controls[name] = {"label": label, "value": value, "available": True, "reason": "",
                              "options": [{"value": v, "label": l, "available": True} for v, l in choices]}

        options = [(0, "Silencieux"), (1, "Standard"), (2, "Turbo"), (3, "Max")]
        if "SuctionMax" in self.switches:
            options.append((4, "Max+"))
        control("suction", "Puissance d'aspiration", suction, options)
        control("washing", "Mode de lavage", iprop(self.properties, 4, 46), [(0, "Léger"), (1, "Standard"), (2, "Profond")])
        control("auto_wash", "Lavage automatique", iprop(self.properties, 4, 34), [(0, "Désactivé"), (1, "Activé")])
        control("hot_wash", "Lavage à l'eau chaude", self.switches.get("HotWash"), [(0, "Désactivé"), (1, "Activé")])
        return controls

    def _busy(self):
        if not self._command_lock.acquire(blocking=False):
            raise ServiceError("Réponse à la commande précédente en attente.")

    def action(self, command: str):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        self._busy()
        try:
            require_ack(self.cloud.rpc(device, "action", action_payload(command, device.id)))
        finally:
            self._command_lock.release()

    def _set(self, device: Device, piid: int, value):
        require_ack(self.cloud.rpc(device, "set_properties", [{"did": device.id, "siid": 4, "piid": piid, "value": value}]))

    def _set_switch(self, device: Device, key: str, value: int):
        if key not in self.switches:
            raise unsupported()
        self._set(device, 50, json.dumps({"k": key, "v": value}, sort_keys=True, separators=(",", ":")))

    def _set_any(self, device: Device, pairs):
        require_ack(self.cloud.rpc(device, "set_properties",
                                   [{"did": device.id, "siid": s, "piid": p, "value": v} for (s, p), v in pairs]))

    def extra_setting(self, name: str, value):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        self._busy()
        try:
            self._set_any(device, [extras.setting_payload(self.extra_rows, name, value)])
        finally:
            self._command_lock.release()

    def set_dnd(self, enabled, start, end):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        self._busy()
        try:
            self._set_any(device, extras.dnd_payload(self.extra_rows, enabled, start, end))
        finally:
            self._command_lock.release()

    def reset_consumable(self, key: str):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        siid, aiid = extras.reset_action(self.extra_rows, key)
        self._busy()
        try:
            require_ack(self.cloud.rpc(device, "action", {"did": device.id, "siid": siid, "aiid": aiid, "in": []}))
        finally:
            self._command_lock.release()

    def setting(self, name: str, value: int):
        device = self.selected
        if device is None:
            raise NotAuthenticated()
        self._busy()
        try:
            if name == "suction":
                if not 0 <= value <= 4 or prop(self.properties, 4, 4) is None:
                    raise unsupported()
                if value == 4:
                    self._set_switch(device, "SuctionMax", 1)
                else:
                    if self.switches.get("SuctionMax") == 1:
                        self._set_switch(device, "SuctionMax", 0)
                    self._set(device, 4, value)
            elif name in ("water", "cleaning_mode"):
                if value not in ((1, 2, 3) if name == "water" else (0, 1, 2, 3)):
                    raise ServiceError("Valeur de réglage invalide.")
                if self.uses_grouped(device.model, self.properties):
                    raw = iprop(self.read([(4, 23)]), 4, 23)
                    if raw is None:
                        raise CloudError("Réponse Dreame inattendue.")
                    # Ne pas écraser une échelle d'humidité plus fine par l'ancienne échelle à 3 niveaux.
                    if name == "water" and not 1 <= raw >> 16 <= 3:
                        raise unsupported()
                    self._set(device, 23, updated_grouped(raw, mode=value if name == "cleaning_mode" else None,
                                                          water=value if name == "water" else None))
                else:
                    piid = 5 if name == "water" else 23
                    if prop(self.properties, 4, piid) is None:
                        raise unsupported()
                    self._set(device, piid, value)
            elif name in ("washing", "auto_wash"):
                piid = 46 if name == "washing" else 34
                if value not in ((0, 1, 2) if name == "washing" else (0, 1)) or prop(self.properties, 4, piid) is None:
                    raise unsupported()
                self._set(device, piid, value)
            elif name == "hot_wash":
                if value not in (0, 1):
                    raise ServiceError("Valeur de réglage invalide.")
                self._set_switch(device, "HotWash", value)
            else:
                raise unsupported()
        finally:
            self._command_lock.release()
        # Jamais d'état optimiste : le prochain sondage relit l'appareil.
