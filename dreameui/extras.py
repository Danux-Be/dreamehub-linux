"""Consommables, statistiques et réglages supplémentaires (propriétés MiOT de Tasshack/dreame-vacuum).

Chaque réglage n'est proposé que si l'appareil a renvoyé la propriété correspondante,
et chaque écriture est bornée à des valeurs documentées en amont.
"""
from __future__ import annotations

import json
import re

from . import device as _device  # import de module : device importe aussi ce fichier

# (clé, libellé, pourcentage (siid, piid), heures restantes (siid, piid), action de remise à zéro (siid, aiid))
CONSUMABLES = [
    ("main_brush", "Brosse principale", (9, 2), (9, 1), (9, 1)),
    ("side_brush", "Brosse latérale", (10, 2), (10, 1), (10, 1)),
    ("filter", "Filtre", (11, 1), (11, 2), (11, 1)),
    ("sensor", "Capteurs", (16, 1), (16, 2), (16, 1)),
    ("wheel", "Roue omnidirectionnelle", (30, 2), (30, 1), (30, 1)),
]

ON_OFF = [(0, "Désactivé"), (1, "Activé")]
# nom : (groupe, libellé, (siid, piid), options ou (min, max, pas, unité))
SETTINGS = {
    "wetness": ("water", "Humidité de la serpillière", (28, 1), (1, 26, 1, "")),
    "water_temperature": ("water", "Température de l'eau de lavage", (28, 8),
                          [(0, "Normale"), (1, "Tiède"), (2, "Chaude"), (3, "Très chaude"), (4, "Max")]),
    "cleangenius": ("robot", "CleanGenius", (28, 5), [(0, "Désactivé"), (1, "Nettoyage courant"), (2, "Nettoyage en profondeur")]),
    "volume": ("robot", "Volume", (7, 1), (0, 100, 1, " %")),
    "obstacle_avoidance": ("robot", "Évitement d'obstacles", (4, 21), ON_OFF),
    "carpet_boost": ("robot", "Boost sur tapis", (4, 12), ON_OFF),
    "resume_cleaning": ("robot", "Reprise après recharge", (4, 11), ON_OFF),
    "child_lock": ("robot", "Sécurité enfant", (4, 27), ON_OFF),
    "drying_time": ("station", "Durée de séchage", (4, 40), [(2, "2 h"), (3, "3 h"), (4, "4 h")]),
}
DND_PAIRS = [(5, 1), (5, 2), (5, 3), (5, 4)]
STAT_PAIRS = [(12, 1), (12, 2), (12, 3), (12, 4)]
PAIRS = sorted({p for c in CONSUMABLES for p in c[2:4]} | {s[2] for s in SETTINGS.values()} | set(DND_PAIRS) | set(STAT_PAIRS))
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _dnd_tasks(rows):
    raw = _device.prop(rows, 5, 4)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        tasks = json.loads(raw)
    except ValueError:
        return None
    return tasks if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict) else None


def describe(rows) -> dict:
    consumables = []
    for key, label, pct, hours, _ in CONSUMABLES:
        percent, left = _device.iprop(rows, *pct), _device.iprop(rows, *hours)
        if percent is None and left is None:
            continue
        consumables.append({"key": key, "label": label, "percent": percent, "hours": left})
    settings = {}
    for name, (group, label, pair, spec) in SETTINGS.items():
        value = _device.iprop(rows, *pair)
        if value is None:
            continue
        entry = {"group": group, "label": label, "value": value}
        if isinstance(spec, list):
            options = list(spec)
            if value not in {v for v, _ in options}:  # valeur inconnue : affichée telle quelle, jamais remplacée
                options.append((value, f"Valeur {value}"))
            entry["options"] = [{"value": v, "label": l} for v, l in options]
        else:
            entry["range"] = {"min": spec[0], "max": max(spec[1], value), "step": spec[2], "unit": spec[3]}
        settings[name] = entry
    tasks = _dnd_tasks(rows)
    if tasks:
        dnd = {"enabled": bool(tasks[0].get("en")), "start": tasks[0].get("st"), "end": tasks[0].get("et")}
    elif _device.prop(rows, 5, 1) is not None:
        dnd = {"enabled": bool(_device.prop(rows, 5, 1)), "start": _device.prop(rows, 5, 2), "end": _device.prop(rows, 5, 3)}
    else:
        dnd = None
    stats = {}
    for key, pair in zip(("first_cleaning", "total_minutes", "count", "total_area"), STAT_PAIRS):
        value = _device.iprop(rows, *pair)
        if value is not None:
            stats[key] = value
    return {"consumables": consumables, "settings": settings, "dnd": dnd, "stats": stats}


def setting_payload(rows, name: str, value) -> tuple[tuple[int, int], object]:
    if name not in SETTINGS:
        raise _device.unsupported()
    _, _, pair, spec = SETTINGS[name]
    current = _device.iprop(rows, *pair)
    if current is None:
        raise _device.unsupported()
    if not isinstance(value, int) or isinstance(value, bool):
        raise _device.ServiceError("Valeur de réglage invalide.")
    if isinstance(spec, list):
        if value not in {v for v, _ in spec}:
            raise _device.ServiceError("Valeur de réglage invalide.")
    elif not spec[0] <= value <= spec[1]:
        raise _device.ServiceError("Valeur de réglage hors limites.")
    if name == "resume_cleaning" and value == 1 and current == 2:
        value = 2  # modèles à recharge intelligente : 2 = activé
    return pair, value


def dnd_payload(rows, enabled, start, end) -> tuple[tuple[int, int], object] | list:
    if not isinstance(enabled, bool) or not (isinstance(start, str) and _TIME.fullmatch(start)) \
            or not (isinstance(end, str) and _TIME.fullmatch(end)) or start == end:
        raise _device.ServiceError("Plage horaire invalide.")
    tasks = _dnd_tasks(rows)
    if tasks:
        tasks = [dict(t) for t in tasks]
        tasks[0].update(en=enabled, st=start, et=end)
        return [((5, 4), json.dumps(tasks, separators=(",", ":")))]
    if _device.prop(rows, 5, 1) is None:
        raise _device.unsupported()
    return [((5, 1), enabled), ((5, 2), start), ((5, 3), end)]


def reset_action(rows, key: str) -> tuple[int, int]:
    entry = next((c for c in CONSUMABLES if c[0] == key), None)
    if entry is None or (_device.iprop(rows, *entry[2]) is None and _device.iprop(rows, *entry[3]) is None):
        raise _device.unsupported()
    return entry[4]
