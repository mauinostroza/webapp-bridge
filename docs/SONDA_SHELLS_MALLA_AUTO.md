# Sonda de shells: malla automática, signo de V y API/tabla

`scripts/sonda_shells_malla_auto.py` completa la sonda fase 0 de shells (SAP2000 v23.3.1) con las pruebas pendientes. Solo lee.

## Qué mide

| Id | Prueba |
|---|---|
| A0 | L2 está mallada automáticamente (más de un elemento por área) |
| A1 | La interpretación por columnas de `AreaElm.GetTransformationMatrix` valida en todos los elementos |
| A2 | Los nudos internos `~n` de la malla automática no son `PointObj` (se leen con `PointElm`) |
| A3 | **Herencia de ejes:** el eje 1 de cada elemento coincide con el del objeto (`AreaObj.GetTransformationMatrix`, ángulo 30°) |
| A4 | La regla por defecto de SAP2000 más el ángulo del objeto (respaldo del puente) reproduce los ejes de los elementos |
| A5 | El puente devuelve ejes para todos los elementos de L2 (y qué avisos da) |
| B1V13, B1V23, B1M11 | **Signo** de V13, V23 y M11 en L3 respecto a L1, elemento a elemento. Esperado: V13 igual, V23 opuesto, M11 opuesto |
| B2 | ΣF3 de `AreaJointForceShell` en ejes globales coincide en L1, L2 y L3 (valida que `frame: "global"` de `shell-joint-forces`, el valor crudo, está en globales) |
| C1 | `Results.AreaForceShell("ALL")` y la tabla «Element Forces - Area Shells» dan los mismos valores en el modelo real |
| Z | Unidades, bloqueo y selección de salida quedan como estaban |

B se corre con el caso Q y con el caso P (carga puntual); C con Q.

## Preparar el modelo (solo para A)

1. Abra `SONDA_F0_SHELL.sdb` (importado de `docs/ejemplos/sonda_f0_shell/SONDA_F0_SHELL.s2k`) con los resultados ya calculados, o
   impórtelo y corra el análisis. Guarde antes una copia: el paso 2 modifica el modelo y obliga a volver a analizar.
2. Borre las áreas 1001–1096 (L2), **sin borrar los nudos**: los de las esquinas conservan los apoyos (1001, 1013, 1105 y 1117).
3. Dibuje L2 de nuevo como un solo área de 4 esquinas, ajustando a esos cuatro nudos (≈ (10; 0), (15,196; 3), (13,196; 6,464), (8; 3,464)):
   sección `LOSA40`, normal +Z, **ángulo de ejes locales 30°** (Assign > Area > Local Axes) y la carga `Q` de 2 tonf/m² de gravedad.
4. Assign > Area > Automatic Area Mesh > «Mesh area into objects of this maximum size» 0,5 × 0,5.
5. Anote el nombre del área nueva (clic derecho sobre ella). Corra el análisis (DEAD, Q, P). Opcional: Display > Show Local Axes en L2.

Para B y C no hace falta cambiar nada: basta el modelo analizado de la sonda.

## Ejecutar

```powershell
py -3.12 -m pip install -r backend\requirements.txt pywin32 comtypes
py -3.12 scripts\sonda_shells_malla_auto.py --l2 NOMBRE_DEL_AREA_L2
```

Opciones: `--l1`, `--l3` (por defecto los rangos 1–96 y 2001–2096), `--caso Q`, `--caso-p P`, `--desplaza-l3 2000`, `--salida-dir`.
Si no hizo el cambio de malla, deje `--l2` por defecto: A0 queda INCONCLUSO y B y C se ejecutan igual.

## Qué anotar y devolver

El `.json` (y el `.md`) de `sonda_shells_malla_auto_AAAAMMDD_HHMM`. Anote además:

- **A3:** el ángulo máximo de desviación que informa y, si es FAIL, una captura de Display > Show Local Axes sobre L2 mallada.
- **B1/B2:** el veredicto de cada una y los `conteo` (iguales y opuestos) de V13, V23 y M11 en Q y en P; los totales ΣF3 de L1, L2 y L3.
- **C1:** la diferencia máxima y los dos tiempos.
- Cualquier `ERROR` con su mensaje.

## Cómo se interpreta

- A3 PASS: los elementos de una malla automática heredan los ejes del objeto; el puente puede leerlos por elemento (como ya hace) y la
  regla por defecto sirve de respaldo. A3 FAIL: la herencia no se cumple; `AreaElm` es la única fuente fiable.
- B1: si V13 sale igual y V23 opuesto, la convención del prototipo web es la correcta; si no, hay que cambiarla en `corteShell.ts`.
- B2 FAIL: SAP2000 no entrega las fuerzas de nudo en ejes globales. Resultado de la sonda con SAP2000 v23.3.1: **PASS** (los valores crudos
  son globales; una versión anterior del puente los rotaba por los ejes del elemento y salía −48 en L3).
- C1 FAIL: la API y la tabla no coinciden en el modelo real; revisar unidades, selección de salida y pasos. La tabla redondea los valores que
  imprime: se acepta una diferencia de hasta 1e-3 (la sonda dio 4,97e-4).
