(async function () {
  try {
    const r = await fetch('/api/config/brand');
    if (!r.ok) return;
    const { logo_url, nombre } = await r.json();
    const el = document.getElementById('nav-brand-empresa');
    if (!el) return;
    if (logo_url) {
      el.innerHTML = `<img src="${logo_url}" alt="" style="height:36px;vertical-align:middle;border-radius:4px">`;
      el.style.display = 'inline-flex';
      el.style.alignItems = 'center';
    } else if (nombre) {
      el.textContent = nombre;
      el.style.display = 'inline-block';
    }
  } catch { /* silencioso */ }
})();
