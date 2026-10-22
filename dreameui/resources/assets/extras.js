/* Consommables détaillés, onglet Réglages et statistiques.
   Lit les réponses /api/state déjà demandées par la page ; n'affiche que ce que l'appareil a signalé. */
(function () {
  const $ = id => document.getElementById(id);

  const style = document.createElement('style');
  style.textContent = `
    .consumable-status { display:none !important; }
    body[data-active-tab="robot"] .topline { display:none; }
    .dui-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:10px; margin-top:12px; }
    .dui-card { padding:12px 14px; border:1px solid var(--hub-line,rgba(255,255,255,.12)); border-radius:13px;
      background:var(--hub-control,rgba(255,255,255,.05)); display:grid; gap:8px; }
    .dui-card-head { display:flex; justify-content:space-between; align-items:baseline; gap:8px; font-weight:650; }
    .dui-card-head span { font-variant-numeric:tabular-nums; }
    .dui-card small { color:var(--secondary,#a8afb8); }
    .dui-bar { height:6px; border-radius:99px; background:rgba(127,127,127,.22); overflow:hidden; }
    .dui-bar i { display:block; height:100%; border-radius:inherit; background:var(--accent,#5b9dff); }
    .dui-card.warn .dui-bar i { background:var(--warning,#f3b85b); }
    .dui-card.bad .dui-bar i { background:var(--danger,#ff8378); }
    .dui-card.bad .dui-card-head span { color:var(--danger,#ff8378); }
    .dui-card .action { justify-self:start; padding:5px 10px; font-size:12px; }
    .dui-section { margin-top:16px; }
    .dui-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:12px; margin-top:12px; }
    .dui-grid label.control { margin:0; }
    .dui-range { display:flex; align-items:center; gap:10px; margin-top:9px; }
    .dui-range input { flex:1; }
    .dui-range output { min-width:48px; text-align:right; font-variant-numeric:tabular-nums; font-weight:650; }
    .dui-dnd { display:flex; flex-wrap:wrap; align-items:end; gap:12px; margin-top:12px; }
    .dui-dnd label { display:grid; gap:6px; font-size:13px; }
    .dui-dnd input[type=time] { font:inherit; padding:8px 10px; border-radius:10px; border:1px solid var(--hub-line,rgba(255,255,255,.14));
      background:var(--hub-control,rgba(255,255,255,.06)); color:inherit; color-scheme:dark; }
    html[data-theme="light"] .dui-dnd input[type=time] { color-scheme:light; }
    .dui-dnd .dui-switch { display:flex; align-items:center; gap:8px; }
    .dui-stats { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:10px; margin-top:12px; }
    .dui-stats div { padding:12px 14px; border-radius:13px; border:1px solid var(--hub-line,rgba(255,255,255,.12));
      background:var(--hub-control,rgba(255,255,255,.05)); }
    .dui-stats strong { display:block; font-size:20px; font-variant-numeric:tabular-nums; }
    .dui-stats span { color:var(--secondary,#a8afb8); font-size:12px; }
    .dui-empty { color:var(--secondary,#a8afb8); margin-top:10px; }`;
  document.head.append(style);

  const toast = message => typeof showToast === 'function' && showToast(message);
  const confirmIt = message => typeof confirmAction === 'function' ? confirmAction(message) : Promise.resolve(window.confirm(message));
  const reload = () => typeof refresh === 'function' && setTimeout(refresh, 1200);

  async function post(path, body) {
    if (typeof api === 'function') return api(path, body);
    throw new Error('Interface non initialisée.');
  }

  function head(kicker, title) {
    const box = document.createElement('div'); box.className = 'compact-section-head';
    const left = document.createElement('div');
    const k = document.createElement('span'); k.className = 'kicker'; k.textContent = kicker;
    const t = document.createElement('strong'); t.textContent = title;
    left.append(k, t); box.append(left); return box;
  }

  // -- Consommables (onglet Entretien) -------------------------------------
  const consumableSection = document.createElement('section');
  consumableSection.className = 'dock-health dui-section'; consumableSection.hidden = true;
  const consumableCards = document.createElement('div'); consumableCards.className = 'dui-cards';
  consumableSection.append(head('Consommables', 'Durée de vie restante'), consumableCards);
  $('dockHealthSection')?.after(consumableSection);

  function renderConsumables(items) {
    consumableSection.hidden = !items.length;
    consumableCards.replaceChildren(...items.map(item => {
      const card = document.createElement('div'); card.className = 'dui-card';
      const pct = Number.isFinite(item.percent) ? Math.max(0, Math.min(100, item.percent)) : null;
      if (pct !== null && pct <= 10) card.classList.add('bad'); else if (pct !== null && pct <= 25) card.classList.add('warn');
      const top = document.createElement('div'); top.className = 'dui-card-head';
      const name = document.createElement('div'); name.textContent = item.label;
      const value = document.createElement('span'); value.textContent = pct === null ? '—' : pct + ' %';
      top.append(name, value);
      const bar = document.createElement('div'); bar.className = 'dui-bar';
      const fill = document.createElement('i'); fill.style.width = (pct ?? 0) + '%'; bar.append(fill);
      const info = document.createElement('small');
      info.textContent = Number.isFinite(item.hours) ? item.hours + ' h restantes' : 'Durée non signalée';
      const reset = document.createElement('button'); reset.className = 'action'; reset.textContent = 'Remettre à zéro';
      reset.onclick = async () => {
        if (!await confirmIt(item.label + ' : remettre le compteur à zéro ? À faire après remplacement ou nettoyage.')) return;
        reset.disabled = true;
        try { await post('/api/consumable/reset', { key: item.key }); toast('Compteur remis à zéro'); reload(); }
        catch (e) { toast(e.message); } finally { reset.disabled = false; }
      };
      card.append(top, bar, info, reset);
      return card;
    }));
  }

  // -- Onglet Réglages -------------------------------------------------------
  const nav = document.querySelector('.tabs');
  const stationButton = document.querySelector('.tab-button[data-tab="station"]');
  const tabButton = document.createElement('button');
  tabButton.className = 'tab-button'; tabButton.dataset.tab = 'robot'; tabButton.setAttribute('aria-label', 'Réglages');
  tabButton.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 7h10m4 0h2M4 17h4m4 0h8M14 5v4M8 15v4"/></svg><span>Réglages</span>';
  tabButton.addEventListener('click', () => typeof activateTab === 'function' && activateTab('robot'));
  if (stationButton) stationButton.after(tabButton); else nav?.append(tabButton);

  const panel = document.createElement('div'); panel.className = 'tab-panel'; panel.id = 'tab-robot'; panel.hidden = true;
  const groups = { robot: 'Robot', water: 'Lavage', station: 'Station' };
  const groupGrids = {};
  for (const [key, title] of Object.entries(groups)) {
    const section = document.createElement('section'); section.className = 'dock-health dui-section'; section.hidden = true;
    const grid = document.createElement('div'); grid.className = 'dui-grid';
    section.append(head('Réglages', title), grid); panel.append(section);
    groupGrids[key] = { section, grid, controls: {} };
  }
  const dndSection = document.createElement('section'); dndSection.className = 'dock-health dui-section'; dndSection.hidden = true;
  const dndBox = document.createElement('div'); dndBox.className = 'dui-dnd';
  dndSection.append(head('Réglages', 'Ne pas déranger'), dndBox); panel.append(dndSection);
  const statsSection = document.createElement('section'); statsSection.className = 'dock-health dui-section'; statsSection.hidden = true;
  const statsBox = document.createElement('div'); statsBox.className = 'dui-stats';
  statsSection.append(head('Historique', 'Statistiques depuis la mise en service'), statsBox); panel.append(statsSection);
  const empty = document.createElement('p'); empty.className = 'dui-empty'; empty.textContent = 'Réglages en attente de l’appareil…';
  panel.prepend(empty);
  const lastPanel = [...document.querySelectorAll('.tab-panel')].pop();
  (lastPanel?.parentElement || document.querySelector('main') || document.body).append(panel);

  const busy = new Set();
  async function sendSetting(name, value, control) {
    busy.add(name); control.disabled = true;
    try { await post('/api/extra-setting', { name, value }); toast('Réglage mis à jour'); reload(); }
    catch (e) { toast(e.message); }
    finally { control.disabled = false; setTimeout(() => busy.delete(name), 2500); }
  }

  function buildControl(name, spec) {
    const label = document.createElement('label'); label.className = 'control';
    label.append(document.createTextNode(spec.label));
    if (spec.options) {
      const select = document.createElement('select');
      select.addEventListener('change', () => sendSetting(name, Number(select.value), select));
      label.append(select);
      return { label, update(s) {
        const key = s.options.map(o => o.value + o.label).join();
        if (select.dataset.key !== key) { select.replaceChildren(...s.options.map(o => new Option(o.label, o.value))); select.dataset.key = key; }
        select.value = String(s.value);
      } };
    }
    const wrap = document.createElement('div'); wrap.className = 'dui-range';
    const input = document.createElement('input'); input.type = 'range';
    const out = document.createElement('output');
    input.addEventListener('input', () => { out.textContent = input.value + (spec.range.unit || ''); });
    input.addEventListener('change', () => sendSetting(name, Number(input.value), input));
    wrap.append(input, out); label.append(wrap);
    return { label, update(s) {
      Object.assign(input, { min: s.range.min, max: s.range.max, step: s.range.step });
      if (document.activeElement !== input) { input.value = s.value; out.textContent = s.value + (s.range.unit || ''); }
    } };
  }

  function renderSettings(settings) {
    for (const group of Object.values(groupGrids)) group.section.hidden = true;
    for (const [name, spec] of Object.entries(settings)) {
      const group = groupGrids[spec.group]; if (!group) continue;
      group.section.hidden = false;
      if (!group.controls[name]) { group.controls[name] = buildControl(name, spec); group.grid.append(group.controls[name].label); }
      if (!busy.has(name)) group.controls[name].update(spec);
    }
  }

  let dndControls = null;
  function renderDnd(dnd) {
    dndSection.hidden = !dnd;
    if (!dnd) return;
    if (!dndControls) {
      const toggle = document.createElement('label'); toggle.className = 'dui-switch';
      const enabled = document.createElement('input'); enabled.type = 'checkbox';
      toggle.append(enabled, document.createTextNode('Activé'));
      const startLabel = document.createElement('label'); startLabel.textContent = 'Début';
      const start = document.createElement('input'); start.type = 'time'; startLabel.append(start);
      const endLabel = document.createElement('label'); endLabel.textContent = 'Fin';
      const end = document.createElement('input'); end.type = 'time'; endLabel.append(end);
      const save = document.createElement('button'); save.className = 'action main'; save.textContent = 'Enregistrer';
      save.onclick = async () => {
        busy.add('dnd'); save.disabled = true;
        try { await post('/api/extra-setting', { name: 'dnd', enabled: enabled.checked, start: start.value, end: end.value }); toast('Ne pas déranger mis à jour'); reload(); }
        catch (e) { toast(e.message); } finally { save.disabled = false; setTimeout(() => busy.delete('dnd'), 2500); }
      };
      dndBox.append(toggle, startLabel, endLabel, save);
      dndControls = { enabled, start, end };
    }
    if (busy.has('dnd') || dndBox.contains(document.activeElement)) return;
    dndControls.enabled.checked = !!dnd.enabled;
    dndControls.start.value = dnd.start || '22:00';
    dndControls.end.value = dnd.end || '08:00';
  }

  function renderStats(stats) {
    const items = [];
    const fmt = n => n.toLocaleString('fr-FR');
    if (Number.isFinite(stats.count)) items.push([fmt(stats.count), 'nettoyages']);
    if (Number.isFinite(stats.total_area)) items.push([fmt(stats.total_area) + ' m²', 'surface nettoyée']);
    if (Number.isFinite(stats.total_minutes)) items.push([fmt(Math.round(stats.total_minutes / 60)) + ' h', 'de nettoyage']);
    if (Number.isFinite(stats.count) && stats.count > 0 && Number.isFinite(stats.total_area))
      items.push([fmt(Math.round(stats.total_area / stats.count)) + ' m²', 'en moyenne par nettoyage']);
    if (Number.isFinite(stats.first_cleaning) && stats.first_cleaning > 1e9)
      items.push([new Date(stats.first_cleaning * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }), 'premier nettoyage']);
    statsSection.hidden = !items.length;
    statsBox.replaceChildren(...items.map(([value, label]) => {
      const box = document.createElement('div'); const strong = document.createElement('strong'); const span = document.createElement('span');
      strong.textContent = value; span.textContent = label; box.append(strong, span); return box;
    }));
  }

  function render(state) {
    const extras = state?.device?.extras;
    if (!extras) return;
    renderConsumables(extras.consumables || []);
    renderSettings(extras.settings || {});
    renderDnd(extras.dnd);
    renderStats(extras.stats || {});
    empty.hidden = Object.keys(extras.settings || {}).length > 0 || !!extras.dnd;
  }

  // Observe les réponses /api/state sans modifier le code de la page.
  const pageFetch = window.fetch;
  window.fetch = async function (input, options) {
    const response = await pageFetch.apply(this, arguments);
    try {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      if (url.pathname === '/api/state' && response.ok) response.clone().json().then(render).catch(() => {});
    } catch (_) { /* jamais bloquant */ }
    return response;
  };
  // La première requête d'état de la page est partie avant ce script : on en refait une.
  pageFetch('/api/state').then(r => r.ok ? r.json() : null).then(render).catch(() => {});
})();
