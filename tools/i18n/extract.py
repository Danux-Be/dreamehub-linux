import re, json, sys
s = open(sys.argv[1], encoding='utf-8').read()
script_start = s.find('<script>', s.find('robot-motion.js'))
html, js = s[:script_start], s[script_start:]
cands = []
# HTML: drop <style>
html_nostyle = re.sub(r'<style>.*?</style>', '', html, flags=re.S)
for m in re.finditer(r'>([^<>]+)<', html_nostyle):
    t = m.group(1).strip()
    if re.search(r'[A-Za-zÇĞİÖŞÜçğıöşü]', t): cands.append(t)
for m in re.finditer(r'(?:aria-label|title|placeholder|alt|content)="([^"]+)"', html_nostyle):
    cands.append(m.group(1))
# JS literals
for m in re.finditer(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`", js):
    t = next(g for g in m.groups() if g is not None)
    if re.search(r'[ÇĞİÖŞÜçğıöşü]', t) or (re.search(r'[a-z]{3,} [a-z]', t, re.I) and not re.search(r'[{};=#]|^\.|^[\w-]+$', t)):
        cands.append(t)
seen = []
for c in cands:
    if c not in seen: seen.append(c)
json.dump(seen, open('strings.json','w'), ensure_ascii=False, indent=0)
print(len(seen))
