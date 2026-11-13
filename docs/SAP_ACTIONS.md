# Acciones SAP2000: Perfiles, Combinaciones y Espectro

Las rutas viven exclusivamente en el agente local de SAP2000 y usan su actor STA. Requieren el mismo `Authorization: Bearer` que las rutas restantes del agente.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/profiles/catalog` | Lee `PropMaterial.GetNameList` y `PropFrame.GetNameList`. |
| `GET /v1/sap/actions/profiles/selected-frames` | Lee la selección (`SelectObj.GetSelected`) y `FrameObj.GetSection` de los frames. |
| `GET /v1/sap/actions/profiles/selected-frame-properties` | Además de frame/sección, lee dimensiones en mm, longitud en mm y (si es posible) Fy de los frames seleccionados, para importarlos a Esbelteces. |
| `POST /v1/sap/actions/profiles/create/preview` | Valida una nueva sección geométrica en mm. |
| `POST /v1/sap/actions/profiles/create/apply` | Con `confirmed: true`, crea I/Channel/Angle/Tube sin reemplazar nombres existentes. |
| `POST /v1/sap/actions/profiles/section/preview` | Valida y devuelve el cambio propuesto. |
| `POST /v1/sap/actions/profiles/section/apply` | Con `confirmed: true`, asigna una sección existente mediante `FrameObj.SetSection`. |
| `POST /v1/sap/actions/combinations/preview` | Valida y enumera combinaciones a crear. |
| `POST /v1/sap/actions/combinations/apply` | Con `confirmed: true`, usa `RespCombo.Add` y `SetCaseList`. |
| `POST /v1/sap/actions/spectrum/preview` | Valida puntos y muestra la función propuesta. |
| `POST /v1/sap/actions/spectrum/apply` | Con `confirmed: true`, usa `Func.FuncRS.SetUser`. |
| `POST /v1/sap/pairing/request` | Sin token: exenta del middleware. Lee el header `Origin`; crea una solicitud de emparejamiento pendiente y dispara el aviso en la bandeja del sistema. |
| `GET /v1/sap/pairing/poll/{request_id}` | Sin token: exenta del middleware. La web hace polling hasta recibir el resultado de la solicitud de emparejamiento anterior. |

Las rutas `preview` no llaman a COM ni mutan el modelo. Las rutas `apply` rechazan `confirmed: false`, nombres inválidos, listas vacías, factores no finitos, períodos no crecientes, secciones fuera del catálogo y nombres de combinación/función existentes. Por tanto no reemplazan definiciones existentes.

Si falla la creación de una combinación después de `Add`, el agente llama `RespCombo.Delete` para cada combinación creada en esa solicitud cuando la API expone el método. El perfil no se revierte: SAP2000 no aporta una transacción de asignación y una reversión podría perder una selección automática previa.

Los payloads de mutación no aceptan rutas de archivos ni comandos. Los nombres tienen máximo 128 caracteres y las listas, 500 elementos.


## Contrato de transporte

Todas las rutas se invocan directamente contra el agente local configurado, por ejemplo `http://127.0.0.1:8765`, con `Authorization: Bearer <SAP2000_AGENT_TOKEN>` y JSON. `OPTIONS` está disponible para los orígenes de `SAP2000_AGENT_ORIGINS` y no requiere token; toda solicitud real sí lo requiere, **salvo** las dos rutas de emparejamiento descritas abajo, que son la excepción explícita. Errores de validación y confirmación devuelven `422`; no hay modelo COM activo devuelve `409`; el agente ejecutado fuera de Windows devuelve `503` con el detalle explícito de que SAP2000 COM requiere Windows.

`POST /v1/sap/pairing/request` no recibe cuerpo ni token: en su lugar lee el header `Origin` de la petición y lo rechaza con `403` si no está en `SAP2000_AGENT_ORIGINS` (la misma lista que ya filtra CORS). Si lo acepta, crea una solicitud pendiente (vence a los 120 s) y devuelve `{ "request_id": "…", "expires_in": 120 }`; el aviso correspondiente aparece en la bandeja del sistema del equipo donde corre el puente, mostrando el `Origin` recibido tal cual. Como mucho una solicitud pendiente por origen (una segunda del mismo origen sustituye a la anterior, que deja de ser consultable por su id viejo) y como mucho 5 pendientes en total entre todos los orígenes; superar ese cupo responde `429`.

`GET /v1/sap/pairing/poll/{request_id}` tampoco recibe token: la web hace polling (~1.5 s) sobre esta ruta. Devuelve `404` si el `request_id` no existe: nunca existió, o una solicitud posterior del mismo origen la reemplazó (vencer por sí solo NO la retira de memoria; una solicitud vencida sigue existiendo indefinidamente con `status: "expired"`, no hay purga automática). Mientras exista, devuelve `{ "status": "pending" }`, `{ "status": "denied" }` o `{ "status": "expired" }` según la persona haya aceptado, rechazado, o no haya respondido a tiempo en la bandeja. Sólo la primera consulta posterior a la aceptación trae el secreto: `{ "status": "approved", "secret": "…", "url": "http://127.0.0.1:8765" }`; en polls siguientes ya sólo llega `{ "status": "approved" }`, sin repetir el secreto. La aprobación o el rechazo ocurren dentro del propio proceso del puente (la bandeja llama directamente a las funciones de aprobación); no hay ningún endpoint HTTP para aprobar una solicitud desde fuera.

`POST /v1/sap/actions/profiles/section/preview`: `{ "frames": ["F1"], "section": "W310" }` devuelve `{ "preview": true, "section": "W310", "frames": ["F1"], "will_mutate": false }`. El mismo cuerpo en `/apply` exige `{ "confirmed": true }` y devuelve `{ "section": "W310", "assigned_frames": ["F1"] }`.

`POST /v1/sap/actions/combinations/preview` y `/apply` reciben `{ "combinations": [{ "name": "ULS1", "combo_type": 0, "items": [{ "kind": 0, "name": "DEAD", "factor": 1.4 }] }], "confirmed": true }`; `/preview` no exige ni usa `confirmed` y `/apply` devuelve `{ "created": ["ULS1"] }`. `kind` es `0` para caso de carga y `1` para combinación.

`POST /v1/sap/actions/spectrum/preview` y `/apply` reciben `{ "name": "RS_X", "points": [{ "period": 0, "value": 0.4 }, { "period": 1, "value": 0.2 }], "damping": 0.05, "confirmed": true }`; `/apply` devuelve `{ "created": "RS_X", "point_count": 2, "damping": 0.05 }`.

La extracción `GET /v1/sap/results/reactions` lee exclusivamente la selección de salida que ya está activa en SAP2000 (`eItemTypeElm.SelectionElm = 3`). El parámetro `load_case` no está disponible porque filtrarlo implicaría cambiar esa selección; el agente devuelve `409` explicándolo.


`GET /v1/sap/actions/profiles/selected-frame-properties` no recibe cuerpo y solo lee: no requiere `confirmed` ni tiene ruta `apply`. Devuelve `{ "frames": [ { "frame": "F1", "section": "W310", "section_type": "I" | "Channel" | "Angle" | "Tube" | null, "h_mm", "b_mm", "tf_mm", "tw_mm": number | null, "fy_mpa": null, "length_mm": number, "note": string | null } ] }`. Sin selección responde `{ "frames": [] }`. La longitud usa dos llamadas verificadas contra la referencia OAPI: `FrameObj.GetPoints` para los nombres de los puntos extremos y `PointObj.GetCoordCartesian` para sus coordenadas; la distancia entre ambos extremos, con las unidades presentes en `eUnits.N_mm_C = 9`, da `length_mm`. Las dimensiones usan `PropFrame.GetISection`/`GetChannel`/`GetAngle`/`GetTube`, firmas de lectura **derivadas** de los `Set*` que ya usa `create_section` y **no verificadas** contra una instancia real de SAP2000: se intenta cada una en orden y se conserva la primera que SAP2000 acepta (código de retorno 0) como familia de la sección. Una sección de un tipo no reconocido no aborta la solicitud: ese frame vuelve con `h_mm`/`b_mm`/`tf_mm`/`tw_mm` en `null` y `note` explicando por qué. `fy_mpa` siempre viaja en `null`: este repositorio no tiene ninguna llamada existente para leer con seguridad el Fy del material de la sección, así que el agente no inventa ese valor — la interfaz debe permitir completarlo a mano. Como en `create_section`, las unidades presentes se restauran en un `finally` aunque la lectura falle a mitad, y un código de retorno distinto de 0 en `SelectObj.GetSelected`, `FrameObj.GetSection` o en la lectura de longitud se convierte en error (409), nunca en un dato silenciosamente incorrecto.

`POST /v1/sap/actions/profiles/create/{preview,apply}` recibe `{ "name": "I-300", "section_type": "I", "material": "A36", "depth": 300, "width": 150, "tw": 8, "tf": 12, "confirmed": true }`. Las dimensiones son milímetros. El agente usa `eUnits.N_mm_C = 9`, llama respectivamente a `SetISection`, `SetChannel`, `SetAngle` o `SetTube` con la firma original y restituye las unidades previamente activas. Rechaza materiales y nombres de propiedad inexistentes/existentes antes de mutar. `/apply` devuelve `created`, `section_type`, `material`, `units` y `dimensions_mm`.

Una asignación de sección que falle después de asignar otros frames responde `409` y conserva el estado parcial real en `detail`: `{ "section", "assigned_frames", "failed_frame", "error", "partial": true }`. No intenta una reversión insegura.

La curva de espectro conserva la firma original `Func.FuncRS.SetUser(name, count, periods, values, damping)`. Los valores se envían como ordenadas espectrales en `g`; no se convierten con las unidades presentes del modelo.

## Hormigón simple

Puerto literal de la parte en vivo de `sap2000_io.py` (proyecto original, `hormigon-simple`): `Sap2000Live.list_frames`, `list_combos`, `frame_geometry` (longitud y sección) y `frame_forces` más `build_demand`, instaladas bajo `/v1/sap/actions/hormigon` (prefijo de protocolo SAP2000; las rutas equivalentes para archivos viven en `/hormigon/sap/...` del router web). Sin conexión COM propia: cada operación recibe el `SapModel` ya conectado a través del actor STA de la sesión, con el mismo envoltorio `call` y autenticación por token que `bridge/diagrama.py`.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/hormigon/frames` | Extrae todos los Frame objects (`FrameObj.GetNameList`) con longitud (m) y sección. Devuelve `{ "frames": [{ "nombre", "longitud_m", "seccion" }] }`. Solo lee. |
| `GET /v1/sap/actions/hormigon/combos` | Combinaciones + Load Cases (`RespCombo.GetNameList`, `LoadCases.GetNameList`, deduplicado). Devuelve `{ "combos": [nombre, ...] }`. Solo lee. |
| `POST /v1/sap/actions/hormigon/frame-forces` | `{ "frame": "F1", "combos": ["C1", "C2"] }`. Lee `Results.FrameForce` del frame con las unidades fijadas a tonf-m y devuelve **datos crudos**, sin envolvente ni inversión de M3: `{ "frame", "longitud_m", "unidades": {"fuerza": "tonf", "longitud": "m"}, "combos", "filas": { "estacion_m", "caso", "paso", "P", "V2", "M3" } }` (columnas paralelas). La demanda (`DemandaViga`) la arma el servidor con `POST /hormigon/sap/demanda-multi-filas`. Solo lee. Sustituye a `frame-demand` y `frame-demand-multi` (retiradas; la web aún usa `frame-demand-multi` como respaldo con un puente antiguo). |

Forma de `demanda` en `POST /hormigon/sap/demanda-multi-filas` (servidor) (`DemandaViga` del frontend): `xs` (m desde el origen del frame), `mu_pos` y `mu_neg` (tonf·m, tracción en fibra inferior y superior), `vu_max` (tonf, máximo \|Vu\| por estación), `source` y `cases` (combinaciones usadas).

**Unidades y comportamiento**:
- Lectura: fuerzas forzadas a `tonf` (eUnits = 12 = tonf-m), longitudes en m; restauradas siempre al terminar (try/finally).
- El cortante se reduce a máximo |Vu| por estación; `invertir_m3` invierte el signo de M3 para vigas con eje local invertido.
- `combos` debe contener entre 1 y 50 combinaciones (422 si no).
- `frame` debe existir (409 si no existe, con mensaje "El frame '<nombre>' no existe en el modelo SAP2000").
- Unidades: lectura en tonf-m, restauración al terminar incluso si falla a mitad.
- Errores de SAP2000 (código de retorno distinto de 0 en `FrameObj.GetNameList`, `FrameObj.GetPoints`, `PointObj.GetCoordCartesian`, etc.) se convierten en `409` con `detail = { "code": "sap_error", "message": "..." }`.

**Rutas web equivalentes para archivos**:
- `POST /hormigon/sap/frame-forces` (multipart: `archivo` CSV/XLSX con la tabla «Element Forces - Frames», `unidad_fuerza?`, `unidad_longitud?`): límites 10 MB, 200 000 filas, 50 casos. Devuelve `{ "frames": [{ "nombre", "longitud_m", "n_filas", "casos" }], "casos", "n_filas" }`.
- `POST /hormigon/sap/demanda` (multipart: `archivo`, `frame`, `casos?`, `invertir_m3?`, unidades): arma la envolvente con `sap2000_io.build_demand`. Devuelve `{ "demanda": { "xs", "mu_pos", "mu_neg", "vu_max", "source", "cases" }, "frame", "casos", "longitud_m" }`, igual que el puente en vivo.

## Esbelteces

Puerto fiel de `esb_app/sap/sap_client.py` (proyecto de escritorio original). Prefijo `/v1/sap/actions/esbelteces`, misma autenticación por token, mismo criterio `preview`/`apply`+`confirmed` y mismo mapeo de errores a HTTP que el resto del agente. `bridge/esbelteces.py` no mantiene estado propio: se instala desde `bridge.actions.install_routes` reusando su mismo envoltorio `call` (actor STA + traducción de excepciones), incluidas las que traen un `.detail` estructurado (p.ej. el código de diseño incorrecto de más abajo).

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/esbelteces/selected` | Extrae todos los frames seleccionados (`SelectObj.GetSelected`, tipo 2) con `PropFrame.GetTypeOAPI` + el `Get*` de cada familia, Fy (`PropMaterial.GetOSteel_1`), `GetSectProps` y longitud modelada. Un frame que falla se omite, no aborta la respuesta (igual que `ImportWorker`). |
| `GET /v1/sap/actions/esbelteces/groups` | `GroupDef.GetNameList`: todos los grupos definidos, sin filtrar por si tienen miembros. |
| `POST /v1/sap/actions/esbelteces/import-groups` | `{ "map": { "<categoría>": "<grupo SAP2000>", ... } }`. Para cada entrada con grupo no vacío, en el orden del mapa: `GroupDef.GetAssignments` (tipo 2) + extracción por frame, con `group_category` seteado a la categoría. Un frame que ya apareció en una categoría anterior se omite (primera categoría gana), igual que `ImportByGroupWorker`. Solo lee: no requiere `confirmed`. |
| `GET /v1/sap/actions/esbelteces/sections` | `PropFrame.GetNameList`: todas las secciones de frame del modelo (para el combo de reemplazo). |
| `POST /v1/sap/actions/esbelteces/section/apply` | `{ "frame": "F1", "section": "W310", "confirmed": true }`. `SetModelIsLocked(False)` (best-effort) + `FrameObj.SetSection` + re-extracción. Si la re-extracción falla, error `"No se pudo re-extraer datos de {frame} tras el cambio de sección."`. Devuelve `{ "member": <FrameSectionData> }`. |
| `POST /v1/sap/actions/esbelteces/highlight` | `{ "frames": ["F1", "F2"] }`. `SelectObj.ClearSelection()` + `FrameObj.SetSelected(name, True)` por cada frame. Devuelve `{ "count": n }`. Solo muta la selección visual de SAP2000, no requiere `confirmed`. |
| `POST /v1/sap/actions/esbelteces/k-factors/apply` | `{ "items": [{ "frame": "F1", "kx": 1.0, "ky": 1.0 }, ...], "confirmed": true }`. Escribe Kx/Ky como Design Overwrite AISC 360-16 (`DesignSteel.AISC360_16.SetOverwrite`, item 23 = K2 Major, item 24 = K2 Minor); un `kx`/`ky` de `0` no se escribe para ese eje. |

`FrameSectionData` (la forma que devuelven `selected`, `import-groups` y `section/apply`) tiene los mismos campos que el dataclass original: `frame_name`, `section_name`, `prop_type`, `prop_type_name`, `h_mm`, `b_mm`, `tf_mm`, `tw_mm`, `material_name`, `fy_mpa`, `rx_mm`, `ry_mm`, `area_mm2`, `i33_mm4`, `i22_mm4`, `length_mm`, `rz_mm`, `is_rz_calculated`, `is_user_defined`, `notes`, `group_category`. Para ángulos, `rz_mm` se recalcula geométricamente (el motor de cálculo del Workbench::section_props`, misma lógica que el original) y **sustituye** a `ry_mm` (el `ry` que entrega SAP2000 es respecto al eje principal rotado, no al eje geométrico débil); si difiere más de 2% del `ry` de SAP2000, se agrega una nota explicándolo.

`import-groups` no valida que las categorías del mapa sean una de las 6 conocidas por el motor de cálculo (`Diagonal_H`, `Diagonal_V`, `Columna_SCBF`, `Viga_SCBF`, `Columna_SMF`, `Viga_SMF`): la clave viaja tal cual a `group_category`, y es el llamador quien decide qué categorías ofrecer (ver `GET /esbelteces/options` en el backend de cálculo).

`k-factors/apply` primero lee `DesignSteel.GetCode("")` y normaliza (mayúsculas, sin espacios/guiones/barras); si no empieza con `AISC36016`, responde `409` con `{ "code": "design_code", "message": "El código de diseño de acero activo en SAP2000 es '<code>', no AISC 360-16. No se escribió ningún valor." }` **sin escribir nada** (ni siquiera para los frames anteriores en la lista) — los índices de Item 23/24 verificados son específicos de ese código de diseño. Una vez pasada esa validación, cada frame se aplica independientemente: una falla de un frame se acumula en `failures: [{ "frame", "error" }]` sin abortar el resto; la respuesta es `{ "applied": n, "failures": [...] }`.

Las rutas de solo lectura (`selected`, `groups`, `import-groups`, `sections`) no requieren `confirmed`. `section/apply` y `k-factors/apply` sí lo requieren (`422` si falta), igual que el resto de las rutas de mutación de este agente.

## Diagrama de interacción (port del escritorio)

Puerto literal de `sap2000_bridge.py` (proyecto de escritorio original, `concrete_section_designer/app/`): `list_groups`, `list_load_combinations`, `get_group_demands`, `get_selected_demands` y sus helpers al patrón de este repositorio: sin conexión COM propia, cada operación recibe el `SapModel` ya conectado a través del actor STA de la sesión y las rutas se instalan desde `bridge.actions.install_routes` con el mismo envoltorio `call` y la misma autenticación por token, igual que `bridge/esbelteces.py`. Mismas llamadas COM y mismos argumentos que el original. Las unidades presentes de SAP2000 se fuerzan a kN-m durante la lectura y se restauran siempre al terminar (en `finally`). SAP2000 reporta P con tracción positiva; se invierte a compresión positiva (convención del motor: `n_kn = -P`). Los cortantes no se leen (igual que el original); viajan como 0.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/diagrama/groups` | `GroupDef.GetNameList`: todos los grupos de columnas definidos, sin filtrar por si tienen miembros. Devuelve `{ "groups": [<nombre>, ...] }`. Solo lee. |
| `GET /v1/sap/actions/diagrama/combos` | `RespCombo.GetNameList()` (varias firmas tolerantes en orden): todas las combinaciones de respuesta del modelo. Devuelve `{ "combos": [<nombre>, ...] }`. Solo lee. |
| `POST /v1/sap/actions/diagrama/group-demands` | `{ "group": "<nombre_grupo>", "combos": [<combo>, ...] }`. Extrae todos los Frame objects del grupo, luego lee `Results.FrameForce(frame, 0)` para cada frame y cada combinación, en todas las estaciones de salida a lo largo de la barra. Devuelve `{ "rows": [<row>, ...], "n_frames": <int>, "n_rows": <int> }`. Solo lee. |
| `POST /v1/sap/actions/diagrama/selected-demands` | `{ "combos": [<combo>, ...] }`. Igual que `group-demands` con los Frame objects actualmente seleccionados en SAP2000 (`SelectObj.GetSelected`, tipo 2). Devuelve el mismo formato. Solo lee. |

Formato de cada `row` en `groups-demands` y `selected-demands`:
```json
{
  "frame": "<nombre_frame>",
  "combo": "<nombre_combinación>",
  "station": <float_distancia_desde_inicio>,
  "n_kn": <compresión positiva = -P_SAP>,
  "mx_knm": <momento_eje_2>,
  "my_knm": <momento_eje_3>,
  "vu_x_kn": 0.0,
  "vu_y_kn": 0.0
}
```

Validación:
- `combos` debe contener entre 1 y 500 combinaciones (`422` si no).
- `group` debe existir (`409` si no, con mensaje "El grupo '<nombre>' no tiene ningún Frame object asignado").
- Frames seleccionados no vacío (`409` si no hay selección, con mensaje "No hay ningún Frame object (columna) seleccionado en SAP2000").
- Unidades: lectura en kN-m, con restauración al terminar incluso si falla a mitad.

No hay ruta `apply` (sólo lectura). Los datos viajan sin transformaciones adicionales; el motor del diagrama los interpreta tal cual.

## Combinaciones (port del escritorio)

Puerto literal de `combinaciones/io/sap2000_com.py` (proyecto de escritorio "Combinaciones" original). Prefijo `/v1/sap/actions/combinaciones`, misma autenticación por token y mismo mapeo de errores a HTTP que el resto del agente. `bridge/combinaciones.py` tampoco mantiene estado COM propio: se instala desde `bridge.actions.install_routes` reusando su mismo envoltorio `call` (actor STA + traducción de excepciones), igual que `bridge/esbelteces.py`. Estas rutas son independientes de las ya existentes `/v1/sap/actions/combinations/*` (con "s", sin la "i" de "combinaciones"), que no se tocan.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/combinaciones/snapshot` | `LoadCases.GetNameList` + `RespCombo.GetNameList`/`GetCaseList` de cada combinación. Devuelve `{ "load_cases": [{ "name", "sap_type": null }], "combinations": [{ "name", "items": [{ "item_type", "name", "factor" }] }] }`. `sap_type` siempre es `null`: `LoadCases` no expone un tipo consultable equivalente a `LoadPatterns.GetLoadType`. Solo lee, no requiere `confirmed`. |
| `GET /v1/sap/actions/combinaciones/existing` | Igual que la mitad de `snapshot` referida a combinaciones: `{ "combinations": [{ "name", "items" }] }`, sin los Load Cases. Solo lee. |
| `POST /v1/sap/actions/combinaciones/delete` | `{ "names": ["C1", "C2"], "confirmed": true }`. `RespCombo.Delete(name)` por cada nombre; devuelve `{ "failed": [...] }` con los nombres cuyo código de retorno fue distinto de cero. Una falla individual no aborta el resto ni lanza excepción. |
| `POST /v1/sap/actions/combinaciones/send` | `{ "matrix": <Matrix JSON>, "confirmed": true }`. Ver más abajo. |

Las llamadas `GetNameList`/`GetCaseList` se intentan con varias firmas en orden (`()`, `(0, [])`, `(0, ())`, `(0, None)` para `GetNameList`; `(name,)`, `(name, 0, [], [], [])`, `(name, 0, (), (), ())`, `(name, 0, None, None, None)` para `GetCaseList`), igual que el original: distintos bindings de `win32com`/`comtypes` esperan formas distintas de argumento, y solo un `TypeError` dispara el siguiente intento (cualquier otra excepción se propaga tal cual). Un código de retorno de SAP2000 distinto de cero, o una respuesta con una forma que el módulo no reconoce, se convierte en un `Sap2000ComError` con el mensaje literal del original (p. ej. `"Error al obtener la lista de patrones de carga de SAP2000. Código de retorno: {code}"`).

`POST /v1/sap/actions/combinaciones/send` recibe la matriz tal como la define el contrato compartido (`headers`, `combinations: [{ name, rule_id, norm, family, canonical_description, factors_by_case: {caso: "texto-decimal"}, envelope_members: [...] | null, envelope_member_factors, envelope_quick_labels }]`) — los factores llegan como strings decimales (fieles al proyecto de escritorio, que los guarda como `Decimal`) y se convierten a `float` recién al enviarlos a SAP2000. Comportamiento, literal del original:

- Todas las combinaciones de la matriz se procesan aunque alguna tenga problemas puntuales; solo se lanza un error (ver abajo) cuando **ninguna** combinación pudo crearse en absoluto (fallo total).
- Nombres repetidos dentro de la matriz generan una advertencia previa al envío: `"'{name}' aparece {n} veces en la matriz - solo se envía la última definición, las anteriores con ese mismo nombre se pisan."` (la última definición con ese nombre es la que efectivamente queda en SAP2000, vía el ciclo Add/Delete/Add de abajo).
- `combo_type` es `1` (Envelope) cuando `envelope_members` no es `null`, `0` (Linear Add) en caso contrario. `RespCombo.Add(name, combo_type)`; si ya existe (código distinto de cero), se llama `RespCombo.Delete(name)` y se reintenta `Add` una vez. Si el reintento también falla: advertencia `"No se pudo crear la combinación '{name}'."` y esa combinación no cuenta en `created` ni recibe términos.
- Combinación envolvente: por cada nombre en `envelope_members`, `RespCombo.SetCaseList(name, 1, member_name, 1.0)` — `CNameType=1` (LoadCombo), factor siempre `1.0` (convención de SAP2000 para miembros de envolvente, no hay factor real). Un código de retorno distinto de cero genera advertencia: `"'{name}' -> '{member}': error al asignar la combinación miembro a la envolvente (código SAP2000 {ret}); revisar que se haya enviado antes."`.
- Combinación normal: por cada `(caso, factor)` de `factors_by_case`, se omite si `factor == 0` (sin llamar a SAP2000). Si el caso no está en `LoadCases.GetNameList()` del modelo (no en `LoadPatterns`: un Response Combination solo puede referenciar Load Cases), advertencia `"'{name}' -> '{caso}': revisar que el estado de carga se encuentre creado en SAP2000."` sin llamar a SAP2000. En caso contrario, `RespCombo.SetCaseList(name, 0, caso, float(factor))` — `CNameType=0` (LoadCase); un código de retorno distinto de cero genera `"'{name}' -> '{caso}': error al asignar el caso de carga a la combinación (código SAP2000 {ret}); revisar que el estado de carga se encuentre creado."`.
- Si `created == 0` y hay advertencias (fallo total), el agente responde `409` con cuerpo estructurado `{ "code": "send_failed", "message": "No se pudo enviar ninguna combinación a SAP2000:\n- " + advertencias unidas con "\n- " }`. En cualquier otro caso responde `200` con `{ "created": n, "warnings": [...] }` (advertencias no bloqueantes).

`delete` y `send` exigen `{ "confirmed": true }` (`422` si falta). `snapshot` y `existing` son de solo lectura.

## Equilibrio plástico (port del escritorio)

Puerto literal de la parte en vivo de `sap2000_import.py` (proyecto de escritorio original) y del flujo del diálogo de importación SAP2000 (`sap2000_import_dialog.py`): `list_combo_names`, `list_group_names`, `list_group_frame_members`, `get_frame_geometry`, `get_frame_axial_force`, `get_frame_axial_force_gravity` más sus helpers y el manejo de excepciones. Prefijo `/v1/sap/actions/equilibrio`, misma autenticación por token y mismo mapeo de errores a HTTP que el resto del agente. `bridge/equilibrio.py` no abre su propia conexión COM: se instala desde `bridge.actions.install_routes` reusando su mismo envoltorio `call` (actor STA de la sesión + traducción de excepciones), igual que `bridge/esbelteces.py`, `bridge/hormigon.py` y `bridge/combinaciones.py`. **No se cambian las unidades presentes de SAP2000:** los valores salen en las unidades con que el modelo esté abierto (tonf y m, según el original).

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/equilibrio/combos` | `RespCombo.GetNameList`: todas las combinaciones de respuesta del modelo. Devuelve `{ "combos": [<nombre>, ...] }`. Solo lee. |
| `GET /v1/sap/actions/equilibrio/groups` | `GroupDef.GetNameList`: todos los grupos definidos, sin filtrar por si tienen miembros de tipo frame. Devuelve `{ "groups": [<nombre>, ...] }`. Solo lee. |
| `GET /v1/sap/actions/equilibrio/group-members?group=<nombre>` | `GroupDef.GetAssignments` filtrando frames (ObjectType == 2): miembros de frame del grupo. Falla si grupo no existe o tiene más de 200 miembros (409). Devuelve `{ "members": [<nombre>, ...] }`. Solo lee. |
| `POST /v1/sap/actions/equilibrio/frame-data` | Cuerpo `{ "frame": "<nombre>", "combo_sismo": "<nombre>" | null, "combo_grav": "<nombre>" | null }`. Flujo «por nivel» del diálogo original: `H, L` de geometría (`GetFrameGeometry`), `Tdis` = fuerza axial máxima en estaciones del combo sísmico (`GetFrameAxialForce`), `qgrav` del combo gravitacional (`GetFrameAxialForceGravity`). Devuelve `{ "frame": "<nombre>", "H": <float_m>, "L": <float_m>, "axial_sismo": <float> | null, "axial_grav": <float> | null, "avisos": [<texto>, ...] }`. Las fuerzas viajan en las unidades presentes del modelo (tonf); avisos si falla lectura de fuerza (sigue sin abortar). Solo lee. |
| `POST /v1/sap/actions/equilibrio/group-demands` | Cuerpo `{ "group": "<nombre>", "combo_sismo": "<nombre>" \| null }`. Flujo «por Group» del diálogo original: un miembro por frame del grupo, `Tn_req = Pn_req` = máxima \|P\| del combo elegido (iguala tracción y compresión, igual que el original). Devuelve `{ "group": "<nombre>", "members": [{ "label": "<frame>", "Tn_req": <float>, "Pn_req": <float>, "H": <float_m>, "L": <float_m>, "avisos": [<texto>, ...] }], "avisos": [<texto>, ...] }`. Si falla la geometría de un frame, `H = L = 5.0` de respaldo con aviso; si falla la fuerza, el miembro se omite con aviso (no aborta). Fuerzas en las unidades presentes del modelo (tonf). Solo lee. |

Las rutas de solo lectura (`combos`, `groups`, `group-members`, `frame-data`, `group-demands`) no requieren `confirmed`. Validación: cuerpo JSON mal formado → `422`; frame/group no existe o grupo vacío → `409` con `{ "code": "sap_error", "message": "..." }`; más de 200 miembros en grupo → `409`. SAP2000 COM requiere Windows; fuera de Windows, todas responden `503` con el detalle explícito de que SAP2000 COM requiere Windows.

## Espectro (port del escritorio)

Puerto literal de `sap2000_bridge.py` + las funciones `_calcular_ps`, `_calcular_qxqy`, `_calcular_modal` y `_enviar_curvas` de `sap2000_dialogs.py` (proyecto de escritorio "Espectro NCh2369" original). Prefijo `/v1/sap/actions/espectro`, misma autenticación por token y mismo mapeo de errores a HTTP que el resto del agente. `bridge/espectro.py` no abre su propia conexión COM (nada de `GetActiveObject`/`SapConnection`): se instala desde `bridge.actions.install_routes` reusando su mismo envoltorio `call` (actor STA de la sesión + traducción de excepciones), igual que `bridge/esbelteces.py` y `bridge/combinaciones.py`. Estas rutas son independientes de las ya existentes `/v1/sap/actions/spectrum/*` (creación de función response-spectrum desde el motor de cálculo del Workbench), que no se tocan.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/espectro/functions` | `Func.GetNameList(0, [], 1)` (`FUNC_TYPE_RESPONSE_SPECTRUM = 1`). Devuelve `{ "nombres": [...] }` con las funciones de espectro de respuesta ya definidas en SAP2000. Solo lee. |
| `GET /v1/sap/actions/espectro/load-cases?only_modal=<bool>` | `LoadCases.GetNameList(0, [])`; si `only_modal=true`, filtra con `LoadCases.GetTypeOAPI(nombre, 0, 0) == 3` (`CASE_TYPE_MODAL`) por cada nombre. Devuelve `{ "nombres": [...] }`. Solo lee. |
| `POST /v1/sap/actions/espectro/send` | `{ "jobs": [{ "nombre", "periods": [...], "values": [...] }], "confirmed": true }`. Por cada job, `Func.FuncRS.SetUser(nombre, len(periods), periods, values, 0.05)` — el mismo nombre sobreescribe la función existente (sirve tanto para crear como actualizar, igual que el original). Todos los jobs se procesan aunque alguno falle; devuelve `{ "enviadas": [nombre, ...], "errores": [{ "nombre", "error" }, ...] }`, nunca lanza por una falla parcial. `confirmed: false` → `422`. |
| `POST /v1/sap/actions/espectro/ps` | `{ "rows": [{ "case", "factor" }] }`. Ps = Σ (`factor` × ΣFZ de `Results.BaseReact` del `case`), seleccionando cada caso con `Results.Setup.DeselectAllCasesAndCombosForOutput()` + `SetCaseSelectedForOutput(case)` antes de leerlo. Se detiene en el primer `case` sin resultados (mismo criterio que `_calcular_ps`: no acumula parcialmente). Devuelve `{ "ps": <float> }`. |
| `POST /v1/sap/actions/espectro/qxqy` | `{ "case_x", "case_y" }`. `Qx` = ΣFX de `Results.BaseReact(case_x)`, `Qy` = ΣFY de `Results.BaseReact(case_y)` — **no** el mismo caso para ambos, igual que `_calcular_qxqy`. Devuelve `{ "qx": <float>, "qy": <float> }`. |
| `POST /v1/sap/actions/espectro/modal` | `{ "modal_case" }`. Selecciona el caso y lee `Results.ModalParticipatingMassRatios(0, [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [])` (17 argumentos por referencia, firma OAPI literal); `raw[4]` = períodos, `raw[5]` = razones UX, `raw[6]` = razones UY. `Tx` = período en el índice del máximo UX, `Ty` = período en el índice del máximo UY (`_calcular_modal`/`get_modal_periods_at_max_mass`). Devuelve `{ "Tx": <float>, "Ty": <float> }`. |

Las lecturas de resultados (`ps`, `qxqy`, `modal`) usan `Results.BaseReact(0, [], [], [], [], [], [], [], [], [], 0.0, 0.0, 0.0)` para las reacciones de base — `raw[4]`/`raw[5]`/`raw[6]` son FX/FY/FZ, sumadas sobre todas las filas devueltas. Sin resultados (código de retorno distinto de cero, o `raw[0]` vacío/falso), el agente responde `409` con cuerpo estructurado `{ "code": "sap_error", "message": "No hay reacciones de base para '{case}'. Corra el análisis en SAP2000 primero (Analyze > Run Analysis)." }` (o el mensaje equivalente `"No hay resultados modales para '{modal_case}'. ..."` para `modal`) — el mismo `NO_RESULTS_HINT` literal del original en ambos casos. Cualquier otra excepción COM se envuelve igual, con el mismo `code: "sap_error"` y un mensaje que incluye el texto de la excepción original.

`send` es la única ruta de mutación y exige `{ "confirmed": true }` (`422` si falta); `functions`, `load-cases`, `ps`, `qxqy` y `modal` son de solo lectura y no lo requieren (`ps`/`qxqy`/`modal` no mutan el modelo, solo seleccionan la salida activa y leen resultados ya calculados).

Sin cambios de unidades en ningún punto: el agente lee y escribe en las unidades presentes que ya tenga el modelo abierto en SAP2000, exactamente como el escritorio original (los períodos y ordenadas espectrales viajan tal cual, sin conversión).

## Diagramas de shells (port del escritorio)

Puerto de `panel_diagramas_shell.py`, `sap2000/diagramas_shell.py`, `sap2000/pedestales_automaticos.py` y `sap2000/estado_modelo.py` del escritorio (`foundations-sap2000/app`). Código del puente: `bridge/foundations_shells.py` (se instala desde `bridge.actions.install_routes` con el mismo envoltorio `call`: actor STA, autenticación por token, mismo mapeo de errores a HTTP). Wrappers y tipos TS: bloque "Diagramas de shells (Beta)" de `web/src/bridge_client.ts`; contrato por claves en `web/src/bridge_contracts.json` (verificado por `tests/test_bridge_contracts.py` y `web/src/bridge_contracts.test.ts`); cálculos puros de la UI en `web/src/foundations/shellCalculos.ts`. Política de estrictez idéntica a la del escritorio: tolerante (varias firmas COM, un elemento/frame que falle no aborta el resto). Todas las rutas son de solo lectura (sólo cambian la salida activa de `Results.Setup` y, momentáneamente, las unidades presentes). Errores: `409` con el mensaje de la excepción, `422` por validación de cuerpo.

### `GET /v1/sap/actions/foundations/cases` (ampliada)

Responde `{ "load_cases": [...], "combos": [...], "envolventes": [...] }`. `envolventes` es el subconjunto de `combos` de tipo Envelope (`RespCombo.ComboType == 1`, con las 3 firmas tolerantes de `_obtener_tipo_combo`; un combo cuyo tipo no se pueda determinar se trata como no envolvente, nunca falla). Aditiva: los consumidores anteriores siguen funcionando; una web contra un puente viejo no recibe la clave (tratarla como `[]`). La UI arma la lista del combo "Caso / Combo:" con `combosConEnvolventes(load_cases, combos, envolventes)` (`shellCalculos.ts`: orden `sorted()` + duplicados `"NOMBRE [max]"`/`"NOMBRE [min]"`) y separa el sufijo con `separarComboYPaso` antes de llamar al puente (SAP2000 solo conoce el nombre real; el sufijo es solo de visualización y se traduce en `paso`).

### `POST /v1/sap/actions/foundations/shell-results`

Cuerpo:

```json
{ "shells": ["S1"], "combinacion": "C1", "tipo": "forces|stresses|soil_pressure",
  "componente": "M11", "paso": "max|min|null", "sin_geometria": false }
```

* `shells`: 1–500 nombres de Area objects. `combinacion`: nombre real (sin sufijo `[max]/[min]`).
* `tipo` y `componente`: forces → `F11 F22 F12 M11 M22 M12 V13 V23`; stresses → `S11 S22 S12 SMax SMin SVM T13 T23`; soil_pressure → el `componente` se ignora (`Pressure`). Componente inválido → `422`.
* `paso` (opcional, `max`/`min`, sin distinguir mayúsculas): filtra por `StepType` para combinaciones envolventes; solo tiene efecto en forces/stresses (en soil_pressure se ignora, la tabla nativa no expone `StepType`, y **no** se exige `StepType`/`StepNum` juntos). Sin `paso`, la última fila por punto gana (comportamiento del escritorio).
* `sin_geometria` (opcional, `false`): `true` devuelve `elementos`, `joints` y `joints_z` vacíos (`{}`) — para refrescar solo resultados cuando la web ya tiene la geometría en caché (el puente igual la lee, la necesita para filtrar).

Respuesta (todas las claves siempre presentes):

```json
{ "elementos": { "<elemento_malla>": ["p1","p2","p3","p4"] },
  "joints": { "<punto>": [x, y] },
  "joints_z": { "<punto>": z },
  "resultados": { "<elemento_malla>": { "<punto>": valor } },
  "mensaje_sin_resultados": "" }
```

`elementos`/`joints`/`resultados` son idénticos en forma a `/foundations/shell-diagram` (que no cambia: `joints` sigue siendo `[x, y]`; la `z` va aparte en `joints_z`, para la columna Z de "Ver tabla de valores..."). Si no hay resultados (`resultados` vacío) `mensaje_sin_resultados` trae el texto literal de `estado_modelo.mensaje_sin_resultados` (modelo no corrido vs. corrido sin resultados para lo pedido); si no, `""`. La UI muestra el título "Sin resultados" con ese mensaje, o, si está vacío, "No se obtuvieron resultados para el caso/combo elegido. Verifica que el modelo esté corrido.".

Lectura: selección de caso con `_select_case_for_output`; geometría con `shell_geometry` (`AreaObj.GetElm`, `AreaElm.GetPoints`, `PointObj.GetCoordCartesian`, en las unidades presentes del modelo, como el escritorio); forces/stresses con la vía rápida `DatabaseTables.GetTableForDisplayArray("Element Forces - Area Shells" | "Element Stresses - Area Shells")` (columnas por nombre, sin distinguir mayúsculas: `AreaElem|AreaElm|Elm|Element`, `Joint|PointElm|Point`, `StepType`, componente) y, si la tabla falla o no matchea, respaldo por elemento con `Results.AreaForceShell`/`AreaStressShell(elm, 1)` (arrays por posición: `PointElm` = índice 2, `StepType` = índice 4, componente en 6 + posición en el orden documentado por CSI). La lectura de forces/stresses va envuelta en `Ton_m_C` (restaura las unidades al terminar). soil_pressure usa `shell_soil_pressure` ("Element Soil Pressure - Area Shells", `Pressure`).

### `POST /v1/sap/actions/foundations/shell-results-multi`

Alimenta "Ver tabla de esfuerzos por franja": el extremo gobernante de cada franja × combinación se calcula en el puente (no viajan mallas completas por combo).

```json
{ "shells": ["S1"],
  "combos": [{ "combinacion": "C1", "paso": null }, { "combinacion": "ENV", "paso": "max" }],
  "tipo": "forces|stresses|soil_pressure",
  "componentes": ["M11", "M22"],
  "franjas": [{ "centro_x": 1, "centro_y": 0.5, "largo": 2, "ancho": 1, "largo_en_x": true }] }
```

* `combos`: 1–500; una entrada por combinación (una envolvente va como dos entradas, `max` y `min`, igual que la lista del combo). Para el progreso "Extrayendo esfuerzos por combinación de SAP2000..." con "Cancelar", llamar una vez por combinación (o por lotes chicos) y descartar las respuestas pendientes al cancelar; el puente no tiene cancelación propia.
* `componentes` (opcional): subconjunto no vacío de los componentes del `tipo`; por defecto, todos los del `tipo` (para soil_pressure, `["Pressure"]`).
* `franjas`: 1–200, solo las visibles; `ancho` también cuenta (rectángulo `centro ± mitad`), igual que `_calcular_estaciones_franja`.

Respuesta:

```json
{ "filas": [{ "franja": 0, "combinacion": "C1", "paso": null, "valores": { "M11": 3.0 } }],
  "errores": [{ "combinacion": "C2", "paso": "max", "error": "..." }] }
```

`filas`: una por franja × combo (en el orden `combos` × `franjas`); `franja` es el índice dentro de `franjas` de la petición; `valores[componente]` = extremo gobernante `abs(max) >= abs(min) ? max : min` de las estaciones de la franja (promedio de esquinas por elemento, buckets por tamaño de malla mediano; ver `calcular_estaciones_franja`, port 1:1 de `shellCalculos.calcularEstacionesFranja`), **sin la clave** si la franja no captura elementos con resultados ("—" en la tabla). Una combinación sin resultados genera filas con `valores: {}`. Si la lectura de una combinación lanza, no genera filas y queda en `errores` (el escritorio la omite y sigue). Texto de la fila en la tabla: `NOMBRE`, `NOMBRE [max]` o `NOMBRE [min]` (la UI lo reconstruye con `paso`).

### `POST /v1/sap/actions/foundations/pedestal-candidates`

Cuerpo `{ "joints": ["J1", "J2", ...] }` (1–200 000 nombres de los joints de los shells del diagrama actual; son las claves de `joints` de `shell-results`). Respuesta:

```json
{ "candidatos": [{ "frame": "C12", "largo": 0.6, "ancho": 0.4, "largo_en_x": true,
                   "centro_x": 1.0, "centro_y": 0.0, "aproximado": false, "motivo_aviso": "" }] }
```

Port literal de `detectar_pedestales_automaticos`: `FrameObj.GetNameList` (si falla, sin candidatos), `FrameObj.GetPoints`, coordenadas con `PointObj.GetCoordCartesian`; frame vertical si `|Δx| < 1e-3`, `|Δy| < 1e-3` y `|Δz| > 1e-3` (en las unidades presentes del modelo); candidato si el joint del pie (z menor) está en `joints`; sección con `FrameObj.GetSection`; dimensiones con `PropFrame.GetRectangle` (los dos primeros `float`: T3 = `largo`, T2 = `ancho`) y, si no, `PropFrame.GetCircle` (T3 como cuadrado, `aproximado: true`, `motivo_aviso` = "Sección circular, aproximada como cuadrado"); sin lectura: 0.30 × 0.30, `aproximado: true`, `motivo_aviso` = "Sección no reconocida, dimensión de relleno — revisar manualmente"; `largo_en_x` desde `FrameObj.GetLocalAxes` (último numérico módulo 180: `< 45` o `>= 135`; sin lectura, `true`). Un frame ilegible se omite sin abortar. El nombre sugerido en la UI es `"Pedestal <frame>"`; un `aproximado` se resalta (#fff3cd) con su `motivo_aviso` en la columna "Aviso".

### Exportar imagen (backend propio, no el puente)

`POST /foundations/diagram/shell.png` (`backend/routers/foundations.py`, mismo `require_user`): cuerpo `{ elementos, joints, resultados, componente, caso, metodo?, unidad?, escalonado?, limites? }` (`elementos`/`joints`/`resultados` tal cual `shell-results`; `metodo` ∈ "Promedio en nodos (contornos)" (defecto) | "Promedio por elemento (sólido)" | "Sin promediar (por elemento)"; `limites` = `[min, max]` manuales, ignorados si `min >= max`). Devuelve `image/png` (200 dpi, `bbox_inches="tight"`, `Content-Disposition: attachment; filename="diagrama_shell.png"`) dibujado con `dibujar_diagrama_shell` — **sin** franjas/pedestales (igual que el escritorio al exportar, ver el docstring de `graficos.dibujar_diagrama_shell`). `422` si no hay elementos con los 4 vértices en `joints`. Wrapper: `exportFoundationsShellPng` (`bridge_client.ts`).

## Diseño de secciones (port del escritorio)

Rutas de `bridge/foundations_cuts.py` (port literal de `app/sap2000/section_cuts.py` del escritorio; mismo envoltorio `call`, mismo actor STA). La web las consume desde `web/src/foundations/panels-b/PanelDisenoSecciones.tsx` con los wrappers de `bridge_client.ts` (bloque "Diseño de secciones"). **Las firmas `SectionCutAnalysis`, `GetTableForEditingArray`/`SetTableForEditingArray`/`ApplyEditedTables` no están verificadas contra un SAP2000 real** (tampoco en el escritorio): se portan tal cual, con los mismos intentos tolerantes; ver `docs/SAP2000_ACCEPTANCE.md` (pasos 56 a 62).

### `GET /v1/sap/actions/foundations/selection?kind=frames`

Ruta existente, extendida: `kind` ∈ `joints` | `shells` | `frames` (`eObjectType` 1 / 5 / 2). Un `kind` inválido responde `422` "kind must be joints, shells or frames". Wrapper: `getFoundationsSelection(creds, 'frames')`.

### `GET /v1/sap/actions/foundations/section-cuts`

Section cuts definidos en el modelo conectado (solo lectura): `{ "cuts": [{ "nombre": "SC_001", "grupo": "GRP_SC_001" }] }`. Lee `DatabaseTables.GetTableForDisplayArray("Section Cut Definitions")` y toma, sin repetir, la columna de nombre (alias normalizados `name`, `sectioncut`, `sectcutname`, `sectcut`, `seccutname`) y, si existe, la de grupo (`group`, `groupname`, `sectcutgroup`; `""` si la versión no la expone). Lista vacía si la tabla no se puede leer. Wrapper: `listFoundationsSectionCuts`.

### `GET /v1/sap/actions/foundations/section-cuts/groups`

`{ "groups": ["ALL", "G1", ...] }`: nombres de **todos** los grupos (`GroupDef.GetNameList`), también los que solo tienen frames (la ruta `foundations/groups` solo devuelve los que tienen nodos o shells). El panel quita "ALL", como el escritorio. Wrapper: `listFoundationsSectionCutGroups`.

### `POST /v1/sap/actions/foundations/section-cuts/preview` y `/apply`

Cuerpo `{ "nombre": "SC_001", "grupo": "GRP_SC_001", "frames": ["F1", "F2"], "desbloquear": false }`; `/apply` exige además `"confirmed": true` (`422` si falta).

* Con `frames`: arma el grupo `grupo` (por defecto `GRP_<nombre>`) con esos frames (`GroupDef.SetGroup` + `FrameObj.SetGroupAssign`, errores ignorados como en el original) y define el corte sobre él (`crear_section_cut`).
* Sin `frames`: `grupo` es un grupo YA existente con nodos/shells/frames (`GroupDef.GetAssignments`, tipos 1/5/2; `crear_section_cut_desde_grupo`). Un grupo vacío responde `409` "El grupo '…' no tiene nodos, shells ni frames asignados en SAP2000.". Sin `frames` ni `grupo` responde `422`.
* Definición del corte: `DatabaseTables.GetTableForEditingArray("Section Cut Definitions")` → fila nueva con `Name`/`Group` por alias normalizado (más `DefinedBy = "Group"` y `ResultType = "Analysis"` si la tabla los tiene; un corte homónimo se reemplaza) → `SetTableForEditingArray` → `ApplyEditedTables(True)`. Si la tabla no se puede leer o no se reconocen los campos, `409` con las instrucciones para definirlo a mano (Define > Section Cuts) e importarlo.
* **Bloqueo**: nunca desbloquea por su cuenta. Con el modelo bloqueado (`GetModelIsLocked`) y `desbloquear: false` no modifica nada y devuelve `requiere_desbloqueo: true`; con `desbloquear: true` ejecuta `SetModelIsLocked(False)` (borra los resultados del análisis) y crea. La web pregunta (Sí/No, "No" por defecto) y reenvía con `desbloquear: true`.
* `/preview` no escribe nada (ni grupo, ni tabla, ni desbloqueo): valida el grupo y responde si haría falta desbloquear.

Respuesta de ambas: `{ "creado": true, "requiere_desbloqueo": false, "mensaje": "Section cut 'SC_001' definido sobre el grupo 'GRP_SC_001'.", "elementos": ["F1", "F2"] }` (`elementos` de grupo: nodos + shells + frames). Wrappers: `previewFoundationsSectionCut`, `applyFoundationsSectionCut` (este último manda `confirmed: true`).

### `POST /v1/sap/actions/foundations/section-cuts/forces`

Cuerpo `{ "cuts": ["SC_001"], "combos": ["COMB1", "COMB2"] }` (`cuts` 1–500; `combos` vacío = todos los load cases). Respuesta:

```json
{ "resultados": { "SC_001": [{ "caso": "COMB1", "P": -10.0, "V2": 3.0, "V3": 4.0, "M2": 6.0, "M3": 8.0, "T": 0.5 }] },
  "model_locked": true }
```

Port de `extraer_fuerzas_section_cut`: unidades forzadas a Ton_m_C (`_with_ton_m_c`, se restauran); selección de casos para salida (deselecciona todo, marca cada caso/combinación pedida; sin `combos`, todos los load cases); `Results.Setup.SetSectionCutSelectedForOutput(nombre, True)` (error ignorado); `Results.SectionCutAnalysis(nombre, 0)` y, si falla, la firma larga con 11 arreglos de salida. Se toman los **últimos 11 arreglos** del retorno (Obj, Elm, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3) y se mapea `P = F3`, `V2 = F1`, `V3 = F2`, `M2 = M2`, `M3 = M3`, `T = M1`, filtrando a los casos pedidos. `V total` y `M total` (`hypot`) se calculan en la web. Una lista vacía para un corte no es error (el panel avisa "Sin resultados"). `model_locked` es `GetModelIsLocked`: `false` = "modelo no corrido".

Errores: `409` con `detail = { "code": "sap_error", "message": "...", "model_not_run": bool }`; `model_not_run` es `true` si el modelo está desbloqueado, y la web muestra entonces "Modelo no corrido" en vez de "Error" (como `modelo_no_corrido` en el panel original). El puente no tiene cancelación propia: el panel llama una vez por section cut y revisa "Cancelar" entre llamadas. Wrapper: `extractFoundationsSectionCutForces` (lanza `SectionCutForcesError` con `modelNotRun`).

## Vida útil, revocación del token y rotación (S-07)

Para reducir el impacto de un eventual XSS (el secreto emparejado vive en `localStorage` de la web), el agente limita la vida del secreto y permite revocarlo. No cambia el contrato existente: es retrocompatible en ambos sentidos.

- **Caducidad.** Un secreto emparejado vence a las 12 h de su emparejamiento (`created_at` de `pairings.json`). Se configura con la variable de entorno del agente `SAP2000_AGENT_TOKEN_TTL_HOURS` (número de horas; `0` desactiva la caducidad). Un secreto vencido recibe `401` con `{"detail": "El token del puente venció; vuelva a conectar"}`; un token inexistente o revocado sigue recibiendo `{"detail": "Unauthorized"}`.
- **Compatibilidad.** Los emparejamientos que ya existían cuando el agente nuevo arrancó por primera vez se anotan en `pairing_policy.json` (junto a `pairings.json`) y **no caducan**, para no obligar a reemparejar. El token de arranque (`SAP2000_AGENT_TOKEN`, modo local) tampoco caduca. Un cliente nuevo contra un agente viejo funciona igual (la ruta `/v1/sap/revoke` responde `404`, que el cliente ignora y aun así borra lo guardado); un cliente viejo contra un agente nuevo funciona hasta que su secreto vence.
- **`POST /v1/sap/revoke`** (autenticada, sin cuerpo): revoca el secreto emparejado con el que se hace la llamada y responde `{"revoked": true}`. Con el token de arranque responde `{"revoked": false}` y no revoca nada (ese token solo cambia reiniciando el puente). La web la usa al «Desconectar» y, ante un `401` con credenciales emparejadas, borra el emparejamiento guardado y muestra «Vuelva a conectar el puente».
- **Cliente.** `bridge_client.ts` no registra el token en consola y lo enmascara si aparece en el `detail` de un error.
- **Rotación (no implementada).** Se omitió la renovación automática por cabecera `X-Bridge-Token-Renewed`: exigiría que `bridge/pairing.py` sustituya el secreto (hash) en caliente y que el cliente lo guarde, un cambio mayor que el mínimo acordado. Con la caducidad de 12 h más «Desconectar», el emparejamiento se renueva volviendo a conectar.


## Aplicaciones independientes que usan el puente

Detalle, alcances por origen y decisiones en `docs/PUENTE_COMPARTIDO.md`.

| Ruta | Quién | Descripción |
|---|---|---|
| `GET /v1/sap/actions/graph/model` | SAP Graph | `{model, units, warnings, read_ms}`: materiales, secciones, puntos, barras, shells y patrones de carga |
| `GET /v1/sap/actions/viewer/snapshot` | Viewer | snapshot 3D completo (`schemaVersion:1`, cabecera `X-Snapshot-Stale`) |
| `POST /v1/sap/actions/viewer/connect`, `refresh`; `GET status` | Viewer | conexión y estado |
| `GET /v1/sap/actions/viewer/sections/props` | Viewer | propiedades de las secciones de barra del modelo abierto, en mm y MPa (solo lectura): `{units, sections{propType, propTypeName, A, I33, I22, r33, r22, rz?, rzCalculated, h, b, tf, tw, material}, materials{fy, fu, E, type, fc?}, warnings}`; convierte desde las unidades activas sin cambiarlas; `rz` solo en ángulos (calculado con `angle_props.section_props`); `fy`/`fu` solo en acero (`type: "Steel"`); `fc` (f'c en MPa, `PropMaterial.GetOConcrete`) solo en hormigón (`type: "Concrete"`) y opcional: si no se puede leer se omite la clave y se avisa en `warnings`, y los puentes antiguos no lo envían; lo ilegible se omite y se explica en `warnings`; 404 sin modelo cargado, 409 si las unidades no se reconocen. Código en `bridge/viewer/core/section_props.py` | Claves añadidas a cada sección, solo cuando se pueden leer (si no, se omiten; nunca 0 ni `null`): `t2b`, `tfb` (ala inferior de la I asimétrica, `GetISection`), `dis` (separación del ángulo doble y del doble canal, `GetDblAngle`/`GetDblChannel`), `J` (mm⁴), `As2`, `As3` (mm²), `S22`, `S33`, `Z22`, `Z33` (mm³) de `GetSectProps`, y `shapeSource`: `"getter"` si las dimensiones salen del getter tipado, `"none"` si solo hay propiedades (General —con la envolvente t3×t2 de `GetGeneral` si la da—, Section Designer, no prismática…). Un Double_Channel sin `GetDblChannel` se entrega solo con propiedades. `?names=A,B` limita la lectura a esas secciones (las inexistentes se avisan como «Sección X: no existe en el modelo; se omite.»; los nombres con coma no se pueden pedir así). Si la lista de secciones (`PropFrame.GetNameList`) no se puede leer, la ruta no falla: sin `names` responde `sections` vacío con un aviso (con el tipo y el `repr` de la última respuesta); con `names` lee cada sección directamente con `PropFrame.GetTypeOAPI`, sin la lista.
| `GET /v1/sap/actions/viewer/results/{cases,design,area,displacements}`, `results/frame/{frame}` | Viewer | resultados |
| `GET /v1/sap/actions/viewer/k`, `k/diagnose`, `k/design`; `POST k/preview`, `k/apply` | Viewer | factores K con nonce, copia de seguridad, relectura y auditoría; `GET k` trae además «L Major», «L Minor» y «L LTB» (7 claves por barra) y `k/preview`/`k/apply` también las escriben (ver nota abajo) |

Nota sobre `GET /v1/sap/actions/viewer/k`: cada barra de `frames` trae las 4 K (`K1 Major`, `K1 Minor`, `K2 Major`, `K2 Minor`) y, si la tabla las incluye, tres relaciones de longitud no arriostrada (`L Major`, `L Minor`, `L LTB`), todas con la forma `{"value": number|null, "auto": boolean}`. Las L son OPCIONALES: si la tabla no trae esas columnas, la barra trae solo las 4 K, `notes` incluye «Esta tabla no trae relaciones de longitud no arriostrada» y `available` sigue en `true` (si faltan solo algunas, se omiten esas claves y una nota las nombra). Reglas: `auto: true` («Program Determined») lleva `value: null`; `value: null` con `auto: false` es sin dato; un ratio menor o igual que 0 o no finito es sin dato más una nota; no hay tope superior (puede ser mayor que 1). `k/preview` y `k/apply` aceptan en `changes` tanto K como L (`"L Major": 2.5`, `"L LTB": "auto"`, también mezcladas en una petición de UNA barra): una L admite cualquier número positivo y finito (sin tope superior; `0`, negativos, `nan` e `inf` dan `422 invalid_changes`) o `"auto"` (Program Determined); una K sigue entre 0,1 y 10. Mismo flujo (nonce, copia de seguridad, `SetTableForEditingArray` solo con los campos pedidos, `ApplyEditedTables`, relectura y verificación; modelo bloqueado y verificación fallida igual que con K). `values` incluye las L escritas y no lleva L si no se pidieron. No se crean overwrites en barras que no los tengan, y si la tabla no trae la columna L pedida, `k/preview` responde `409 column_missing` y no se escribe nada. ADVERTENCIA: los nombres de columna (`LRatioMajor`, `L Ratio Major`, `Unbraced Length Ratio (Major)`, `L Major`, y análogos Minor/LTB) son hipótesis sin verificar contra un SAP2000 real (la escritura de L tampoco está verificada; la sonda `scripts/sonda_oapi.py --fase K`, ver `docs/SONDA_FASE_K_ACERO.md`, sirve para comprobarlo); `k/diagnose` lista las columnas L reconocidas y las que parecen L pero no se reconocieron.

`GET /v1/sap/actions/viewer/k/design` (solo lectura; NO cambia la forma de `GET k`): K y L CALCULADOS por el diseño de acero, leídos de «Steel Design 2 - PMM Details - AISC 360-16» (Design Data > Steel Frame > Steel Detail Data) con `DatabaseTables.GetTableForDisplayArray`. Verificado en SAP2000 23.3.1: esta tabla NO figura en `GetAvailableTables` pero se lee directamente (55 columnas, UNA fila por barra, la gobernante); «Steel Details 1/2» SÍ figuran pero devuelven basura y se descartan. El visor recorre todos los candidatos y usa el primero válido. Respuesta: `{"available": boolean, "reason": string, "tableKey": string, "notes": string[], "frames": {"F1": {"K1 Major": {"value": number|null}, "K1 Minor", "K2 Major", "K2 Minor", "K LTB", "L Major", "L Minor", "L LTB"}}, "governing": {"F1": {"combo": string|null, "station": number|null, "ratio": number|null, "designSect": string|null, "rows": number}}}`. La tabla es por barra x combinación x estación: por barra se devuelve la fila gobernante (mayor ratio; sin columna de ratio, la primera, con nota) y `notes` avisa si K/L difieren entre filas de la misma barra. Si el diseño no se ha ejecutado (tabla ausente o vacía) responde 200 con `available: false`, `frames: {}` y `reason` «ejecute el diseño de acero antes». Claves de columna que faltan en la tabla se omiten de `frames` y se nombran en `notes`. Nombre y columnas comprobados con la sonda en SAP2000 23.3.1 (AISC 360-16): Frame, DesignSect, Combo, Location (estación), TotalRatio (ratio), K1Major...KLTB y XLMajor/XLMinor/XLLTB. Otras versiones o códigos de diseño: sin verificar. ADVERTENCIA: verificado solo en SAP2000 23.3.1 (con o sin «Unitless» en las columnas K/XL); la sonda `--fase K` vuelca `resultados_diseno` para comprobarlo en otra versión. K/L CALCULADOS: solo existen si el diseño de acero está ejecutado en la sesión. Con el diseño sin ejecutar, SAP2000 real (23.3.1) devolvió para la tabla listada 17 columnas con nombre vacío y una fila de otra tabla (código de retorno 1): esa lectura BASURA (todas las columnas vacías, o sin columna de barra ni K/L reconocible) se descarta y se responde 200 con `available: false`, `frames: {}`, `governing: {}` y `reason` «SAP2000 devolvió datos de otra tabla o columnas vacías: probablemente el diseño de acero no está ejecutado en esta sesión. Ejecútelo (Design > Steel Frame Design > Start Design/Check of Structure) y vuelva a leer.»; nunca se muestran valores de esa fila. Con el diseño ya ejecutado (sonda 23.3.1), «Steel Details» sigue dando basura: la basura por sí sola no prueba que el diseño falte. Ver `docs/SONDA_FASE_K_ACERO.md`.

`GET /v1/sap/actions/viewer/k/effective` (solo lectura; NO cambia la forma de `GET k` ni `GET k/design`): K y L EFECTIVOS por barra, combinando overwrites (`read()`) y diseño (`read_design()`) en una sola llamada. Respuesta (siempre HTTP 200): `{"available": boolean, "reason": string, "tableKey": string|null (overwrites), "designTableKey": string|null, "notes": string[], "frames": {"F1": {"K1 Major": {"value": number|null, "source": "overwrite"|"design"|"none", "overwriteAuto": boolean|null, "designValue": number|null}, "K1 Minor", "K2 Major", "K2 Minor", "K LTB", "L Major", "L Minor", "L LTB"}}, "governing": {"F1": {"combo", "station", "ratio", "designSect", "rows"}}}`. Reglas: overwrite numérico -> `source: "overwrite"`, `value` = overwrite (prevalece sobre el diseño); overwrite automático (`auto: true`) con valor de diseño -> `source: "design"`, `value` = `designValue`, `overwriteAuto: true`; automático sin diseño -> `source: "none"`, `value: null`, `overwriteAuto: true` y nota «ejecute el diseño de acero»; `/k` no disponible pero `/k/design` sí -> se devuelve el diseño con `source: "design"` y `overwriteAuto: null` (igual para K LTB, que solo existe en el diseño); ninguno disponible -> `available: false` con `reason`, `frames: {}`. K LTB y los campos de diseño solo aparecen si existen en `/k/design`. ADVERTENCIAS: el K de diseño es el de la fila gobernante de cada barra (ver `governing`), no necesariamente el de cada combinación; tomar el 0 como «automático» es una hipótesis por tabla sin verificar (`overwriteAuto` lo expone); tras escribir overwrites o cambiar el modelo los resultados de diseño dejan de valer y las celdas automáticas pasan a `source: "none"` hasta volver a ejecutar el diseño.
| `GET /v1/sap/actions/viewer/results/{reactions,frames,envelope}` | Viewer | reacciones, esfuerzos de varias barras (paginados) y envolventes; ver «Resultados en bloque» más abajo |

**Snapshot del Viewer: cargas por componente y combinaciones (`GET viewer/snapshot`; `connect` y `refresh` devuelven la misma forma).** Claves nuevas de nivel superior: `combinations`, `combinationsTruncated` y `loadsTruncated`. Son opcionales en el contrato, igual que `combos`: no se añaden a `keys` de `web/src/bridge_contracts.json`, porque esa ruta está protegida y `scripts/check_protected.py` no admite cambiar listas existentes; `tests/viewer/test_viewer_contrato_completo.py` exige su presencia aparte. El visor las consume según `docs/WEB_VIEWER.md` del repositorio SAP2000_AI_ASSISTANT, no de este. Solo lectura: no escribe unidades, selección, patrones ni resultados (prueba en `tests/viewer/test_viewer_snapshot_components.py`).

*Cargas (`loads`).* Política de compatibilidad, elegida para no romper lo existente: `loads` conserva las entradas antiguas, una por carga y muestra de barra o una por joint con la magnitud de sus fuerzas F1–F3, sin campo `component`, con los mismos campos y signos de antes (las cargas de momento no tenían entrada antigua y siguen sin tenerla). Además, por cada carga se AÑADE una entrada por componente global no nula: `component` ∈ {`Fx`, `Fy`, `Fz`, `Mx`, `My`, `Mz`} (ejes globales), `direction` = eje unitario de esa componente (e_X, e_Y o e_Z), `value` = componente CON SIGNO (el vector de la carga es `value × direction`), `unit`, `id` único, y los campos `position`, `pattern`, `objectType`, `objectId` y `kind` como en las antiguas. El visor nuevo debe usar solo las entradas con `component`. Consecuencia: un visor que aún no conozca `component` dibuja cada carga dos veces (entrada antigua más componentes) hasta actualizarse; no pierde datos. Reglas:
- Todos los patrones: la lectura no recibe patrón ni caso seleccionado y usa una lectura por lote (tablas de SAP2000, con lectura por objeto como alternativa), igual que antes.
- Dirección de carga de barra: 1–3 locales, convertidas a globales con la matriz de la barra (`load_direction_vector`) y descompuestas por componente; si la orientación local no está disponible, la carga se omite con aviso (como antes). 4–6 globales; 7–9 proyectadas (misma dirección global); 10 y 11 gravedad = −Z, así que un valor positivo da `Fz` negativa. No se añade `coordSys`: las componentes siempre son globales.
- Momentos: de barra tipo 2 (`Moment`) y de joint (`M1`–`M3`). Joint solo en `Global`, como antes.
- Unidades: `value` sale en las unidades del modelo (`units.source`, p. ej. `tonf_m_C`), y `unit` lleva fuerza `F` para fuerzas puntuales, `F·L` para momentos puntuales (p. ej. `tonf·m`), `F/L` para fuerzas distribuidas y `F` para momentos distribuidos (momento por longitud). Posiciones en metros.
- Límite: 200 000 entradas en total (antiguas más componentes); al superarlo se trunca y `loadsTruncated` es `true`.

*Combinaciones (`combinations`).* Forma: `[{ "name": "ASD1.01", "items": [{ "pattern": "Dead", "factor": 1.0 }] }]`, con lectura de `RespCombo.GetNameList` y `RespCombo.GetCaseList(name)` (mismas firmas que `bridge/combinaciones.py`). Solo casos de carga; las combinaciones anidadas se expanden multiplicando factores; los patrones repetidos se suman y los factores nulos se descartan. `pattern` coincide con `loads[].pattern` cuando el caso tiene el mismo nombre que el patrón (valor por defecto de SAP2000); los casos modales o espectrales no coinciden con ningún patrón. Ciclos y referencias a combinaciones inexistentes se omiten con aviso. Límites: 5 000 combinaciones y 500 patrones por combinación; al superarlos se trunca y `combinationsTruncated` es `true`. Limitación: el tipo de combinación (suma lineal o envolvente) no se lee; todas se expanden como suma lineal. Con un ciclo, el resultado de cada combinación depende de desde dónde se expande (no verificado si SAP2000 llega a permitir ciclos).

Estado: validado con COM falso (modelo demo y filas controladas). **Sin validar con SAP2000 real:** las firmas `RespCombo.GetCaseList` y el orden de sus listas (ya usado por `combinaciones`, pero no contrastado en este visor), y la lectura por lote de las tablas de cargas por patrón. En este repositorio el snapshot no pasa ningún patrón a las lecturas, así que un filtrado por patrón seleccionado no se origina aquí; no se ha podido reproducir con SAP2000 real.

*Patrones para mostrar (escritura temporal, siempre restaurada).* Lo anterior dice que el snapshot no escribe la selección; ese punto queda matizado aquí. Las lecturas por lote de cargas (`Frame Loads - Distributed`, `Frame Loads - Point`, `Joint Loads - Force`) llaman a `DatabaseTables.GetTableForDisplayArray`, y SAP2000 solo devuelve en esas tablas las filas de los load patterns marcados «para mostrar» (`Show Load Patterns`). El camino por objeto (`FrameObj.GetLoadDistributed` y similares) sí devuelve todos, así que el snapshot podía quedar incompleto según la selección del usuario. Para evitarlo, `all_load_patterns_displayed(model, errors)` en `bridge/viewer/sap2000/reader_utils.py` envuelve cada lectura por lote de cargas de patrón (`_frame_distributed_loads_from_database`, `_frame_point_loads_from_database` y `_joint_force_loads_from_database` en `reader_geometry.py`): lee la selección actual con `DatabaseTables.GetLoadPatternsSelectedForDisplay`, la sustituye por todos los nombres de `LoadPatterns.GetNameList` con `SetLoadPatternsSelectedForDisplay`, hace la lectura y **restaura siempre la selección original** al salir, también si la lectura lanza una excepción (una selección original vacía se restaura vacía). Es un cambio del estado de visualización, no del modelo estructural: no toca cargas, unidades ni resultados. Si SAP2000 no admite la llamada, o el modelo la rechaza (p. ej. bloqueado), la lectura sigue con la selección actual, es decir, con el comportamiento anterior, y el aviso aparece en `warnings` del snapshot con el prefijo «Patrones para mostrar». Ningún fallo de este mecanismo se convierte en error. Las lecturas de casos y combinaciones no usan este mecanismo: el snapshot no lee tablas de resultados por caso o combinación, así que no se añadieron `SetLoadCasesSelectedForDisplay` ni `SetLoadCombinationsSelectedForDisplay`. Estado: las firmas de `Get`/`SetLoadPatternsSelectedForDisplay` (`ref string[]`; el retorno se acepta como `[ret, lista]` en cualquier orden, con o sin tupla) **sin verificar contra SAP2000 real**; validado con el fake en `tests/viewer/test_viewer_display_load_patterns.py`.

**Claves opcionales del Viewer (contrato en `web/src/bridge_contracts.json`).** `GET viewer/k` con `available: false` responde sin `tableKey` (`bridge/viewer/core/k_factors.py:70-71`); la web del Viewer trata `tableKey` como opcional. `GET viewer/results/design` puede traer además `combos` (barra → combinación crítica), también opcional: esta rama no lo devuelve y el contrato no lo lista en `keys`.

### Placa Base

Port de `sap2000_integration.py` y de las acciones de `bridge/actions.py` de Placa Base (`placa_base_simple@01b7330`) en `bridge/placa_base.py`. Autenticación y alcance como el resto de rutas del puente (solo `/v1/sap/actions/placa-base/*` y las comunes para el origen de Placa Base). Límites: 500 nombres por lista y 100 caracteres por nombre (422 si se superan). Detalle en `docs/PUENTE_COMPARTIDO.md`.

| Ruta | Efecto |
|---|---|
| `GET /v1/sap/actions/placa-base/cargas` | Solo lee. `{ "modelo", "casos", "combinaciones", "seleccion": { "casos", "combinaciones" }, "seleccion_desconocida" }`. `seleccion_desconocida` es `true` si SAP no informó la marca de salida de algún caso o combinación; esos nombres no figuran en `seleccion`. |
| `POST /v1/sap/actions/placa-base/seleccionar-cargas` | Cambia la selección de salida. Cuerpo `{ "casos"?, "combinaciones"? }`. Descarta los nombres que no existen y los lista en `ignorados`. Devuelve `{ "modelo", "casos", "combinaciones", "ignorados": { "casos", "combinaciones" } }`. 422 si ambos están vacíos o ninguno existe; 409 si SAP rechaza una marca (la selección puede quedar incompleta). |
| `GET /v1/sap/actions/placa-base/nodos` | Solo lee. `{ "modelo", "seleccionados", "restringidos", "para_diseno" }`. `para_diseno` son los seleccionados con restricción o, si no hay selección, todos los restringidos. |
| `POST /v1/sap/actions/placa-base/reacciones` | Cuerpo `{ "nodos": [...], "casos"?, "combinaciones"?, "angulo_deg"? }`. Solo cambia la selección de salida si se envían casos o combinaciones. Fija tonf-m mientras lee y restaura las unidades al terminar (si la restauración falla, 409). Devuelve `{ "unidades": "tonf-m", "ejes": "globales" o "locales", "angulo_deg", "reacciones": [{ "node", "load_name", "step_type", "step_num", "P", "Vx", "Vy", "Mx", "My", "Mz" }] }`. Con el valor absoluto del ángulo igual o mayor que 0.5 gira Vx, Vy, Mx y My al eje local; P y Mz no se giran. 409 si no hay filas (suele faltar el análisis). |
| `POST /v1/sap/actions/placa-base/columna-en-nodo` | Cuerpo `{ "nodos": [...] }`. Solo lee. Devuelve `{ "columnas": [{ "nodo", "columna" }] }`, donde `columna` es `{ "node", "frame", "section", "length_m", "local_angle_deg", "depth_cm", "width_cm", "flange_thickness_cm", "web_thickness_cm" }` o `null`. `null` solo si el nodo no tiene columna vertical (verticalidad, dz dividido entre L, de al menos 0.50, y desplazamiento XY de como máximo máx(0.02 m, 0.03·L)). Las dimensiones son `null` si la sección no es I. Un fallo de COM es error (409), no `null`. |
| `POST /v1/sap/actions/placa-base/resaltar-nodos` | Cuerpo `{ "nodos": [...] }`. Limpia la selección de SAP, selecciona esos nodos y refresca la vista. Devuelve `{ "resaltado": true }`; 409 si SAP rechaza algún paso. |

## CAD-DRWR: ZWCAD/AutoCAD y lectura de fundaciones

Todas son `POST` bajo `/v1/sap/actions/cad/`. Las del CAD usan un actor COM propio (hilo STA aparte, timeouts largos) y NO necesitan SAP2000 abierto. Las de SAP usan el `call` común del puente (timeout de 30 s del actor: la lectura corre en segundo plano y la web sondea el estado).

| Ruta | Efecto |
|---|---|
| `cad/status` | Detecta AutoCAD/ZWCAD/BricsCAD abierto: `{conectado, programa, version, documento, detalle}` (200 incluso sin CAD). |
| `cad/pick` | Pide un clic de inserción en el CAD (comando nativo, foco forzado con `AttachThreadInput`). `{mensaje?, timeout_s?}` → `{x, y}`. Cancelación o error COM → 409 con el texto real. |
| `cad/draw/begin` | Abre una sesión de dibujo (**exige `confirmed: true`**): crea capas y variables de cota. `{confirmed, origen?, n_total, th}` → `{sesion, programa, documento}`. TTL 10 min. |
| `cad/draw/batch` | Emite hasta 1000 entidades (`line`, `circle`, `arc`, `poly`, `filled`, `text`, `dim`). Valida tipos, números finitos, capas, textos ≤ 500 y polilíneas ≤ 2000 puntos. `{sesion, ents}` → `{creadas, omitidas, errores:[{indice,tipo,motivo}]}`. |
| `cad/draw/end` | Regen + ZoomExtents y libera la sesión. → `{documento, creadas, omitidas, resumen}`. |
| `cad/sap/groups` | Grupos de SAP2000 con shells: `{grupos:[{nombre, n_shells}]}`. |
| `cad/sap/foundation/start` | Inicia la lectura de una fundación (un trabajo a la vez, 409 si hay otro): `{grupo}` → `{job}`. |
| `cad/sap/foundation/status` | `{estado, etapa, hechas, total, transcurrido_s, resultado?, error?}`; `resultado` es `FundacionGeom.to_dict()`. |
| `cad/sap/foundation/cancel` | Cancelación cooperativa (la siguiente operación COM la detecta). |

Reglas heredadas del puente: nunca rutas de archivo en el payload; mutaciones con `confirmed: true`; errores de validación 422 en español; errores COM 409 con el texto real. Las firmas COM no verificadas contra una instancia real quedan marcadas en `SAP2000_ACCEPTANCE.md`.

## Resultados en bloque del Viewer (`results/reactions`, `results/frames`, `results/envelope`)

Solo lectura, bajo `/v1/sap/actions/viewer`, con el mismo secreto emparejado que el resto.
Código en `bridge/viewer/core/results_bulk.py`. Los errores llevan
`detail: {code, message}`.

| Ruta | Parámetros | Respuesta |
|---|---|---|
| `GET results/reactions` | `case` | `{case, nodes: {nodo: [F1,F2,F3,M1,M2,M3]}, units, toMeters, sum, max, warnings}` |
| `GET results/frames` | `case`, `ids` (coma), `limit` (≤ 500), `cursor` | `{case, frames: {barra: [{x, step, P, V2, V3, T, M2, M3}]}, units, toMeters, next, warnings}` |
| `GET results/envelope` | `kind` = `reactions`\|`frames`, `cases` (coma, ≤ 30), `ids`, `limit`, `cursor` | `{kind, cases, max, min, governing, units, toMeters, next, warnings}` |

- **Reacciones:** solo nodos que devuelve `Results.JointReact`; con varios pasos (`StepType`/`StepNum`) se
  toma, por componente, el valor de mayor módulo y se avisa. `sum` = `[ΣF1, ΣF2, ΣF3, null, null, null]`
  (los momentos no se suman sin punto de referencia); es `null` si hubo varios pasos o falta algún valor.
- **Barras en bloque:** una única llamada `Results.FrameForce("ALL")` por petición, no barra a barra.
  SAP2000 devuelve todas las filas de los casos seleccionados y el filtro por `ids` y la paginación se
  hacen en Python (`FrameForce` no admite una lista de barras sin tocar el modelo o la selección de
  objetos). Las páginas siguientes (con `cursor`) reutilizan una caché de 2 minutos de esa lectura; la
  primera página siempre lee de nuevo. Cada barra tiene el mismo formato que `results/frame/{id}`.
  Cursor opaco; página máxima 500 barras; sin `ids` se devuelven todas, ordenadas.
- **Envolvente:** los casos pedidos se seleccionan a la vez y se lee una sola vez; se agrega por
  estación (barras) o nodo (reacciones). Sirve igual para casos sueltos (opción B) que para una
  combinación de tipo envolvente del modelo, que trae filas `Max`/`Min` (opción A). `governing[barra][P|V2|V3|T|M2|M3]`
  da el caso que alcanza el máximo y el mínimo de la barra (`{max, min}`); en reacciones, por nodo y
  componente `F1…M3`. `Min ≤ Max` siempre.
- **Errores:** `404 case_not_found` (caso inexistente en la lista real del modelo), `409 no_results` (sin
  análisis o sin filas), `400 too_many_cases`, `400 bad_cursor`, `400 bad_kind`, `400 no_cases`.
  Las rutas antiguas (`results/frame`, `displacements`, `area`) conservan su código `422 unknown_case`.
- **Selección de salida:** antes de cada lectura se guardan los casos y combinaciones marcados para
  salida y se restauran al terminar (también en `results/frame`, `displacements` y `area`). Si no se
  puede leer la selección, la respuesta lo avisa en `warnings`.
- **Unidades:** `units = {force, length}` y `toMeters` (`null` si la unidad de longitud no se reconoce;
  nunca se supone metros). Nota: `length_factor_to_meters` (usada por la geometría y las secciones)
  sigue cayendo a 1,0 ante una unidad desconocida; no se cambió porque una prueba existente lo fija.
- **No medido:** el rendimiento con un modelo de ≥ 5 000 barras no se ha medido; tampoco nada de esto
  se ha validado con SAP2000 real (solo contra `FakeResultsModel`, en `fake_model.py`). Ver la lista de
  aceptación manual en la especificación de la sesión Viewer.


## Zapatas biaxiales

Rutas del módulo «Zapatas biaxiales» (fase 2) en el agente local, bajo `/v1/sap/actions/zapata-biaxial/`. Código en `bridge/zapata_biaxial.py`. La lógica sin COM (equivalentes, modos de importación, signos, combinaciones y plan de exportación) vive en el motor de cálculo del Workbench. Requieren el mismo `Authorization: Bearer` que el resto del agente y usan el `call` común (timeout de 30 s del actor).

El plan de exportación no lo arma el puente: lo genera el backend con `POST /zapata-biaxial/plan-exportacion`, que no toca SAP2000. El plan se envía tal cual a `exportar/preview` y `exportar/apply`.

Unidades: las lecturas van en tonf y tonf·m, las coordenadas en m y el plan en Ton_m_C.

**Estado de verificación:** ninguna de estas rutas se ha ejecutado contra SAP2000 real. Las pruebas con COM falso están en `tests/test_bridge_zapata_biaxial.py`; ese archivo no estaba confirmado en git al redactar esta sección, y tenía 14 de sus 105 pruebas fallando. Ver la validación manual pendiente en `docs/SAP2000_ACCEPTANCE.md` (sección «Zapatas biaxiales (fase 2)»).

### Rutas

| Ruta | Método | Efecto | Entrada | Salida | ¿Escribe en SAP2000? |
|---|---|---|---|---|---|
| `estado` | GET | Nombre del modelo y si está bloqueado. | — | `{modelo, bloqueado}`. `bloqueado` es `null` si no se pudo leer. | No |
| `casos` | GET | Casos y combinaciones del modelo, y los marcados para salida. | — | `{modelo, casos, combinaciones, seleccion: {casos, combinaciones}, seleccion_desconocida}`. `seleccion_desconocida` es `true` si no se pudo leer alguna marca de salida. | No |
| `nodos` | POST | Lee las coordenadas de los nodos seleccionados (o, sin selección, los restringidos). Con `nodos` en el cuerpo lee las de esos nodos, sin depender de la selección. No modifica la selección. | Sin cuerpo o `{}`: selección actual (una selección vacía o ilegible —`None`, `NumberSelected = 0`, respuesta sin arreglos, solo barras— no es un error; se usa el fallback). `{nodos: [1..500]}`: esos nodos. | Sin `nodos`: `{modelo, origen: "seleccion" \| "restringidos", unidades, columns: ["Joint","X","Y","Z"], data}`. Con `nodos`: lo mismo con `origen: "nodos"`, más `z_min`, `z_max` y `dispersion_z`. Con el fallback (sin nodos seleccionados: los que tienen alguna restricción en `PointObj.GetRestraint`) `origen` es `"restringidos"` y aparece `avisos: ["No había selección: se tomaron N nodos con restricciones."]`; sin ninguno, 409 `sin_nodos` «No hay nodos seleccionados ni con restricciones.». Un nodo inexistente: 409 `nodo_desconocido`. | No |
| `reacciones` | POST | Marca para salida los casos y combinaciones indicados y lee `Results.JointReact` de los nodos. | `{nodos?: [1..500], casos: [...], combinaciones: [...]}`. Sin `nodos` se usan los seleccionados o, sin selección, los que tienen restricciones (mismo fallback que `nodos`; la respuesta añade `origen`, `nodos` y, con fallback, `avisos`). Hace falta al menos un caso o combinación. | `{modelo, unidades: "tonf-m", columns: [Joint, OutputCase, CaseType, StepType, F1, F2, F3, M1, M2, M3], data}` | **Sí**: cambia la selección de salida (desmarca todo y marca lo indicado). No restaura la selección anterior. |
| `exportar/preview` | POST | Revisa el plan contra el modelo: bloqueo, colisiones de nombres y existencia del material. | `{plan}`. El campo `huella` se ignora. | `{ok, modelo, bloqueado, conflictos, avisos, material_existe, resumen, huella}` | No |
| `exportar/apply` | POST | Crea material, secciones, nodos, shells, pedestal, patrones, cargas y resortes del plan, y relee lo creado. | `{plan, huella, confirmed: true}` | `{ok, verificado, creado: {material, secciones_frame, secciones_shell, nodos, shells, frames, patrones, cargas, resortes}, resumen, modelo}` | **Sí** |

**Columnas de `reacciones`:** `Joint, OutputCase, CaseType, StepType, F1, F2, F3, M1, M2, M3` (en este orden, con los valores de siempre: `CaseType` es `Combination` o `LoadCase`) y, añadidas al final, `StepNum` y `CaseTypeSap` (tipo real del caso en SAP2000, de `LoadCases.GetTypeOAPI_1`: `LinStatic`, `LinRespSpec`…; vacío si SAP no lo informa). Claves opcionales de la respuesta, solo si hay algo que avisar: `avisos` y `nodos_ejes_locales` (nudos con ejes locales girados; las reacciones se entregan tal como las devuelve `JointReact`, sin girar: sin confirmar con SAP2000 real). La selección de salida del usuario se restaura al terminar. Con `"sonda": true` (un caso o combinación) devuelve la respuesta cruda de `JointReact` para diagnóstico.

Las comprobaciones de `exportar/preview` no escriben nada. `exportar/apply` repite esas mismas comprobaciones antes de escribir.

### Límites

| Límite | Valor | Dónde se aplica | Respuesta si se supera |
|---|---|---|---|
| Nodos por lectura (seleccionados o restringidos) | 500 | `nodos`; `nodos` del cuerpo de `reacciones` | `nodos`: 409 `demasiados_nodos`. `reacciones`: 422 (validación del cuerpo). |
| Nodos del modelo recorridos sin selección | 20 000 | `nodos` | 409 `demasiados_nodos` |
| Casos y combinaciones por petición | 500 cada uno | `reacciones` | 422 |
| Filas de reacciones en una respuesta | 50 000 | `reacciones` | 409 `demasiadas_filas` |
| Objetos de un plan (nodos, shells, frames, patrones, cargas y resortes) | 25 000 | `exportar/preview` y `exportar/apply` | 422 |
| Nodos de la malla de la zapata | 10 000 | Generador del plan (backend, `POST /zapata-biaxial/plan-exportacion`) | 422 en el backend; el puente no llega a recibir el plan |

### Flujo preview → apply

1. Obtener el plan con `POST /zapata-biaxial/plan-exportacion` del backend. No toca SAP2000.
2. Llamar a `exportar/preview` con el plan. La respuesta trae `huella`: 16 caracteres hexadecimales, resumen SHA-256 truncado del plan serializado con las claves ordenadas.
3. Si `ok` es `false` (modelo bloqueado o colisiones de nombres), no seguir. `exportar/apply` lo rechazaría con 409.
4. Llamar a `exportar/apply` con el mismo plan, la `huella` de la revisión y `confirmed: true`. Sin `confirmed: true` la respuesta es 422 y no se escribe nada.
5. Si la `huella` no coincide con el plan recibido, la respuesta es 409 `plan_cambiado`: hay que repetir la revisión. Si se omite `huella`, no se compara con ninguna revisión; la web debe enviarla siempre.
6. Apply repite la revisión (bloqueo y colisiones). Si ya no pasa, responde 409 `bloqueado` o `conflictos`.
7. Escribe en este orden: material, secciones de frame, secciones de shell, nodos, shells, frames (pedestal), patrones, cargas y resortes.
8. Relee lo creado y lo compara con el plan. Si no coincide, responde 409 `verificacion`. La relectura **no incluye los resortes**: `_verify_applied` no lee `PointObj.GetSpring`.

Si la escritura falla a mitad, la respuesta es 409 `parcial` con `creado`. **No hay deshacer**: el modelo queda con lo que se llegó a crear y hay que revisarlo en SAP2000 antes de volver a intentarlo.

### Errores

Los errores del módulo llegan como `detail: {code, message, ...}`. Los errores de validación de FastAPI (422) llegan como lista. Un error COM no clasificado llega como 409 con `detail` en texto.

| HTTP | `code` | Significado |
|---|---|---|
| 422 | (sin `code`) | Validación del cuerpo: nombres no válidos, listas mal formadas, plan que no está en Ton_m_C, coordenadas o cargas no finitas, rigidez de resorte negativa o más de 25 000 objetos. También `exportar/apply` sin `confirmed: true` (en ese caso `detail` es un texto). |
| 409 | `sin_nodos` | No hay nodos seleccionados ni restringidos. |
| 409 | `demasiados_nodos` | Más de 500 nodos seleccionados, o más de 20 000 nodos en el modelo sin selección. |
| 409 | `caso_desconocido` | Algún caso o combinación pedido no existe en el modelo. |
| 409 | `demasiadas_filas` | Más de 50 000 filas de reacciones. |
| 409 | `sin_resultados` | SAP2000 no devolvió reacciones. Ejecutar el análisis y revisar la selección de resultados. |
| 409 | `bloqueado` | El modelo está bloqueado (tiene resultados) y no admite altas de objetos. Sólo en `exportar/apply`. |
| 409 | `conflictos` | Ya existe un nodo, shell, frame o patrón con ese nombre, sin distinguir mayúsculas. También si el patrón indicado no existe cuando `crear_load_case` es falso. La lista va en `conflictos`. |
| 409 | `plan_cambiado` | La `huella` no coincide con el plan. Repetir la revisión. |
| 409 | `parcial` | Fallo a mitad de la escritura. Incluye `partial: true` y `creado`. |
| 409 | `verificacion` | La escritura terminó, pero la relectura no coincide con el plan. Incluye `partial: true`, `creado` y `fallos` (hasta 20). |
| 409 | `sap_error` | Error de SAP2000 no clasificado (código por defecto). |

### Resortes del suelo (`PointObj.SetSpring`): no validado

`exportar/apply` crea un resorte vertical (solo U3) en cada nodo de la malla de la zapata, no en el nodo superior. La rigidez es uniforme: `Kv · Lx · Ly / n`, donde `n` es el número de nodos de la malla. La llamada usa la firma `SetSpring(Name, K[6], ItemType=0, IsLocalCSys=True, Replace=True)`. Si esa firma falla, prueba una forma corta de dos argumentos.

Ni una ni otra se han probado contra SAP2000 real: sólo con COM falso. Los resortes tampoco se releen tras escribir, así que su comprobación es sólo manual. `exportar/preview` lo indica en `avisos` cuando el plan tiene resortes. Hasta completar la fase E de `docs/SONDA_FASE_E_ZAPATA.md`, no usar los resortes en un modelo que se vaya a analizar.

## Modificación de Fundaciones (Fundaciones SAP2000)

Código: `bridge/modificacion_fundaciones.py` (instalado desde `bridge.actions.install_routes`, mismo envoltorio `call`). Contrato de datos en `docs/PLAN_MODIFICACION_FUNDACIONES.md`; claves en `web/src/bridge_contracts.json`.

* `POST /v1/sap/actions/foundations/modif/frames` `{frames?: [..]}` -> `modif_source_v1` (nodo inferior, huella en planta con giro, nombres existentes). Sin `frames` usa los seleccionados en SAP2000.
* `POST .../modif/catalog` -> `{area_sections, materials, load_patterns, groups}`.
* `POST .../modif/preview` `{plan}` -> `{ok, conflicts, will_create, model_locked}` (solo lectura).
* `POST .../modif/apply` `{plan, expected_hash}` -> `{ok, created, verification:{ok, issues}}`. Orden: sección -> puntos nuevos -> áreas -> restricciones (`PointObj.SetRestraint`, conserva los otros grados del nodo) -> grupo -> carga (`AreaObj.SetLoadUniform`, Dir 6) y relectura completa. `409` si el hash no coincide, el modelo está bloqueado o hay conflictos; un fallo a medias devuelve `{partial: true, created, message}`.

## Nomogramas: frames seleccionados (vista «Columna y nudos»)

Código: `bridge/nomogramas.py` (instalado desde `bridge.actions.install_routes`, mismo envoltorio `call`). Claves en `web/src/bridge_contracts.json`.

* `GET /v1/sap/actions/nomogramas/selected-frames` -> `{ "modelo", "unidades": "N_mm_C", "frames": [...] }`. Solo lee: fija `N_mm_C` durante la lectura y restaura las unidades siempre (también si algo falla). Sin frames seleccionados devuelve `frames: []`.
* Cada frame: `frame`, `section`, `material`, `e_mpa`, `i33_mm4`, `i22_mm4`, `area_mm2`, `length_mm`, `point_i`, `point_j`, `orientacion` (`vertical` si el ángulo con la vertical es menor de 5°, `horizontal` si el ángulo con la horizontal es menor de 5°, si no `inclinada`), `releases` (`i_m2`, `i_m3`, `j_m2`, `j_m3`, booleanos), `modificadores` (`i22`, `i33`) y `note`.
* Un dato aislado que no se puede leer (sección no reconocida, material, E, liberaciones, modificadores, orientación) llega como `null` y se explica en `note`; no falla toda la petición. Un fallo de SAP2000 al leer la selección o la longitud responde 409 `sap_error`.
* Valores no finitos (NaN/inf) en área, I33, I22, E, longitud, modificadores o coordenadas llegan como `null` con `note`, y la respuesta se sanea de forma recursiva (float no finito -> `null`) para que sea siempre JSON válido (Starlette serializa con `allow_nan=False`; un NaN daba 500 sin cabeceras CORS, o sea «Failed to fetch» en el navegador). Una excepción inesperada al leer un frame (TypeError, AttributeError, etc.) deja esa entrada con todo a `null` y un `note`, sin afectar a los demás. `GetPresentUnits` puede devolver un entero o una tupla con el código de retorno.
* El orden del código de retorno se normaliza con `bridge.com_orden.normalizar`.

### Llamadas COM no verificadas contra SAP2000 real

Solo se han probado con COM falso (`tests/test_bridge_nomogramas.py`):

* `PropFrame.GetMaterial(section)`: si falla se prueba el material de `PropFrame.GetISection`, como en `bridge/graph.py`.
* `FrameObj.GetReleases(frame)`: se leen M2 (índice 4) y M3 (índice 5) de los arrays de los extremos i y j.
* `FrameObj.GetModifiers(frame)`: I22 (índice 4) e I33 (índice 5).

Verificar contra un modelo real antes de fiarse de `releases` y `modificadores`. La web solo propone «articulado» a partir de las liberaciones; no lo aplica sin confirmación.

Datos REALES de la sonda (SAP2000 con AISC 360-16, modelo con resultados): la tabla de overwrites es «Overwrites - Steel Design - AISC 360-16» (listada, 45 columnas): Frame, DesignSect, FrameType, Fy, RLLF, AreaRatio, XLMajor, XLMinor, XLLTB, K1Major, K1Minor, K2Major, K2Minor, KLTB, CmMajor... En ella el valor `0` en K y XL significa «Program Determined»: `GET viewer/k` lo devuelve como `auto: true`, `value: null` (solo cuando la tabla usa 0 y no el texto) y las L se reconocen como `XLMajor`/`XLMinor`/`XLLTB` (también con «Unitless»). Para ESCRIBIR «auto» en una tabla que usa 0 se escribe `0` (HIPÓTESIS sin verificar); si la tabla usa el texto, «Program Determined». KLTB se reconoce internamente pero NO forma parte de las 7 claves de `GET viewer/k` (contrato fijo); solo se expone en `GET viewer/k/design`.

## Cargas nodales de Fundaciones: ida y vuelta con Excel (sin SAP2000)

Código: `bridge/excel_reacciones.py` (instalado desde `bridge.actions.install_routes`). No usa COM ni el envoltorio `call`: los errores salen con `detail = {"code", "message"}`. Tests: `tests/test_bridge_excel_reacciones.py`. Claves en `web/src/bridge_contracts.json`.

* `POST /v1/sap/actions/excel/open-reactions` con `{"rows": [{"joint", "load_pat", "coord_sys", "f1", "f2", "f3", "m1", "m2", "m3"}]}` (todos strings, máximo 5000 filas). Escribe `<tempdir>/ew_reacciones/reacciones_<uuid>.xlsx` con la hoja «Cargas» (cabeceras `Joint, LoadPat, CoordSys, F1, F2, F3, M1, M2, M3`; F en Ton, M en Ton·m) y una hoja «Leeme» con esa nota. Las fuerzas y momentos se escriben como número si el texto convierte a un número finito; el resto, como texto. Abre el fichero con `os.startfile` solo si existe (Windows). Respuesta: `{"path", "rows_written", "opened", "note"}`; fuera de Windows `opened: false` y `note` lo explica. Al abrir borra los ficheros de esa carpeta con más de un día.
* `POST /v1/sap/actions/excel/read-reactions` con `{"path"}`. Lee la hoja «Cargas» (`read_only`, `data_only`). Las cabeceras se reconocen sin distinguir mayúsculas ni espacios y en cualquier orden; las filas con `joint` vacío se ignoran; las celdas vacías son `""`. Respuesta: `{"rows": [9 campos como string], "count"}`.
  * Solo lee ficheros `.xlsx` dentro de `<tempdir>/ew_reacciones/` (la ruta se resuelve con `Path.resolve()`).
  * Errores (todos con `detail: {"code", "message"}`):

| Situación | Código HTTP | `code` |
|---|---|---|
| Ruta fuera de la carpeta o extensión distinta de `.xlsx` | 422 | `path_not_allowed` |
| Fichero inexistente | 404 | `file_not_found` |
| Fichero bloqueado (abierto en Excel) | 409 | `file_locked` |
| Sin hoja «Cargas», sin cabeceras o no es un `.xlsx` válido | 422 | `invalid_workbook` |
| Falta `openpyxl` | 409 | `openpyxl_missing` |

Flujo: `open-reactions` → el usuario edita la hoja «Cargas», guarda y cierra Excel → `read-reactions` con el `path` devuelto.

## Fundaciones SAP2000 en el Viewer (`viewer/foundations/*`)

Solo lectura; código en `bridge/viewer/foundations.py` y `bridge/viewer/routes.py`.

* `POST /v1/sap/actions/viewer/foundations/results`: cuerpo `{ "groups": [{ "name", "joints", "shells" }], "cases"?, "combos"? }`. Lee, para la unión de nudos y shells de los grupos, lo mismo que `POST /v1/sap/snapshot` (mismos `step_type` y `step_num`): `coordinates`, `reactions`, `displacements`, `soil_pressures`, `groups`, `model_locked`; más `units: "Ton_m_C"`, `model: {name, path}`, `truncated` y `warnings`. Claves aditivas: `areas` (`[{name, nodes}]`: nudos de cada área pedida, de `AreaObj.GetPoints`) y `faltantes` (`{joints, shells}`: nombres pedidos que no existen en el modelo; se omite si no se pudo leer la lista de nombres). Cada lista de filas se corta en 50.000 y `truncated` pasa a `true`. Sin `cases` ni `combos` se leen todos los casos. A diferencia del snapshot, la selección de salida del usuario y las unidades se restauran al terminar (también si la lectura falla); si no se pudiera restaurar, lo dice `warnings`.
* `GET /v1/sap/actions/viewer/foundations/groups`: `{ "groups": [{ "name", "joints", "shells" }] }` con los grupos de SAP2000 que tienen algún nudo o shell.
* Nombres: `joints` y `shells` son los de `PointObj` y `AreaObj`, los mismos que `nodes.ids` y `areas[].id` de `GET viewer/snapshot`. El snapshot omite (con aviso en `warnings`/`skipped`) los nudos sin coordenadas finitas y las áreas con menos de 3 puntos o con puntos omitidos, así que sus listas pueden ser un subconjunto de los nombres de SAP2000, nunca nombres distintos.

## Acero AISC 360-22: fuerzas de barras y demandas de diseño (validado con SAP2000 23.3.1, sonda v3)

Código: `bridge/acero_aisc360_fuerzas.py` (instalado desde `bridge.actions.install_routes`, mismo envoltorio `call`). Tests: `tests/test_bridge_acero_fuerzas.py` (COM falso). Claves en `web/src/bridge_contracts.json`. Cliente web: `web/src/acero_aisc360/sapFuerzas.ts` (sin conectar a la interfaz).

> **Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14).** Lectura de `FrameForce` con la firma corta, signo de P (compresión negativa), eje fuerte `M3` con V2 acompañando, combinación lineal, envolvente Max/Min y Cb F1-1 con las estaciones por defecto. Registro completo en `docs/TRASPASO_ACERO_AISC360.md`. Las funciones de un solo dato (`GetPresentUnits`, `GetModelIsLocked`) llegan como escalar; el puente acepta también la tupla con código. Con pywin32 la firma larga de `FrameForce` (17 argumentos) no sirve: el puente usa la corta y la larga solo como respaldo.

* `GET /v1/sap/actions/acero-aisc360/combos` -> `{ "combinaciones", "casos" }` (`RespCombo.GetNameList`, `LoadCases.GetNameList`).
* `GET /v1/sap/actions/acero-aisc360/selected` -> `{ "frames": [{ "frame", "seccion", "longitud_m" }] }` con la selección de SAP2000. La longitud sale en m con las unidades actuales del modelo. Solo lee.
* `POST /v1/sap/actions/acero-aisc360/fuerzas` con `{ "frames": [1..500 nombres] | "selected", "combos": [1..300], "estaciones": [x/L entre 0 y 1, opcional] }`.
  * Valores fuera de rango (501 barras, combos vacíos, más de 300 combinaciones, `estaciones` fuera de [0, 1]): 422.
* `por_estacion` (opcional, `false` por defecto): con `true` la respuesta añade, sin cambiar nada de lo anterior, `cb_pasos` en cada demanda (`[{step, Cb, interpolado, forzado, gap_max_m}]` (`gap_max_m`: separación máxima entre estaciones usada para ese Cb): el Cb de cada paso, p. ej. Max y Min por separado; `Cb` sigue siendo el menor) y `demandas_estacion`: una demanda por barra, combinación, paso y estación (`frame, combo, step, x_m, Pu_kN, Tu_kN, Vux_kN, Vuy_kN, Mux_kNm, Muy_kNm, T_kNm, Cb`) calculada solo con las fuerzas de esa estación y paso. Respeta `estaciones`.
  * La web («Traer fuerzas») pide las combinaciones en lotes de hasta 50 y fusiona las respuestas en el navegador; el puente acepta hasta 300 por petición.
  * Barras que no existen en el modelo: se omiten y se avisan en `warnings`. Si ninguna existe, 409.
  * Combinación o caso que no existe en el modelo, o sin barras seleccionadas con `"selected"`: 409 `sap_error`.
  * Respuesta: `{ "unidades": "kN-m", "ejes": "locales 1-2-3", "validado_con_sap": true, "validado_con": "SAP2000 23.3.1 (pywin32), sonda v3", "validado_fecha": "2026-10-14", "frames": [...], "demandas": [...], "envolvente": [...], "warnings": [...] }`.
    * `frames[]`: `frame`, `longitud_m`, `seccion`, `combinaciones[]` (`combo`, `step`, `estaciones[]` con `x_m`, `P_kN`, `V2_kN`, `V3_kN`, `T_kNm`, `M2_kNm`, `M3_kNm`). `estaciones` solo filtra esta salida; las demandas usan todas las estaciones.
    * `demandas[]`: una entrada por barra y combinación (`frame`, `combo`, `Pu_kN`, `Tu_kN`, `Vux_kN`, `Vuy_kN`, `Mux_kNm`, `Muy_kNm`, `T_kNm`, `Cb`, `x_Mmax_m`).
    * `envolvente[]`: una entrada por barra. Cada componente es el máximo sobre las combinaciones; `gobierna` indica la combinación que da cada valor. `Cb` es el mínimo de las combinaciones.

Convención: `FrameForce` en kN, kN·m y m (`SetPresentUnits(6)`, kN_m_C) y restauración de las unidades originales siempre en `finally`. Si la restauración falla, se avisa en `warnings`. P de tracción positiva: `Pu = max(-P_min, 0)`, `Tu = max(P_max, 0)` (validado: compresión = P negativa).

Criterios de cálculo:

* Eje fuerte 3-3 (`EJE_FUERTE = "M3"`, validado): `Mux = max|M3|`, `Vux = max|V2|`. Eje débil 2-2: `Muy = max|M2|`, `Vuy = max|V3|`. Cambiar la constante invierte la asignación.
* `Cb` (AISC 360-22 Ec. F1-1) con |M| del eje fuerte en L/4, L/2, 3L/4 y el máximo del tramo. Validado: viga con estaciones en L/4, L/2 y 3L/4 da Cb = 1,1364 (sonda v3 y sonda v4, F4-4 «seg0.5»). Si no hay estación en esas posiciones (p. ej. un voladizo) se interpola linealmente sobre el momento con signo y se avisa en `warnings`. El momento en los extremos no entra en F1-1 y no se usa.
* Regla del Cb con pocas estaciones: si el Cb se ha interpolado y la mayor separación entre estaciones consecutivas de un paso supera L/10, el Cb de esa combinación y paso pasa a 1,0 (conservador) y se avisa («solo N estaciones de salida en SAP2000»). La envolvente toma el mínimo, así que el 1,0 se propaga. Causa: la interpolación lineal de un diagrama de momentos curvo subestima M en L/4 y 3L/4 y sube el Cb (error ~ cuadrado de la separación). Sonda v4, viga de 6 m con carga uniforme (Cb teórico 1,136): 3 estaciones (0, 3 y 6 m) dan 1,316 (+16 %); 11 estaciones (cada 0,6 m) dan 1,143 (+0,6 %) y se mantienen con aviso de interpolado; 13 estaciones (cada 0,5 m) dan 1,1364 exacto. Solución en SAP2000: Assign > Frame > Output Stations con al menos 11 secciones o segmento máximo 0,5 m, y volver a traer fuerzas. Sin longitud medida en SAP2000 la regla no se aplica.
* `Lb` no sale de SAP2000 y no se calcula aquí; la comprobación de pandeo lateral la define el usuario (`warnings` lo recuerda).

Límites de la acción:

* La envolvente es por componente sobre todas las combinaciones: es conservadora, porque cada componente puede venir de una combinación distinta. `gobierna` indica cuál.
* Signo axial: `Pu` y `Tu` van siempre en magnitud. Si P cambia de signo dentro de una combinación (entre estaciones o pasos), ambas salen > 0 y se diseñan a la vez; el motor evalúa la compresión y la tracción por separado y cada una crece con su solicitud, así que es equivalente a evaluar los dos casos y quedarse con el mayor. Quedarse sólo con el caso dominante no es conservador. Se avisa en `warnings`. En la interacción H1-1 `Tu` entra frente a φtPn (§H1.1) y, en perfiles I de doble simetría, §H1.2 amplifica Cb para la rama de tracción (ver `docs/MODULES_SOURCE.md`, «Interacción con tracción»). En la web, C junto a Pu y T junto a Tu.
* `SetOutputStations` (estaciones adicionales en la viga) no se usa en la acción: su firma es `(Name, MyType, MaxSegSize, MinSections, …)`. La sonda v4 (F4-4) confirmó que SAP2000 la acepta con MyType (sección «Resultado F4-4 (16-oct-2026)» de `docs/SONDA_ACERO_AISC360_V4.md`); el puente sigue sin pedir estaciones.

## Acero AISC 360-22: enviar perfiles a SAP2000 y asignarlos (validado con SAP2000 23.3.1, sondas v3 y v4)

Código: `bridge/acero_aisc360_enviar.py` (instalado desde `bridge.actions.install_routes`, mismo envoltorio `call`). Tests: `tests/test_bridge_acero_enviar.py` (COM falso). Claves en `web/src/bridge_contracts.json`. Cliente web: `web/src/acero_aisc360/sapEnviar.ts` (sin conectar a la interfaz). Las secciones se escriben con `bridge.actions.create_section`; las asignaciones, con `FrameObj.SetSection`.

> **Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14):** creación por tipo (`SetISection`, `SetChannel`, `SetAngle`, `SetTube`, `SetPipe`), relectura de las secciones, `FrameObj.SetSection` y `GetSection`, sobrescritura (SAP2000 sobrescribe en silencio), asignación parcial sin reversión automática, desbloqueo y su efecto sobre los resultados. Registro en `docs/TRASPASO_ACERO_AISC360.md`.
>
> **Validado con SAP2000 23.3.1 (sonda v4, 2026-10-16):** relectura posición a posición de la geometría de I, canal, ángulo, tubo y pipe con `PropFrame.Get*` tras enviarlos por las rutas del puente (diferencia 0,000 mm), y el control negativo del comparador. Revertir, con una I, restaura la sección previa y desbloquea. Resultados en `docs/SONDA_ACERO_AISC360_V4.md`, sección 10.

* `POST /v1/sap/actions/acero-aisc360/enviar/preview`: solo lecturas COM, no escribe. Cuerpo: `{ "perfil": { "nombre", "tipo", "dimensiones_mm" }, "material", "barras": [1..500 nombres] | "selected", "sobrescribir": false, "desbloquear": false }`.
  * `tipo`: `I`, `canal`, `angulo`, `tubo_rect`, `tubo_circ` o `pipe`. Una tee u otro tipo: 422 con mensaje. Una tee no está soportada.
  * `dimensiones_mm` según el tipo: rectangulares (`I`, `canal`, `angulo`, `tubo_rect`) con `d`, `bf`, `tf`, `tw` (en `tubo_rect` también `t3` y `t2` como alias de `d` y `bf`); circulares (`tubo_circ`, `pipe`) con `D` y `t`. Una clave que no aplica al tipo, o que falta, es 422.
  * Llamada por tipo: `I` → `SetISection(nombre, material, d, bf, tf, tw, bf, tf, …)`; `canal` → `SetChannel`; `angulo` → `SetAngle`; `tubo_rect` → `SetTube`; `tubo_circ` y `pipe` → `SetPipe(nombre, material, D, t)`. Un HSS circular se envía como Pipe.
  * Respuesta: `{ "preview": true, "will_mutate": false, "validado_con_sap": true, "validado_con": "SAP2000 23.3.1 (pywin32), sonda v3", "validado_fecha": "2026-10-14", "seccion": { "nombre", "tipo_sap", "accion": "crear" | "sobrescribir" | "existe_sin_sobrescribir" }, "material": { "nombre", "existe" }, "barras": [{ "frame", "seccion_actual", "cambia" }], "cambios", "sin_cambio", "modelo_bloqueado", "warnings" }`.
  * Avisos: sección existente, material inexistente, modelo bloqueado (desbloquear borra los resultados del análisis), barras que no existen, barras fuera de la lista que usan una sección que se sobrescribe.
* `POST /v1/sap/actions/acero-aisc360/enviar/apply`: mismo cuerpo. Valida igual que `preview` y después escribe:
  * Material inexistente: 422 con los materiales disponibles. Sección existente con `sobrescribir: false`: 409 `section_exists`. Sin escribir nada en ambos casos.
  * Sobrescribir exige `sobrescribir: true`: es la confirmación explícita del puente, porque SAP2000 sobrescribe sin avisar.
  * Modelo bloqueado con `desbloquear: false`: 409 `modelo_bloqueado`, sin cambios. Con `desbloquear: true` se llama a `SetModelIsLocked(False)` y la respuesta trae `modelo_desbloqueado: true` y `resultados_invalidados: true`, con un aviso: desbloquear BORRA los resultados del análisis (`FrameForce` deja de devolver filas), así que hay que volver a analizar el modelo.
  * Crea la sección (o la sobrescribe con `sobrescribir: true`), asigna cada barra que cambia con `FrameObj.SetSection` y relee `FrameObj.GetSection` de todas las barras.
  * Fallo a mitad: no se continúa y no se revierte automáticamente. Responde 409 con `code: "partial"`, `partial: true`, `fallida`, `aplicadas` (barra y sección previa de cada una), `revertir` (`{barra: sección_previa}`), `resultados_invalidados`, `validado_con_sap` (true solo si el tipo es I, ver abajo) y un aviso para usar `revertir`.
  * Respuesta OK: `{ "ok", "partial": false, "validado_con_sap": (true solo para I), "validado_con": "SAP2000 23.3.1 (pywin32), sonda v3", "validado_fecha": "2026-10-14", "seccion": { "nombre", "tipo_sap", "accion": "creada" | "sobrescrita" }, "modelo_desbloqueado", "resultados_invalidados", "cambios", "sin_cambio", "aplicadas", "revertir", "verificacion": [{ "barra", "esperado", "leido", "ok" }], "warnings" }`. Si alguna relectura no coincide, `ok` es `false` (la respuesta sigue siendo 200).
  * Las unidades se fuerzan a N_mm_C y se restauran siempre en `finally`. Si la restauración falla, se avisa en `warnings`.
* `POST /v1/sap/actions/acero-aisc360/enviar/revertir`: `{ "revertir": {barra: sección}, "desbloquear": false }`. Reasigna con `SetSection` (sin unidades) y verifica igual que `apply`; devuelve `validado_con_sap: false` (la sonda v4 solo lo probó con una I y la operación no recibe el tipo), `validado_con` y `validado_fecha`, más `resultados_invalidados`. Las secciones que ya no existen dan 422. Un fallo a mitad da el mismo `partial`, con `revertir` apuntando a lo que había antes de esa llamada.

Límites de la acción:

* Fallo a mitad: sin reversión automática (validado en la sonda E2). Las barras aplicadas quedan así hasta usar `revertir`.
* Revertir no restaura una sección sobrescrita (sus propiedades anteriores se pierden) ni borra una sección nueva; ambos casos se avisan en `warnings`.
* La geometría de una sección I se relee con `PropFrame.GetISection` (en mm dentro de `apply`) y se compara con lo enviado; si no coincide o no se puede leer, `ok` es `false`. Para canal, ángulo, tubo y pipe la acción no relee la geometría: solo comprueba que el nombre existe. La sonda v4 sí la comparó con `PropFrame.Get*` (D4-1).
* `validado_con_sap` depende de la operación y, en `apply`, del tipo (función `validacion`; tabla en `tests/test_bridge_acero_validado_tipo.py`). `preview` es true para todos los tipos. `apply` y el apply parcial son true para I, canal, ángulo, tubo y pipe (sonda v4, D4-1); cualquier otro tipo es false. `revertir` es false: la sonda v4 (D4-2) lo probó con una I y la operación no recibe el tipo. En canal, ángulo, tubo y pipe el aviso «Geometría de canal/ángulo/tubo/pipe…» en `warnings` recuerda que la acción no relee la geometría y que hay que verificar en su versión de SAP2000.
* Desbloquear borra los resultados del análisis: hay que volver a analizar antes de leer fuerzas.
* Nombres: `*` y espacios se aceptan (validado). La acción rechaza `/`, `:`, `?` y similares con 422 por su propia regla de nombres, aunque SAP2000 acepte `/` (sonda D1).

## CAESAR II → SAP2000: transferencia de cargas nodales (validado con COM falso; pendiente de SAP2000 real)

Código: `bridge/caesar_cargas.py` (instalado desde `bridge.actions.install_routes`, mismo envoltorio `call`). Tests: `tests/test_bridge_caesar_cargas.py` (COM falso). Claves en `web/src/bridge_contracts.json`. El módulo web `web/src/caesar_sap2000/` todavía no llama a estas rutas; su cliente se hace aparte.

> **Estado: validado con COM falso; pendiente de validar con SAP2000 real.** Las firmas COM (`LoadPatterns.Add`, `LoadPatterns.GetLoadType`, `PointObj.SetLoadForce`, `GetPresentUnits`/`SetPresentUnits`) se han tomado del código existente y de la sonda de Acero (`docs/SONDA_ACERO_AISC360.md`), no de una prueba de esta acción. **La prueba con un modelo real queda pendiente.** Hasta entonces no hay código de validación (`validado_con_sap`) en estas respuestas.

Rutas (todas bajo `/v1/sap/actions/caesar/`):

| Método | Ruta | Escribe | Cuerpo | Respuesta |
|---|---|---|---|---|
| GET | `modelo` | no | - | `{ok, locked, unidadesModelo, nPuntos, puntosTruncado, puntos, loadPatterns}` |
| POST | `cargas/preview` | no | `CargasBody` | `{ok, locked, unidadesModelo, unidadesEnvio, patterns, nodos, cargas, cargasPrevias, avisos}` |
| POST | `cargas/apply` | sí | `CargasBody` + `confirmarBloqueo` | `{ok, estado, patternsCreados, aplicadas, fallidas, nFallidas, unidadesRestauradas, unidadesModelo, modeloDesbloqueado, avisos}` |

Cuerpo (`extra` prohibido: una clave desconocida es 422):

```json
{
  "unidades": { "fuerza": "kN", "longitud": "m" },
  "crearLoadPatterns": true,
  "loadPatterns": [{ "name": "VIENTO", "type": "Other" }],
  "cargas": [
    { "nodo": 1, "loadPattern": "VIENTO", "fx": 1.5, "fy": 0, "fz": -2.0 },
    { "nodo": 9, "loadPattern": "DEAD", "fx": 0, "fy": 0, "fz": -4.0 }
  ],
  "modo": "reemplazar"
}
```

* `unidades.fuerza`: `kgf`, `N`, `kN`, `tonf`, `lb` o `kip`. `unidades.longitud`: `m`, `mm`, `cm`, `ft` o `in`. Solo se admiten las 16 combinaciones que existen en la tabla eUnits (`EUNITS_POR_UNIDADES`): `lb-in`, `lb-ft`, `kip-in`, `kip-ft`, `kN-mm`, `kN-m`, `kgf-mm`, `kgf-m`, `N-mm`, `N-m`, `tonf-mm`, `tonf-m`, `kN-cm`, `kgf-cm`, `N-cm` y `tonf-cm`. Otra combinación (p. ej. `kgf-ft`) es 422 con la lista.
* `crearLoadPatterns`: si es `true`, se crean los patrones de `loadPatterns` que no existan. Si es `false`, `loadPatterns` se ignora (aviso).
* `loadPatterns[].type`: solo `Dead` (1) y `Other` (8), los dos códigos verificados con la sonda. Por defecto `Other`. Otro tipo es 422 hasta comprobar su código en SAP2000 real.
* `cargas[]`: `nodo` (entero >= 1, nombre del nodo en SAP2000), `loadPattern`, `fx`, `fy`, `fz` (finitos). **No hay componentes de momento.** Una clave `mx`, `my` o `mz` es 422.
* `modo`: `reemplazar` (`SetLoadForce` con `replace=true`: sustituye la carga existente del mismo nodo y patrón) o `sumar` (`replace=false`: suma).

Límites (422 si se superan): hasta 200 000 cargas (al menos 1), 2 000 patrones, nombres válidos para SAP2000 (sin `\ / : * ? " < > |`, hasta 128 caracteres), sin nodo repetido para el mismo patrón (sin distinguir mayúsculas), sin nombres de patrón repetidos y valores finitos.

**GET modelo.** `puntos` lista los nombres de los nudos. Si hay más de 50 000, `puntos` va vacío, `puntosTruncado` es `true` y `nPuntos` da el recuento. `loadPatterns[]` trae `{name, type}`; `type` es `desconocido` si no se puede leer. `unidadesModelo` es `null` si no se pueden leer las unidades.

**POST cargas/preview.** Solo lecturas COM.
* `patterns`: `existentes` (ya están en el modelo, sin distinguir mayúsculas), `aCrear` (de `loadPatterns`, no están en el modelo) y `faltantes` (usados en `cargas`, no están y no se crean).
* `nodos`: `total` (nodos distintos en `cargas`), `encontrados`, `faltantes` (primeros 200, como enteros) y `nFaltantes`.
* `cargas`: `total`, `conValor` y `ceros` (FX, FY y FZ todas a cero).
* `cargasPrevias`: nº de pares nodo x patrón existente que ya tienen carga en SAP2000 (cuenta solo pares con valor distinto de cero). `null` si hay más de 2 000 nodos a leer o si falla la lectura. Es un coste de una lectura por nodo.
* `ok`: `true` si no hay nodos ni patrones que impidan aplicar tal cual.
* Avisos: momentos no transferidos, unidades que se usarán, significado de `modo`, cargas nulas que no se escriben, nodos inexistentes, patrón con el mismo nombre que un caso de carga (SAP2000 da al caso otro nombre) y modelo bloqueado.

**POST cargas/apply.** Mismo cuerpo, más `confirmarBloqueo` (por defecto `false`).

Ejemplo de respuesta correcta (200):

```json
{
  "ok": true,
  "estado": "ok",
  "patternsCreados": ["VIENTO"],
  "aplicadas": 2,
  "fallidas": [],
  "nFallidas": 0,
  "unidadesRestauradas": true,
  "unidadesModelo": "kN · m · °C",
  "modeloDesbloqueado": false,
  "avisos": ["Solo se transfieren FX, FY y FZ: los momentos Mx, My y Mz se envían a cero.", "..."]
}
```

Errores (el cuerpo va en `detail`, como en el resto del puente):

| HTTP | Cuándo | Cuerpo | Escribe |
|---|---|---|---|
| 422 | cuerpo inválido, unidades no soportadas | validación de FastAPI | no |
| 422 | patrones que no existen y no se crean | `detail` (texto) | no |
| 409 | modelo bloqueado sin `confirmarBloqueo` | `{code: "model_locked", message}` | no |
| 409 | fallo a mitad o alguna carga no aplicada | `{ok: false, estado: "partial", code: "partial", partial: true, error, patternsCreados, aplicadas, fallidas, nFallidas, unidadesRestauradas, unidadesModelo, modeloDesbloqueado, avisos}` | sí, lo ya aplicado |

**Qué escribe.** `LoadPatterns.Add` solo para los patrones que no existen (peso propio 0 y con caso de análisis); `PointObj.SetLoadForce(nodo, patrón, [FX, FY, FZ, 0, 0, 0], replace, "Global")` por cada carga no nula; `SetPresentUnits` temporal (ver abajo); `SetModelIsLocked(False)` solo con `confirmarBloqueo=true` y bloqueo.

**Qué no escribe.** No borra cargas: una carga con FX, FY y FZ a cero no se escribe (SAP2000 devuelve error con una carga nula), así que en modo `reemplazar` tampoco borra la existente. No transfiere momentos. No modifica patrones existentes (tipo ni peso propio). No crea nudos, combinaciones ni casos. No analiza el modelo.

Política de unidades (set-apply-restore): se guarda `GetPresentUnits` (escalar o tupla con el código de retorno), se fijan las unidades del envío y se restauran en `finally`, aunque falle la escritura. Si no se pueden restaurar, `unidadesRestauradas` es `false` y se avisa en `avisos` (la respuesta sigue siendo 200 si la escritura fue bien). Si las unidades ya son las del envío, no se tocan.

Modelo bloqueado: con `confirmarBloqueo=false`, `apply` es 409 `model_locked` y no escribe nada. Con `confirmarBloqueo=true` se llama a `SetModelIsLocked(False)`, la respuesta trae `modeloDesbloqueado: true` y un aviso: desbloquear **BORRA los resultados del análisis**; hay que volver a analizar el modelo antes de leer fuerzas.

Fallos parciales: un nodo que no existe va a `fallidas` con el motivo `nodo no existe en el modelo` y no se llama a `SetLoadForce` para él. Una carga que SAP2000 rechaza o que lanza excepción también va a `fallidas` y la escritura sigue. No hay reversión automática: la respuesta es 409 `partial` con lo aplicado. Si falla la creación de un patrón, o hay un error inesperado, no se escribe ninguna carga más. `fallidas` es una lista de hasta 200 entradas; `nFallidas` da el total.

Tiempo: `apply` va con 600 s de tiempo límite en el actor COM (`bridge/com_tiempo.py`), porque el valor por defecto (30 s) no basta con decenas de miles de cargas. El tiempo real con 200 000 cargas está por medir en SAP2000.

Registro: una línea de `logging` por apply con recuentos (aplicadas, fallidas, patrones creados, unidades restauradas, error). No incluye nombres de nodo, de patrón ni valores.

Pendiente de validar con SAP2000 real (lista para la siguiente sonda):
* `LoadPatterns.Add(nombre, tipo, 0.0, True)` con `Other` (8) y `Dead` (1), y su comportamiento con un nombre igual al de un caso de carga (sonda previa: el caso recibe sufijo).
* `LoadPatterns.GetLoadType(nombre)` → `[ret, tipo]`.
* `PointObj.SetLoadForce` con `replace=true` y `replace=false`: que sustituya y que sume, y el código de retorno con una carga nula (se ha visto 1).
* `GetPresentUnits`/`SetPresentUnits` con las 16 combinaciones de la tabla.
* `SetModelIsLocked(False)` y el borrado de resultados que provoca.
* Tiempo de `apply` con 10 000 y 200 000 cargas.
* Prueba de extremo a extremo con un modelo real: crear patrones, aplicar, releer con `PointObj.GetLoadForce` y comparar.

### CAESAR II → SAP2000: lecturas para el módulo web (`seleccion` y `cargas/existentes`)

Dos rutas de **solo lectura** para que la web vea la selección de SAP2000 y las cargas que ya hay en los nudos antes de escribir. Validadas con COM falso (`tests/test_bridge_caesar_cargas.py`); pendientes de SAP2000 real como el resto del módulo. No cambian la selección, no escriben en el modelo y no tocan los patrones mostrados.

| Método | Ruta | Escribe | Cuerpo | Respuesta |
|---|---|---|---|---|
| GET | `seleccion` | no | - | `{ok, nPuntos, puntos, puntosTruncado, nodos, noNumericos, nNoNumericos, unidadesModelo}` |
| POST | `cargas/existentes` | no | `{unidades, loadPatterns, nodos}` | `{ok, unidadesEnvio, unidadesModelo, unidadesRestauradas, cargas, nCargas, cargasTruncadas, nodosInexistentes, nNodosInexistentes, patternsInexistentes, avisos}` |

**GET seleccion.** Puntos (joints) seleccionados ahora, con `selected_objects(model, "joints")` (`SelectObj.GetSelected`, tipo de objeto 1). Sin selección responde `ok: true` con listas vacías, no es un error.
* `puntos`: nombres como texto, como mucho 50 000; por encima, `puntosTruncado` es `true` y `nPuntos` da el total.
* `nodos`: los nombres que son un entero >= 1 escrito sin ceros a la izquierda. `«007»` y `«0»` **no** son nodo: van a `noNumericos`. Sin tope propio (no supera el nº de nudos del modelo); es la lista que la web debe usar para pedir las cargas.
* `noNumericos`: los nombres que no son nodo CAESAR utilizable, como mucho 200; `nNoNumericos` da el total.
* `unidadesModelo`: `null` si no se pueden leer las unidades.

**POST cargas/existentes.** Cuerpo (`extra` prohibido, una clave desconocida es 422):

```json
{
  "unidades": { "fuerza": "kN", "longitud": "m" },
  "loadPatterns": ["DEAD", "VIENTO"],
  "nodos": [1, 2, 9]
}
```

* `unidades`: las mismas 16 combinaciones que `cargas/apply` (`EUNITS_POR_UNIDADES`).
* `loadPatterns`: de 1 a 2 000 nombres válidos, sin repetidos (sin distinguir mayúsculas). Vacío es 422.
* `nodos`: de 1 a 200 000 enteros >= 1. Los repetidos se ignoran.
* Lectura **por objeto**: `PointObj.GetLoadForce` por nodo, con el helper `_existing_loads_no_units` de `bridge/actions.py`. No se usa la tabla «Joint Loads - Force» por lote, porque solo ve los patrones mostrados, ni `all_load_patterns_displayed` (`bridge/viewer/sap2000/reader_utils.py`), que cambia la selección de patrones mostrados de la tabla y es más invasivo de lo que hace falta aquí.
* Unidades set-apply-restore: se guardan las actuales (`GetPresentUnits`, escalar o tupla), se fijan las del cuerpo para leer y se restauran **siempre** en `finally`, también si la lectura falla. Si no se pueden restaurar, `unidadesRestauradas` es `false` y se avisa en `avisos`.
* Solo FX, FY y FZ. Los momentos no se devuelven (aviso).
* Entradas del mismo nodo y patrón, también de distinto sistema de coordenadas, se **suman**; si hay alguna que no es `Global`, se avisa.
* `cargas`: solo las que tienen alguna componente distinta de cero, ordenadas por nodo y, dentro del nodo, por el orden de `loadPatterns`. Como mucho 200 000; `nCargas` es el total encontrado y `cargasTruncadas` indica si se ha recortado.
* `nodosInexistentes` (como mucho 200; `nNodosInexistentes` da el total) y `patternsInexistentes` no hacen `ok` falso: la lectura se completó y la web decide qué hacer con ellos. Los nodos y patrones inexistentes no se leen.
* Errores: cuerpo inválido, 422 de validación. Error de SAP2000 al leer, 409 con `{code: "sap_error", message}`.
* Tiempo límite de lectura: 120 s. Cada nodo es una llamada COM, así que con muchos nodos el tiempo crece; está por medir en SAP2000 real.

Pendiente de validar con SAP2000 real: `SelectObj.GetSelected` con una selección real (incluida la forma con el retorno al inicio), que `PointObj.GetLoadForce` por objeto devuelva los patrones no mostrados, el sistema de coordenadas de las filas y el tiempo con 200 000 nodos.
