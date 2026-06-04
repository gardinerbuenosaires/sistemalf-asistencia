(function () {
  const _fetch = window.fetch.bind(window);
  window.fetch = function (...args) {
    return _fetch(...args).then(function (res) {
      if (res.status === 401) {
        const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
        if (!url.includes('/api/auth/login')) {
          _sesionExpirada();
        }
      }
      return res;
    });
  };

  function _sesionExpirada() {
    if (document.getElementById('_sx-overlay')) return;
    const d = document.createElement('div');
    d.id = '_sx-overlay';
    d.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;'
      + 'align-items:center;justify-content:center;z-index:99999;font-family:sans-serif';
    d.innerHTML =
      '<div style="background:#fff;border-radius:8px;padding:32px 40px;text-align:center;max-width:300px;box-shadow:0 8px 32px rgba(0,0,0,.25)">'
      + '<div style="font-size:1.05rem;font-weight:600;margin-bottom:8px">Sesión expirada</div>'
      + '<div style="color:#666;font-size:.9rem;margin-bottom:20px">Redirigiendo al inicio de sesión...</div>'
      + '<div style="height:4px;background:#e5e7eb;border-radius:2px">'
      + '<div id="_sx-bar" style="height:4px;background:#2563eb;border-radius:2px;width:100%;transition:width 2.8s linear"></div>'
      + '</div></div>';
    document.body.appendChild(d);
    setTimeout(function () {
      const b = document.getElementById('_sx-bar');
      if (b) b.style.width = '0%';
    }, 50);
    setTimeout(function () { location.href = '/login'; }, 3000);
  }
  // ── Aviso de borradores de distribución pendientes ──────────────────────────
  async function _checkAvisosDistribucion() {
    try {
      const res = await _fetch('/api/distribucion/avisos');
      if (!res.ok) return;
      const avisos = await res.json();
      if (!avisos.length) return;

      if (document.getElementById('_dist-aviso-banner')) return;

      const semanas = avisos.map(a => {
        const [y, m, d] = a.semana_inicio.split('-').map(Number);
        const lunes = new Date(y, m - 1, d);
        const domingo = new Date(y, m - 1, d + 6);
        const fmt = dt => dt.toLocaleDateString('es-AR', { day: 'numeric', month: 'long' });
        return `<strong>${a.departamento_nombre} (${a.turno})</strong>: semana del ${fmt(lunes)} al ${fmt(domingo)}`;
      }).join('<br>');

      const banner = document.createElement('div');
      banner.id = '_dist-aviso-banner';
      banner.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:9000;'
        + 'background:#856404;color:#fff;padding:12px 20px;font-family:sans-serif;'
        + 'font-size:.88rem;display:flex;align-items:flex-start;gap:16px;'
        + 'box-shadow:0 -2px 8px rgba(0,0,0,.2)';
      banner.innerHTML =
        '<div style="flex:1"><strong>⚠ Distribución pendiente de confirmar:</strong><br>' + semanas + '</div>'
        + '<a href="/distribucion" style="color:#fff;font-weight:bold;text-decoration:underline;white-space:nowrap">Ir a Distribución</a>'
        + '<button onclick="this.parentElement.remove()" style="background:none;border:none;color:#fff;'
        + 'font-size:1.2rem;cursor:pointer;padding:0 4px;line-height:1" title="Cerrar">×</button>';
      document.body.appendChild(banner);
    } catch (_) {}
  }

  // ── Timer de inactividad del cliente ───────────────────────────────────────
  var _ultimaActividad = Date.now();
  var _INACTIVIDAD_MS = 10 * 60 * 1000;

  ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart'].forEach(function(ev) {
    document.addEventListener(ev, function() { _ultimaActividad = Date.now(); }, { passive: true });
  });

  setInterval(function() {
    if (location.pathname.startsWith('/login')) return;
    if (Date.now() - _ultimaActividad > _INACTIVIDAD_MS) _sesionExpirada();
  }, 60000);

  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(_checkAvisosDistribucion, 1500);
  });
})();
