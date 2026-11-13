# Plan: escritor IFC4 propio (sustituir `ifcopenshell`)

> **Estado: pendiente, sin ejecutar.** Se guarda para retomarlo en unos días. Plan generado por un agente planificador
> (modelo Opus) leyendo la rama `ifc-ifcopenshell` (commit `c9c00ac`); **no se ha comprobado nada de lo que dice**.
> Los supuestos están marcados. `main` **no** contiene el IFC (ni `cad_ifc.py`, ni la ruta `cad/ifc`).
>
> Referencias de código: las rutas y números de línea de este documento son los de la rama `ifc-ifcopenshell`
> (`bridge/cad_ifc.py`, `bridge/cad_drwr.py`, `docs/CAD_IFC.md`, `tests/test_bridge_cad_ifc.py`,
> `tests/fixtures/estructura_v1_*.json`).

## Supuestos a confirmar antes de usar el plan
- El orden de atributos y las enumeraciones de la sección 1 salen del conocimiento del agente sobre IFC4 ADD2 TC1, no de
  una comprobación. Hay que confirmarlos con el generador de la fase 1 antes de usarlos.
- Los «~100 MB» que añade `ifcopenshell` al `.exe` son un dato aportado por el dueño, **no medido**.
- La versión actual con `ifcopenshell` **nunca se ha abierto en Revit** (`docs/CAD_IFC.md`): si falla también en Revit con
  el escritor propio, el problema podría estar en el modelado y no en el escritor.
- Que Revit lea los caracteres por encima de U+FFFF (`\X4\`) es un supuesto; si falla, sustituirlos por `?`.

## 1. Qué hay que reemplazar

### 1.1 Superficie de `ifcopenshell` que usa `bridge/cad_ifc.py` (~30 líneas)

| Uso | Dónde | Sustituto |
|---|---|---|
| `ifcopenshell.file(schema="IFC4")` | `_Escritor.__init__`, l. 547-551 | `ArchivoStep()` propio |
| `self.f.create_entity(tipo, *args, **kw)` | `ent()`, l. 556-557 (todo pasa por aquí: 61 llamadas `ent/raiz/relacion`) | `ArchivoStep.crear_entidad` con la misma firma |
| `ifcopenshell.guid.compress(uuid5(...).hex)` | `_gid`, l. 535-539 | `comprimir_guid` propio, idéntico bit a bit |
| `ifcopenshell_wrapper.schema_by_name("IFC4")…enumeration_items()` | `_admite`, l. 912-923 | tabla `ENUMERACIONES` del esquema propio |
| `self.f.header.file_description/file_name` | `cabecera`, l. 622-630 | `ArchivoStep.cabecera(...)` |
| `self.f.to_string()` | `bytes_ifc`, l. 1090-1091 | `ArchivoStep.a_texto()` |
| `obj.GlobalId` (l. 607, 616, 689), `obj.is_a("IfcTypeObject")` (l. 611), lectura/escritura de `obj.HasPropertySets` (l. 612), `self.site.ObjectPlacement` (l. 676, 683, 795) | | `Entidad` con `__getattr__`/`__setattr__` validados y `is_a()` con cadena de supertipos |
| `_importable/disponible/FaltaIfcopenshell` (l. 165-184, 1102-1105) y el 503 en `cad_drwr.py` l. 560-561 | | Desaparecen (ver 5.4) |

### 1.2 Inventario de tipos (literales `"Ifc…"` de `cad_ifc.py`: 51 nombres)
40 entidades instanciables, 10 tipos definidos y 1 supertipo abstracto que solo se consulta con `is_a` (`IfcTypeObject`).

Bloques comunes: **R** (IfcRoot) = GlobalId, OwnerHistory, Name, Description · **P** (IfcProduct) = R + ObjectType,
ObjectPlacement, Representation · **E** (IfcElement) = P + Tag. `*` = atributo derivado; `$` = opcional que el código no rellena.

| Entidad | Atributos en orden (n.º) | Notas de uso |
|---|---|---|
| IfcProject (IfcContext) | R, ObjectType, LongName, Phase, RepresentationContexts, UnitsInContext (9) | l. 657 |
| IfcSite | P, LongName, CompositionType, RefLatitude, RefLongitude, RefElevation, LandTitleNumber, SiteAddress (14) | `CompositionType=ELEMENT` |
| IfcBuilding | P, LongName, CompositionType, ElevationOfRefHeight, ElevationOfTerrain, BuildingAddress (12) | |
| IfcBuildingStorey | P, LongName, CompositionType, Elevation (10) | `Elevation=0.0` |
| IfcFooting / IfcColumn / IfcPlate / IfcBuildingElementProxy | E, PredefinedType (9) | tabla `CLASES` l. 118-157 |
| IfcMechanicalFastener | E, NominalDiameter, NominalLength, PredefinedType (11) | `_extra_perno` |
| IfcElementAssembly | E, AssemblyPlace, PredefinedType (10) | `SITE`, `USERDEFINED` |
| IfcReinforcingBar | E, SteelGrade, NominalDiameter, CrossSectionArea, BarLength, PredefinedType, BarSurface (14) | l. 1037-1051 |
| IfcReinforcingBarType | R, ApplicableOccurrence, HasPropertySets, RepresentationMaps, Tag, ElementType, PredefinedType, NominalDiameter, CrossSectionArea, BarLength, BarSurface, BendingShapeCode, BendingParameters (16) | se le añaden Psets después (l. 612) |
| IfcRelAggregates | R, RelatingObject, RelatedObjects (6) | 6 sitios |
| IfcRelContainedInSpatialStructure | R, RelatedElements, RelatingStructure (6) | |
| IfcRelDefinesByProperties | R, RelatedObjects, RelatingPropertyDefinition (6) | |
| IfcRelDefinesByType | R, RelatedObjects, RelatingType (6) | |
| IfcRelAssociatesMaterial | R, RelatedObjects, RelatingMaterial (6) | |
| IfcRelDeclares | R, RelatingContext, RelatedDefinitions (6) | |
| IfcPropertySet | R, HasProperties (5) | |
| IfcPropertySingleValue | Name, Description, NominalValue, Unit (4) | NominalValue es SELECT: `IFCLABEL('…')` |
| IfcMaterial | Name, Description, Category (3) | |
| IfcSIUnit | Dimensions(*), UnitType, Prefix, Name (4) | Dimensions se escribe `*` |
| IfcUnitAssignment | Units (1) | |
| IfcGeometricRepresentationContext | ContextIdentifier, ContextType, CoordinateSpaceDimension, Precision, WorldCoordinateSystem, TrueNorth (6) | `3` INTEGER, `1e-5` REAL |
| IfcGeometricRepresentationSubContext | ContextIdentifier, ContextType, *, *, *, *, ParentContext, TargetScale, TargetView, UserDefinedTargetView (10) | 4 atributos derivados |
| IfcCartesianPoint | Coordinates | 2D y 3D |
| IfcDirection | DirectionRatios | |
| IfcAxis2Placement3D | Location, Axis, RefDirection | |
| IfcAxis2Placement2D | Location, RefDirection | |
| IfcLocalPlacement | PlacementRelTo, RelativePlacement | |
| IfcPolyline | Points | |
| IfcCircle | Position, Radius | |
| IfcArbitraryClosedProfileDef | ProfileType, ProfileName, OuterCurve (3) | |
| IfcArbitraryProfileDefWithVoids | ProfileType, ProfileName, OuterCurve, InnerCurves (4) | |
| IfcExtrudedAreaSolid | SweptArea, Position, ExtrudedDirection, Depth (4) | |
| IfcShapeRepresentation | ContextOfItems, RepresentationIdentifier, RepresentationType, Items (4) | `SweptSolid` / `AdvancedSweptSolid` |
| IfcProductDefinitionShape | Name, Description, Representations (3) | |
| IfcCartesianPointList3D | CoordList (1) | IFC4X3 añade TagList; en IFC4 no (supuesto) |
| IfcIndexedPolyCurve | Points, Segments, SelfIntersect (3) | Segments: lista de SELECT envueltos `(IFCLINEINDEX((1,2)),IFCARCINDEX((2,3,4)))` |
| IfcSweptDiskSolid | Directrix, Radius, InnerRadius, StartParam, EndParam (5) | |

Supertipos abstractos necesarios para `is_a()`: IfcRoot, IfcObjectDefinition, IfcObject, IfcContext, IfcProduct, IfcElement,
IfcBuildingElement, IfcElementComponent, IfcReinforcingElement, IfcSpatialElement, IfcSpatialStructureElement,
IfcTypeObject, IfcTypeProduct, IfcElementType, IfcElementComponentType, IfcReinforcingElementType, IfcRelationship y
sus intermedias, IfcPropertyDefinition, IfcPropertySetDefinition, IfcProperty, IfcSimpleProperty, IfcNamedUnit y las de geometría.

**Tipos definidos (10).** Siempre envueltos cuando van en SELECT: IfcLabel, IfcIdentifier, IfcText (STRING); IfcReal,
IfcPositiveLengthMeasure, IfcMassMeasure (REAL); IfcInteger; IfcBoolean. Listas de enteros: IfcLineIndex (LIST [2:?]),
IfcArcIndex (LIST [3:3]). En atributos que no son SELECT se escriben desenvueltos (`NominalDiameter` → `19.`).

**Enumeraciones usadas** (de memoria; confirmar con el generador): IfcUnitEnum (LENGTHUNIT, AREAUNIT, VOLUMEUNIT, MASSUNIT,
PLANEANGLEUNIT), IfcSIPrefix (MILLI, KILO), IfcSIUnitName (METRE, SQUARE_METRE, CUBIC_METRE, GRAM, RADIAN),
IfcElementCompositionEnum (ELEMENT), IfcGeometricProjectionEnum (MODEL_VIEW), IfcProfileTypeEnum (AREA),
IfcAssemblyPlaceEnum (SITE) y los tipos predefinidos:

| Enumeración | Valores IFC4 | Qué hace el código |
|---|---|---|
| IfcFootingTypeEnum | CAISSON_FOUNDATION, FOOTING_BEAM, PAD_FOOTING, PILE_CAP, STRIP_FOOTING, USERDEFINED, NOTDEFINED | usa PAD_FOOTING |
| IfcColumnTypeEnum | COLUMN, PILASTER, USERDEFINED, NOTDEFINED | PEDESTAL es de IFC4X3 → USERDEFINED |
| IfcPlateTypeEnum | CURTAIN_PANEL, SHEET, USERDEFINED, NOTDEFINED | BASE_PLATE y STIFFENER_PLATE → USERDEFINED |
| IfcMechanicalFastenerTypeEnum | ANCHORBOLT, BOLT, DOWEL, NAIL, NAILPLATE, RIVET, SCREW, SHEARCONNECTOR, STAPLE, STUDSHEARCONNECTOR, USERDEFINED, NOTDEFINED | NUT y WASHER → USERDEFINED |
| IfcBuildingElementProxyTypeEnum | COMPLEX, ELEMENT, PARTIAL, PROVISIONFORVOID, PROVISIONFORSPACE, USERDEFINED, NOTDEFINED | GROUT → USERDEFINED |
| IfcElementAssemblyTypeEnum | …, USERDEFINED, NOTDEFINED | usa USERDEFINED |
| IfcReinforcingBarTypeEnum | ANCHORING, EDGE, LIGATURE, MAIN, PUNCHING, RING, SHEAR, STUD, USERDEFINED, NOTDEFINED | `TIPOS_BARRA` más NOTDEFINED |

**Estructura que se escribe:** proyecto, sitio, edificio y planta con IfcRelAggregates (l. 686-692); ensamblaje,
sub-ensamblajes ZAPATA e hijos (l. 925-969); barras: tipos, IfcRelDefinesByType e IfcRelDeclares (l. 1006-1088); un
IfcRelAssociatesMaterial por material (l. 826-832); un único IfcRelContainedInSpatialStructure (l. 833-838); contexto
`Model` y subcontexto `Body`/MODEL_VIEW; unidades mm, m², m³, kg, rad. `OwnerHistory` nunca se rellena (`$`), igual que hoy.

## 2. Diseño del escritor propio

### 2.1 Módulos
- **`bridge/ifc_step.py`** (nuevo, ~350-450 líneas, a mano): serializador ISO-10303-21 (`ArchivoStep`, `Entidad`, `Valor`),
  `comprimir_guid`, `ErrorEscritor(RuntimeError)`. Solo biblioteca estándar.
- **`bridge/ifc4_esquema.py`** (nuevo, **generado y versionado**): datos puros.
  - `ENTIDADES: dict[str, tuple[supertipo|None, abstracta, tuple[Atributo,...]]]` con atributos propios (la lista completa se
    calcula subiendo por los supertipos, en el orden de IFC4).
  - `Atributo = (nombre, clase, opcional, derivado)`; `clase` ∈ `"ENT"`, `"SELECT"`, `"REAL"`, `"INT"`, `"STR"`, `"BOOL"`,
    `"LOGICAL"`, `("ENUM", nombre)`, `("LIST", clase_elemento, min, max)`.
  - `TIPOS_DEFINIDOS: dict[str, clase_base]` y `ENUMERACIONES: dict[str, frozenset[str]]`.
  - Cabecera de comentario «GENERADO por scripts/generar_esquema_ifc4.py desde IFC4 (ADD2 TC1) — no editar».
- **`scripts/generar_esquema_ifc4.py`** (nuevo, solo desarrollo, usa `ifcopenshell_wrapper.schema_by_name("IFC4")`): parte de la
  lista `USADAS` (40 entidades y 10 tipos), calcula el cierre (supertipos, enumeraciones y tipos definidos de sus atributos) y
  emite `bridge/ifc4_esquema.py` formateado con ruff. `--comprobar` regenera en memoria y falla si difiere.

**Origen de la tabla: generada una sola vez desde `ifcopenshell_wrapper` y guardada como datos** (no a mano): la versión
actual valida contra ese mismo esquema, elimina el riesgo de un orden mal copiado y la prueba de deriva la mantiene
honesta. Tabla parcial (~65 entidades con supertipos), estimada en 10-20 KB. Alternativa: volcar el esquema IFC4 completo
(~770 entidades, ~150-300 KB estimados): quita el paso de «añadir a `USADAS`» pero hincha la revisión.

### 2.2 API mínima (la que usa `cad_ifc.py`)
```python
class ArchivoStep:
    def __init__(self, esquema=ifc4_esquema, nombre_esquema="IFC4"): ...
    def crear_entidad(self, tipo: str, *args, **kw) -> Entidad | Valor
    create_entity = crear_entidad       # alias de compatibilidad (facilita el adaptador de pruebas)
    def cabecera(self, *, descripcion: tuple[str, ...], nombre: str, marca_tiempo: str,
                 sistema: str, preprocesador: str = "bridge.ifc_step") -> None
    def a_texto(self) -> str            # ASCII puro garantizado
    def admite_enum(self, entidad: str, atributo: str, valor: str) -> bool   # sustituye a _admite
```
- Con un **tipo definido** (`"IfcLabel"`, `"IfcLineIndex"`, `"IfcPositiveLengthMeasure"`…) `crear_entidad` devuelve un
  `Valor(tipo, dato)` inmutable que se escribe en línea (sin `#id`), como hace ifcopenshell; así siguen valiendo
  `self.ent(v[0], v[1])` (l. 589), `self.ent("IfcLineIndex", idx)` (l. 779) y `self.ent("IfcBoolean", v)` (l. 591).
- Con una **entidad**: asigna `#n` correlativo en orden de creación, mapea `*args` por posición y `**kw` por nombre sobre la
  lista completa de atributos y lanza `ErrorEscritor` si el tipo no existe o es abstracto, el atributo no existe, se pasa un
  atributo derivado, una enumeración está fuera de su conjunto o el tipo de Python no casa. Coerción: `int` en REAL se
  escribe como real; `bool` nunca cuenta como `int`.
- `Entidad`: `__slots__ = ("_tipo", "_id", "_vals", "_archivo")`, `__getattr__` por nombre (`None` si no asignado),
  `__setattr__` validado (hace falta para `HasPropertySets`, l. 612), `is_a(t=None)` y `id()`.
- Al serializar, un atributo obligatorio vacío lanza `ErrorEscritor` (se detecta antes que `validate`).

### 2.3 Serialización STEP
- **Cabecera:**
  ```
  ISO-10303-21;
  HEADER;
  FILE_DESCRIPTION(('ViewDefinition [ReferenceView_V1.2]'),'2;1');
  FILE_NAME('<id>.ifc','<AAAA-MM-DDThh:mm:ss>',(''),(''),'bridge.ifc_step <ver>','Workbench puente local (bridge.cad_ifc)','');
  FILE_SCHEMA(('IFC4'));
  ENDSEC;
  DATA;
  ```
  Entidades ordenadas por `#id`; termina con `ENDSEC;` y `END-ISO-10303-21;`; separador `\n`.
- **Nombres** en mayúsculas (`#12=IFCEXTRUDEDAREASOLID(#9,#10,#11,400.);`); referencias `#n`; ausente `$`; derivado `*`;
  enumeración `.NOMBRE.`; booleano `.T.`/`.F.`; lógico también `.U.`.
- **Listas** `(a,b,c)`, anidadas recursivamente (`CoordList`: `((0.,0.,0.),(1.,2.,3.))`); se comprueban los límites `[min:max]`
  del esquema (p. ej. `RelatedObjects` no puede ir vacío).
- **SELECT:** el `Valor` va envuelto (`IFCPOSITIVELENGTHMEASURE(19.)`); en atributo no-SELECT, desenvuelto.
- **Reales:** `repr(float(x))`; si hay exponente, `e` → `E`; se garantiza punto en la mantisa (`1e-05` → `1.E-05`,
  `1e+16` → `1.E+16`, `1000.0` igual); `-0.0` → `0.`; NaN e infinito lanzan `ErrorEscritor` (la validación `_num`, l. 190,
  ya los impide).
- **Enteros:** `str(int)`.
- **Cadenas** (ISO 10303-21 ed. 2): literales los caracteres 0x20-0x7E; `'` → `''`; `\` → `\\`; cualquier otro (incluidos
  controles como `\n` y los no ASCII `Ø`, `ó`, `·` de los 7 fixtures) como `\X2\HHHH…\X0\` (UCS-2 hex en mayúsculas,
  agrupando consecutivos); por encima de U+FFFF `\X4\HHHHHHHH…\X0\`. Salida ASCII pura: `bytes_ifc` puede usar
  `.encode("ascii")`, que además sirve de comprobación. El fixture `perno_pg` contiene `\` y `pedestal` contiene `'`.
- **GlobalId:** se porta el algoritmo de `ifcopenshell.guid.compress` (dominio público); se comprueba contra la librería en CI.
  ```python
  _C = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
  def _b64(v, n): return "".join(_C[(v // 64**i) % 64] for i in range(n))[::-1]
  def comprimir_guid(h: str) -> str:
      b = bytes.fromhex(h)            # 16 bytes
      return _b64(b[0], 2) + "".join(_b64((b[i] << 16) | (b[i+1] << 8) | b[i+2], 4) for i in range(1, 16, 3))
  ```
  Determinismo igual que ahora: `uuid5(_NS, "/".join(partes))` (l. 51 y 535-539); los GlobalId serán **idénticos** a los de
  la versión con ifcopenshell.
- **Orden y deduplicación:** orden de creación (determinista: los dicts conservan el orden). **Sin deduplicación en el MVP**
  (ifcopenshell tampoco deduplica, así la equivalencia es 1:1); una caché de puntos y direcciones constantes queda para la
  fase 5. `generar_ifc(modelo, *, marca_tiempo=None)`: con marca fija la salida es reproducible byte a byte (pruebas golden).

## 3. Estrategia de migración sin riesgo
1. **`main` no contiene el IFC.** Recomendación: **no fusionar la versión con ifcopenshell en `main`**; trabajar en una rama
   nueva desde `c9c00ac` y fusionar la versión pura. Ningún `.exe` publicado lleva los ~100 MB.
2. **Inyección del archivo:** `_Escritor.__init__(self, m, archivo=None)` usa `archivo or ifc_step.ArchivoStep()`; `ent()` queda
   en una línea (`return self.f.crear_entidad(tipo, *args, **kw)`). El resto de `_Escritor` no cambia.
3. **No hace falta capa de compatibilidad completa con ifcopenshell:** basta el alias `create_entity`, `Entidad.is_a` y el
   acceso por atributo.
4. **Ambas implementaciones en paralelo, solo en pruebas:** `tests/ifc_referencia.py` define `ArchivoIfcopenshell` (~30 líneas
   sobre `ifcopenshell.file(schema="IFC4")`): `crear_entidad`→`create_entity`, `cabecera`→`header`, `a_texto`→`to_string`,
   `admite_enum`→wrapper. `bridge/` deja de tener cualquier referencia a ifcopenshell.
5. **Cambios en `cad_ifc.py` (~30 líneas):** docstring (l. 5-7); borrar `FaltaIfcopenshell`, `_importable` y la caché (l. 165-184;
   `disponible()` devuelve `True` o se elimina, ver 5.4); `_gid` usa `ifc_step.comprimir_guid`; `__init__` (l. 547-551); `ent`
   (l. 556-557); `cabecera` (l. 622-630); `_admite` usa `self.f.admite_enum(ifc, "PredefinedType", predef)`; `bytes_ifc`;
   quitar el 503 de `generar_ifc` (l. 1102-1105).
6. **Cambios en `cad_drwr.py`:** docstring (l. 10-11); `res["ifc"] = True` (l. 411); quitar `except cad_ifc.FaltaIfcopenshell`
   (l. 560-561).

## 4. Estrategia de verificación (lo más importante)

### 4.1 Unitarias del serializador, sin ifcopenshell (`tests/test_ifc_step.py`, siempre)
- **Vectores fijos de `comprimir_guid`:** `00000000…0` → `0000000000000000000000`, más 5-10 GUID conocidos copiados de la
  salida de ifcopenshell en CI; longitud 22 y primer carácter en `0-3`.
- **Reales:** `0.0`, `-0.0`, `1.0`, `1e-5`, `1e16`, `6.123e-17`, `123456.789`, subnormales; formato STEP con expresión regular y
  `float(texto_reparado)` igual al original.
- **Cadenas:** apóstrofo, barra invertida, `Ø`, `ó`, `·`, `\n`, tabulador, un emoji (≥ U+10000), cadena vacía y 10 000
  caracteres; decodificador mínimo de solo pruebas (`\X2\`, `\X4\`, `''`, `\\`, ~25 líneas).
- **Fuzz con semilla fija** (`random.Random(1234)`, cientos de casos, sin `hypothesis`): cadenas Unicode, reales aleatorios
  (`struct` sobre bits filtrando NaN e infinito), listas anidadas de 0 a 3 niveles; ida y vuelta con el decodificador.
- **Errores:** atributo inexistente, tipo abstracto, enumeración fuera de rango, obligatorio vacío, lista fuera de `[min:max]`,
  `bool` en REAL → todos `ErrorEscritor`.
- **Estructura:** un IfcCartesianPoint y un IfcPropertySingleValue con `IFCLABEL(...)` producen exactamente la línea esperada;
  una entidad derivada (`IfcSIUnit`) empieza por `*`.

### 4.2 Ida y vuelta con ifcopenshell (CI)
- Cadenas y reales del fuzz escritos en un IfcLabel y un IfcCartesianPoint de un archivo mínimo, reabiertos con
  `ifcopenshell.file.from_string`, con igualdad exacta (`wrappedValue`, `Coordinates`).
- **GUID:** `comprimir_guid(u.hex) == ifcopenshell.guid.compress(u.hex)` para 10 000 `uuid4` y para todos los `uuid5` de los fixtures.

### 4.3 Equivalencia estructural (`tests/test_bridge_cad_ifc_equivalencia.py`, CI)
Para cada uno de los 7 fixtures y 2 variantes (origen y ángulo distintos; varias zapatas): generar con `ArchivoStep` y con
`ArchivoIfcopenshell`, reabrir ambos con `ifcopenshell.open` y exigir mismo número de entidades por `is_a()`, mismo
conjunto de GlobalId y, por cada `IfcRoot` emparejado por GlobalId, forma canónica recursiva igual (`get_info(recursive=True)`
sin `id`, reales con tolerancia relativa y absoluta de 1e-12, orden de listas conservado). No depende de la numeración `#n`.
Diferencias permitidas: solo la cabecera.

### 4.4 Validación EXPRESS (CI)
Mantener las pruebas actuales con `ifcopenshell.validate.validate(f, log, express_rules=True)` y `log.statements == []`
(`_valida`, `test_el_ifc_valida…`, `test_nuevos_valida_y_reabre`, `test_conjuntos_valida…`) sobre la salida propia, y añadir
una parametrizada sobre **todos** los fixtures y un modelo sintético de 5000 instancias de barras (el límite) que también mida
tiempo y memoria (objetivo orientativo, sin medir: < 3 s en CI).

### 4.5 GlobalId y geometría (CI)
Mantener `test_globalid_deterministas_y_unicos`, `test_globalid_cambian_si_cambia_el_conjunto`, `*_globalid_estables` y todas
las de volumen con `ifcopenshell.geom` (barras π·d²/4·L, placa con agujeros, HSS, planchas, zapatas, al 3 %). Añadir:
GlobalId propio == GlobalId de la versión ifcopenshell (sale de 4.3).

### 4.6 Golden, deriva del esquema, cobertura y ausencia de la dependencia
- **Golden:** con `marca_tiempo="2026-01-01T00:00:00"`, los 7 IFC contra `tests/fixtures/ifc/*.ifc` (o SHA-256 si el total supera
  ~1 MB, a medir); script documentado para regenerar. **Se ejecuta sin ifcopenshell.**
- **Deriva:** `python scripts/generar_esquema_ifc4.py --comprobar` como prueba pytest (skip si falta ifcopenshell; en CI no se salta).
- **Cobertura:** con `ast`, los literales `"Ifc…"` de `cad_ifc.py` y `CLASES[*].ifc` deben estar en `ENTIDADES ∪ TIPOS_DEFINIDOS`;
  tabla esperada de la decisión `PredefinedType`/`USERDEFINED` por clase (PEDESTAL, BASE_PLATE, STIFFENER_PLATE, NUT, WASHER,
  GROUT → USERDEFINED) contra `admite_enum`.
- **Sin ifcopenshell en el puente:** ampliar `test_el_modulo_no_importa_ifcopenshell_ni_el_motor_al_cargar` para recorrer con
  `ast.walk` **todo** `bridge/**/*.py`.
- **Guarda de empaquetado:** una prueba que lea `scripts/build_bridge.ps1` y falle si aparece `ifcopenshell`.
- **CI no debe saltar en silencio:** hoy la fixture autouse `_requiere_ifcopenshell` (l. 41-44) salta si falta la librería; en
  CI (`os.environ.get("CI")`) debe fallar. Las pruebas de ruta 200/422 y las golden ya no la necesitan.

### 4.7 Revit (manual, en Windows)
1. **Artefacto:** un job de CI (o `scripts/muestras_ifc.py`) genera `muestras_ifc.zip` con, por fixture, `<id>__propio.ifc` y
   `<id>__ifcopenshell.ifc` (datos sintéticos, aptos para repositorio público).
2. **Pasos por archivo** (con la versión de Revit que decida el dueño): a) Abrir › IFC con opciones por defecto y aparte
   Insertar › Vincular IFC; b) anotar diálogos de error o aviso; c) contar elementos por categoría; d) medir cotas: zapata de
   `fundacion` 3000 × 3500 mm, placa de `placa_base` 440 × 590 mm, hormigón 640 × 640 mm; e) posición del ensamblaje
   `FUNDACION` en (1500, 1750, −1000) mm; f) propiedades de ejemplar: `IfcGUID`, `Pset_WB_Vinculo`, `Pset_WB_Pieza`, textos con
   `Ø`/`ó` legibles, `ObjectType` = `BASE_PLATE`/`NUT`…; g) barras visibles como DirectShape con los arcos de `B2a`; h) recargar el
   vínculo con el archivo regenerado sin duplicar elementos (GUID estables).
3. **Comparación lado a lado** con `__ifcopenshell`. La versión actual nunca se ha probado en Revit (`docs/CAD_IFC.md` l. 139):
   si falla en las dos, el problema es del modelado y no del escritor.
4. **Opcional:** subir los mismos archivos sintéticos al validador de buildingSMART y abrirlos en un visor gratuito. Necesita el
   visto bueno del dueño para enviar datos a un servicio externo.

**Criterio de aceptación** (para los 7 fixtures): a) EXPRESS con 0 incidencias; b) equivalencia estructural con 0 diferencias;
c) GlobalId idénticos a los de la versión ifcopenshell; d) en Revit, el archivo `__propio` abre y se vincula sin errores, con el
mismo recuento por categoría, las mismas propiedades y las mismas cotas que `__ifcopenshell`; e) el `.exe` compilado en
`windows-latest` responde 200 a `cad/ifc` y `ifc: true` a `cad/status`, y se ha medido su tamaño frente a la versión anterior.

## 5. Empaquetado, CI, peso, `cad/status` y contratos
- **5.1 `scripts/build_bridge.ps1`:** quitar las l. 26-27 (comentario y `pip install -r backend\requirements-puente-ifc.txt`) y
  `"--collect-all", "ifcopenshell"` (l. 45) con la coma de la línea anterior. `bridge.ifc_step` y `bridge.ifc4_esquema` se importan
  de forma estática desde `cad_ifc` (que importa `cad_drwr`), así que PyInstaller los encuentra sin `--hidden-import`.
- **5.2 Requisitos y CI:** borrar `backend/requirements-puente-ifc.txt`; mover `ifcopenshell==0.9.0.post1` a
  `backend/requirements-dev.txt`; en `.github/workflows/ci.yml` l. 19 quitar `-r backend/requirements-puente-ifc.txt`; añadir el
  paso `python scripts/generar_esquema_ifc4.py --comprobar` (o confiar en la prueba pytest). `numpy` ya está en
  `requirements.txt` y `lark` viene con ifcopenshell. `docs/CAD_IFC.md` menciona `scripts/build_portable.ps1` y
  `scripts/exportar_puente.py`, que viven en el repositorio del Workbench (supuesto): también hay que quitarles el requirements
  y el `--collect-all`.
- **5.3 Peso:** medido, `bridge/cad_ifc.py` ocupa 46 004 bytes. **Estimado, sin medir:** `ifc_step.py` de 14-18 KB y
  `ifc4_esquema.py` parcial de 10-20 KB (25-40 KB de código fuente en total). **No medido:** el ahorro de «~100 MB»; puede
  haber además dependencias transitivas de ifcopenshell (lark, isodate, shapely…). Cómo medirlo: lanzar `build-bridge.yml` en
  la rama `ifc-ifcopenshell` y en la nueva y comparar `SAP2000Bridge.zip`, `SAP2000Bridge.exe` y el tiempo de arranque; anotar
  el resultado en `docs/MEJORAS_PENDIENTES.md`.
- **5.4 `cad/status` y contratos. Recomendado: conservar `ifc`, siempre `true`.** Sirve de indicador de capacidad (un `.exe`
  antiguo sin la ruta no trae la clave y la web lo distingue) y quitarla desactivaría el botón si la web ya la lee. El contrato
  ampliado ya está preparado en la rama (`web/src/bridge_contracts.json` l. 247, `bridge_contracts.cad_drwr.json` l. 4,
  `CLAVES_AMPLIADAS` en `tests/test_bridge_contracts_integrados.py` l. 143): no hay que tocar ningún contrato. Pruebas afectadas:
  `test_ruta_503_sin_ifcopenshell_y_estado` → `test_estado_ifc_siempre_true`; `tests/test_cad_drwr_bridge.py` l. 358 → `"ifc": True`;
  se elimina el 503 de la ruta y de `docs/CAD_IFC.md`, `docs/SAP_ACTIONS.md` y `README.md`.
- **5.5 Documentación:** reescribir en `docs/CAD_IFC.md` «Instalación de `ifcopenshell`» como «Escritor IFC propio
  (`bridge/ifc_step.py`)»: esquema generado, cómo añadir una entidad (añadir a `USADAS`, regenerar y probar), formato de reales
  y cadenas, y que ifcopenshell queda solo para pruebas; quitar la fila 503.

## 6. Riesgos y mitigaciones
| Riesgo | Mitigación |
|---|---|
| Revit rechaza el archivo por un atributo mal ordenado o una enumeración errónea | Orden y enumeraciones generados desde el esquema; validación al crear; EXPRESS en CI; equivalencia 1:1 con ifcopenshell; prueba en Revit comparando con el archivo de referencia |
| Deriva respecto al esquema IFC4 (versión de ifcopenshell, ADD2 TC1 frente a otras) | Fijar `ifcopenshell==0.9.0.post1` en dev; prueba `--comprobar`; `FILE_SCHEMA(('IFC4'))` explícito; cambiar a IFC4X3 sería otro proyecto |
| Una clase nueva del modelo usa una entidad que no está en la tabla | Prueba de cobertura (4.6); error claro en `crear_entidad`; procedimiento documentado |
| Formato de números (`1e-05` o enteros donde va REAL) | Coerción por tipo de atributo; reales siempre con punto y `E`; fuzz con ida y vuelta exacta |
| Codificación de texto (`Ø`, `ó`, saltos de línea, emojis) | `\X2\`/`\X4\` y salida ASCII estricta; si Revit no lee `\X4\`, sustituir por `?` |
| Rendimiento con 5000 instancias en Python puro | Prueba de tamaño máximo con medida de tiempo; `"".join`; `__slots__` |
| Un error de programación acaba en 500 en lugar de IFC | Todas las clases y fixtures cubiertas en CI; `ErrorEscritor` no se oculta como 422 |
| Licencia | La dependencia LGPL desaparece del `.exe`. La tabla generada contiene hechos del esquema de buildingSMART, no código de ifcopenshell (supuesto razonable); el algoritmo del GUID es de dominio público |
| Pruebas que se saltan en silencio | En CI, fallar en lugar de saltar (4.6) |
| Riesgo de producto | Bajo: `main` no tiene IFC, así que no hay usuarios ni archivos previos que romper |

## 7. Fases, entregables, criterios de salida y esfuerzo
Incertidumbre global ±40 %; días de una persona.

| Fase | Entregables | Criterio de salida | Esfuerzo |
|---|---|---|---|
| **F0. Decisiones** | Respuestas a las preguntas de abajo | Decisiones cerradas | 0,5 d |
| **F1. Núcleo** | `scripts/generar_esquema_ifc4.py`, `bridge/ifc4_esquema.py` generado, `bridge/ifc_step.py` (serializador y GUID), `tests/test_ifc_step.py`, ida y vuelta con ifcopenshell (4.1-4.2) | Pruebas en verde; vectores GUID iguales; ruff sin avisos | 2-3 d |
| **F2. Integración y equivalencia** | `cad_ifc.py` y `cad_drwr.py` adaptados (sección 3); `tests/ifc_referencia.py`; `tests/test_bridge_cad_ifc_equivalencia.py`; pruebas existentes adaptadas; modelo de 5000 instancias | 0 diferencias estructurales en 7 fixtures más variantes; EXPRESS y volúmenes en verde | 1,5-2,5 d |
| **F3. Limpieza** | Requisitos movidos, `ci.yml`, `build_bridge.ps1`, guardas (AST en todo `bridge/`, script de compilación), CI sin skips, documentación | CI en verde sin ifcopenshell en el runtime | 0,5-1 d |
| **F4. Windows y Revit** | `build-bridge.yml` en las dos ramas con medida de tamaño y arranque; prueba del `.exe` (`cad/ifc` 200, `ifc: true`); `muestras_ifc.zip`; protocolo de 4.7 ejecutado y anotado en `docs/CAD_IFC.md` | Criterio de aceptación de 4.7 | 1-2 d (depende del acceso a Windows y Revit) |
| **F5. Endurecimiento (opcional)** | Golden o hashes; validación buildingSMART; caché de primitivas si el tamaño lo justifica; prueba en Tekla | Sin regresiones en golden ni equivalencia | 1-2 d |

- **MVP:** F0+F1+F2+F3 y la parte de compilación de F4 (`.exe` y endpoint): **4,5-7 d**. La prueba en Revit puede seguir justo
  después, porque la equivalencia con ifcopenshell garantiza que el archivo es el mismo que habría salido antes.
- **Completo:** MVP + Revit + F5: **7-11 d**.
- **Paralelizable:** generador del esquema y serializador (F1); adaptador `ArchivoIfcopenshell` y esqueleto de las pruebas de
  equivalencia (en paralelo con F1); preparación del artefacto de muestras y reserva del equipo con Revit (con F2); documentación (con F2 y F3).

**Decisiones que necesita el dueño antes de empezar**
1. ¿Fusionar directamente la versión pura (recomendado) o fusionar antes la de ifcopenshell en `main`?
2. Clave `ifc` de `cad/status`: ¿conservarla siempre `true` (recomendado) o eliminarla?
3. ¿Qué versiones de Revit y quién hace la prueba manual en Windows? ¿Entra Tekla en esta fase?
4. ¿Se permite subir IFC **sintéticos** al validador de buildingSMART?
5. ¿Golden files `.ifc` en el repositorio (diff legible, ~1 MB a medir) o solo hashes?

## 8. Archivos críticos y carpetas
```
bridge/
  cad_ifc.py              (modificar: ~30 líneas, sección 3)
  cad_drwr.py             (modificar: docstring, l. 411, l. 560-561)
  ifc_step.py             (NUEVO: ArchivoStep, Entidad, Valor, formateo STEP, comprimir_guid)
  ifc4_esquema.py         (NUEVO, GENERADO: ENTIDADES, TIPOS_DEFINIDOS, ENUMERACIONES)
scripts/
  build_bridge.ps1        (modificar: quitar pip install y --collect-all ifcopenshell)
  generar_esquema_ifc4.py (NUEVO, solo desarrollo; --comprobar)
  muestras_ifc.py         (NUEVO, opcional: zip de muestras para Revit)
backend/
  requirements-dev.txt    (añadir ifcopenshell==0.9.0.post1)
  requirements-puente-ifc.txt (BORRAR)
.github/workflows/ci.yml  (modificar l. 19; paso --comprobar)
tests/
  test_ifc_step.py                       (NUEVO: serializador, GUID, fuzz; sin ifcopenshell)
  test_ifc_step_ifcopenshell.py          (NUEVO: ida y vuelta, GUID frente a la librería, deriva)
  ifc_referencia.py                      (NUEVO: adaptador ArchivoIfcopenshell)
  test_bridge_cad_ifc_equivalencia.py    (NUEVO)
  test_bridge_cad_ifc.py                 (modificar: 503 → ifc siempre true, skips, AST en todo bridge/)
  test_cad_drwr_bridge.py                (modificar l. 358)
  fixtures/ifc/                          (NUEVO, opcional: golden o SHA256SUMS)
docs/CAD_IFC.md, docs/SAP_ACTIONS.md, docs/MEJORAS_PENDIENTES.md, README.md (modificar)
web/src/*.json            (sin cambios si se conserva la clave `ifc`)
```

## Resumen ejecutivo
1. `cad_ifc.py` usa de ifcopenshell solo `file`/`create_entity` (40 entidades y 10 tipos definidos), `guid.compress`, la consulta de
   enumeraciones y `to_string`, todo concentrado en unas 30 líneas.
2. Se sustituye por `bridge/ifc_step.py`: serializador ISO-10303-21 a mano con el mismo `crear_entidad(tipo, *args, **kw)`.
3. El orden de atributos y las enumeraciones salen de `bridge/ifc4_esquema.py`, generado una vez desde `ifcopenshell_wrapper`;
   el CI vigila que no haya deriva.
4. Los GlobalId siguen siendo deterministas e idénticos a los de ahora.
5. Salida ASCII pura (`\X2\`/`\X4\`), reales que vuelven exactos al leerlos; sin deduplicación para que la equivalencia sea 1:1.
6. Verificación: equivalencia estructural con ifcopenshell sobre los 7 fixtures, EXPRESS, volúmenes con `geom`, fuzz con semilla
   fija, golden y Revit manual comparando lado a lado.
7. ifcopenshell pasa a `requirements-dev.txt`; se quitan `--collect-all` y el `pip install` del `.exe`, desaparece el 503 y `ifc`
   queda siempre `true`, sin cambiar contratos.
8. Código nuevo de unos 25-40 KB (estimado) frente a ~100 MB (dato del encargo, a medir con `build-bridge.yml`); la LGPL sale del `.exe`.
9. Esfuerzo: MVP 4,5-7 días y completo 7-11 días, ±40 %. Riesgo bajo porque `main` aún no tiene IFC.
10. Cinco decisiones pendientes del dueño: estrategia de fusión, clave `ifc`, Revit y quién prueba, validador externo y golden o hashes.
