# Mejoras pendientes de evaluar

Ideas detectadas en las revisiones del puente. **No están hechas**; se dejan aquí para decidir más tarde.

## Exportación IFC
- **Estado:** la ruta `POST /v1/sap/actions/cad/ifc` (IFC4 desde un modelo `estructura-v1`, con `ifcopenshell`) **no está
  en `main`**. La versión completa y probada vive en la rama `ifc-ifcopenshell` (PR #9 cerrado sin fusionar). Se
  retomará cuando lo pida el otro programa.
- **Escritor IFC propio (sin `ifcopenshell`):** `ifcopenshell` añade del orden de 100 MB al `.exe` (DLL de OpenCASCADE,
  esquemas IFC2X3/IFC4/IFC4X3, geometría) y la ruta solo usa una parte mínima: crear unas 50 entidades IFC4, los
  `GlobalId` y consultar enumeraciones del esquema. Un escritor propio (serializador STEP + tablas de las entidades
  usadas) pesaría del orden de decenas de KB, quitaría la dependencia LGPL y el 503 por falta de librería.
  - Riesgo principal: producir un IFC que Revit rechace por un atributo mal ordenado o una enumeración errónea.
  - Mitigación: conservar `ifcopenshell` solo como dependencia de desarrollo (CI y pruebas: `validate`, volúmenes con
    `geom`) y comparar la salida del escritor propio con la de la librería.
  - Esfuerzo estimado: unos días (51 entidades, pruebas de equivalencia).
- **Si se retoma con `ifcopenshell` empaquetada:** medir el peso real del `.exe` con `--collect-all`, valorar recoger
  solo IFC4, y revisar la licencia LGPL frente al «Todos los derechos reservados» del README.

## Seguridad y robustez
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
