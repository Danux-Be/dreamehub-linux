/* Remplace le popup natif des <select> (menu GTK blanc sous WebKitGTK) par une liste stylée.
   Le <select> reste la source de vérité : on change sa valeur puis on émet « change ». */
(function () {
  const style = document.createElement('style');
  style.textContent = `
    .dui-popup { position:fixed; inset:auto; margin:0; z-index:1000; max-height:min(320px,60vh); overflow:auto; padding:5px;
      border:1px solid var(--hub-line,rgba(255,255,255,.14)); border-radius:12px;
      background:var(--paper,#202631); color:var(--text,#edf2f8);
      box-shadow:0 18px 44px rgba(0,0,0,.42),inset 0 1px rgba(255,255,255,.06);
      font:inherit; font-size:13px; animation:dui-in .12s ease-out; }
    html[data-theme="liquid"] .dui-popup { background:rgba(40,50,66,.86); backdrop-filter:blur(24px) saturate(150%);
      -webkit-backdrop-filter:blur(24px) saturate(150%); }
    html[data-theme="light"] .dui-popup { background:#fff; color:#17211d; border-color:#dfe5e1;
      box-shadow:0 18px 44px rgba(21,49,38,.18); }
    .dui-option { display:flex; align-items:center; gap:8px; padding:8px 10px; border-radius:8px; cursor:default;
      white-space:nowrap; }
    .dui-option::before { content:''; width:14px; flex:none; text-align:center; }
    .dui-option[aria-selected="true"]::before { content:'✓'; color:var(--accent,#5b9dff); font-weight:700; }
    .dui-option.active { background:var(--accent-soft,rgba(91,157,255,.16)); }
    .dui-option[aria-disabled="true"] { opacity:.42; }
    .dui-group { padding:8px 10px 4px; font-size:11px; font-weight:700; opacity:.6; text-transform:uppercase; }
    @keyframes dui-in { from { transform:translateY(-4px); } }
    @media (prefers-reduced-motion:reduce) { .dui-popup { animation:none; } }`;
  document.head.append(style);

  let popup = null, owner = null, items = [], active = -1;

  function close() {
    popup?.remove(); popup = null; items = []; active = -1;
    if (owner) { owner.focus({ preventScroll: true }); owner = null; }
  }

  function choose(option) {
    const select = owner;
    if (!select || option.disabled) return;
    const changed = select.value !== option.value;
    select.value = option.value;
    close();
    if (changed) {
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function setActive(index) {
    items[active]?.el.classList.remove('active');
    active = index;
    const item = items[active];
    if (item) { item.el.classList.add('active'); item.el.scrollIntoView({ block: 'nearest' }); }
  }

  function move(step) {
    if (!items.length) return;
    let i = active;
    for (let n = 0; n < items.length; n++) {
      i = (i + step + items.length) % items.length;
      if (!items[i].option.disabled) return setActive(i);
    }
  }

  function open(select) {
    close();
    owner = select;
    popup = document.createElement('div');
    popup.className = 'dui-popup';
    popup.setAttribute('role', 'listbox');
    let group = null;
    for (const option of select.options) {
      if (option.hidden) continue;
      const parent = option.parentElement;
      if (parent.tagName === 'OPTGROUP' && parent !== group) {
        group = parent;
        const label = document.createElement('div');
        label.className = 'dui-group'; label.textContent = parent.label;
        popup.append(label);
      }
      const el = document.createElement('div');
      el.className = 'dui-option'; el.setAttribute('role', 'option');
      el.textContent = option.textContent;
      el.setAttribute('aria-selected', String(option.selected));
      if (option.disabled || parent.disabled) el.setAttribute('aria-disabled', 'true');
      const index = items.length;
      el.addEventListener('pointerenter', () => setActive(index));
      el.addEventListener('click', () => choose(option));
      items.push({ el, option });
      popup.append(el);
    }
    // Dans un <dialog> modal, la liste doit vivre dans le dialogue pour rester interactive ;
    // le mode popover la place dans la couche supérieure, au-dessus de tout.
    (select.closest('dialog[open]') || document.body).append(popup);
    if (popup.showPopover) { popup.popover = 'manual'; popup.showPopover(); }
    const rect = select.getBoundingClientRect();
    popup.style.minWidth = rect.width + 'px';
    const height = popup.offsetHeight, width = popup.offsetWidth;
    const below = window.innerHeight - rect.bottom - 8;
    popup.style.top = (below >= height || below >= rect.top ? rect.bottom + 4 : rect.top - height - 4) + 'px';
    popup.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + 'px';
    setActive(Math.max(0, items.findIndex(item => item.option.selected)));
  }

  document.addEventListener('mousedown', event => {
    const select = event.target.closest?.('select');
    if (select && !select.multiple && select.size <= 1 && event.button === 0) {
      event.preventDefault();
      if (owner === select) close(); else if (!select.disabled) { select.focus({ preventScroll: true }); open(select); }
      return;
    }
    if (popup && !popup.contains(event.target)) close();
  }, true);

  document.addEventListener('keydown', event => {
    if (!popup) {
      const select = event.target.closest?.('select');
      // Espace / Alt+↓ ouvrent la liste ; les flèches seules gardent le comportement natif (changer la valeur).
      if (select && !select.multiple && (event.key === ' ' || (event.altKey && event.key === 'ArrowDown'))) {
        event.preventDefault(); open(select);
      }
      return;
    }
    if (event.key === 'Escape' || event.key === 'Tab') { event.preventDefault(); close(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (items[active]) choose(items[active].option); }
  }, true);

  window.addEventListener('resize', close);
  document.addEventListener('scroll', event => { if (popup && !popup.contains(event.target)) close(); }, true);
})();
