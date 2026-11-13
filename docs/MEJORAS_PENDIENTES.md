# Mejoras pendientes de evaluar

Ideas detectadas en la revisión de riesgos del puente. **No están hechas**; se dejan aquí para decidir más tarde.

## Seguridad y robustez
- **Límite de tamaño del cuerpo** de las peticiones (hoy solo `cad/ifc` limita su JSON a 2 MB; las demás rutas no).
- **Registro de auditoría de las escrituras** en SAP2000 (`apply`, `send`, `enviar`, `revertir`, `delete`…). El visor ya
  tiene uno (`bridge/viewer/safety/audit_log.py`); no se aplica al resto del puente.
- **Avisar cuando las unidades de SAP2000 no se pudieron restaurar** tras una lectura que las cambia temporalmente
  (hoy solo se avisa en algunas rutas).
- **Alcance de rutas para las webs `hosted` y `fusion`:** hoy tienen acceso completo; se podría acotar por familias
  (`bridge/alcances.py`) como ya se hace con las aplicaciones independientes.
- **Copia del modelo antes de escribir:** proponer o exigir que la web recuerde guardar el `.sdb` antes de aplicar.

## Empaquetado y distribución
- **Firma del `.exe`** (evitaría la advertencia de SmartScreen y reduciría falsos positivos del antivirus).
- **Peso y arranque del `.exe`** con `ifcopenshell` (`--collect-all`): medirlo y valorar excluir ficheros que no se usen.
- **Licencia:** `ifcopenshell` es LGPL y el README declara «Todos los derechos reservados»; revisar qué implica
  distribuirlo dentro del ejecutable.

## Datos y pruebas
- **Historial de git:** el nombre de un modelo real de SAP2000 estuvo en un fixture (ya sustituido) y sigue en commits
  anteriores; decidir si hace falta reescribir el historial o publicar un repositorio limpio.
- **Validar con SAP2000 real** lo hecho con modelos simulados: orden de salidas de `GetDblChannel`, códigos de tipo de
  sección 12/13/14, ejes de `JointReact` en nudos con ejes locales girados, forma de `SelectObj.GetSelected` y
  `PropFrame.GetNameList`.
- **Endurecer `bridge/hormigon.py:176`** (`n = int(n)` sobre la salida de `Results.FrameForce`) como ya se hizo en acero.
- **Exportador del Workbench:** `scripts/exportar_puente.py` no copia `tests/fixtures/estructura_v1_*.json` y regenerar
  `webapp-bridge` desde el Workbench sobrescribiría los arreglos hechos directamente en el puente.
