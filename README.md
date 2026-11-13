# webapp-bridge

Puente de SAP2000 y de los documentos Word locales para el Workbench (`mauinostroza/webapp_workbench`).
Corre en el PC del usuario (`127.0.0.1:8765`), se empareja con la web desde el icono de la bandeja y
atiende las rutas `/v1/sap/*` (SAP2000) y `/v1/documentos/*` (Word local).

## Origen de este repositorio

Este repositorio **se genera** desde el Workbench con `scripts/exportar_puente.py`; el Workbench es la fuente de
verdad. No se edita aquí a mano: un cambio hecho solo en este repositorio se pierde en la siguiente exportación.
Lo que se exporta (puente, módulos de documentos, generadores, pruebas, documentos) lo decide ese script.

## Informes Word

El puente lleva los generadores de informes (`backend/report_builder/generators/`, solo formato Word), sus
esquemas JSON y el catálogo `catalogo.json`. Por eso `POST /v1/documentos/sync` **funciona en local**: el puente
genera el contenido de cada módulo y lo escribe en el Word del usuario.

El flujo de fragmentos es opcional y sigue disponible:

1. `POST /v1/documentos/plan-fragmentos`: huella del Word, lienzo ligero (solo estilos) y un paquete por módulo.
2. El servidor del Workbench genera el fragmento (`POST /report-builder/fragmento`).
3. `POST /v1/documentos/aplicar-fragmentos`: el puente comprueba que el Word no cambió y escribe el contenido.

El puente no lleva los motores de cálculo del Workbench: lee SAP2000 y devuelve datos crudos; el cálculo vive
en el servidor.

## Orígenes de las webs

El repositorio no lleva las direcciones de ninguna web alojada. El puente solo acepta peticiones de los
orígenes que se le indiquen:

- **Para uso propio:** crea `%LOCALAPPDATA%\EngineeringWorkbench\bridge\origins.json` con una lista de direcciones
  (`["https://mi-web.example"]`) o define `SAP2000_AGENT_ORIGINS` (lista separada por comas).
- **Al compilar:** define el **secreto** de repositorio `ORIGENES_ALOJADOS` (Settings → Secrets and variables →
  Actions → Secrets; no una variable, porque los registros públicos mostrarían su valor) con un JSON `{"hosted": "...", "fusion": "...", "graph": "...", "viewer": "...",
  "placa_base": "..."}`. El workflow lo incorpora al ejecutable (`bridge/hosted.py`).

## Desarrollo

```
pip install -r backend/requirements.txt -r backend/requirements-dev.txt -r backend/requirements-integrados.txt -r backend/requirements-puente-ifc.txt
ruff check . && ruff format --check .
python -m pytest -q
```

Compilación (Windows, workflow *Build SAP2000 Bridge* o `scripts/build_bridge.ps1`): genera `SAP2000Bridge.zip`.
El script de compilación lista como hidden-imports todos los generadores de informes.

## Contratos

`web/src/bridge_contracts.json` y `web/src/cad_drwr/bridge_contracts.cad_drwr.json` son una **copia** de los
contratos del Workbench (las pruebas de contratos los leen). El contrato manda en el Workbench: no los edite aquí.

## Licencia

Todos los derechos reservados. Sin licencia de uso. (Aviso provisional: hasta que se defina una licencia, no se
concede permiso para usar, copiar, modificar ni distribuir este código.)
