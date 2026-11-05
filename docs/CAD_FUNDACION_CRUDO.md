# CAD › Fundación desde SAP2000: formato crudo `sap-crudo-v1`

Parte del plan `docs/PUENTE_SIN_MOTORES.md` (pasos 9 y 10). El puente **solo lee SAP2000**; el servidor
(`fundacion_desde_crudo`) hace toda la geometría (componentes conexas, contorno, simplificación, pedestales).

## Resultado del trabajo `…/cad/sap/foundation/status` (`resultado`)

```json
{
  "formato": "sap-crudo-v1",
  "grupo": "ZAPATAS",
  "unidades": "mm",
  "areas": [
    {"nombre": "A1", "seccion": "ZAP40", "espesor": 400.0,
     "pts_nombres": ["1", "2", "3", "4"],
     "pts_malla": ["1", "2", "3", "4", "10"]}
  ],
  "nudos": {"1": [0.0, 0.0, 0.0], "10": [500.0, 500.0, 0.0]},
  "frames": [
    {"frame": "P1", "punto_i": "10", "punto_j": "20", "seccion": "COL40x40",
     "rect": [400.0, 400.0], "circ": null, "angulo_local": 0.0}
  ]
}
```

- Unidades: mm (el puente fija las unidades presentes a mm, como hoy `link.set_units_mm()`).
- `areas`: solo los shells del grupo, con `espesor` = `null` si no se pudo leer; `pts_malla` = joints de la malla de
  análisis (si SAP no devuelve ninguno, el puente repite `pts_nombres`, igual que hoy).
- `nudos`: coordenadas `[x, y, z]` de **todos** los joints citados en `areas[*].pts_nombres`, `areas[*].pts_malla`
  y en los frames incluidos (extremos `punto_i` y `punto_j`).
- `frames`: **solo** los frames con al menos un extremo en la unión de `pts_nombres` y `pts_malla` de las áreas
  (los demás no pueden ser pedestales). Si el puente no pudo leer los extremos o las coordenadas de un frame, lo omite
  (hoy también se descarta).
  - `seccion`: nombre de la sección del frame (si falla la lectura, se omite el frame, como hoy).
  - `rect`: `[largo, ancho]` en mm si la sección es rectangular, si no `null`.
  - `circ`: `[d, d]` en mm si es circular, si no `null`. Si no es ni rectangular ni circular: ambos `null`.
  - `angulo_local`: ángulo de ejes locales en grados (el valor de `GetLocalAxes`), o `null` si no se pudo leer.
- Cualquier fallo de lectura de un dato opcional produce `null`, nunca una excepción (el servidor decide el valor
  por defecto). Fallos de lectura del grupo o sin shells siguen siendo errores del trabajo, como hoy.

## Qué hace el servidor (`fundacion_desde_crudo(crudo) -> FundacionGeom`)

Reproduce **exactamente** lo que hacía `bridge/cad_sap_link.py::leer_fundacion` y `detectar_pedestales` tras la
lectura: `agrupar_por_adyacencia`, `contorno_exterior`, `simplificar_colineales`, verticalidad (`_EPS_VERTICAL_MM`),
pie del pedestal (extremo de menor z), dimensiones (rectángulo → círculo aproximado como cuadrado → relleno
`_DIMENSION_PLACEHOLDER_MM` con aviso), orientación (`angulo % 180 < 45 or >= 135`, por defecto `true` si
`angulo_local` es `null`), y el nombre de la zapata (el pedestal si hay uno solo, si no `F{k+1}`).
Resultado: la misma forma de `FundacionGeom.to_dict()` de hoy.

## Compatibilidad

- Web nueva + puente viejo: `resultado` no trae `formato` → se usa tal cual (forma antigua).
- Puente nuevo + web vieja: `esGeomValida` rechaza el resultado → error visible «resultado inválido».
