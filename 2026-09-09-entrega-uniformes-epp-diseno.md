# Uniformes y EPP — diseño y estado del módulo

> **Qué es esto.** Diseño del módulo de entrega de uniformes y EPP, conversado y cerrado
> con el usuario entre el 2026-09-02 y el 2026-09-09 **antes de escribir código**, y
> actualizado a medida que se implementó. Cada decisión lleva su porqué. Es autocontenido.
>
> El módulo se llamó `entregas` durante el diseño y se renombró a `uniformes` al empezar a
> implementarlo (ver *Decisiones cerradas*). La rama conserva el nombre `feat/entregas`.

## Estado al 2026-09-15

**El módulo está completo, incluida la ropa pendiente al momento de la baja.** Tandas 0 a 6
implementadas y probadas —287 chequeos, que corren sobre una copia temporal de la base—, en
la rama `feat/entregas`, publicada en GitHub. **No está en `main` ni en producción.**

| Tanda | Qué | Estado |
|---|---|---|
| 0 | Maqueta de la constancia, impresa y comparada contra el papel | hecha |
| 1 | Tablas, catálogos, bandera `uniformes_activo` | hecha |
| 2 | Talles del personal, con recuento para comprar | hecha |
| 3 | Constancias: emitir, anular, carga histórica | hecha |
| 4 | Constancia imprimible con datos reales, datos fiscales | hecha |
| 5a | Reportes, puestos que no reciben uniforme, pestaña Configuración | hecha |
| 5b | Resumen en la ficha del empleado y ficha por persona | hecha |
| 6a | Ropa pendiente, bandeja de bajas y cierre del circuito (API) | hecha |
| 6b | Las pantallas de la tanda 6 | hecha |

### Decisiones que surgieron implementando

No estaban en el diseño original; se tomaron al ver el sistema funcionando.

- **Se excluyen los empleados de tipo `acceso`**: existen solo para abrir puertas con la
  huella, no son personal. Constante `EXCLUIR_NO_PERSONAL`, mismo criterio que
  `distribucion.py` y `barmans.py`. Los de `parking` quedan adentro (ver *Sin decidir*).
- **Un tipo de talle por escala.** `Pantalón (número)` y `Pantalón (letra)` son dos tipos,
  así una misma persona puede tener 42 y L al mismo tiempo. Se descartaron dos campos fijos
  en la tabla porque atan a exactamente dos escalas: una tercera obligaría a cambiar el
  esquema.
- **La «misma prenda» se deduce del nombre**, con la forma `Prenda (escala)`. De esa regla
  salen el encabezado agrupado de la grilla de talles y el desplegable de la constancia.
- **El talle de un renglón acepta cualquier escala de la misma prenda.** El elemento define
  la escala esperada —de ahí sale la precarga—, pero si el artículo que se entrega vino en
  la otra, se puede registrar: el sistema anota lo que pasó, no lo que debía pasar. El valor
  se guarda en la escala a la que pertenece y no pisa el de la otra.
- **La hoja imprimible lee los datos de empresa de `/api/uniformes/empresa`**, no de
  `/api/configuracion`, que exige `usuarios:ver`: RRHH no lo tiene y no habría podido
  imprimir lo que emite.
- **Flujo de impresión.** Al emitir aparece un aviso con botón *Imprimir*; no se abre sola
  porque los navegadores bloquean las ventanas que no provoca un clic directo. Se reimprime
  desde el listado. Las anuladas salen con ANULADA cruzado; las históricas no se imprimen.
- **Datos fiscales** en Configuración → Sistema, dentro de la sección de empresa, visibles
  solo con la bandera prendida.
- **La línea «Recibí de conformidad…»** arriba de la firma: confirmada.
- **El alta de una constancia reemplaza al listado** mientras está abierta, con cabecera
  propia. Compartiendo pantalla y estilo, no se distinguía en qué modo se estaba.
- **`carga_inicial` se puede sacar de verdad gracias a la rama `fix/permisos-default`, que
  tiene que mergearse ANTES que este módulo.** Sin ese arreglo, el sistema reinsertaba los
  permisos por defecto en cada arranque: si a RRHH se le sacaba la carga histórica desde
  Roles, la recuperaba sola en el primer reinicio. El arreglo aplica cada permiso por
  defecto una sola vez por rol; se comprobó en seco que las dos ramas se juntan sin
  conflictos.
- **Reportes: una consulta, no tres.** Filtros por empleado, cargo, departamento, rubro y
  período. *Detalle* muestra lo que se imprimió (la copia de la constancia); *resumen* agrupa
  por elemento —por id, con el nombre de hoy— con el desglose por talle, que es el dato para
  comprar. Cuentan solo entregas emitidas: las anuladas y las devoluciones no suman, las
  históricas sí. El cargo y el rubro se filtran por el dato actual, no por el texto copiado.
- **Última entrega por persona** se arma desde la lista de empleados, no desde las entregas:
  quien nunca recibió nada no tiene fila de entrega y es justamente quien tiene que ir arriba.
  Los meses se calculan en el servidor.
- **Excel** en los dos reportes, con fechas reales de Excel (se ordenan y filtran) y los filtros
  aplicados en el título.
- **Puestos que no reciben uniforme** (tabla `uniformes_cargos_sin_uniforme`, la octava). Sin
  ellos, el reporte de última entrega llenaba su parte de arriba con gente que nunca va a
  recibir nada. Se marca por cargo —el reparto real es limpio por puesto—, se marca lo que
  **no** recibe —un cargo nuevo sigue a la vista hasta que alguien decida— y la marca oculta
  solo el «nunca»: si alguien de un puesto marcado recibe algo, aparece igual. Editable con
  `uniformes:editar`. En producción hay que cargarla en cada instancia: es un dato.
- **Pestaña Configuración.** Elementos, Rubros, Tipos de talle y Puestos son sub-pestañas de
  una sola pestaña: arriba quedan solo las del uso diario. Puestos estuvo primero debajo de
  Rubros y después en un diálogo del reporte, y en los dos lugares quedaba escondido; el
  botón del reporte quedó como atajo.
- **El resumen del empleado va junto a la foto, no en el pie de la ficha.** Lo que dice —cuándo
  recibió algo por última vez y si algo venció— es información sobre la persona, no una acción
  del formulario, y se tiene que ver apenas se abre la ficha. Además es la zona más estable del
  archivo: `main` no la toca desde mayo. El pie quedó como en `main`, con *Legajo* solo.
- **El resumen sale del mismo cálculo que el reporte de última entrega**, así no pueden
  contradecirse. Hay una prueba que compara los dos, fecha por fecha y rubro por rubro.
- **El detalle de una persona vive en el módulo**, no duplicado dentro de Empleados: el botón
  abre `/uniformes?empleado=ID`. Esa misma ficha aparece al filtrar el listado de constancias
  por un empleado.
- **Los botones que abren otra pantalla usan una pestaña con nombre**, que el navegador
  reutiliza, en vez de acumular una por clic. Se evaluó abrir en la misma pestaña y se descartó:
  la ficha del empleado no avisa al salir, así que perdería lo que alguien esté editando sin
  guardar.
- **Las pruebas viven en `tests/`** y cada una trabaja sobre una copia temporal de la base,
  abierta en solo lectura: nunca modifican una base real. Si la base no tiene el módulo, la copia
  se migra y se siembra sola, así sirven para ensayar contra copias de producción antes de salir.
  Se corren con `python tests/correr_todo.py [--origen ruta.db]`.
- **El talle se carga solo desde la entrega**, y por eso la grilla de Talles casi no hay que
  tocarla a mano: lo que se le entregó a alguien es la mejor evidencia de qué talle usa. El valor
  se guarda en la escala a la que pertenece, así entregar un pantalón «L» completa el talle en
  letra sin borrar el número. Quién pisa y quién no: **una entrega del sistema pisa** el talle
  anterior, porque se está cargando hoy; **una carga histórica solo completa el que falte**, ya
  que la hoja puede ser de hace dos años y la persona pudo cambiar; **una devolución no toca
  nada**. Anular una constancia no revierte el talle: el talle es el estado de hoy, no un
  historial, y si quedó mal se corrige en la grilla.

#### La ropa pendiente al momento de la baja (tanda 6)

- **El sistema no dice cuánta ropa debe alguien, porque no puede saberlo.** Sabe qué se le
  entregó y cuándo; qué conserva, no. Entre las entregas nadie registra devoluciones: la
  prenda gastada se reemplaza y listo. Por eso no hay una deuda calculada sino una lista
  corta y fechada, y la decisión la toma la persona que liquida. El valor no está en el
  número, está en que quede asentado quién decidió qué.
- **Se mira una ventana de meses, no todo el historial.** El total de la vida no sirve: nadie
  devuelve las doce chaquetas que recibió en seis años, porque cada una reemplazó a la
  anterior. Se suman las entregas de los últimos meses —6 por defecto, configurable— y lo
  anterior no desaparece: queda en un renglón aparte con su fecha, que es lo que salva la
  pantalla cuando alguien se va ocho meses después de su última entrega.
- **Agrupado por prenda, y además las constancias del período.** Por constancia solo
  dependería de cómo se partió el papeleo: el mismo uniforme en tres hojas o en una da
  recortes distintos. Arriba el resumen por prenda —qué pedirle—, abajo los papeles del
  período con su botón de imprimir, que es lo concreto que se le muestra al que se va.
- **El cierre es lo que le da fin al circuito.** Sin él la bandeja de bajas solo crece y
  a los pocos meses no la mira nadie. Es un acto administrativo, no un documento: no lleva
  número ni se imprime, y **no exige que haya una constancia de devolución** — si la
  exigiera, el día que alguien se va sin devolver nada habría que emitir un papel vacío para
  poder cerrar. Lo devuelto se escribe en la observación, o se registra aparte si hay algo
  que firmar. Tres resultados: devolvió todo, devolvió parte, no devolvió.
- **El cierre guarda congelado lo que quedaba pendiente**, por el mismo motivo que la
  constancia copia sus datos: dentro de dos años el catálogo va a ser otro.
- **Y funciona como fecha de corte**: lo entregado después vuelve a contar. Con eso la
  recontratación se resuelve sola, sin mirar `fecha_recontratacion` ni agregar un campo. Si
  el cierre y la entrega caen el mismo día, desempata la hora en que se cargó cada uno.
- **Reabrir no borra**: el cierre queda como historia con su motivo, y va con el permiso de
  eliminar —el mismo que anular una constancia—, porque deshacer una decisión asentada no es
  lo mismo que tomarla.
- **La bandeja nace vacía, y se llena con la carga histórica.** Comprobado sobre una copia de
  Gardiner: con el módulo recién migrado hay 255 bajas en la base y **cero** filas en la
  bandeja, porque no muestra bajas sino bajas con ropa registrada, y todavía no hay
  ninguna entrega cargada. Basta cargar **una** constancia histórica de alguien que ya se fue
  para que aparezca. O sea que el problema no es el día del deploy: aparece a medida que RRHH
  digitaliza el papel.
- **Por eso el cierre masivo se corre al terminar la carga histórica**, no antes. Marca esas
  bajas como anteriores al sistema y deja la bandeja con los que se van de verdad a partir de
  ahí. Existe de dos formas: `scripts/cerrar_bajas.py`, que es la que conviene —simula por
  defecto, dice sobre qué base escribe y es idempotente—, y el mismo endpoint desde la
  pantalla, con permiso de carga inicial. La fecha de cada cierre es la del egreso y no la de
  hoy, para que el corte quede donde corresponde: si esa persona vuelve, lo que se le entregue
  después cuenta desde su egreso.
- **A una persona dada de baja no se le registra una entrega**, ni siquiera digitalizando un papel
  viejo. Su
  liquidación final ya se pagó, así que ese registro no sirve para nada y encima sumaría a la
  bandeja una deuda que nadie va a reclamar. **La devolución sí**, porque llega siempre después
  de la baja: por eso el bloqueo mira el tipo de movimiento y no a la persona. El selector del
  alta muestra solo personal activo —hoy esa pantalla emite únicamente entregas—, y los
  bajas volverán a aparecer ahí el día que exista la pantalla de devolución. La lista de
  `/api/uniformes/empleados` los sigue trayendo, porque el filtro del listado y los reportes los
  necesitan; lo que cambia es quién puede recibir una entrega.
- **Cada botón aparece solo donde puede funcionar.** En la ficha de una persona, *«Nueva
  constancia para esta persona»* aparece solo si está activa: a una baja la API le rechaza la
  entrega, así que ese botón solo podía terminar en un mensaje de error. Y *«Cerrar circuito»*
  aparece solo si está de baja, porque el cierre existe para completar una baja: ofrecerlo sobre
  alguien que sigue trabajando invita a poner en cero la ropa que todavía tiene puesta. La ficha
  dice además desde cuándo está de baja, que es el dato que falta cuando se llega desde la
  bandeja. Y lo mismo con el **alta general del listado** —el botón de la tarjeta «Constancias
  emitidas»—: mientras se está mirando la ficha de una baja no aparece, porque en ese momento
  la pantalla es el detalle de esa persona y se lee como «nueva constancia para él»; sacando el
  filtro vuelve. La regla general: un control que no puede funcionar en ese contexto no se
  muestra deshabilitado, no se muestra.
- **Dónde vive cada cosa.** La bandeja es una sub-pestaña de Reportes, al lado de los otros dos
  reportes; el panel de ropa pendiente es parte de la ficha de la persona dentro del módulo,
  debajo de los talles; la ventana de meses es una sub-pestaña de Configuración. En la ficha del
  empleado, en cambio, solo aparece una línea diciendo que la ropa quedó cerrada: el detalle no
  se duplica fuera del módulo.

## El problema

Hoy la entrega de ropa de trabajo y EPP se registra en una planilla de papel —
*"Constancia de entrega de ropa de trabajo y elementos de protección personal"*.

**Encabezado:** RAZÓN SOCIAL, C.U.I.T., DIRECCIÓN, LOCALIDAD, C.P., PROVINCIA (datos de la
empresa), más APELLIDO Y NOMBRE y D.N.I. del empleado.

**Dos recuadros de texto libre:**
- *"Breve descripción del puesto de trabajo en el/los cuales se desempeña"*
- *"Elementos de protección personal necesarios para el trabajador según el puesto de trabajo"*

**Ninguno de los dos se reproduce en la hoja del sistema.** Ver *Decisiones cerradas*.

**Grilla de renglones:** PRODUCTO · TIPO/MODELO · MARCA · POSEE CERTIF. (S/N) · CANTIDAD ·
FECHA ENTREGA · FIRMA.

Se quiere cargar la entrega en el sistema y **generar una hoja imprimible** para que el
empleado la firme de puño y letra y se archive en su legajo físico.

### Lo que la planilla enseñó

La fecha y la firma están **por renglón**, no por documento: el papel es una **ficha
acumulativa por empleado** — una hoja por persona, un renglón cada vez que se le da algo,
firma renglón por renglón, hoja nueva cuando se llena.

**Eso no se reproduce en el sistema, y es deliberado.** Si la ficha acumulativa se
reimprimiera cada vez, los renglones viejos saldrían sin firma —la firma está en la
impresión anterior, ya archivada— y el legajo se llenaría de hojas parciales sin saber cuál
es la buena. Reproducirlo exigiría escanear firmas, que está fuera de alcance.

Decisión: **una hoja por entrega**, con el formato visual del papel. Los renglones son los
ítems de esa entrega y la grilla termina donde terminan (sin renglones vacíos de relleno,
que además son riesgosos: un papel firmado con espacios en blanco se puede completar
después). Cada hoja archivada queda completa y cerrada el día que se emitió.

La ficha acumulativa deja de ser papel: pasa a ser el **historial en pantalla**, que es más
completo y no hay que reimprimirlo nunca.

## Restricción de entrada

**No debe tocar la parte sensible del sistema.** Verificado: no existe hoy nada de
uniformes, talles ni EPP en el código. Terreno limpio, módulo enteramente aditivo — cero
contacto con `sync/evaluador.py`, `fichajes`, `resultados_dia`, `planificacion` o `premios`.
**Ni un solo `ALTER TABLE` sobre tablas existentes.**

---

## Decisiones cerradas

| Decisión | Elegido | Por qué |
|---|---|---|
| Documento | **Una hoja por entrega** | La ficha acumulativa del papel no se puede reimprimir sin perder las firmas. Ver arriba. |
| Estado `borrador` | **No existe** | El acto real es instantáneo: el empleado está enfrente, le dan tres cosas, se imprime, firma. El borrador ya existe naturalmente — es el formulario abierto sin guardar. Guardar = emitir. Elimina un estado, una pantalla de pendientes y el permiso `confirmar`. |
| Stock / inventario | **No** | Solo registro de qué se entregó a quién y cuándo. Sumar inventario duplicaba el módulo y exigía cargar cada compra. |
| Puesto | **Campo `PUESTO` en el encabezado**, con el `cargo` del legajo | El texto *"breve descripción del puesto de trabajo…"* **no se imprime**: es una consigna del formulario de papel, no un dato. El puesto va como campo etiquetado, igual que D.N.I. o C.U.I.T. Sin recuadro de texto libre, sin tabla intermedia, sin texto por cargo. Se copia al emitir. |
| Recuadro de EPP necesario | **No existe** | Ni el recuadro ni su texto: no aplica al caso. Esto cancela el "EPP requerido por puesto" que la versión anterior mandaba a fase 2 — ya no es un pendiente, es una decisión de no hacerlo. |
| Firma | **Una sola al pie**, sobre papel, al legajo físico | Consecuencia directa de "una hoja por entrega": si el documento es un solo acto, con una fecha y un conjunto de elementos, la firma cubre la hoja entera. La firma por renglón solo tenía sentido cuando la hoja era acumulativa y cada línea era un acto distinto. Se van de la grilla las columnas **FIRMA** y **FECHA ENTREGA**; la fecha sube al encabezado. Sin escaneo adjunto ni firma digital — es lo que espera una auditoría de la SRT. |
| Renglones por hoja | **Máximo 14, sin paginado** | Medido sobre la maqueta impresa: entran 16 con textos de una línea. El tope de 14 deja margen para nombres que bajen de renglón. Si hicieran falta más, son dos constancias. |
| Logo | **Centrado arriba**, si hay logo cargado | Reusa `logo_empresa` de `configuracion`, que ya se carga desde Configuración. Si no hay logo, la hoja sale solo con el texto — sin configurar nada. |
| Encabezado del remito | **Configurable** | Razón social, CUIT, dirección, localidad, CP y provincia en la tabla `configuracion`. Cuesta lo mismo que hardcodearlo y no ata a una sola razón social — hay **dos instancias productivas**. |
| Carga histórica | **Sí, desde el día uno** | Ver sección propia. Sin ella, al prender el módulo todos figuran como "nunca recibió nada" y la alerta es ruido durante meses. |
| Devolución | **Columna preparada, no construida** | Ver sección propia. |
| Talle del empleado | **Registro propio** | Ver sección propia. |
| Nombre interno | **`uniformes`** | Se probó `entregas` —nombrar por el acto y no por la cosa, ya que ni "uniformes" ni "EPP" cubren las dos categorías— y se descartó: en un restaurante "Entregas" se lee como entregas de mercadería. Nombrar por el acto solo funciona si el acto es inequívoco. Etiqueta del menú: **"Uniformes y EPP"**, que aclara que también cubre el EPP. |

---

## Modelo de datos

Ocho tablas nuevas, todas con prefijo `uniformes_`. La octava, de puestos que no reciben
uniforme, se describe en *Decisiones que surgieron implementando*.

### `uniformes_categorias` — los rubros

Catálogo chico y editable. Arranca con dos filas: *Ropa de trabajo* y *EPP*.

| campo | notas |
|---|---|
| `id`, `nombre`, `activo` | |
| `meses_alerta` | INTEGER, nullable. `NULL` = nunca avisa, solo informa. |

**El umbral vive acá, no global.** Seis meses tiene sentido para la ropa; para los guantes,
que se reponen cuando se rompen, no significa nada. Un umbral único haría que todos los
empleados tengan la alerta permanente y en dos semanas nadie la mire.

> **Nota de nomenclatura.** Ya existe una tabla `categorias` en el sistema
> ([db/database.py:439](db/database.py#L439)) que es la categoría del empleado por convenio,
> con su ABM en `/api/categorias`. La tabla nueva no choca técnicamente, pero **en la UI hay
> que llamarle "Rubro"**, nunca "Categoría", o se confunden.

### `uniformes_tipos_talle`

| campo | notas |
|---|---|
| `id`, `nombre`, `orden`, `activo` | Calzado, Pantalón, Pantalón (letra), Chaqueta, Campera… |

### `uniformes_talle_valores`

Los valores válidos de cada tipo. Al cargar un talle se elige de un desplegable, no se
escribe.

| campo | notas |
|---|---|
| `tipo_talle_id` | → `uniformes_tipos_talle` |
| `valor` | `42`, `L`, `XXL` |
| `orden` | lo que permite mostrar S, M, L, XL en ese orden y no alfabético |
| `activo` | |

Con texto libre, en 60 empleados aparecen `L`, `l`, `Large` y `L ` como cuatro talles
distintos, y el recuento para comprar sale mal justo cuando se lo necesita.

### `uniformes_elementos` — el catálogo

ABM completo, nada hardcodeado.

| campo | de dónde sale |
|---|---|
| `nombre` | columna PRODUCTO |
| `tipo_modelo` | columna TIPO/MODELO |
| `marca` | columna MARCA |
| `posee_certificado` | columna POSEE CERTIF. (S/N) |
| `categoria_id` | → `uniformes_categorias` (el rubro) |
| `tipo_talle_id` | → `uniformes_tipos_talle`, **nullable**: NULL = no lleva talle |
| `activo` | se desactiva, **nunca se borra** |

Al cargar una entrega se elige el elemento de una lista y las cuatro columnas del formulario
se completan solas. Solo se tipea **cantidad** y, si corresponde, **talle**.

Los elementos no se borran: el DELETE de la API debe rechazar si el elemento tiene ítems.

### `uniformes_talles_empleado`

| campo | notas |
|---|---|
| `empleado_id`, `tipo_talle_id`, `valor` | |

Solo existen filas para quien tenga talle cargado — cero filas para el personal que no
recibe ropa, que era la objeción a ponerlo en el legajo.

### `uniformes_movimientos` — el evento

| campo | notas |
|---|---|
| `empleado_id`, `fecha` | |
| `tipo` | `entrega` / `devolucion` — ver sección Devolución |
| `origen` | `sistema` / `historico` — ver sección Carga histórica |
| `numero` | correlativo, **NULL** en los históricos |
| `estado` | `emitida` / `anulada` |
| `observaciones` | |
| `empleado_apellido_nombre`, `empleado_dni`, `cargo_nombre` | **copia**, ver abajo |
| `anulada_en`, `anulada_por`, `motivo_anulacion` | |
| `creado_por`, `creado_en` | |

**El correlativo se asigna dentro de la misma transacción** que crea el movimiento
(`MAX+1` bajo la misma transacción, no leído antes). Si no, dos usuarios emitiendo a la vez
sacan el mismo número en un documento firmado. Serie separada por `tipo`. Entre las dos
instancias productivas no hay choque: cada base lleva su propio correlativo.

### `uniformes_items` — los renglones

| campo | notas |
|---|---|
| `movimiento_id` | ON DELETE CASCADE |
| `elemento_id` | → `uniformes_elementos` |
| `cantidad`, `talle`, `orden`, `observacion` | |
| `elemento_nombre`, `tipo_modelo`, `marca`, `posee_certificado`, `categoria_nombre` | **copia**, ver abajo |

### Relación por id **y** copia — el punto crítico

Cada renglón guarda el `elemento_id` (la relación) **y además** copia el nombre, tipo/modelo,
marca, certificado y rubro al momento de emitir. No es redundancia, son dos usos distintos:

- **La relación** sirve a los reportes: *"cuántos pares de calzado salieron este año"*
  agrupa por id, no por texto, y sigue funcionando aunque después se corrija el nombre.
- **La copia** sirve al papel: si el año que viene cambian de proveedor de guantes y editan
  el elemento, una hoja firmada hace ocho meses no puede empezar a decir otra marca. Es un
  documento con validez legal.

Lo mismo con los datos del empleado (apellido y nombre, DNI, cargo): van impresos en un
papel firmado, así que se copian. Si mañana corrigen un DNI mal cargado, la hoja ya firmada
no cambia.

Editar el catálogo cambia lo que se entrega de ahí en adelante, **nunca lo que ya se firmó**.
Es la misma filosofía con la que `premios_evaluacion` guarda valores calculados en vez de
recalcularlos.

Como no hay estado borrador, **la copia se sella al crear**, que es el mismo acto que emitir.

---

## Flujo

```
1. Alta       → empleado, fecha; se copian apellido/nombre, DNI y cargo
2. Renglones  → elementos del catálogo: cantidad y talle (precargado del registro)
3. Guardar    → asigna número y sella la copia          [emitida]
4. Imprimir   → A4 con el formato de la planilla actual
5. Firma      → de puño y letra, al legajo físico
```

Un movimiento emitido es de **solo lectura**. Si algo salió mal: anular con motivo
obligatorio y hacer uno nuevo. Nunca se edita un documento ya firmado. Mismo criterio que
`periodos_cerrados`, aunque sin engancharse a esas tablas — esto no es liquidación.

**La constancia entra siempre en una sola hoja: máximo 14 renglones. No hay paginado.** En la
práctica una entrega tiene tres o cuatro ítems; si alguna vez hicieran falta más, son dos
constancias.

El tope es 14 y no 16 —que es lo que entra con textos de una sola línea, medido sobre la
maqueta impresa— para dejar margen a los nombres largos que bajan de renglón y hacen crecer
la fila. Es una constante, cambiarla es un número.

---

## Carga histórica

RRHH puede cargar entregas anteriores al sistema para alimentar el historial y digitalizar
las fichas de papel.

- Columna `origen = 'historico'` en el movimiento.
- **No llevan número.** El correlativo identifica hojas que imprimió el sistema; un registro
  histórico no tiene hoja propia — la hoja es el papel firmado que ya está en el legajo.
- **No se imprimen.** Sin botón de imprimir, justamente porque la firma ya existe. Emitir un
  papel nuevo que dice lo mismo que uno firmado es la forma de terminar con dos documentos
  que no coinciden.
- Fecha libre hacia atrás, sin validaciones.
- En el historial del empleado se ven mezclados y ordenados por fecha, con una marca discreta
  de que vienen del papel.

**Permiso: `carga_inicial`.** Ya existe en `ACCIONES` ([auth/core.py:31](auth/core.py#L31))
y lo usan `asistencia` y `vacaciones` para exactamente esto. Separado de `editar`, así se le
da a RRHH mientras dure la digitalización y se le saca después.

**Por qué importa más de lo que parece:** sin carga histórica, el día que se prende el módulo
todos los empleados figuran como *"nunca recibió nada"* y la alerta por rubro es ruido puro
durante meses. Con ella, la alerta sirve desde el primer día.

Efecto secundario: a medida que RRHH digitalice las fichas, el sistema **sí va a saber qué
tiene cada empleado**, lo que con el tiempo destraba el reporte de devolución pendiente.

---

## Talles

Se registran aparte del historial de entregas, y el motivo es concreto: **para comprar hacen
falta los talles de todo el personal**, incluida la gente que nunca recibió nada por el
sistema. El historial solo conoce a los que ya recibieron; no alcanza.

No van en el legajo: no aplican a todo el mundo y ensuciarían la ficha con campos vacíos para
la mayoría del personal.

### La escala cambia según la marca

El problema no es que el talle sea "mixto": es que **una misma persona es 42 en una marca y L
en otra**, y las dos cosas son ciertas al mismo tiempo.

La salida no complica el modelo: se crean **dos tipos de talle**, "Pantalón" y "Pantalón
(letra)". El empleado puede tener uno, el otro o los dos. Y como **cada elemento apunta a su
tipo de talle**, al cargar la entrega el sistema ya sabe cuál de los dos pedir y precarga el
correcto — no hay que acordarse.

Si trabajan con una sola marca, tienen un tipo solo por prenda y el asunto no existe.

### Dónde se cargan

- **En el modal de entregas del empleado**, una línea arriba del historial. Al cargar una
  entrega el talle sale de ahí precargado; si se escribe distinto, se actualiza el registro
  —la entrega más reciente es la mejor evidencia— y siempre se puede corregir a mano.
- **En una grilla dentro de `/uniformes`**: empleados en filas, tipos de talle en columnas,
  celdas editables, filtrable por cargo o departamento. Con eso se cargan los talles de los
  30 de cocina de una sentada, en el mismo formato de grilla que ya se usa en mozos y
  distribución.

La fila de cada empleado muestra **todos los tipos**, dejando vacíos los que no apliquen. Es
más simple que definir qué le corresponde a cada cargo — que es exactamente el agujero de
"EPP por puesto" que se decidió no abrir.

### La grilla es también el reporte de compra

Al pie de cada columna, el recuento por valor sobre el conjunto filtrado:

```
Cocina — Chaqueta:   S: 2   M: 7   L: 11   XL: 4        (24 de 26 cargados)
Cocina — Calzado:   40: 1  41: 5   42: 8   43: 7  44: 3
```

Una sola pantalla que sirve para cargar y para comprar. El "24 de 26" dice cuántos faltan sin
tener que buscarlos.

---

## Devolución — preparada, no construida

Se modela la columna `tipo` (`entrega` / `devolucion`) **ahora**, y la funcionalidad se
implementa más adelante. No porque sea difícil, sino porque el circuito real todavía no está
claro, y una pantalla construida sobre un circuito que no se entiende es una pantalla que
nadie usa. La columna hoy vale cero; agregarla en seis meses es una migración.

Lo que ya está decidido de su diseño, para cuando toque:

- **El empleado va a estar dado de baja.** No es un problema: en este sistema los empleados
  nunca se borran, se desactivan (`activo = 0` + `fecha_egreso`). El `empleado_id` sigue
  existiendo. Lo que hace falta es que el selector **incluya las bajas cuando el movimiento es
  una devolución**, y solo entonces: el alta de entregas los excluye a propósito. O sea que la
  pantalla de devolución necesita su propia lista, o una marca de tipo que cambie la lista del
  alta actual — no alcanza con reusar el selector tal como está hoy.
- **La fecha no se valida contra `fecha_egreso`.** Devolver la ropa tres semanas después de
  irse es lo normal, no un error.
- **Sin validar contra lo entregado.** Puede devolver ropa de 2023 que el sistema nunca
  registró. Coherente con no llevar inventario.
- Mismo catálogo, mismo circuito, misma hoja con otro título.
- Serie de numeración propia por tipo.
- El campo `observacion` por renglón cubre lo que en la práctica va a importar: *en uso*,
  *roto*, *sin devolver*.

**Límite honesto:** el sistema no va a poder decir *qué le falta devolver* a alguien, salvo
para el personal cuyo historial esté cargado. Lo que sí puede decir desde el día uno es
*"estas bajas no tienen ninguna devolución registrada"* — una lista, no un saldo.

---

## Dónde vive en la UI

Se evaluó meterlo como sección del legajo y se descartó: el legajo ya está denso, y sobre
todo **los reportes cruzan empleados por definición**, así que la pantalla propia hace falta
igual. Una vez que existe, duplicarlo adentro del legajo sería mantener lo mismo en dos lados.

Queda en tres capas, cada una con más lugar que la anterior:

| Capa | Qué muestra | Altura |
|---|---|---|
| **Botón en la ficha del empleado** | resumen de una línea | fija |
| **Modal** | talles + desglose por rubro + historial completo + reimprimir | libre |
| **Módulo `/uniformes`** | todos los empleados, filtros, catálogos, export | pantalla propia |

El botón va al lado del de *Legajo* que ya existe en
[empleados.html:1140](web/templates/empleados.html#L1140). Mismo patrón, ya establecido.

El modal reusa el CSS existente `.foto-modal-overlay` / `.foto-modal`
([empleados.html:123](web/templates/empleados.html#L123)), que ya se usa dos veces (fotos
pendientes y QR). No hace falta CSS nuevo.

`/uniformes` va con pestañas, reusando el patrón `.cfg-tab` / `switchCfgTab()` que ya existe
en [configuracion.html:228](web/templates/configuracion.html#L228):

```
Constancias | Talles | Reportes | Configuración
                                  └ Elementos · Rubros · Tipos de talle · Puestos
```

Los catálogos van en el módulo propio —en su pestaña Configuración— y **no** en la pantalla
de Configuración del sistema, a propósito:
`configuracion.html` es un archivo grande y muy tocado, y meter mano ahí es pedir un
conflicto de merge.

### El resumen del botón

Este punto costó tres iteraciones y conviene no re-litigarlo. El texto es:

```
Uniformes (12) · última hace 1 semana                       ← normal
Uniformes (12) · última hace 1 semana  ⚠ ropa 20 meses      ← con alerta
```

**Siempre arranca por el hecho llano** — cuándo recibió algo por última vez, sin importar
qué. **Y solo si algo cruzó el umbral de su rubro** se agrega la advertencia.

Las dos alternativas más simples se probaron y las dos mienten:

- **Una sola fecha global** esconde la ropa atrasada detrás de las reposiciones de EPP. Un
  cocinero con guantes nuevos de la semana pasada y chaqueta de hace 20 meses aparece como
  "última entrega: hace 1 semana" y se pasa de largo.
- **Solo el rubro más atrasado** hace lo inverso: muestra a alguien como abandonado cuando en
  realidad recibió cosas tres veces este año.

Cualquier número único que resuma algo multidimensional distorsiona para algún lado. La
salida es **mostrar el hecho y la excepción**, en una línea de altura fija sin importar
cuántos rubros existan.

El cálculo de meses transcurridos va **en el servidor**, no en el navegador: el reporte
necesita ordenar y filtrar por ese campo, y de paso se evita el problema de zona horaria
documentado en `web/static/js/date-utils.js`.

**El umbral pinta, nunca bloquea.** No impide registrar una entrega porque "todavía no
corresponde". Nada de reglas duras de fechas. El marco de dos uniformes por año es criterio
del usuario, no una regla del sistema.

---

## Reportes

Los tres que se pidieron —por empleado, por puesto, por período— **no son tres reportes: son
una consulta con cuatro filtros.**

| Filtro | |
|---|---|
| Empleado | uno, varios o todos |
| Cargo / departamento | **filtra por `cargo_id`**, no por el texto copiado |
| Período | rango de fechas libre |
| Rubro | ropa / EPP / todos |

Más un interruptor entre **detalle** (una fila por renglón) y **resumen** (agrupado por
elemento, con totales). Exportable a Excel con `openpyxl`, que ya se usa en la planilla
mensual ([api/asistencia_mensual.py:702](api/asistencia_mensual.py#L702)).

Así, además de los tres pedidos, sale gratis cualquier combinación: *"qué se les entregó a
los cocineros en el último trimestre"*, o *"cuántos pares de calzado salieron este año"* para
presupuestar compras.

El más útil de todos: **listado de empleados ordenado por antigüedad de la última entrega**,
filtrable por rubro y cargo. Arriba, quien hace más tiempo que no recibe nada.

> **Ojo con este:** los que **nunca** recibieron nada no tienen fila en
> `uniformes_movimientos` y son justamente los que tienen que estar arriba de todo. Tiene que
> ser un LEFT JOIN desde `empleados`, no un GROUP BY sobre movimientos.

Y el reporte de compra, que sale de la grilla de talles (ver sección Talles).

**Límite conocido:** estos reportes dicen **lo que sí se entregó, nunca lo que falta**. El
usuario lo confirmó y le sirve así.

---

## Permisos

Módulo `uniformes` en el grupo **Personal** de `MODULO_GRUPOS`. Reusa el vocabulario existente
de `ACCIONES` — **no hace falta agregar acciones nuevas**:

| Acción | Significa |
|---|---|
| `ver` | consultar movimientos, historial y talles |
| `editar` | registrar entregas, cargar talles y mantener los catálogos |
| `carga_inicial` | cargar entregas históricas desde el papel |
| `eliminar` | anular un movimiento emitido, con motivo |

Defaults propuestos: `sistema` todo; `rrhh` ver/editar/carga_inicial/eliminar;
`administracion` y `gerencia` solo ver.

**La anulación va a RRHH, no solo a `sistema`.** El que se equivoca emitiendo es RRHH, y a
los diez minutos quiere corregirlo; con el permiso restringido cada error es un llamado. Lo
que cubre el riesgo es la trazabilidad (`motivo_anulacion` obligatorio + `anulada_por` +
`anulada_en`), no la fricción.

---

## Archivos

**Nuevos**

- `db/uniformes_schema.py` — todo el `CREATE TABLE IF NOT EXISTS` del módulo
- `api/uniformes.py` — catálogos, talles, movimientos, anular, historial, reportes
- `web/templates/uniformes.html` — pantalla con pestañas
- `web/templates/uniformes_remito.html` — el imprimible, molde de `legajo_imprimible.html`
- `scripts/sembrar_uniformes.py` — catálogo inicial sugerido; idempotente, con simulación
- `scripts/cerrar_bajas.py` — cierre masivo de las bajas anteriores al módulo, para correr
  una vez al terminar la carga histórica
- `tests/` — diez pruebas, cada una sobre una copia temporal de la base

**Tocados — todo aditivo, ni un `ALTER TABLE` sobre tablas existentes**

| Archivo | Cambio |
|---|---|
| [db/database.py](db/database.py) | **1 línea**: `migrar_uniformes(conn)` al final de `_migrate()`, más las claves de empresa en el `INSERT OR IGNORE` de `configuracion` |
| [main.py](main.py) | 1 import, 1 `include_router`, las rutas `/uniformes` y `/uniformes/{id}/remito` |
| [auth/core.py](auth/core.py) | 4 entradas en listas (`MODULOS`, `MODULO_ACCIONES`, `MODULO_GRUPOS`, `PERMISOS_DEFAULT`) |
| [web/templates/index.html](web/templates/index.html) | 1 link en el nav |
| [web/templates/empleados.html](web/templates/empleados.html) | el bloque con el resumen, en la fila de la foto: **34 líneas agregadas, ninguna quitada** |

El schema va en archivo aparte y no en un bloque de 80 líneas dentro de `_migrate()` por una
razón concreta: con el módulo en una rama durante semanas, un bloque grande adentro de
`_migrate()` es un merge feo garantizado; una línea no genera conflicto.

### Claves nuevas en `configuracion`

**Nombre comercial y razón social son dos campos distintos.** `nombre_empresa` ya existe
([db/database.py:522](db/database.py#L522)) y es el nombre comercial que se muestra en el
login y en la barra de navegación — "Happening". La razón social es la persona jurídica —
"HAPPENING SA" — y es la que va en un documento legal. Reusar uno para el otro rompería el
nombre que ya se muestra en pantalla.

| Clave | Estado | Para qué |
|---|---|---|
| `nombre_empresa` | ya existe, **no se toca** | nombre comercial: login, nav, reportes |
| `empresa_razon_social` | nueva | razón social: constancia y legajo |
| `empresa_cuit` | nueva | |
| `empresa_direccion` | nueva | |
| `empresa_localidad` | nueva | |
| `empresa_cp` | nueva | |
| `empresa_provincia` | nueva | |

Más la bandera `uniformes_activo` (ver abajo).

Todas arrancan vacías y se cargan una vez desde **Configuración → Sistema**, ampliando la
sección *"Nombre y logo de la empresa"* que ya existe
([configuracion.html:253](web/templates/configuracion.html#L253)). Es lo que permite que las
dos instancias impriman su propio membrete corriendo el mismo código.

**Fallback:** si `empresa_razon_social` está vacía, la constancia imprime `nombre_empresa`,
para que la hoja nunca salga con el membrete en blanco.

De paso, estas claves le sirven al legajo imprimible, que hoy no tiene encabezado de empresa.

---

## Cómo se desarrolla sin tocar producción

Producción es `C:\SistemAlf`, un clon del repo; [scripts/update.bat](scripts/update.bat) hace
`git pull` y reinicia el servicio NSSM. **Lo que está en `main` está en producción en cuanto
alguien corre update.bat.** Todo lo demás se deduce de ahí.

Tres capas, las tres:

**1 — Rama `feat/entregas`.** Prod tira de main; mientras no se mergee, no existe. El patrón
ya se usa en el repo (`feat/turno-forzado-mes`).

**2 — Worktree.** El problema no es la rama, es que una carpeta no puede estar en dos ramas a
la vez: si aparece un bug urgente a mitad de camino, con una sola carpeta hay que stashear,
cambiar de rama, arreglar, volver. `git worktree` da una segunda carpeta con la otra rama,
compartiendo el mismo `.git`:

```
...\GitHub\sistemalf-asistencia    ← main, siempre limpio, para hotfixes
...\GitHub\sistemalf-entregas      ← feat/entregas, el trabajo nuevo
```

Bug urgente: se arregla en la carpeta de main, se pushea, update.bat en el server. La rama de
entregas ni se entera. La carpeta nueva necesita su propia `data/` (la DB está gitignoreada)
y conviene levantarla en otro puerto para correr las dos a la vez.

**3 — Bandera `uniformes_activo` en `configuracion`, default `'0'`.** Para el día del merge.
El sistema ya usa este patrón tres veces (`trapos_cocina_activo`, `vp_activo`,
`distribucion_reemplaza_planificacion`). Con la bandera en 0: el link no aparece en el nav,
las rutas devuelven 404 y el módulo no figura en la matriz de roles. Prod puede pullear main
sin que aparezca nada, y se prende desde Configuración cuando se quiera, sin reiniciar.

Y resuelve algo que la rama no resuelve: son **dos instancias**. Si una lo quiere y la otra
no, la bandera lo decide por base.

**La instancia de desarrollo nunca toca el reloj real.** La copia de la base trae la
configuración de producción, incluida la IP del ZKTeco y la limpieza automática de los días
1 y 15. Si una instancia de desarrollo corriera en la red del restaurante, sincronizaría los
fichajes a su copia, y en la pasada siguiente la limpieza vería «0 registros nuevos» y
borraría el reloj: producción perdería lo que todavía no había bajado. `iniciar-dev.ps1`
fuerza `device_ip=127.0.0.1` y la limpieza apagada **en cada arranque**, así sobrevive a
volver a copiar la base. Se descubrió porque la instancia de desarrollo quedó corriendo un
fin de semana e intentó sincronizar con el reloj 408 veces (todas fallaron: esa PC no
llega al reloj).

---

## Orden de trabajo

Tandas verificables, no todo de una:

**0 — La maqueta del remito.** `uniformes_remito.html` con datos escritos a mano adentro del
HTML. Sin base, sin API, sin nada. Imprimirlo en A4 y ponerlo al lado de la planilla real.
Mirando una foto de la planilla ya aparecieron tres cosas que faltaban en el modelo (el DNI,
los dos recuadros de texto y que no hay columna de talle); con la hoja impresa al lado
aparecen las que queden. Cuesta una hora y evita descubrir en la semana 3 que falta un campo,
con tablas, API y pantalla ya armadas alrededor del modelo equivocado.

**1 — Tablas y catálogos.** Rubros, tipos de talle, valores de talle y elementos. Con eso se
cargan los artículos reales y se ve si el modelo aguanta antes de seguir.

**2 — Talles por empleado.** La grilla editable con el recuento al pie. Es útil sola, aunque
todavía no haya ni una entrega cargada.

**3 — Entregas.** Alta, anulación y carga histórica: el circuito completo por API y pantalla.

**4 — El remito imprimible real.** El ajuste fino contra la planilla, ya con datos de verdad.

**5 — El botón y el modal en la ficha del empleado, y los reportes.**

---

## Fuera de alcance

- **Devolución** — columna `tipo` preparada, funcionalidad para más adelante
- **Vida útil / alertas por elemento** — hoy la alerta es por rubro, que alcanza
- **Hoja acumulativa por empleado** con el formato del papel actual (ver "Lo que la planilla
  enseñó": no se puede reimprimir sin perder las firmas)
- **Escaneo del remito firmado** adjunto al movimiento — la infraestructura de subida de fotos
  por celular con PIN ya existe y se podría reusar
- **EPP requerido por puesto** — decidido no hacerlo, no es un pendiente

## Sin decidir

- **Los 10 empleados de tipo `parking` no tienen cargo.** Son tercerizados que fichan y
  que **sí reciben ropa**, así que quedan dentro del módulo — se los excluye por `tipo` en
  asistencia, calendarios y vacaciones, pero no acá. Consecuencia: su constancia va a
  imprimir **`PUESTO` en blanco**, y no aparecen al filtrar por cargo ni en el reporte de
  compra agrupado por cargo.
  Se arregla creando un cargo (*Valet*, o como los llamen) y asignándoselo: es un cambio de
  **datos, no de código**, y se verificó que un cargo sin departamento y con `aplica_premio`
  en 0 no los mete en ningún otro módulo. **Se decide en producción**, no en la copia de
  desarrollo, para no dar por sentado allá algo que no existe. El filtro
  *«— Sin cargo asignado —»* de la grilla de talles es lo que permite encontrarlos.

- Si el **legajo imprimible** (`legajo_imprimible.html`) debe incluir el historial de
  entregas. Esa hoja tiene layout cerrado de una página y una lista de largo variable la
  desarma; si se quiere, conviene una **hoja anexa** que se imprima solo si hay entregas, no
  meterlo en la hoja actual.
- Los nombres definitivos de los rubros iniciales y sus `meses_alerta`.
