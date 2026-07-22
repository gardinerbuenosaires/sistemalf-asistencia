(function () {
  setInterval(async function () {
    try {
      const r = await fetch('/api/ping', { headers: { 'X-Poll': '1' } });
      if (r.status === 401) location.href = '/login';
    } catch (_) {}
  }, 60000);
})();
