/* Liste des langues : remplie depuis le catalogue officiel du modèle plutôt que TR/EN codés en dur. */
(function () {
  const display = new Intl.DisplayNames(['fr'], { type: 'language' });
  const codes = { THA: 'th', NB: 'nb', ZH: 'zh' };

  window.duiLanguageName = function (pack) {
    const code = (codes[pack.id] || pack.id).toLowerCase();
    try {
      const name = display.of(code);
      if (name && name.toLowerCase() !== code) return name[0].toLocaleUpperCase('fr') + name.slice(1);
    } catch (_) { /* code non reconnu par Intl */ }
    return pack.name || pack.id;
  };

  let loaded = false, loading = null;

  async function loadPacks() {
    if (loaded) return;
    if (loading) return loading;
    loading = (async () => {
      const response = await fetch('/api/voice/packs');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Catalogue des voix indisponible.');
      const select = document.getElementById('voiceLanguage');
      const previous = select.value;
      const packs = (data.packs || []).map(pack => ({ ...pack, label: window.duiLanguageName(pack) }))
        .sort((a, b) => (b.id === 'FR') - (a.id === 'FR') || a.label.localeCompare(b.label, 'fr'));
      select.replaceChildren(...packs.map(pack => new Option(pack.label, pack.id)));
      const current = typeof data.current === 'string' ? data.current.toUpperCase() : previous;
      if (packs.some(pack => pack.id === current)) select.value = current;
      loaded = true;
    })().finally(() => { loading = null; });
    return loading;
  }

  function tryLoad() {
    loadPacks().catch(error => typeof showToast === 'function' && showToast(error.message));
  }

  document.querySelector('[data-tab="voice"]')?.addEventListener('click', tryLoad);
  if (document.body.dataset.activeTab === 'voice') tryLoad();
})();
