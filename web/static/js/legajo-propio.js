// Reglas sobre el propio legajo: planificacion:propia, asistencia:fichaje_propio,
// asistencia:correccion_propia, vacaciones:propia y empleados:propia.
//
// El servidor es el que bloquea (auth/core.py, check_no_es_*). Esto solo sirve
// para avisar ANTES de que lo intente, y para que el rechazo se lea como una
// regla y no como una falla: una ventana que hay que cerrar, no un cartel que se
// va solo.
(function () {
  const MSG = {
    planificacion: 'No podés modificar tu propia planificación. Si necesitás un cambio, pedíselo a otro encargado.',
    fichadas:      'No podés cargar ni borrar tus propias fichadas. Si necesitás una corrección, pedísela a otro encargado.',
    correccion:    'No podés cargar, cambiar ni borrar novedades en tu propio legajo. Si necesitás una, pedísela a otro encargado.',
    vacaciones:    'No podés modificar tus propias vacaciones pagadas ni tu saldo inicial de vacaciones. Si necesitás un cambio, pedíselo a otro encargado.',
    legajo:        'No podés cambiar en tu propio legajo el ingreso, el tipo, el cargo, la categoría, el estado ni la jubilación. Si hace falta, pedíselo a otro encargado.',
  };

  // Mientras no llegue la respuesta se asume que puede: el servidor igual frena.
  let info = { empleado_id: null, planificacion: true, fichadas: true, correccion: true,
               vacaciones: true, legajo: true };

  function esPropio(empleadoId) {
    return !!info.empleado_id && Number(empleadoId) === Number(info.empleado_id);
  }

  function _esc(t) {
    return String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function noPermitido(msg) {
    let ov = document.getElementById('np-overlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'np-overlay';
      ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:5000;display:flex;align-items:center;justify-content:center';
      ov.innerHTML =
        '<div style="background:#fff;border-radius:8px;max-width:420px;width:90%;padding:24px 26px;box-shadow:0 8px 30px rgba(0,0,0,.25);border-top:5px solid #c0392b">' +
        '<div style="font-weight:bold;font-size:1.05rem;color:#c0392b;margin-bottom:10px">🚫 No permitido</div>' +
        '<div id="np-msg" style="font-size:.92rem;color:#333;line-height:1.5"></div>' +
        '<div style="text-align:right;margin-top:18px"><button id="np-ok" style="background:#2c3e50;color:#fff;border:none;border-radius:5px;padding:8px 18px;cursor:pointer;font-size:.9rem">Entendido</button></div>' +
        '</div>';
      document.body.appendChild(ov);
      ov.querySelector('#np-ok').onclick = () => { ov.style.display = 'none'; };
    }
    ov.querySelector('#np-msg').textContent = msg;
    ov.style.display = 'flex';
    ov.querySelector('#np-ok').focus();
  }

  // Cualquier rechazo de estas reglas, venga del botón que venga, se muestra en
  // la ventana aunque esa pantalla solo sepa poner un cartel que se va solo.
  // Se reconocen por el texto: "No podés…" / "No tenés permiso…".
  const _fetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    const res = await _fetch(...args);
    if (res.status === 403) {
      res.clone().json().then(e => {
        if (e && typeof e.detail === 'string' && /^No (podés|tenés permiso)/.test(e.detail)) noPermitido(e.detail);
      }).catch(() => {});
    }
    return res;
  };

  // Para después de un fetch: si el servidor dijo 403, devuelve true para que el
  // llamador corte ahí y no ponga además su propio cartel de error.
  async function siNoPermitido(res) {
    if (!res || res.status !== 403) return false;
    const e = await res.clone().json().catch(() => ({}));
    noPermitido(e.detail || 'No tenés permiso para hacer esto.');
    return true;
  }

  // Recuadro para mostrar dentro de una ventana o sección del propio legajo.
  function avisoHtml(msg) {
    return '<div style="background:#fdecea;border:1px solid #f5c6cb;border-left:4px solid #c0392b;border-radius:5px;' +
           'padding:10px 12px;margin:8px 0;font-size:.85rem;color:#7b241c;line-height:1.45">' +
           '<b>Este es tu propio legajo.</b> ' + _esc(msg) + '</div>';
  }

  const listo = _fetch('/api/auth/me')
    .then(r => (r.ok ? r.json() : {}))
    .then(me => {
      const p = new Set((me.permisos || []).map(x => `${x.modulo}:${x.accion}`));
      info = {
        empleado_id:   me.empleado_id || null,
        planificacion: !me.empleado_id || p.has('planificacion:propia'),
        fichadas:      !me.empleado_id || p.has('asistencia:fichaje_propio'),
        correccion:    !me.empleado_id || p.has('asistencia:correccion_propia'),
        vacaciones:    !me.empleado_id || p.has('vacaciones:propia'),
        legajo:        !me.empleado_id || p.has('empleados:propia'),
      };
      return info;
    })
    .catch(() => info);

  window.LegajoPropio = { MSG, listo, esPropio, noPermitido, siNoPermitido, avisoHtml,
                          get puedePlanificacion() { return info.planificacion; },
                          get puedeFichadas() { return info.fichadas; },
                          get puedeCorreccion() { return info.correccion; },
                          get puedeVacaciones() { return info.vacaciones; },
                          get puedeLegajo() { return info.legajo; } };
})();
