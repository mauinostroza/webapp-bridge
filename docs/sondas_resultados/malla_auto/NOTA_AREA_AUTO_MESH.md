# Tabla «Area Auto Mesh Assignments» de SAP2000 v23.3.1 (leída por API)

Origen: se asignó `Assign > Area > Automatic Area Mesh > Auto Mesh Area Into Objects of This Maximum Size`
(0,5 m a lo largo de los bordes 1-2 y 1-3) al área `1` de una copia desbloqueada de `MUROS_F0_T`, y se leyó la tabla con
`DatabaseTables.GetAllFieldsInTable`, `GetTableForDisplayArray` y `GetTableForEditingArray`.

Nombre de la tabla (`GetAvailableTables`): `Area Auto Mesh Assignments`.

## Campos (`GetAllFieldsInTable`, 17 campos; clave = nombre)
`Area`, `MeshType`, `MeshGroup`, `Number1`, `Number2`, `Max1`, `Max2`, `PtsFromLine`, `PtsFromPt`, `RotAngle`,
`GenMaxSize`, `LocalEdge`, `LocalFace`, `SuppEdge`, `SuppFace`, `SubMesh`, `SubMeshSize`.

## Columnas de `GetTableForDisplayArray` (10; omite los campos que no aplican al tipo de malla)
`Area`, `MeshType`, `MeshGroup`, `Max1`, `Max2`, `LocalEdge`, `LocalFace`, `SuppEdge`, `SuppFace`, `SubMesh`.

## Fila completa (`GetTableForEditingArray`, los 17 campos)
| Campo | Valor |
|---|---|
| Area | 1 |
| MeshType | Maximum Size |
| MeshGroup | ALL |
| Number1 | (vacío) |
| Number2 | (vacío) |
| Max1 | 0.5 |
| Max2 | 0.5 |
| PtsFromLine | (vacío) |
| PtsFromPt | (vacío) |
| RotAngle | (vacío) |
| GenMaxSize | (vacío) |
| LocalEdge | No |
| LocalFace | No |
| SuppEdge | No |
| SuppFace | No |
| SubMesh | No |
| SubMeshSize | (vacío) |

## Valores válidos de `MeshType`
La descripción que da SAP2000 para el campo (`GetAllFieldsInTable`) dice: «Number of Elements, Maximum Size, Points On
Edges, Cookie Cut Lines, Cookie Cut Points or General». Solo `Maximum Size` está comprobado asignándolo y leyéndolo; los
otros cinco salen de esa descripción, sin asignarlos. «None» del cuadro de diálogo equivale a no tener fila para el área.
Campos que aplican a cada tipo (según las descripciones de SAP): `Number1/Number2` con Number of Elements, `Max1/Max2` con
Maximum Size, `PtsFromLine/PtsFromPt` con Points On Edges, `RotAngle` con Cookie Cut Points, `GenMaxSize` con General.

## Causa de que los `.s2k` generados no dieran la malla de 0,5 m
Los generadores escribían `MeshType="Mesh into objects of maximum size"` y los campos `MaxSize1` y `MaxSize2`. SAP2000
aceptó la tabla sin error, pero no reconoció esos campos y aplicó el tamaño por defecto (Max1 = Max2 = 1,2192 m):
- L2 (6 x 4 m) salió con 20 elementos (5 x 4) en vez de 96.
- Los muros `_AUTO` salieron con 12 a 20 elementos por área en vez de la malla de 0,5 m.
Con `MeshType="Maximum Size"   Max1=0.5   Max2=0.5` L2 da 96 elementos (comprobado: importar, analizar y contar con
`AreaObj.GetElm`). El `.s2k` corregido es `SONDA_F0_SHELL_L2AUTO_MS.s2k` (en esta carpeta).

Hay que cambiar `TABLA_MALLA_AUTO`/`CAMPOS_MALLA_AUTO` y el valor de `MeshType` en `generar_s2k.py` de losas y de muros y
volver a generar los `_AUTO`. Con el campo mal escrito también quedan por defecto `LocalEdge` y `SuppEdge` en Yes.
