# Ayudante local de documentos (puente en el PC del usuario)

Este documento describe el servicio de documentos Word del puente local
(`bridge/documentos_locales.py`), el contrato de rutas para la web y lo que
guarda el servidor como respaldo. Lo consume la web alojada; la integración de
la web no forma parte de este cambio.

## 1. Arquitectura

```
┌──────────────────────────┐   HTTP (Bearer)    ┌────────────────────────────────┐
│ Web alojada (Render, …)  │ ─────────────────▶ │ Puente local 127.0.0.1:8765     │
│ interfaz del informe     │                    │ bridge/sap2000_agent.py         │
└────────────┬─────────────┘                    │  └─ bridge/documentos_locales.py│
             │ bundles (respaldo)               └──────────────┬─────────────────┘
             ▼                                                 │ lee y escribe
┌──────────────────────────┐                    ┌──────────────▼─────────────────┐
│ Servidor (respaldo)      │                    │ PC del usuario                  │
│ backend/routers/projects │                    │ Memoria.docx                    │
│ guarda copia de cada     │                    │ Memoria.report_manifest.json    │
│ paquete de módulo        │                    │ Memoria_reporte/<modulo>/…      │
└──────────────────────────┘                    │ report_backups/ y report_logs/  │
                                                └────────────────────────────────┘
```

- **La web es sólo la interfaz.** No decide dónde está el informe.
- **El archivo de trabajo vive en el PC.** El .docx se modifica en su sitio; el
  manifiesto y la carpeta `<nombre_del_docx>_reporte/` quedan junto a él.
- **El servidor es respaldo.** Sigue recibiendo los paquetes por
  `POST /projects/{id}/bundles`. El espejo local lo escribe el puente, no el
  servidor.
- **Mismo motor.** El puente importa `backend.report_builder` (el motor de
  informes que ya usa el backend). No hay una segunda implementación de anclas,
  manifiesto ni sincronización.

## 2. Qué hay en el PC

| Elemento | Ruta | Quién lo crea |
|---|---|---|
| Registro de documentos | `%LOCALAPPDATA%\EngineeringWorkbench\documentos.json` | el puente (`pick`) |
| Documento de trabajo | la ruta que eligió la persona | nadie: es el suyo |
| Manifiesto | `<nombre>.report_manifest.json`, junto al .docx | `prepare` |
| Espejo de módulo | `<nombre>_reporte\<slug_modulo>\<slug>.json` e `imagenes\*.png` | `export-module` |
| Respaldos del .docx | `report_backups\` (junto al .docx) | sincronización y anclas |
| Registros de sincronización | `report_logs\` (junto al .docx) | sincronización |

`documentos.json` tiene esta forma (máximo 200 documentos; los más antiguos se
descartan):

```json
{
  "version": 1,
  "documentos": {
    "3f9c0e1a7b2d4c58a1e6f0d9b7c2a4e8": "C:\\Informes\\Memoria.docx"
  }
}
```

Al arrancar, el puente carga este archivo. Cada documento se marca con
`available: false` si ya no existe o ya no es un .docx válido. El registro no
guarda nada del contenido del documento.

## 3. Contrato de rutas

Todas las rutas van bajo `/v1/documentos/` y exigen el token del puente
(`Authorization: Bearer …`). El `doc_id` es un identificador opaco de 32
caracteres hexadecimales (uuid). Las rutas `GET` lo reciben como query
(`?doc_id=`); las `POST` en el cuerpo. Los errores llevan `detail` (texto para
la persona), igual que el resto del puente.

| Método y ruta | Cuerpo / query | Respuesta de éxito | Errores |
|---|---|---|---|
| `POST /v1/documentos/pick` | — | `cancelled`, `doc_id`, `nombre`, `carpeta_mostrar` | 501 sin diálogo disponible |
| `GET /v1/documentos/list` | — | `documentos[]` | — |
| `POST /v1/documentos/prepare` | `{"doc_id"}` | `ok`, `manifest_path`, `created`, `title_count`, `positions`, `warnings` | 404 id desconocido; 409 Word lo tiene abierto o ya no está; 422 no es un .docx válido |
| `GET /v1/documentos/positions` | `?doc_id` | `positions[]` | 404, 409 |
| `GET /v1/documentos/manifest` | `?doc_id` | `manifest` | 404 sin manifiesto (llame antes a `prepare`); 422 manifiesto inválido |
| `POST /v1/documentos/anchors` | ver §3.1 | `ok`, `tag`, `module_id`, `json_relativo` | 422 (fuente, esquema, Word bloqueado), 404 id |
| `DELETE /v1/documentos/anchors/{tag}?doc_id=` | — | `ok` | 404 sección no encontrada; 422 Word bloqueado |
| `POST /v1/documentos/sync` | `{"doc_id", "dry_run", "force"}` | `ok`, `dry_run`, `results[]` | 422 (motor o Word), 404 sin manifiesto |
| `POST /v1/documentos/export-module` | ver §3.2 | `slug`, `json_relativo`, `n_imagenes`, `ruta` | 413 >20 MB; 422 ZIP o módulo no válido |
| `POST /v1/documentos/open-in-word` | `{"doc_id"}` | `ok`, `nombre` | 500 si el sistema no puede abrirlo |
| `GET /v1/documentos/storage` | `?doc_id` (opcional) | `modo`, `registro`, `documento` | 404, 409 |
| `GET /v1/documentos/backup` | `?doc_id` | ZIP (`application/zip`) con el .docx y, si existe, su manifiesto (§3.4) | 404, 409 (Word ya no está en su ruta), 413 >20 MB |

`dry_run` vale `true` por defecto, como en la ruta de sincronización del backend.

### 3.1 Anclas: `source`

`source` indica de dónde sale el JSON del módulo. Hay dos casos:

- **Una ruta relativa dentro de `_reporte`**, por ejemplo `esbelteces/esbelteces.json`.
  Debe existir ya (lo deja `export-module`). Se rechaza cualquier ruta absoluta,
  con `..` o que salga de la carpeta.
- **`json_inline`**: el JSON viaja en `datos`. El puente lo valida contra el
  esquema del módulo **antes** de escribir nada y, si es válido, lo guarda como
  espejo en `_reporte/<slug>/<slug>.json`. No toca las imágenes de ese módulo.

```json
{
  "doc_id": "3f9c0e1a7b2d4c58a1e6f0d9b7c2a4e8",
  "module_id": "esbelteces",
  "source": "json_inline",
  "datos": { "results": [ { "frame_name": "V1", "section_name": "W310" } ] },
  "position_index": 3,
  "subchapter": false,
  "insert_at_start": false
}
```

Respuesta:

```json
{
  "ok": true,
  "tag": "report:esbelteces:seccion",
  "module_id": "esbelteces",
  "json_relativo": "Memoria_reporte/esbelteces/esbelteces.json"
}
```

`position_index` sale de `GET /v1/documentos/positions`. Los `module_id` válidos
son los que descubre el motor (`esbelteces`, `fundaciones`, `combinaciones`,
`espectro`, `diagrama_interaccion`, `nomogramas`, `hormigon_simple`,
`equilibrio_plastico`, `sulzberger`, `bombas`, `placas_base`).

### 3.2 Exportar un módulo: `bundle_base64`

`bundle_base64` es un ZIP en base64, con la misma forma que el paquete que la
web ya sube al servidor (un JSON de módulo y, si lo tiene, sus imágenes).

```json
{
  "doc_id": "3f9c0e1a7b2d4c58a1e6f0d9b7c2a4e8",
  "module_id": "esbelteces",
  "bundle_base64": "UEsDBBQAAAAIA…"
}
```

Reglas (idénticas a `POST /projects/{id}/bundles`):

- Exactamente un JSON de módulo; el módulo detectado por contenido debe coincidir con `module_id`.
- Tipos permitidos: `docx`, `json`, `png`, `jpg`, `jpeg`, `csv`, `xlsx`.
- Máximo 20 MB por paquete, 200 archivos y 100 MB descomprimidos. Máximo 20 MB por archivo.
- Las rutas del ZIP no pueden ser absolutas, contener `..` ni `:`, ni repetirse.
- Las referencias de imagen del JSON (relativas a su carpeta dentro del ZIP) se
  reescriben a `imagenes/<nombre>.png`.

Respuesta:

```json
{
  "slug": "esbelteces",
  "json_relativo": "Memoria_reporte/esbelteces/esbelteces.json",
  "n_imagenes": 2,
  "ruta": "C:\\Informes\\Memoria_reporte\\esbelteces"
}
```

### 3.4 Copia de respaldo: `backup`

`GET /v1/documentos/backup?doc_id=` devuelve un ZIP con dos entradas, nombradas
como el archivo en el PC: el .docx (`Memoria.docx`) y su manifiesto
(`Memoria.report_manifest.json`) si existe. No cambia ni el documento ni el
manifiesto. La web lo usa para la copia de respaldo en el servidor tras una
sincronización correcta (la sustitución de la copia anterior la hace la web).
Como las demás rutas, sólo la web alojada tiene acceso (token del puente y
alcance del origen, igual que el resto de `/v1/documentos/*`). En
`web/src/bridge_contracts.json` figura como `{"keys": [], "binario": "application/zip"}`.

### 3.5 Formato del manifiesto (rutas relativas)

El manifiesto `<nombre>.report_manifest.json` vive junto al `.docx` y **nunca guarda rutas
absolutas del PC** (no lleva el nombre de usuario de Windows ni las carpetas). Sus rutas son
relativas a su propia carpeta, con `/`:

- `project.docx_path`: el nombre del documento, p. ej. `Memoria.docx`.
- `modules[].source_json`: `Memoria_reporte/fundaciones/fundaciones.json` (espejo de un
  documento vinculado) o `<uuid>.json` (almacén del servidor, misma carpeta).
- `modules[].last_sync.docx_backup`: `report_backups/Memoria_<fecha>.docx`.

Así la carpeta entera (Word, manifiesto y `_reporte`) puede moverse sin reanclar nada, y la copia
del manifiesto que se guarda en el servidor no revela rutas del equipo. `..` sólo aparece si el
usuario lo pide explícitamente (p. ej. reasignar el documento a otra carpeta).

**Migración:** un manifiesto antiguo con rutas absolutas sigue funcionando (se resuelve tal cual).
Al guardarlo se convierten a relativas las rutas que caen dentro de su carpeta. Una ruta absoluta
ajena se conserva (no se pierde información) y `GET /v1/documentos/manifest` la declara en
`warnings`, sin mostrar la ruta. Si la carpeta se movió, el puente reancla la fuente al archivo del
mismo nombre en `_reporte`. Las rutas que devuelve la API (`source_json`, `docx_path`) son relativas.
Los campos de visualización local (`manifest_path`, `manifest_path_display`) siguen mostrando la ruta
real sólo en el equipo y no se persisten.

### 3.3 Listas y registro

```json
{
  "documentos": [
    {
      "doc_id": "3f9c0e1a7b2d4c58a1e6f0d9b7c2a4e8",
      "nombre": "Memoria.docx",
      "carpeta_mostrar": "C:\\Informes",
      "available": true
    }
  ]
}
```

`pick` devuelve la misma forma con `cancelled`. Si la persona cancela el
diálogo, `cancelled` vale `true` y el resto de campos son `null`.

## 4. Seguridad

La web alojada es un origen que no controlamos del todo. Por eso:

1. **Ninguna ruta recibe una ruta de la web para leer o escribir**, salvo
   `link`. Todo lo demás trabaja con un `doc_id` registrado. `link` valida la
   ruta igual que la aplicación (`backend.linked_documents.check_linkable`).
2. **Nada se escribe fuera de la carpeta del Word elegido.** Cada escritura
   comprueba `resolve()` y prefijo. Los nombres de carpeta e imagen se sanean.
3. **`open-in-word`** sólo abre el .docx registrado (`os.startfile` en
   Windows; `open` o `xdg-open` en otros sistemas, sin shell). No ejecuta nada más.
4. **Autenticación, Host y Origin** son los del puente: token obligatorio
   (`require_token`), sólo loopback (`loopback_host_only`) y CORS con la lista de
   orígenes. El método `DELETE` se añadió a la lista de métodos de CORS para el
   borrado de secciones (`bridge/sap2000_agent.py`, sin más cambios en CORS). La web alojada tiene acceso completo a esta familia de rutas (no
   tiene alcance restringido en `bridge/alcances.py`). Los orígenes de SAP Graph,
   Viewer y Placa Base reciben 403.
5. **Límites**: 20 MB por archivo, paquete o JSON; 200 documentos en el
   registro; base64 limitado en longitud.
6. **Escritura atómica** del .docx (`save_document_atomic`), del manifiesto y
   del espejo (`replace_with`). Si la escritura del Word falla, se restauran el
   .docx y el manifiesto (`_cambio_seguro`).

**Registro único por diálogo:** `link` (ruta pegada) se retiró. El único modo de registrar un
.docx es `pick`, el diálogo nativo del equipo, así que la web no puede indicar una ruta arbitraria
del PC. Si el puente no puede abrir el diálogo (falta tkinter o entorno gráfico) responde 501 y la
web pide instalar o activar el diálogo de archivos del puente.

## 5. Qué guarda el servidor (respaldo)

El servidor no cambia su comportamiento: sigue recibiendo los paquetes por
`POST /projects/{id}/bundles` y los guarda en su almacén. Ese almacén es el
respaldo. Como el servidor conserva los paquetes, el espejo se puede reconstruir a partir
de ellos (cómo lo hace la web es decisión de su integración). El puente no lee
del servidor, y la sincronización local usa siempre los archivos del PC.

## 6. Empaquetado (PyInstaller)

El `.exe` del puente (`SAP2000Bridge.exe`, variante `bridge` y `separate`) se
compila en `scripts/build_portable.ps1`, función `Build-Bridge`. Para este
servicio se añadieron:

- `--hidden-import` de `bridge.documentos_locales`, `backend.linked_documents`,
  `backend.report_builder.service` (y los módulos de `carpeta_reporte`,
  `modulos_exportacion` y `manifest_ops`).
- `--hidden-import` de cada generador del motor. `discover_generators()` los
  carga con `importlib`, así que PyInstaller no los detecta solo.
- `--add-data backend\report_builder\generators\schemas;…`: sin los esquemas JSON
  la validación de fuentes falla en el `.exe`. La variante `allinone` ya los
  incluía.
- `--collect-data docx`: datos de python-docx, por si el hook de PyInstaller no
  los trae.

No se ha podido compilar en este entorno (Linux, sin PyInstaller ni Windows).
La comprobación real es la del workflow de compilación del usuario.

## 7. Pruebas

`tests/test_bridge_documentos_backup.py` (6 pruebas): autenticación 401, origen
compartido 403, id desconocido 404, ZIP con el .docx y el manifiesto sin
modificarlos, ZIP sin manifiesto y Word que ya no está (409).

`tests/test_bridge_documentos_locales.py` (20 pruebas, con diálogo y
`os.startfile` simulados):

- Autenticación 401 y alcance 403 para un origen compartido.
- Registro: `pick` (incluido cancelado y sin tkinter 501), `link` con todas las
  validaciones, persistencia en `documentos.json` y `available` tras un reinicio.
- `prepare`: creación del manifiesto, segunda llamada sin cambios, Word bloqueado 409.
- Anclas `json_inline` y desde espejo, sincronización reabriendo el .docx,
  rechazo de fuentes fuera de `_reporte`, borrado y tags.
- `export-module`: rutas relativas, zip-slip y rutas absolutas rechazadas,
  formatos no permitidos.
- `open-in-word` con la ruta registrada y sin copias; `storage` con rutas reales.
- Las claves de las respuestas coinciden con `web/src/bridge_contracts.json`.

Las rutas nuevas también están en `web/src/bridge_contracts.json` (sólo se han
añadido entradas), así que `tests/test_bridge_contratos_cobertura.py` sigue
comprobando que cada ruta del puente tiene contrato.

## 8. Pendiente

- Integración en la web: hecha (`web/src/report/ayudanteLocal.ts`,
  `ayudanteLocalAcciones.ts`, `ReportBuilderFloating.tsx`, `Shell.tsx`). Usa
  `pick`/`link`, `prepare`, `anchors`, `sync`, `export-module`, `open-in-word`,
  `storage`, `manifest` y `backup`, y muestra `available: false`. Sin puente, la
  web avisa y sigue por el servidor, etiquetado «(solo servidor)».
- Confirmación en la bandeja para `link` (ver §4, riesgo abierto).
- Compilar y probar el `.exe` en Windows con un Word real (bloqueo, abrir, sincronizar).
- `POST /bundles` del servidor y `export-module` del puente comparten la lógica
  de paquetes pero están duplicados (`backend/routers/projects.py` no se ha
  tocado). Conviene extraerla a `backend/report_builder` cuando se pueda editar ese archivo.
