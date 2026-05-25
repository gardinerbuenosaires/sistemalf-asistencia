(function () {
  // ── Estilos ──────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #_um-wrap { position: relative; display: inline-block; }
    #_um-trigger { cursor: pointer; font-size: .78rem; color: #555; padding: 4px 8px;
                   border-radius: 4px; user-select: none; }
    #_um-trigger:hover { background: rgba(0,0,0,.06); }
    #_um-dropdown { display: none; position: absolute; right: 0; top: calc(100% + 4px);
                    background: white; border: 1px solid #ddd; border-radius: 6px;
                    box-shadow: 0 4px 16px rgba(0,0,0,.12); min-width: 180px; z-index: 9999; }
    #_um-dropdown.open { display: block; }
    #_um-dropdown button { display: block; width: 100%; text-align: left; padding: 10px 14px;
                           background: none; border: none; cursor: pointer; font-size: .85rem; color: #333; }
    #_um-dropdown button:hover { background: #f4f6f9; }
    #_um-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45);
                   z-index: 99990; align-items: center; justify-content: center; }
    #_um-overlay.open { display: flex; }
    #_um-modal { background: white; border-radius: 10px; padding: 28px 32px; width: 320px;
                 box-shadow: 0 8px 32px rgba(0,0,0,.2); font-family: Arial, sans-serif; }
    #_um-modal h3 { margin: 0 0 20px; font-size: 1rem; color: #2c3e50; }
    #_um-modal label { display: block; font-size: .78rem; color: #666; margin-bottom: 3px; margin-top: 12px; }
    #_um-modal input { width: 100%; padding: 8px 10px; border: 1px solid #ced4da;
                       border-radius: 5px; font-size: .88rem; box-sizing: border-box; }
    #_um-modal input:focus { outline: none; border-color: #2563eb; }
    #_um-error { color: #c0392b; font-size: .78rem; margin-top: 10px; min-height: 18px; }
    #_um-modal .btns { display: flex; gap: 8px; margin-top: 20px; justify-content: flex-end; }
    #_um-modal .btn-cancel { padding: 7px 16px; border: 1px solid #ccc; border-radius: 5px;
                              background: white; cursor: pointer; font-size: .85rem; }
    #_um-modal .btn-save { padding: 7px 16px; border: none; border-radius: 5px;
                            background: #2c3e50; color: white; cursor: pointer; font-size: .85rem; }
    #_um-modal .btn-save:hover { background: #3d5166; }
    #_um-modal .btn-save:disabled { opacity: .5; cursor: default; }
  `;
  document.head.appendChild(style);

  // ── HTML ─────────────────────────────────────────────────────────────────────
  const wrap = document.createElement('div');
  wrap.id = '_um-wrap';
  wrap.innerHTML = `
    <span id="_um-trigger"></span>
    <div id="_um-dropdown">
      <button onclick="_umAbrirModal()">Cambiar contraseña</button>
    </div>
  `;

  const overlay = document.createElement('div');
  overlay.id = '_um-overlay';
  overlay.innerHTML = `
    <div id="_um-modal">
      <h3>Cambiar contraseña</h3>
      <label>Contraseña actual</label>
      <input type="password" id="_um-actual" autocomplete="current-password">
      <label>Nueva contraseña</label>
      <input type="password" id="_um-nueva" autocomplete="new-password">
      <label>Confirmar nueva contraseña</label>
      <input type="password" id="_um-confirma" autocomplete="new-password">
      <div id="_um-error"></div>
      <div class="btns">
        <button class="btn-cancel" onclick="_umCerrar()">Cancelar</button>
        <button class="btn-save" id="_um-btn-save" onclick="_umGuardar()">Guardar</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  // Insertar wrap junto al span #user-info (lo dejamos oculto para que el código de la página siga funcionando)
  window.addEventListener('DOMContentLoaded', function () {
    const ui = document.getElementById('user-info');
    if (!ui) return;
    ui.style.display = 'none';
    ui.parentNode.insertBefore(wrap, ui.nextSibling);

    // Sincronizar trigger con el texto que la página asigne a #user-info
    const sync = () => {
      document.getElementById('_um-trigger').textContent = ui.textContent || '';
    };
    const observer = new MutationObserver(sync);
    observer.observe(ui, { childList: true, characterData: true, subtree: true });

    // Cerrar dropdown al hacer clic fuera
    document.addEventListener('click', function (e) {
      if (!wrap.contains(e.target)) {
        document.getElementById('_um-dropdown').classList.remove('open');
      }
    });

    document.getElementById('_um-trigger').addEventListener('click', function (e) {
      e.stopPropagation();
      document.getElementById('_um-dropdown').classList.toggle('open');
    });
  });

  // ── Funciones globales ───────────────────────────────────────────────────────
  window._umAbrirModal = function () {
    document.getElementById('_um-dropdown').classList.remove('open');
    document.getElementById('_um-actual').value = '';
    document.getElementById('_um-nueva').value = '';
    document.getElementById('_um-confirma').value = '';
    document.getElementById('_um-error').textContent = '';
    document.getElementById('_um-btn-save').disabled = false;
    document.getElementById('_um-overlay').classList.add('open');
    setTimeout(() => document.getElementById('_um-actual').focus(), 50);
  };

  window._umCerrar = function () {
    document.getElementById('_um-overlay').classList.remove('open');
  };

  window._umGuardar = async function () {
    const actual   = document.getElementById('_um-actual').value;
    const nueva    = document.getElementById('_um-nueva').value;
    const confirma = document.getElementById('_um-confirma').value;
    const errEl    = document.getElementById('_um-error');
    const btn      = document.getElementById('_um-btn-save');

    errEl.textContent = '';
    if (!actual)               { errEl.textContent = 'Ingresá la contraseña actual.'; return; }
    if (nueva.length < 6)      { errEl.textContent = 'La nueva contraseña debe tener al menos 6 caracteres.'; return; }
    if (nueva !== confirma)    { errEl.textContent = 'Las contraseñas no coinciden.'; return; }

    btn.disabled = true;
    try {
      const res = await fetch('/api/auth/cambiar-clave', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clave_actual: actual, clave_nueva: nueva }),
      });
      if (res.ok) {
        _umCerrar();
        // Toast simple si existe, sino alert
        if (typeof toast === 'function') toast('Contraseña actualizada');
        else alert('Contraseña actualizada correctamente.');
      } else {
        const data = await res.json().catch(() => ({}));
        errEl.textContent = data.detail || 'Error al cambiar la contraseña.';
        btn.disabled = false;
      }
    } catch {
      errEl.textContent = 'Error de conexión.';
      btn.disabled = false;
    }
  };

  // Cerrar modal con Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') _umCerrar();
  });
  // Confirmar con Enter en el último campo
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && document.getElementById('_um-overlay').classList.contains('open')) {
      _umGuardar();
    }
  });
})();
