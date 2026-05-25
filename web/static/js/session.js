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
})();
