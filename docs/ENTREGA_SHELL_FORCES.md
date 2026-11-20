# Entrega: `POST /v1/sap/actions/viewer/foundations/shell-forces`

Ruta de solo lectura del puente (`webapp-bridge`) para el visor 3D y el módulo Fundaciones del Engineering Workbench.
Implementada en el PR 20 y fusionada en `main`. Contrato completo: `docs/SAP_ACTIONS.md`, sección «Fundaciones SAP2000 en el Viewer».

## 1. Archivos

Nuevos:

- `bridge/viewer/shell_forces.py`
- `tests/viewer/test_viewer_shell_forces.py`

Modificados:

- `bridge/viewer/routes.py` (ruta y `ShellForcesRequest`)
- `web/src/bridge_contracts.json` (entrada de contrato con las once claves)
- `tests/viewer/test_viewer_routes.py` (`RUTAS_ESPERADAS`)
- `docs/SAP_ACTIONS.md` y `docs/SAP2000_ACCEPTANCE.md`

## 2. Cómo ejecutar los tests

```bash
python -m pytest tests/viewer/test_viewer_shell_forces.py -q
python -m pytest -q
```

Resultado: 42 tests nuevos con un SAP simulado; suite completa 1974 correctos y 68 omitidos; `ruff` limpio.

## 3. Qué el cliente puede esperar

- Cuerpo: `{ areas, cases?, combos?, components?, include_axes, max_elements }` (`extra="forbid"`; `422` ante campos extra,
  componentes inválidos o repetidos y `max_elements` fuera de 1–200000).
- Respuesta (siempre las once claves): `ok`, `units` (`{force:"tonf", length:"m"}`), `model_locked` (siempre `true`), `truncated`,
  `warnings`, `mesh_hash`, `elements`, `nodes`, `axes`, `columns`, `rows`.
- `rows`: una por elemento × esquina × caso/combinación × paso; `step` es `""`, `"Max"`, `"Min"` o `"Step"`; un valor no finito
  llega como `null`. Triángulos con 3 nudos (sin duplicar el tercero).
- `axes`: `{elm: {v1, v2, v3}}` unitarios, ortonormales y dextrógiros; `{}` con `include_axes: false`.
- Errores: `409 model_not_run` (modelo sin analizar; no toca selección ni unidades), `409 no_results`, `409 no_areas`,
  `409 units_not_restored`, `422 unknown_area`, `422 unknown_case`.
- Límites: `max_elements` (primeros elementos en orden de áreas) y `MAX_FILAS = 200000` filas, que corta en un caso completo y
  lista los omitidos en `warnings` (si el primer caso ya supera el tope, se devuelve entero y se avisa).
- El puente restaura siempre las unidades y la selección de salida del usuario; no desbloquea ni analiza.
- Mirar siempre `warnings`: dice qué vía de lectura se usó (tabla o respaldos) y cuántos elementos tienen ejes derivados de la
  geometría en vez de leídos de SAP2000.

## 4. Verificado y sin verificar con SAP2000

Verificado con SAP2000 v23.3.1 (sonda de shells): los alias de las tres tablas, la interpretación por columnas de
`GetTransformationMatrix`, la sensibilidad de la tabla de fuerzas a las unidades y a la selección de salida, y que la tabla no trae
`StepNum`. Orden de lectura resultante: `Results.AreaForceShell("ALL", GroupElm)` primero (1,14 s con 10 000 elementos) y la tabla
(20,4 s) solo como respaldo con aviso; los ejes por elemento (11,9 s) se guardan en una caché en memoria por `mesh_hash`.

Sin verificar: las firmas de `Get/SetLoadCasesSelectedForDisplay`, la herencia de ejes en mallas automáticas de otras versiones y los
valores de `StepType` distintos de `""`, `Max`, `Min` y `Step`.

## 5. Puntos del contrato que no se cumplen tal cual

- La detección de la matriz traspuesta no distingue las losas horizontales (solo funciona en planos inclinados).
- Con la geometría por tablas, el orden de los elementos es el de la tabla de áreas y no el de `AreaObj.GetElm`; con el
  respaldo por objeto sí es el de `GetElm`.
- `MAX_FILAS` es 200000 (el prompt original decía 500000): con 8 componentes, 500000 filas son del orden de 50 MB de JSON.

## 6. Pendiente

- Aceptación manual con SAP2000 real: `docs/SAP2000_ACCEPTANCE.md`, sección «Viewer: esfuerzos de shell por esquina».
- `POST viewer/foundations/shell-joint-forces` (`Results.AreaJointForceShell`; la sonda confirmó 12 arreglos y el momento en M2 en ejes del elemento) queda para una petición futura, cuando la sonda
  confirme su firma y sus ejes.
