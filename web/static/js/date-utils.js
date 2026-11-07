/* ============================================================================
   Utilidades de fecha — FUENTE ÚNICA DE VERDAD.

   Regla de oro: las fechas "de calendario" (qué día es hoy, días de una grilla)
   se arman SIEMPRE desde componentes LOCALES (getFullYear/getMonth/getDate).

   NUNCA usar `new Date().toISOString().slice(0,10)` para obtener "hoy": eso
   devuelve la fecha en UTC y en Argentina (UTC-3) se corre un día a la
   tarde/noche (después de las ~21:00 ya es "mañana" en UTC).
   ============================================================================ */

/** "YYYY-MM-DD" de la fecha dada (o de ahora), en hora LOCAL. */
function ymdLocal(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** "YYYY-MM-DD" de HOY, en hora local. */
function todayLocal() {
  return ymdLocal(new Date());
}

/** Período vacacional al que se descuenta una V en esa fecha ("YYYY-MM-DD" o
    "YYYY-MM"). El período N se gana al 31/12 de N y se toma de diciembre de N
    a noviembre de N+1: diciembre ya es del período de su año.
    Misma regla que api/vacaciones.periodo_vacaciones. */
function periodoVacaciones(fecha) {
  const anio = parseInt(fecha.slice(0, 4)), mes = parseInt(fecha.slice(5, 7));
  return mes === 12 ? anio : anio - 1;
}
