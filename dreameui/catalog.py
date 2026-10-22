"""Identité d'affichage des modèles. L'appartenance au catalogue n'active aucune capacité."""
from __future__ import annotations

from pathlib import Path

RESOURCES = Path(__file__).parent / "resources"

DREAME_NAMES = {
    "r2338a": "L10s Pro Ultra Heat", "r2377": "L10s Pro Ultra Heat", "r2338h": "L10s Pro Ultra Heat",
    "r9309a": "L10s Pro Ultra", "r2469a": "L10s Ultra Gen 2", "r2364": "L10s Pro Gen 2",
    "r2253b": "L20 Ultra", "r2492a": "L40 Ultra", "r501t": "L40 Ultra Gen 2", "r2551a": "L40s Ultra",
    "r2579c": "L40s Ultra AE", "r2562c": "L40s Ultra CE", "r9493a": "L50 Ultra", "r501da": "L60 Pro Ultra",
    "r5090a": "L60 Ultra", "r50397": "L60 Ultra PE", "r2416a": "X40 Ultra", "r2489a": "X50 Ultra",
    "r5089b": "X60 Ultra", "r2513": "Matrix10 Ultra", "r5082a": "Matrix10 Pro", "r9535a": "Aqua10 Ultra Roller",
    "r501ba": "Aqua10 Roller AE", "r9533a": "Aqua10 Roller", "r2422": "D9 Max Gen 2", "r531ra": "D20 Air Plus",
    "r2564b": "D20 Plus", "r2566a": "D20 Pro Plus", "r50573": "D30 Ultra", "r500yb": "D30 Ultra CE",
}

# (nom, image, identifiants exacts) — pas de correspondance par préfixe.
MOVA = [
    ("E20 Plus", "e20-plus", "dreame.vacuum.r2459a dreame.vacuum.r2459h dreame.vacuum.r2459k"),
    ("E30 Ultra", "e30-ultra", "dreame.vacuum.r2427 dreame.vacuum.r2427a"),
    ("E40 Ultra", "e40-ultra", "mova.vacuum.r5732a mova.vacuum.r9504a"),
    ("E50 Pro Ultra", "e50-pro-ultra", "mova.vacuum.r6710a mova.vacuum.r6710g mova.vacuum.r6710h mova.vacuum.r6710t"),
    ("E50 Ultra", "e50-ultra", "mova.vacuum.r67095 mova.vacuum.r6709a mova.vacuum.r6709g mova.vacuum.r6709h"),
    ("M1", "m1", "dreame.vacuum.r2380"),
    ("MOBIUS 60", "mobius-60", "mova.vacuum.r2599h mova.vacuum.r2599k mova.vacuum.r2599u"),
    ("P50 Pro Ultra", "p50-pro-ultra", "mova.vacuum.r2475a mova.vacuum.r2475h mova.vacuum.r2475t mova.vacuum.r2587a mova.vacuum.r9416d"),
    ("P50 Ultra", "p50-ultra", "mova.vacuum.r2519a"),
    ("P70 Pro Ultra", "p70-pro-ultra", "mova.vacuum.r5770 mova.vacuum.r590q mova.vacuum.r5977a mova.vacuum.r5977c mova.vacuum.r5977f mova.vacuum.r5977g mova.vacuum.r5977h mova.vacuum.r5977k mova.vacuum.r5977t"),
    ("S10 Plus", "s10-plus", "dreame.vacuum.r2383a dreame.vacuum.r2383k"),
    ("S70 Roller", "s70-roller", "mova.vacuum.r5769a mova.vacuum.r5769f mova.vacuum.r5769g mova.vacuum.r5769h mova.vacuum.r5769q mova.vacuum.r5769t"),
    ("S70 Ultra Roller", "s70-ultra-roller", "mova.vacuum.r5770a mova.vacuum.r5770g mova.vacuum.r5770h mova.vacuum.r5770q mova.vacuum.r5770t mova.vacuum.r590qf"),
    ("V50 Ultra", "v50-ultra", "mova.vacuum.r2525a mova.vacuum.r2525e mova.vacuum.r2525h"),
    ("V50 Ultra Complete", "v50-ultra", "mova.vacuum.r2582a mova.vacuum.r2582h mova.vacuum.r2582c mova.vacuum.r2582k"),
    ("V70 Ultra Complete", "v70-ultra-complete", "mova.vacuum.r5979a mova.vacuum.r5979f mova.vacuum.r5979g mova.vacuum.r5979h mova.vacuum.r5979t"),
    ("V70 Ultra Complete X", "v70-ultra-complete-x", "mova.vacuum.r59795 mova.vacuum.r59796 mova.vacuum.r59797 mova.vacuum.r59799 mova.vacuum.r5979u"),
    ("V70 Ultra Pure", "v70-ultra-pure", "mova.vacuum.r5978a mova.vacuum.r5978h mova.vacuum.r5978q"),
    ("Z50 Ultra", "z50-ultra", "mova.vacuum.r2430a mova.vacuum.r2430u"),
    ("Z60 Ultra Roller Complete", "z60-ultra-roller-complete", "mova.vacuum.r9540a mova.vacuum.r9540h mova.vacuum.r9540k mova.vacuum.r9540n mova.vacuum.r9540u"),
    ("Z60 Ultra Roller Standalone", "z60-ultra-roller-standalone", "mova.vacuum.r9544a"),
    ("Z70 Ultra Roller Complete", "z70-ultra-roller-complete", "mova.vacuum.r5765h mova.vacuum.r5765k mova.vacuum.r5765p mova.vacuum.r5765q mova.vacuum.r5765u mova.vacuum.r5766h mova.vacuum.r5766k mova.vacuum.r5766q"),
]


def _mova(model: str):
    return next((entry for entry in MOVA if model in entry[2].split()), None)


def product_name(model: str) -> str | None:
    entry = _mova(model)
    if entry:
        return "MOVA " + entry[0]
    if model.startswith("dreame.vacuum."):
        name = DREAME_NAMES.get(model.rsplit(".", 1)[-1])
        return f"Dreame {name}" if name else None
    return None


def product_image(model: str) -> str:
    entry = _mova(model)
    if entry:
        path = f"/assets/products/mova/{entry[1]}.png"
        if (RESOURCES / path.lstrip("/")).exists():
            return path
    suffix = model.rsplit(".", 1)[-1]
    for ext in ("png", "webp", "svg"):
        custom = f"/assets/products/dreame/{suffix}.{ext}"
        if (RESOURCES / custom.lstrip("/")).exists():
            return custom
    return "/assets/products/generic.svg"
