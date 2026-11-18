# Mejoras pendientes de evaluar

Ideas detectadas en las revisiones del puente. **No están hechas**; se dejan aquí para decidir más tarde.

## Exportación IFC
- **Estado:** la ruta `POST /v1/sap/actions/cad/ifc` (IFC4 desde un modelo `estructura-v1`) está en `main` con un **escritor
  IFC propio sin `ifcopenshell`** (`bridge/ifc_step.py`). La versión anterior con `ifcopenshell` queda en la rama
  `ifc-ifcopenshell` (PR #9 cerrado sin fusionar) solo como referencia.
- **Escritor IFC propio: HECHO (F0-F3)**, ver [`docs/PLAN_ESCRITOR_IFC.md`](PLAN_ESCRITOR_IFC.md). Falta la fase F4: compilar en
  Windows, **medir el peso real del `.exe`** frente a la rama `ifc-ifcopenshell`, comprobar `cad/status` → `ifc: true` y abrir los
  IFC de muestra en **Revit** (la versión con `ifcopenshell` tampoco se había abierto nunca en Revit).
- **Riesgo residual del escritor propio:** que Revit rechace algún atributo o carácter (`\X4\` por encima de U+FFFF es un
  supuesto). Mitigado con equivalencia entidad por entidad con `ifcopenshell` y validación EXPRESS en CI.

## Seguridad y robustez
- **`combinaciones/send` sobrescribe** una combinación del mismo nombre sin preguntar (por diseño): valorar bloquearlo
  salvo confirmación explícita, igual que `esbelteces/section/apply` con el modelo bloqueado.
- **Selección de los cortes de sección** (`section_cut`): no se restaura tras leerlos.
- **`hormigon.get_frame_forces`:** `n = int(n)` sin validar (un valor no numérico da un error genérico).
- **Límite de tamaño del cuerpo** de las peticiones (las rutas no lo limitan).
- **Registro de auditoría de las escrituras** en SAP2000 (`apply`, `send`, `enviar`, `revertir`, `delete`…). El visor ya
  tiene uno (`bridge/viewer/safety/audit_log.py`); no se aplica al resto del puente.
- **Avisar cuando las unidades de SAP2000 no se pudieron restaurar** tras una lectura que las cambia temporalmente
  (hoy solo se avisa en algunas rutas).
- **Alcance de rutas para las webs `hosted` y `fusion`:** hoy tienen acceso completo; se podría acotar por familias
  (`bridge/alcances.py`) como ya se hace con las aplicaciones independientes.
- **Copia del modelo antes de escribir:** proponer o exigir que la web recuerde guardar el `.sdb` antes de aplicar.

## Empaquetado y distribución
- **Firma del `.exe`** (evitaría la advertencia de SmartScreen y reduciría falsos positivos del antivirus).

## Datos y pruebas
- **Historial de git:** el nombre de un modelo real de SAP2000 estuvo en un fixture (ya sustituido) y sigue en commits
  anteriores; decidir si hace falta reescribir el historial o publicar un repositorio limpio.
- **Validar con SAP2000 real** lo hecho con modelos simulados: orden de salidas de `GetDblChannel`, códigos de tipo de
  sección 12/13/14, ejes de `JointReact` en nudos con ejes locales girados, forma de `SelectObj.GetSelected` y
  `PropFrame.GetNameList`.
- **Endurecer `bridge/hormigon.py:176`** (`n = int(n)` sobre la salida de `Results.FrameForce`) como ya se hizo en acero.
- **Exportador del Workbench:** `scripts/exportar_puente.py` no copia `tests/fixtures/estructura_v1_*.json` y regenerar
  `webapp-bridge` desde el Workbench sobrescribiría los arreglos hechos directamente en el puente.
