# Puente compartido de SAP2000 (SAP2000Bridge)

Un único puente local (`SAP2000Bridge.exe`, `127.0.0.1:8765`) sirve a tres aplicaciones web independientes, que se abren por separado en el navegador:

| Aplicación | Origen (web alojada) | Qué usa del puente |
|---|---|---|
| Engineering Workbench | `https://hosted.invalid` | todas las acciones actuales (`/v1/sap/*` y `/v1/sap/actions/*`) |
| Engineering Workbench (despliegue fusion) | `https://fusion.invalid` | las mismas que el Workbench (sin alcance acotado) |
| SAP Graph | `https://sap-graph.invalid` | `status`, `attach`, `actions/graph/*` (solo lectura) |
| SAP2000 Viewer | `https://viewer.invalid` | `status`, `attach`, `actions/viewer/*` (lectura y factores K con confirmación) |
| Placa Base | `https://placa-base.invalid` | `status`, `attach`, `actions/placa-base/*` (cargas, nodos, reacciones, columna y resaltado; sólo cambia la selección de salida y de nodos) |

Objetivo: **eliminar el puente propio de SAP Graph (puerto 8767) y el del Viewer (puerto 8765, que chocaba con este)**. Queda un solo ejecutable, un solo emparejamiento por aplicación y un solo lugar donde arreglar las particularidades de la API COM.

## Decisiones de diseño

1. **Rutas con espacio de nombres.** Las rutas existentes del Workbench no cambian. SAP Graph y Viewer usan `/v1/sap/actions/graph/*` y `/v1/sap/actions/viewer/*`, que se añaden sin tocar el contrato de las demás. Se mantienen `GET /v1/sap/status` y `POST /v1/sap/attach`, comunes.
2. **Emparejamiento por origen y con alcance.** El secreto emitido queda ligado al origen que lo pidió (ya se guarda `origin` en `pairings.json`). Un secreto sólo vale si la petición llega con ese mismo `Origin` (una petición sin cabecera `Origin`, es decir, un cliente que no es un navegador, se admite, pero el alcance de rutas se aplica igual según el origen emparejado) y sólo para las rutas de su alcance:
   - Workbench: todo lo actual.
   - SAP Graph: `status`, `attach`, `actions/graph/*`.
   - Viewer: `status`, `attach`, `actions/viewer/*`.
   - Placa Base: `status`, `attach`, `actions/placa-base/*`.
   Así una aplicación de sólo lectura no puede escribir en el modelo aunque su token se filtre.
3. **Orígenes.** Los tres orígenes alojados van de fábrica, más los de desarrollo (`localhost:5173`, `127.0.0.1:5173`). El usuario puede añadir otros en `origins.json` o con `SAP2000_AGENT_ORIGINS`.
4. **Orden del código de retorno COM.** Cada módulo portado usa la convención con la que se escribió (SAP Graph: código primero; Viewer: se verifica al portar). Un normalizador por sesión (`bridge/com_orden.py`) determina una sola vez el orden real con `GetModelFilename` y adapta las tuplas a la convención del módulo, sin heurísticas por llamada.
5. **Seguridad heredada.** Sólo loopback; token con caducidad (12 h); revocación; ventana local de confirmación del código; sin escritura salvo los factores K del Viewer, que mantienen preview con nonce, copia de seguridad, relectura y auditoría.
6. **Puerto.** 8765 para todos. SAP Graph cambia su puerto por defecto (8767 → 8765). Los modos «local» de las aplicaciones (servir su web desde el propio puente) desaparecen; para desarrollo, la web corre en `localhost:5173` y llama al puente compartido.

## Contrato nuevo

### SAP Graph — `GET /v1/sap/actions/graph/model`
Respuesta `{model, units, warnings, read_ms}` con la misma forma que hoy devuelve `GET /v1/sap/model` del puente propio de SAP Graph (`points`, `frames`, `shells`, `materials`, `frame_sections`, `shell_sections`, `load_patterns`). Mismos códigos: 409 sin conexión, 504 a los 60 s, 413 sobre 40 MB.

### Viewer — `/v1/sap/actions/viewer/*`
| Método | Ruta | Equivale a (puente actual del Viewer) |
|---|---|---|
| GET | `snapshot` | `GET /api/snapshot` (JSON `schemaVersion:1`) |
| POST | `connect` | `POST /api/connect` (`demo` queda sólo en la web) |
| POST | `refresh` | `POST /api/refresh` |
| GET | `status` | `GET /api/status` |
| GET | `results/cases` | `GET /api/results/cases` |
| GET | `results/design` | `GET /api/results/design` |
| GET | `results/area?case&component` | `GET /api/results/area` |
| GET | `results/displacements?case` | `GET /api/results/displacements` |
| GET | `results/frame/{frame}?case` | `GET /api/results/frame/{frame}` |
| GET | `k` y `k/diagnose` | `GET /api/k`, `/api/k/diagnose` (cada barra de `k`: 4 K + «L Major», «L Minor», «L LTB» opcionales, misma forma `{value, auto}`; sin columnas L, solo las 4 K y una nota; `auto` => `value` null; ratio <= 0 o no finito => sin dato + nota; sin tope superior; `POST k/preview` y `k/apply` también escriben L (número positivo finito o `auto`, mezclable con K; columna L ausente => `409 column_missing`, sin escribir); nombres de columna L y su escritura sin verificar contra SAP2000 real) |
| GET | `k/design` | `GET /api/k/design` (solo lectura; K1/K2 Major/Minor, K LTB y L Major/Minor/LTB CALCULADOS por el diseño, `{value}` por barra de la fila gobernante + `governing` {combo, station, ratio}; sin diseño ejecutado `available:false` + `reason`, no error; tabla «Steel Details 2 - PMM Details - AISC 360-16» vista en SAP2000 real; columnas de barra/combinación/estación/ratio sin verificar. KLTB solo aquí, no en `k`; en la tabla de overwrites real 0 = Program Determined (auto) y las L son XLMajor/XLMinor/XLLTB) |
| GET | `k/effective` | `GET /api/k/effective` (solo lectura; no cambia `k` ni `k/design`). `{available, reason, tableKey (overwrites), designTableKey, notes, frames:{barra:{campo:{value, source:"overwrite"\|"design"\|"none", overwriteAuto:bool\|null, designValue}}}, governing:{barra:{combo, station, ratio, designSect, rows}}}`, campos K1/K2 Major/Minor, K LTB, L Major/Minor/LTB. Overwrite numérico manda; automático + diseño ejecutado -> valor de diseño (`source:"design"`); automático sin diseño -> `none` + nota «ejecute el diseño de acero»; sin `k` pero con diseño -> `design` y `overwriteAuto:null`; nada disponible -> `available:false` (HTTP 200). El K de diseño es el de la fila gobernante, y el 0 como automático es hipótesis por tabla; tras cambiar overwrites el diseño queda obsoleto (`none`) |
| POST | `k/preview` | `POST /api/k/preview` (nonce de un solo uso, 300 s) |
| POST | `k/apply` | `POST /api/k/apply` (copia de seguridad, relectura, auditoría) |

### Viewer — snapshot ampliado (cargas por componente y combinaciones)
`GET viewer/snapshot` (y `connect`/`refresh`, misma forma) añade `combinations`, `combinationsTruncated` y `loadsTruncated`, y en `loads` añade entradas por componente global (`component` = `Fx`…`Mz`, `direction` = eje unitario, `value` con signo) junto a las entradas antiguas, que no cambian. Todos los patrones, sin filtrar por el seleccionado. Política de compatibilidad, unidades, límites (200 000 cargas; 5 000 combinaciones y 500 patrones por combinación) y reglas de ejes: en `docs/SAP_ACTIONS.md`, sección «Snapshot del Viewer». El visor consume el contrato según `docs/WEB_VIEWER.md` del repositorio SAP2000_AI_ASSISTANT. Solo lectura; validado con COM falso, pendiente de SAP2000 real (firma de `RespCombo.GetCaseList` y lectura por lote de cargas).

**Matiz (patrones para mostrar).** El snapshot del Viewer deja de ser estrictamente de solo lectura: durante las lecturas por lote de cargas escribe temporalmente la selección de load patterns «para mostrar» (`DatabaseTables.SetLoadPatternsSelectedForDisplay`) para que SAP2000 devuelva todos los patrones, y la restaura siempre al terminar, también si la lectura falla. No cambia cargas, unidades ni resultados. Si la API no está disponible o el modelo la rechaza, la lectura sigue con la selección actual y el snapshot lleva un aviso «Patrones para mostrar» en `warnings`. Motivo, detalle y firmas sin verificar: `docs/SAP_ACTIONS.md`, sección «Snapshot del Viewer». Pendiente de SAP2000 real.

### Documentos Word locales (no es SAP)

Las rutas `/v1/documentos/*` no tocan COM: guardan el informe Word en el PC del usuario (el .docx, su manifiesto y la carpeta `<nombre>_reporte/`). Usan la misma autenticación, Host y Origin que el resto del puente y el alcance por origen de `bridge/alcances.py`: la web alojada (`HOSTED_ORIGIN`) tiene acceso completo; las aplicaciones compartidas (SAP Graph, Viewer, Placa Base) reciben 403. Detalle, contrato JSON y seguridad en `docs/AYUDANTE_LOCAL_DOCUMENTOS.md`.

| Ruta | Efecto |
|---|---|
| `POST /v1/documentos/pick` | Diálogo nativo para elegir un .docx; registra el `doc_id`. |
| `GET /v1/documentos/list` | Documentos conocidos, con `available`. |
| `POST /v1/documentos/prepare` | Revisa el .docx y crea el manifiesto junto a él si no existe. |
| `GET /v1/documentos/positions`, `GET /v1/documentos/manifest` | Posiciones de anclaje y manifiesto. |
| `POST /v1/documentos/anchors`, `DELETE /v1/documentos/anchors/{tag}` | Instalar o quitar una sección. |
| `POST /v1/documentos/sync` | Sincronizar (`dry_run`, `force`). |
| `POST /v1/documentos/export-module` | Desempaqueta el paquete de un módulo en `<nombre>_reporte/<slug>/`. |
| `POST /v1/documentos/open-in-word` | Abre el .docx registrado con el programa asociado. |
| `GET /v1/documentos/storage` | Rutas reales: Word, manifiesto, carpeta `_reporte`, respaldos y registros. |
| `GET /v1/documentos/backup` | ZIP con el .docx y su manifiesto, para la copia de respaldo en el servidor (sólo lectura). |

El prefijo es `/v1/documentos/` y no `/v1/sap/actions/`: este servicio no es SAP. Cuenta, en cambio, con el mismo `require_token` que el resto del puente.

## Convención de trabajo

**Los workflows de GitHub Actions (compilar el puente, CI manual, etc.) los ejecuta siempre el usuario a mano.** Quien trabaje en este repositorio (persona o sesión de Claude) no debe lanzarlos por su cuenta: avisa de que hace falta una ejecución y de la rama o `main` sobre la que debe correr, y espera a que el usuario la lance y diga el resultado. Lo mismo vale para las aplicaciones que usan este puente (SAP Graph y el Viewer).

## Estado de la implementación

| Id | Estado |
|---|---|
| B1 orígenes de fábrica | hecho (`bridge/hosted.py`) |
| B2 emparejamiento ligado al origen con alcance de rutas | hecho (`bridge/alcances.py`, `bridge/sap2000_agent.py`; pruebas en `tests/test_bridge_alcances.py`) |
| B3 normalizador del orden de retorno | hecho (`bridge/com_orden.py`) |
| B4 lector de SAP Graph | hecho (`bridge/graph.py`, `GET /v1/sap/actions/graph/model`) |
| B5 núcleo del Viewer | hecho (`bridge/viewer/`: copia con sólo los imports reescritos, `routes.py` con 13 rutas) |
| B6 pruebas y documentación | hecho (734 pruebas de puente; este documento, `SAP_ACTIONS.md`, `SAP2000_ACCEPTANCE.md`) |
| B7 reconstruir y validar con SAP2000 real | pendiente (usuario) |
| B8 documentos Word locales (`/v1/documentos/*`, no es SAP) | hecho (`bridge/documentos_locales.py`; pruebas en `tests/test_bridge_documentos_locales.py`); contrato y ejemplos en `docs/AYUDANTE_LOCAL_DOCUMENTOS.md` |

### Decisiones que surgieron al implementar

- **El núcleo del Viewer se conserva con comtypes.** Se escribió y probó con comtypes (código de retorno al final) y su `LocalBridge` tiene su propio hilo COM. Reescribirlo para pywin32 habría cambiado miles de líneas sin poder validarlas con SAP2000 real; por eso `bridge/viewer/` lleva su propio conector y el `.exe` incluye `comtypes`. El resto de acciones del puente sigue usando pywin32. Unificar los dos enlaces es posible más adelante, cuando haya validación real.
- **El lector de SAP Graph se ejecuta en el hilo COM del puente (pywin32)** y el normalizador lo adapta al orden que espera (código primero).
- **Tiempo límite por acción.** El hilo COM esperaba 30 s; las lecturas completas de modelo usan hasta 60 s (`bridge/com_tiempo.py`).
- **`X-Snapshot-Stale` expuesta por CORS** para que el navegador del Viewer pueda leerla.
- **Datos del Viewer en la carpeta del usuario.** Copias de seguridad de K, auditoría y alias de tablas van a `%LOCALAPPDATA%\EngineeringWorkbench` (el original escribía junto al código).
- **Desviaciones respecto al Viewer original** (documentadas también en `docs/MODULES_SOURCE.md`): sin modo demo en el puente (queda en la web), sin `model_writer.py`, sin comprobaciones propias de Host/Origin (las hace el puente compartido), tests de creación de combinaciones, cortes de sección y escritura de K con `model_writer` no portados.

## Plan de trabajo

### A. Puente (este repositorio)
| Id | Tarea | Notas |
|---|---|---|
| B1 | Orígenes de fábrica de SAP Graph y Viewer + `origins.json` documentado | trivial |
| B2 | Emparejamiento con alcance por origen (`bridge/pairing.py`, `sap2000_agent.py`) | rutas protegidas: autorización dada |
| B3 | Normalizador `bridge/com_orden.py` | prueba con dobles en ambos órdenes |
| B4 | `bridge/graph.py`: portar `ModelReader.read_model` (≈600 líneas, `app/sap/reader.py`) y exponer `actions/graph/model` | sólo lectura |
| B5 | `bridge/viewer/`: portar `sap2000/` (conector, lectores, `steel_k`, `backup`, `results_views`, `units`) y `web_bridge/` (`snapshot`, `results`, `sections`, `k_factors`) | ≈6000 líneas; paridad de lógica; no se porta `model_writer.py` |
| B6 | Pruebas de contrato y de ambos órdenes de retorno; `docs/SAP_ACTIONS.md`; `docs/SAP2000_ACCEPTANCE.md` | |
| B7 | Reconstruir el puente (workflow) y validar con SAP2000 real | usuario |

### B. Cambios en cada aplicación (los ejecutan sus sesiones)
Ver los dos mensajes preparados: `instrucciones_sap_graph.md` e `instrucciones_viewer.md`.

### C. Riesgos
- El código de lectura de ambas aplicaciones **nunca se validó con SAP2000 real** (lo dicen sus propios documentos). Al unificarlos hay que validarlo una vez, en el puente compartido.
- Cambiar el puerto o el contrato de una aplicación obliga a actualizar a la vez web y puente; se usa `contract` en `/v1/sap/status` para avisar «su puente es anterior».
- El `.exe` es uno: quien lo instale debe tener la versión que incluye ambas familias de acciones.
- Dos puentes en 8765 no pueden convivir; durante la transición el Viewer debe dejar de lanzar el suyo.

- **comtypes dentro de un ejecutable (PyInstaller):** `comtypes.client.GetModule` genera módulos en `comtypes.gen` en tiempo de ejecución; en un `.exe` de un solo archivo esa carpeta es temporal. Es el primer punto a revisar si el Viewer no conecta con el `.exe` pero sí desde Python.
- **Dos enlaces COM en el mismo proceso** (pywin32 para el resto de acciones, comtypes para el Viewer), cada uno con su hilo de apartamento. Es válido, pero un fallo de inicialización se ve como «no se pudo conectar» en una familia y no en la otra.

### D. Orden recomendado
B1 → B2 → B3 → B4 (SAP Graph puede migrar ya) → B5 (Viewer migra después) → B6 → B7.

## CAESAR II → SAP2000: transferencia de cargas nodales (validado con COM falso; pendiente de SAP2000 real)

Módulo nuevo del puente (`bridge/caesar_cargas.py`, rutas `/v1/sap/actions/caesar/*`, mismo envoltorio `call` y mismo actor COM). El detalle (cuerpos, respuestas, errores y límites) está en `docs/SAP_ACTIONS.md`.

| Método | Ruta | Escribe |
|---|---|---|
| GET | `/v1/sap/actions/caesar/modelo` | no: nudos, patrones con tipo, unidades y bloqueo |
| POST | `/v1/sap/actions/caesar/cargas/preview` | no: qué se crearía y qué se aplicaría |
| POST | `/v1/sap/actions/caesar/cargas/apply` | sí: crea los patrones que faltan y aplica las cargas nodales |
| GET | `/v1/sap/actions/caesar/seleccion` | no: puntos seleccionados ahora en SAP2000, con los que son nº de nodo |
| POST | `/v1/sap/actions/caesar/cargas/existentes` | no: FX, FY y FZ que ya hay en esos nodos y patrones |

Decisiones:

* **Solo fuerzas.** Se transfieren FX, FY y FZ; los momentos se envían a cero y el cuerpo no admite `mx`, `my` ni `mz`.
* **Unidades set-apply-restore.** Se fijan las unidades del envío solo mientras dura la escritura y se restauran siempre en `finally`. Solo se admiten las 16 combinaciones fuerza-longitud de la tabla eUnits.
* **Modelo bloqueado.** `apply` responde 409 `model_locked` y no escribe, salvo `confirmarBloqueo=true`, que desbloquea y BORRA los resultados del análisis.
* **Sin reversión automática.** Un nodo inexistente o una carga rechazada van a `fallidas` y no abortan; un error a mitad responde 409 `partial` con lo aplicado.
* **Tipos de patrón verificados.** Solo `Dead` (1) y `Other` (8), los códigos de la sonda. Los demás se añadirán al comprobarlos en SAP2000 real.
* **Lecturas auxiliares, sin escritura.** `seleccion` lee la selección de puntos sin cambiarla. `cargas/existentes` lee las cargas actuales por objeto (no por tabla), con unidades set-apply-restore; no cambia los patrones mostrados ni escribe en el modelo.

Estado: validado con COM falso (`tests/test_bridge_caesar_cargas.py`). **La prueba con un modelo real de SAP2000 queda pendiente**, junto con las firmas COM que la lista de `docs/SAP_ACTIONS.md` marca como no comprobadas.
