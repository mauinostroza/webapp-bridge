# CAD › IFC desde el puente local

El IFC se **escribe en el puente** (decisión del dueño), no en el servidor: el servidor produce el modelo semántico
`estructura-v1` (`backend/motor_calculo/cad_drwr/modelo`) y el puente lo convierte a IFC4 con `ifcopenshell`.
Destino principal: **Revit**; después, Tekla. El módulo es `bridge/cad_ifc.py` y la ruta está en `bridge/cad_drwr.py`.
`bridge/cad_ifc.py` es autónomo (no importa `backend.*`; lo vigila `tests/test_bridge_sin_motor.py`).

## Ruta

`POST /v1/sap/actions/cad/ifc` (mismo token Bearer que el resto del puente). No necesita CAD ni SAP2000 abiertos.

```json
{ "modelo": { "formato": "estructura-v1", "unidades": "mm", "id": "pedestal", "...": "..." } }
```

Respuesta 200: `{ "nombre": "<id>.ifc", "tamano": <bytes>, "contenido_base64": "<IFC en base64>" }`.

| Código | Cuándo |
|---|---|
| 422 | Formato distinto de `estructura-v1`, unidades distintas de `mm`, clase sin soporte (hoy solo `Soldadura`) o `geom.tipo` incoherente con la clase, `base` con ejes no perpendiculares, agujeros o `huecos` fuera del contorno, `padre` inexistente / que no admite hijos / con ciclo / con varias instancias, ids repetidos, tramos de eje discontinuos o arcos con puntos colineales, más de **5000 instancias** (suma de todos los elementos) o más de **2 MB** de JSON. Cuerpo con campos de más: 422 estándar del puente. |
| 503 | El puente no tiene `ifcopenshell`. |

El estado del puente (`POST /v1/sap/actions/cad/status`) añade `ifc: true/false`.

## Instalación de `ifcopenshell`

Solo la necesita el puente. Está en `backend/requirements-puente-ifc.txt` y **no** en `requirements.txt` ni en
`requirements-integrados.txt`, de modo que la imagen Docker del servidor no crece.

- Desarrollo / CI: `pip install -r backend/requirements-puente-ifc.txt` (ya está en `.github/workflows/ci.yml`).
- Portable de Windows: `scripts/build_portable.ps1` (y `scripts/build_bridge.ps1` de `webapp-bridge`) la instalan y
  añaden `--collect-all ifcopenshell` a PyInstaller. Esa compilación solo puede validarse en Windows: la primera vez
  hay que abrir `SAP2000Bridge.exe` y comprobar que `cad/status` devuelve `ifc: true`.
- Exportación del puente público: `scripts/exportar_puente.py` copia el requirements y este documento.
- Sin la librería el puente arranca igual: `ifc: false` y la ruta responde 503.

## Qué se escribe

IFC4, vista de referencia (`ViewDefinition [ReferenceView_V1.2]`), unidades en mm (`IfcSIUnit MILLI METRE`),
contexto `Model`/`Body`.

```
IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey "Fundaciones"
  IfcElementAssembly (USERDEFINED, ver tabla)   origen/angulo del conjunto
    [IfcElementAssembly "ZAPATA" por meta.zapata]   solo en 'fundacion'
      <elementos sin `padre`>                       agregados al ensamblaje (IfcRelAggregates)
        <elementos con `padre`>                     agregados a su padre (IfcRelAggregates)
```

| `tipo` del conjunto | Ensamblaje (`ObjectType`) |
|---|---|
| `perno_anclaje` | `PERNO_ANCLAJE` |
| `perfil_refuerzos` | `PERFIL_REFORZADO` (perfil + planchas y rigidizadores) |
| `fundacion` | `FUNDACION`, con un sub-ensamblaje `ZAPATA` por `meta.zapata` |
| `perfil` | **sin ensamblaje** si trae una sola pieza (el `IfcColumn` cuelga de la planta y lleva el origen, `Pset_WB_Vinculo` y avisos); con varias piezas, `PERFIL` |
| resto (`base_columna`...) | `BASE_COLUMNA` |

Decisión: una pieza suelta no necesita ensamblaje (sería un contenedor de un solo hijo que Revit trataría como grupo
inútil); el origen del conjunto se aplica entonces a la propia pieza.

**Fundación** (`foundation_sap`): cada área SAP es una `Zapata` (una extrusión por área, con `meta.area`,
`meta.zapata`, `meta.pts_nombres`) y cada frame un `Pedestal` (`meta.frame`, `meta.punto_pie`). Los elementos sin
`padre` se agrupan por `meta.zapata` (zapata física, que puede tener varias áreas) en un `IfcElementAssembly`
`ZAPATA` llamado `Zapata <F>` que agrega sus áreas y pedestales y cuelga del ensamblaje `FUNDACION`. El origen del
conjunto (puede traer miles de mm) va en el `IfcLocalPlacement` del ensamblaje `FUNDACION`; la geometría queda en
coordenadas locales. Psets: `Pset_WB_Vinculo` en el ensamblaje (frames/áreas/joints del `vinculo` del conjunto) y en
cada sub-ensamblaje (los de su zapata); `Pset_WB_Fundacion` en cada zapata/pedestal (`Zapata`, `Area`, `Joints`,
`Frame`, `PuntoPie`, `Continuacion`, `Aproximado`); `Pset_WB_Avisos` en la raíz del conjunto con los `avisos`
(`NumAvisos`, `Aviso1`, `Aviso2`...).

### Clases soportadas

La tabla vive en `bridge/cad_ifc.py` (`CLASES`): añadir una clase es añadir una fila (entidad IFC, tipo predefinido,
Pset común, extras) y, si trae geometría nueva, su representación. IFC4 no tiene en sus enumeraciones varios tipos
útiles (`BASE_PLATE`, `STIFFENER_PLATE`, `NUT`, `WASHER`...): el escritor consulta el esquema y, si falta, escribe
`PredefinedType = USERDEFINED` con el nombre en `ObjectType` (así lo lee Revit igualmente).

| Clase del modelo | Entidad IFC4 | `PredefinedType` / `ObjectType` | Geometría | Psets |
|---|---|---|---|---|
| `Zapata` | `IfcFooting` | `PAD_FOOTING` | extrusión | `Pset_FootingCommon`, `Pset_WB_Fundacion` |
| `Pedestal` | `IfcColumn` | `USERDEFINED` / `PEDESTAL` | extrusión | `Pset_ColumnCommon`, `Pset_WB_Fundacion` |
| `PlacaBase` | `IfcPlate` | `USERDEFINED` / `BASE_PLATE` | extrusión con agujeros | `Pset_PlateCommon`, `Pset_WB_Pieza` |
| `Rigidizador` | `IfcPlate` | `USERDEFINED` / `STIFFENER_PLATE` | extrusión | `Pset_PlateCommon`, `Pset_WB_Pieza` |
| `Perfil` | `IfcColumn` | `COLUMN` | extrusión del contorno (con `huecos`) | `Pset_ColumnCommon`, `Pset_WB_Pieza` (`Seccion`, `TipoPerfil`, `AreaMm2`, `PesoLinealKgM` desde `meta`); admite hijos |
| `Tuerca` | `IfcMechanicalFastener` | `USERDEFINED` / `NUT` | extrusión | `Pset_WB_Pieza` |
| `Arandela` | `IfcMechanicalFastener` | `USERDEFINED` / `WASHER` | extrusión | `Pset_WB_Pieza` |
| `PernoAnclaje` | `IfcMechanicalFastener` | `ANCHORBOLT` | `IfcSweptDiskSolid` sobre el eje | `Pset_MechanicalFastenerAnchorBolt`, `Pset_WB_Pieza`; `NominalDiameter`, `NominalLength` |
| `Grout` | `IfcBuildingElementProxy` | `USERDEFINED` / `GROUT` | extrusión | `Pset_WB_Pieza` |
| `BarraRefuerzo` | `IfcReinforcingBar` | MAIN / LIGATURE / ANCHORING | barrido | `Pset_ReinforcingBarCommon` |
| `Soldadura` | sin soporte (422) | | | |

`Grout` es `IfcBuildingElementProxy` y no `IfcCovering`: los tipos de `IfcCovering` son acabados (suelo, falso techo,
revestimiento) y Revit lo trataría como tal; un proxy llega como modelo genérico, que es lo que es un grout de nivelación.

### Geometría

- **Extrusión**: `contorno` en ejes locales (u, v). Colocación con `base` = `{"o","x","y"}`: `x` e `y` (perpendiculares)
  son los ejes u, v en coordenadas del conjunto y la extrusión va en `x × y`; sin `base`, plano XY a `z0` hacia +Z.
  `agujeros` `[{"c":[u,v],"d":Ø}]` pasantes → `IfcArbitraryProfileDefWithVoids`; los huecos son `IfcCircle` (IFC exige
  `IfcCurve` en `InnerCurves`, no un `IfcCircleProfileDef`) y se valida que queden dentro del contorno.
  `huecos` `[[[u,v],…],…]` son huecos poligonales (p. ej. la caja HSS) → `IfcPolyline` cerradas en `InnerCurves`; se
  combinan con `agujeros` en el mismo `IfcArbitraryProfileDefWithVoids`. Cada hueco debe tener ≥ 3 puntos, área no
  nula, todos sus vértices dentro del contorno y ningún lado que cruce el contorno (422 si no). El `Perfil` I/W
  (contorno de 12 puntos) no lleva huecos.
- **Instancias**: `instancias` `[[dx,dy,dz]]` genera **una entidad IFC por instancia** (`Nombre #i`), con la posición
  de `base.o` + desplazamiento. Un `padre` debe tener una sola instancia.
- **Barrido**: `IfcSweptDiskSolid` sobre `IfcIndexedPolyCurve` (`IfcLineIndex` / `IfcArcIndex`), eje desplazado por la instancia.
- Barras: `BarLength` (desarrollo calculado del eje: rectas por distancia, arcos por tres puntos), `SteelGrade`, un
  `IfcReinforcingBarType` por marca (y diámetro/tipo) con `IfcRelDefinesByType` e `IfcRelDeclares`. Pernos: `NominalLength`
  igual al desarrollo del eje.
- Materiales `IfcMaterial` (`hormigon` → Concrete; `acero` y `acero_refuerzo` → Steel) con `IfcRelAssociatesMaterial`.
- `Pset_WB_Vinculo` en el ensamblaje (`ConjuntoId`, `Marca`, `Tipo`, `Modulo`, `Frames`, `Joints`, `Areas`, `Huella`).
  `Pset_WB_Pieza` (propio): `Marca`, `Clase`, `Seccion` y `PesoKg` (`peso` del elemento entre sus instancias, en kg).
- Los elementos con `padre` se agregan a él (`IfcRelAggregates`): armadura, pernos y grout al hormigón; perfil y
  rigidizadores a la placa; en `perfil_refuerzos`, planchas y rigidizadores (`padre="perfil"`) al `IfcColumn` del perfil.
  Un `Perfil` con varias instancias no puede ser padre. Regla anti-ciclos: se sube por la cadena de padres de cada
  elemento y se rechaza si reaparece un id ya visto (incluido el propio elemento). Todo queda además contenido en la planta (`IfcRelContainedInSpatialStructure`).
- `GlobalId` deterministas: `uuid5(conjunto.id / elemento.id / índice de instancia)` comprimido con
  `ifcopenshell.guid.compress` (sin índice en elementos sin `instancias`, como antes). Dos exportaciones del mismo
  modelo dan los mismos `GlobalId`; solo cambia la marca de tiempo de la cabecera.

## Nota sobre Revit

Revit importa IFC4 Reference View como **geometría de referencia**: la zapata, el pedestal y el perfil llegan como elementos
de categoría estructural; las placas (`IfcPlate`), pernos, tuercas y arandelas (`IfcMechanicalFastener`) y el grout (proxy)
llegan como geometría de referencia, normalmente en categorías genéricas, y con `USERDEFINED` el tipo concreto
(`BASE_PLATE`, `NUT`...) solo se ve en `ObjectType`/Psets. Pero las barras (`IfcReinforcingBar` con `IfcSweptDiskSolid`) llegan como **DirectShape**
(`Modelo genérico` / `Armadura` según la configuración de la importación), **no como `Rebar` nativa de Revit**: no
se pueden editar como armadura ni entran en sus planillas de armadura. Sirven para coordinación, visualización,
cantidades por `BarLength`/`NominalDiameter` y para vincularlas con el modelo de SAP2000 mediante `Pset_WB_Vinculo`.
Para tener `Rebar` nativa haría falta un complemento de Revit (API) que cree las barras a partir de este mismo modelo.

## Pruebas y limitaciones

`tests/test_bridge_cad_ifc.py` reabre el IFC, lo valida con `ifcopenshell.validate` (esquema y reglas EXPRESS, para lo
que basta `lark`, que viene con `ifcopenshell`), comprueba clases, relaciones, Psets, `GlobalId`, `BarLength` frente
al eje y, con `ifcopenshell.geom`, que el volumen de varias barras (con y sin arco) coincide con `π·d²/4·L` (3 %).

No se puede comprobar en este entorno cómo los interpreta Revit (ni Tekla): categorías asignadas a placas, pernos y
grout, tratamiento de `IfcElementAssembly` con partes también contenidas en la planta, agregación de un `IfcColumn`
(perfil) dentro de un `IfcPlate`, o el aspecto de los arcos teselados. Las pruebas de placa base y pernos
(`estructura_v1_placa_base/perno_pg/perno_l.json`) validan EXPRESS, cuentan entidades por clase, comprueban `GlobalId`
estables y que el volumen de la placa (menos agujeros) y del perno coincide con la fórmula (3 %). Sin soporte: `Soldadura`.

Los conjuntos `perfil_hss`, `perfil_refuerzos` y `fundacion` (fixtures `estructura_v1_*.json`) se prueban igual: EXPRESS sin
errores, `GlobalId` estables, conteos por clase, jerarquía de padres, volumen del perfil HSS con hueco ≈ área neta ×
longitud, volumen de planchas ≈ ancho·espesor·largo y suma de zapatas ≈ Σ área·espesor (3 %). Tampoco se puede
verificar sin Revit: cómo trata un `IfcColumn` que agrega `IfcPlate` (planchas del perfil), los `IfcElementAssembly`
anidados de la fundación (sub-ensamblaje por zapata) y el `IfcArbitraryProfileDefWithVoids` con polilínea interior
(la caja HSS hueca), ni la lectura de `Pset_WB_*` en las propiedades de ejemplar. Como en el resto, las piezas están
además contenidas en la planta además de agregadas a su ensamblaje.
