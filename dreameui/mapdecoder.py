"""Décodage des cartes Dreame.

Port Python de MapDecoder.swift (dreamehub, MIT), qui documente la disposition
binaire du décodeur MIT de Tasshack/dreame-vacuum. Aucune donnée n'est inventée.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
import time
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAX_INFLATED = 33_554_432


class MapError(Exception):
    pass


class IncrementalFrame(MapError):
    def __init__(self):
        super().__init__("Carte incrémentale : la trame complète précédente est requise.")


def _malformed(reason: str) -> MapError:
    return MapError(f"Données de carte invalides : {reason}")


def _i16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<h", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _numeric(value):
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            n = float(value)
        except ValueError:
            return None
        return n if math.isfinite(n) else None
    return None


def _number(value, fallback: int = 0) -> int:
    n = _numeric(value)
    return int(n) if n is not None else fallback


def _describe(value) -> str:
    """Équivalent de String(describing:) pour les identifiants JSON."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "<null>"
    return str(value)


def inflate(data: bytes) -> bytes:
    decompressor = zlib.decompressobj()
    try:
        out = decompressor.decompress(data, MAX_INFLATED)
    except zlib.error as error:
        raise MapError("Décompression de la carte impossible.") from error
    if decompressor.unconsumed_tail:
        raise MapError("Carte décompressée trop volumineuse.")
    return out


def inflate_envelope(raw: str, iv: str | None = None, key: str | None = None) -> bytes:
    parts = raw.split(",", 1)
    encoded = parts[0].replace("_", "/").replace("-", "+")
    encoded += "=" * ((4 - len(encoded) % 4) % 4)
    try:
        compressed = base64.b64decode(re.sub(r"[^A-Za-z0-9+/=]", "", encoded))
    except ValueError as error:
        raise _malformed("base64") from error
    if not compressed:
        raise _malformed("base64")
    secret = key if key is not None else (parts[1] if len(parts) == 2 else None)
    if secret is not None:
        if not iv or len(iv.encode()) != 16:
            raise MapError("Carte chiffrée : IV de 16 octets requis pour ce modèle.")
        aes_key = hashlib.sha256(secret.encode()).hexdigest()[:32].encode()
        if len(compressed) % 16:
            raise MapError("Déchiffrement de la carte impossible.")
        decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv.encode())).decryptor()
        compressed = decryptor.update(compressed) + decryptor.finalize()
        # CCCrypt sans option de bourrage : zlib ignore les octets de bourrage résiduels.
    return inflate(compressed)


def decode(raw: str, version: int = 1, iv: str | None = None, key: str | None = None) -> dict:
    return decode_inflated(inflate_envelope(raw, iv, key), version)


def _pixel_type(p: int, version: int, saved_status: int, frame_map: bool) -> int:
    if p <= 0:
        return 0
    if version == 3:
        segment, wall = p & 31, (p >> 5) & 3
        if segment == 0:
            return 0
        if segment == 31:
            return 231 if wall == 3 else 255 if wall > 0 else (253 if saved_status in (0, 1) else 254)
        return 200 + segment if wall == 3 else 100 + segment if wall > 0 else segment
    if frame_map:
        segment = p >> 2
        if segment > 0:
            return {63: 255, 62: 254, 61: 252}.get(segment, segment)
        return 255 if p & 63 == 2 else (253 if p & 63 in (1, 3) else 0)
    if saved_status in (0, 1):
        return 255 if p & 63 == 2 else (253 if p & 63 in (1, 3) else 0)
    if saved_status == 2:
        return 0 if p & 63 == 0 else (255 if p & 63 == 2 else 253)
    segment = p & 63
    return (255 if segment == 0 else 100 + segment) if p >> 7 else segment


_PATH = re.compile(r"([MWSLl])(-?\d+),(-?\d+)")


def _path(text: str) -> list[list[int]]:
    groups, group, x, y = [], [], 0, 0
    for op, xs, ys in _PATH.findall(text):
        xx, yy = int(xs), int(ys)
        if op == "L":
            x, y = x + xx, y + yy
        else:
            x, y = xx, yy
        if op not in ("L", "l"):
            if len(group) > 2:
                groups.append(group)
            group = [1 if op == "S" else 2 if op == "W" else 3]
        if not group:
            group = [3]
        group += [x, y]
    if len(group) > 2:
        groups.append(group)
    return groups


def _numeric_lines(rows) -> list[list[float]]:
    result = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, list) and len(row) >= 4:
            values = [_numeric(v) for v in row[:4]]
            if all(v is not None for v in values):
                result.append(values)
    return result


def _decode_obstacles(rows, version: int) -> list[list]:
    result = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 4:
            continue
        x, y, certainty = _numeric(row[0]), _numeric(row[1]), _numeric(row[3])
        if x is None or y is None or certainty is None or not 0 <= certainty <= 1:
            continue
        kind, probability = _number(row[2]), int(certainty * 100)
        if version == 3 and 0 < probability < 50:
            continue
        last = _describe(row[-1])
        if len(row) > 18:
            ignore = _number(row[18])
        else:
            lv = _number(row[-1], -1)
            ignore = lv if len(last) == 1 and 0 <= lv <= 4 else 0
        if ignore in (3, 4) or (version == 3 and ignore == 2):
            continue
        # L'identifiant d'origine sert à récupérer la photo ; sa présence ne garantit pas qu'elle existe.
        oid = row[4] if len(row) > 4 and _describe(row[4]).strip() != "" else None
        detailed = len(row) >= 8 and (_numeric(oid) or 0) >= 1000

        def position(at):
            if detailed and len(row) > at:
                n = _numeric(row[at])
                return n * 100 if n is not None else None
            return None

        result.append([str(len(result) + 1), x, y, kind, probability, ignore,
                       (_number(row[11]) if len(row) >= 13 else 2) if detailed else 0, oid,
                       position(7), position(8), position(9), position(10), None, None, None, None])
    return result


def _add_run(layers: dict, kind: int, x: int, y: int):
    runs = layers.setdefault(str(kind), [])
    if len(runs) >= 3 and runs[-2] == y and runs[-3] + runs[-1] == x:
        runs[-1] += 1
    else:
        runs += [x, y, 1]


def _cells(runs: list[int]) -> int:
    return sum(runs[2::3])


def decode_inflated(data: bytes, version: int = 1, nesting: int = 0) -> dict:
    if nesting > 4:
        raise _malformed("imbrication excessive")
    if len(data) < 27:
        raise _malformed("en-tête trop court")
    if data[4] != 73:
        if data[4] == 80:
            raise IncrementalFrame()
        raise _malformed(f"type de trame non pris en charge {data[4]}")
    width, height, grid = _i16(data, 19), _i16(data, 21), _i16(data, 17)
    if not (0 < width <= 8192 and 0 < height <= 8192 and grid > 0
            and width * height <= 16_777_216 and len(data) >= 27 + width * height):
        raise _malformed("dimensions ou cellules")
    json_start = 27 + width * height
    metadata: dict = {}
    if json_start < len(data):
        tail = data[json_start:].rstrip(b"\x00")
        try:
            metadata = json.loads(tail)
        except ValueError:
            # Les blocs CBC déchiffrés peuvent porter du bourrage après le JSON.
            try:
                metadata, _ = json.JSONDecoder().raw_decode(tail.decode("utf-8", "replace"))
            except ValueError as error:
                raise _malformed("métadonnées JSON") from error
        if not isinstance(metadata, dict):
            raise _malformed("métadonnées JSON")
    if any(k in metadata for k in ("saveMapId", "cover", "diff", "curtain")):
        version = max(3, version)
    if not 0 <= version <= 3:
        raise _malformed("version de carte")
    origin = metadata.get("origin")
    has_origin = isinstance(origin, list) and len(origin) >= 2
    left = int(origin[0]) if has_origin else _i16(data, 23)
    top = int(origin[1]) if has_origin else _i16(data, 25)
    if abs(left) > 100_000_000 or abs(top) > 100_000_000:
        raise _malformed("origine de la carte")
    status = _number(metadata.get("ris"), -1)
    frame_map = _number(metadata.get("fsm")) == 1

    layers: dict[str, list[int]] = {}
    room_cells: dict[int, list[int]] = {}
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = data[27 + y * width: 27 + (y + 1) * width]
        for x, p in enumerate(row):
            if not p:
                continue
            kind = _pixel_type(p, version, status, frame_map)
            if kind <= 0:
                continue
            _add_run(layers, kind, x, y)
            if x < min_x: min_x = x
            if y < min_y: min_y = y
            if x > max_x: max_x = x
            if y > max_y: max_y = y
            if kind < 100:
                cell = room_cells.setdefault(kind, [0, 0, 0])
                cell[0] += x; cell[1] += y; cell[2] += 1
            if version == 3:
                carpet = bool(p & 128)
            elif frame_map or status in (0, 1, 2):
                carpet = p & 3 == 3
            else:
                carpet = bool(p & 64)
            if carpet:
                _add_run(layers, 512, x, y)
    crop = [min_x, height - max_y - 1, width - max_x - 1, min_y] if max_x >= 0 else [0, 0, 0, 0]
    rotation = _number(metadata.get("mra"))
    seg_info = metadata.get("seg_inf") if isinstance(metadata.get("seg_inf"), dict) else {}
    segments = []
    for sid in sorted(room_cells):
        sx, sy, count = room_cells[sid]
        info = seg_info.get(str(sid)) or {}
        segments.append([sid, left + sx * grid // count, top + sy * grid // count,
                         _number(info.get("type")), info.get("name"), _number(info.get("index")), (sid - 1) % 4])

    def pose(offset: int, absent: str):
        x, y, angle = _i16(data, offset), _i16(data, offset + 2), _i16(data, offset + 4)
        if _number(metadata.get(absent)) != 0 or angle == 32767 or (x == 32767 and y == 32767):
            return None
        return [x, y, angle]

    obstacles = _decode_obstacles(metadata.get("ai_obstacle"), version)
    laser = []
    for row in metadata.get("laser_obstacle") or []:
        if isinstance(row, list) and len(row) >= 3:
            x, y = _numeric(row[0]), _numeric(row[1])
            if x is None or y is None:
                continue
            confidence = _numeric(row[3]) if len(row) > 3 else None
            pct = int(confidence * 100) if confidence is not None and 0 <= confidence <= 1 else None
            laser.append([x, y, _number(row[2]), pct])
    thresholds = metadata.get("vws") if isinstance(metadata.get("vws"), dict) else {}
    cliffs = _numeric_lines(thresholds.get("cliff"))
    routes = _path(metadata.get("tr") if isinstance(metadata.get("tr"), str) else "")
    now = int(time.time() * 1000)
    ts = _numeric(metadata.get("timestamp_ms"))
    timestamp = int(ts) if ts is not None and ts >= 0 else None
    wall_count = _cells(layers.get("255", []))
    cell_count = sum(_cells(r) for k, r in layers.items() if k != "512")
    result = {
        "version": version, "map_id": _i16(data, 0), "frame_id": _i16(data, 2), "frame_type": "I",
        "timestamp_ms": timestamp, "rotation": rotation,
        "size": [left, top, left, top, width, height, grid, rotation, crop, None],
        "data": layers, "segments": segments, "path": routes, "robot_position": pose(5, "nr"),
        "charger_position": pose(11, "nc"), "obstacles": obstacles, "laser_obstacles": laser,
        "cliffs": cliffs, "empty_map": not layers, "docked": _number(metadata.get("oc")) != 0,
        "saved_map_status": status,
        "saved_map": status == -1 and not frame_map and metadata.get("iscleanlog") is not True,
        "history_map": metadata.get("iscleanlog") is True,
        "telemetry": {
            "source": "dreame_map", "pose_source": "map_entity",
            "observed_at": timestamp, "received_at": now,
            "freshness_ms": max(0, now - timestamp) if timestamp is not None else None,
            "actual": ["occupancy_grid", "robot_position", "path", "obstacles", "laser_obstacles", "cliffs"],
            "simulated": [],
            "capabilities": {"raw_lidar_rays": False, "raw_radar_frequency": False, "raw_vision_frames": False,
                             "proximity_distances": False,
                             "obstacle_photos": any(o[7] is not None for o in obstacles)},
            "metrics": {"frame_id": _i16(data, 2),
                        "path_points": sum(max(0, (len(g) - 1) // 2) for g in routes),
                        "occupancy_cells": cell_count, "wall_cells": wall_count,
                        "vision_objects": len(obstacles), "laser_obstacles": len(laser),
                        "cliffs": len(cliffs), "grid_mm": grid},
        },
    }
    for source, target in (("cs", "cleaned_area"), ("ct", "cleaning_time"), ("wm", "work_status"),
                           ("cf", "completed"), ("clean_finish_remain_electricity", "remaining_battery")):
        value = metadata.get(source)
        if isinstance(value, (int, float)):
            result[target] = value
    walls = metadata.get("vw") if isinstance(metadata.get("vw"), dict) else {}
    result["virtual_walls"] = _numeric_lines(walls.get("line"))
    result["no_go"] = walls.get("rect") if isinstance(walls.get("rect"), list) else []
    result["no_mop"] = walls.get("mop") if isinstance(walls.get("mop"), list) else []
    nested = metadata.get("rism")
    if isinstance(nested, str) and nested:
        saved_bytes = inflate_envelope(nested)
        saved = decode_inflated(saved_bytes, version, nesting + 1)
        _compose_saved(result, data, metadata, saved_bytes, saved)
    return result


def _encode_cells(cells: list[int], width: int, height: int):
    layers: dict[str, list[int]] = {}
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            kind = cells[y * width + x]
            if kind <= 0:
                continue
            _add_run(layers, kind, x, y)
            min_x, max_x, min_y, max_y = min(x, min_x), max(x, max_x), min(y, min_y), max(y, max_y)
    crop = [min_x, height - max_y - 1, width - max_x - 1, min_y] if max_x >= 0 else [0, 0, 0, 0]
    return layers, crop


def _compose_saved(result: dict, current: bytes, metadata: dict, saved_bytes: bytes, saved: dict):
    current_size, saved_size = result["size"], saved["size"]
    status = _number(metadata.get("ris"), -1)
    fsm = _number(metadata.get("fsm")) == 1
    empty = result.get("empty_map", True)
    restored = _number(metadata.get("rpur")) == 1 or (empty and status == 2 and not fsm)
    recovery = _number(metadata.get("us")) == 1
    segments = result.get("segments", [])
    saved_segments = saved.get("segments", [])
    use_saved = restored or recovery or (status == 2 and (empty or (not fsm and _number(result.get("version")) != 0)))
    if use_saved:
        segments = saved_segments
        if status == 2 and not fsm:
            cl, ct, cw, ch, grid = (_number(current_size[i]) for i in (0, 1, 4, 5, 6))
            sl, st, sw, sh = (_number(saved_size[i]) for i in (0, 1, 4, 5))
            if grid <= 0 or grid != _number(saved_size[6]):
                raise _malformed("pas de grille de la carte enregistrée")
            left, top = min(cl, sl), min(ct, st)
            width = (max(cl + cw * grid, sl + sw * grid) - left) // grid
            height = (max(ct + ch * grid, st + sh * grid) - top) // grid
            if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_777_216
                    and len(current) >= 27 + cw * ch and len(saved_bytes) >= 27 + sw * sh):
                raise _malformed("limites de la carte fusionnée")
            cells = [0] * (width * height)
            si, sj, ni, nj = (sl - left) // grid, (st - top) // grid, (cl - left) // grid, (ct - top) // grid
            for y in range(height):
                for x in range(width):
                    sv, segment = None, 0
                    if si <= x < si + sw and sj <= y < sj + sh:
                        sv = saved_bytes[27 + (y - sj) * sw + x - si]
                        segment = sv & 63
                    clean = 0
                    if not restored and ni <= x < ni + cw and nj <= y < nj + ch:
                        clean = current[27 + (y - nj) * cw + x - ni] & 3
                    value = 0
                    if segment != 0 or empty:
                        if restored and sv is not None:
                            value = 255 if sv >> 7 == 1 else 253 if sv == 63 else segment
                        else:
                            value = 255 if clean == 2 else segment
                    elif clean != 0:
                        value = 254 if clean == 1 else 255 if clean == 2 else 253
                    cells[y * width + x] = value
            layers, crop = _encode_cells(cells, width, height)
            if restored and "512" in saved.get("data", {}):
                shifted = list(saved["data"]["512"])
                for i in range(0, len(shifted), 3):
                    shifted[i] += si
                    shifted[i + 1] += sj
                layers["512"] = shifted
            elif "512" in result.get("data", {}):
                layers["512"] = result["data"]["512"]
            result["data"] = layers
            result["size"] = [left, top, left, top, width, height, grid, _number(result.get("rotation")), crop, None]
            result["empty_map"] = False
        else:
            result["data"], result["size"], result["empty_map"] = saved["data"], saved["size"], saved["empty_map"]
    else:
        for reference in saved_segments:
            if len(reference) < 7:
                continue
            index = next((i for i, s in enumerate(segments) if _number(s[0]) == _number(reference[0])), None)
            if index is not None:
                if status == 2:
                    segments[index][1], segments[index][2] = reference[1], reference[2]
                for f in range(3, min(len(segments[index]), len(reference))):
                    segments[index][f] = reference[f]
            elif status == 2:
                segments.append(reference)
    result["segments"] = segments
    result["saved_map_id"] = saved.get("map_id")
    if result.get("charger_position") is None and (status == 2 or _number(metadata.get("oc")) != 0) and not recovery:
        result["charger_position"] = saved.get("charger_position")
    for key in ("virtual_walls", "no_go", "no_mop"):
        if not result.get(key) and key in saved:
            result[key] = saved[key]
    metrics = result["telemetry"]["metrics"]
    layers = result["data"]
    metrics["occupancy_cells"] = sum(_cells(r) for k, r in layers.items() if k != "512")
    metrics["wall_cells"] = _cells(layers.get("255", []))


def _i16_bytes(n: int) -> bytes:
    return struct.pack("<H", n & 0xFFFF)


class MapStream:
    """Accumulateur de trames complètes (I) et incrémentales (P). N'invente jamais de cellules."""

    def __init__(self):
        self.base: bytes | None = None
        self.version = 1

    def reset(self):
        self.base = None

    def accept(self, raw: str, version: int = 1, iv: str | None = None, key: str | None = None) -> dict:
        return self.accept_inflated(inflate_envelope(raw, iv, key), version)

    def accept_inflated(self, incoming: bytes, version: int = 1) -> dict:
        p = incoming
        if len(p) < 27:
            raise _malformed("en-tête incrémental")
        if p[4] == 73:
            result = decode_inflated(p, version)
            self.base, self.version = p, result.get("version", version)
            return result
        if p[4] != 80 or self.base is None:
            raise IncrementalFrame()
        b = self.base
        if _i16(b, 0) != _i16(p, 0) or _u16(p, 2) != (_u16(b, 2) + 1) & 0xFFFF:
            raise IncrementalFrame()
        bw, bh, pw, ph, grid = _i16(b, 19), _i16(b, 21), _i16(p, 19), _i16(p, 21), _i16(b, 17)
        if not (grid > 0 and _i16(p, 17) == grid and 0 <= pw <= 8192 and 0 <= ph <= 8192
                and pw * ph <= 16_777_216 and len(p) >= 27 + pw * ph):
            raise _malformed("dimensions incrémentales")

        def meta(data: bytes, w: int, h: int) -> dict:
            start = 27 + w * h
            if start > len(data):
                raise _malformed("métadonnées incrémentales")
            if start == len(data):
                return {}
            try:
                value = json.loads(data[start:])
            except ValueError as error:
                raise _malformed("métadonnées incrémentales") from error
            if not isinstance(value, dict):
                raise _malformed("métadonnées incrémentales")
            return value

        old, delta = meta(b, bw, bh), meta(p, pw, ph)
        if not (self.version == 3 or old.get("fsm") == 1):
            raise IncrementalFrame()
        changes = delta.get("diff") if isinstance(delta.get("diff"), dict) else {}
        if self.version == 3 and isinstance(delta.get("cover"), dict):
            for k in ("timestamp_ms", "tr", "small_tr", "moptype", "laser_obstacle"):
                if k in delta["cover"]:
                    delta[k] = delta["cover"][k]

        def origin(m, d):
            o = m.get("origin")
            return (o[0], o[1]) if isinstance(o, list) and len(o) > 1 else (_i16(d, 23), _i16(d, 25))

        bl, bt = origin(old, b)
        pl, pt = origin(delta, p)
        left, top = min(bl, pl), min(bt, pt)
        if any((v - ref) % grid for v, ref in ((bl, left), (pl, left), (bt, top), (pt, top))):
            raise IncrementalFrame()
        w = (max(bl + bw * grid, pl + pw * grid) - left) // grid
        h = (max(bt + bh * grid, pt + ph * grid) - top) // grid
        if not (0 < w <= 8192 and 0 < h <= 8192 and w * h <= 16_777_216
                and -32768 <= left <= 32767 and -32768 <= top <= 32767):
            raise _malformed("dimensions fusionnées")
        cells = bytearray(w * h)
        bx, by, px, py = (bl - left) // grid, (bt - top) // grid, (pl - left) // grid, (pt - top) // grid
        for y in range(bh):
            start = (y + by) * w + bx
            cells[start:start + bw] = b[27 + y * bw: 27 + (y + 1) * bw]
        for y in range(ph):
            for x in range(pw):
                value = p[27 + y * pw + x]
                if value:
                    at = (y + py) * w + x + px
                    cells[at] = value if self.version == 3 else (cells[at] + value) & 0xFF
        route = delta.get("tr")
        if isinstance(route, str) and route:
            # Un L relatif en tête n'a pas d'ancre locale fiable.
            if route[0] == "L":
                raise IncrementalFrame()
            old["tr"] = (old.get("tr") or "") + route
        if self.version == 3:
            incoming_obs = changes.get("ai_obstacle", delta.get("ai_obstacle"))
            obstacles = old.get("ai_obstacle") if isinstance(old.get("ai_obstacle"), list) else []

            def identity(row):
                return _describe(row[12]) if isinstance(row, list) and len(row) >= 13 else "null"

            if isinstance(incoming_obs, list):
                for obj in incoming_obs:
                    idx = next((i for i, o in enumerate(obstacles) if identity(o) == identity(obj)), None)
                    if idx is None:
                        obstacles.append(obj)
                    else:
                        obstacles[idx] = obj
            deleted = (changes.get("deleteContent") or {}).get("ai_obstacle") if isinstance(changes.get("deleteContent"), dict) else None
            if isinstance(deleted, list):
                ids = {_describe(d) for d in deleted}
                obstacles = [o for o in obstacles if identity(o) not in ids]
            old["ai_obstacle"] = obstacles
        for k, v in delta.items():
            if k in ("tr", "cover", "diff") or (self.version == 3 and k == "ai_obstacle"):
                continue
            old[k] = v
        old["origin"] = [left, top]
        old["fsm"] = 1
        # Les détections laser sont des observations ponctuelles, pas des enregistrements permanents.
        old["laser_obstacle"] = delta.get("laser_obstacle") or []
        header = bytearray(p[:27])
        header[4] = 73
        for at, n in ((19, w), (21, h), (23, left), (25, top)):
            header[at:at + 2] = _i16_bytes(n)
        merged = bytes(header) + bytes(cells) + json.dumps(old, separators=(",", ":")).encode()
        result = decode_inflated(merged, self.version)
        result["frame_type"] = "P"
        result["incremental_verified_chain"] = True
        self.base = merged
        return result


def frame_header(inflated: bytes):
    """(map_id, frame_id, largeur, hauteur) d'une trame décompressée."""
    return _i16(inflated, 0), _i16(inflated, 2), _u16(inflated, 19), _u16(inflated, 21)
