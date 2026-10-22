"""Traduit l'index.html amont (turc) en français : python3 tools/i18n/apply.py SRC DST"""
import json, re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding='utf-8').read()
d = json.load(open(__file__.rsplit('/', 1)[0] + '/fr.json', encoding='utf-8'))
missing = []
for tr, fr in sorted(d.items(), key=lambda kv: -len(kv[0])):
    if '<h3>' not in fr:
        fr = fr.replace("'", "’")
    pat = re.compile(r"([>'\"`]\s*)" + re.escape(tr) + r"(\s*[<'\"`])")
    s, n = pat.subn(lambda m: m.group(1) + fr + m.group(2), s)
    if n == 0: missing.append(tr)
s = s.replace('<html lang="tr"', '<html lang="fr"')
s = s.replace('aria-label="Tercihleri kapat"', 'aria-label="Fermer les préférences"')
s = s.replace('<script src="/assets/robot-motion.js"></script>', '<script src="/assets/robot-motion.js"></script>\n<script src="/assets/select-popup.js"></script>')
s = s.replace("toLocaleLowerCase('tr')", "toLocaleLowerCase('fr')").replace("'tr-TR'", "'fr-FR'")
# Langues des packs vocaux : catalogue officiel du modèle (assets/voice-packs.js).
for old, new in (
    ('<option value="TR">Turc</option><option value="EN">English</option>', '<option value="">Chargement…</option>'),
    ("(lang==='TR'?'Turc':'Anglais')", "$('voiceLanguage').selectedOptions[0].textContent"),
    ("pack.id==='TR'?'Turc':pack.id==='EN'?'English':pack.name", "window.duiLanguageName(pack)"),
    # Éditeur d'annonces : indiquer la règle de pare-feu (ufw) quand le robot ne télécharge rien.
    ("vérifie l’isolation entre appareils sur le réseau.'}", "vérifie l’isolation entre appareils sur le réseau.'+(transfer.firewall?' Pare-feu actif : exécute « '+transfer.firewall+' » dans un terminal.':'')}"),
    ("showToast(result.changed_count+' modification(s) : transfert lancé');", "showToast(result.firewall?'Pare-feu actif : exécute « '+result.firewall+' »':result.changed_count+' modification(s) : transfert lancé');"),
    ('</body>', '<script src="/assets/voice-packs.js"></script>\n<script src="/assets/extras.js"></script>\n</body>'),
):
    if s.count(old) != 1:
        raise SystemExit(f'motif introuvable ou ambigu : {old}')
    s = s.replace(old, new)
open(dst, 'w', encoding='utf-8').write(s)
print('not found:', missing)
