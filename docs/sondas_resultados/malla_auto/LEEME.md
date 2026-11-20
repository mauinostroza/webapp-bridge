# Resultados de la sonda de malla automática (webapp-bridge, SAP2000 v23.3.1)

`scripts/sonda_shells_malla_auto.py` sobre el modelo de la sonda F0 de losas.

- `..._1647`: L2 explícita (96 elementos): A0 y A2 INCONCLUSO, resto PASS salvo B2 y C1 (FAIL).
- `..._1649`: L2 como un solo área con malla automática (20 elementos, `SONDA_F0_SHELL_L2AUTO.s2k`): 14 PASS, 2 FAIL.

Resultados con L2 mallada:
- A0-A5 PASS: los elementos heredan los ejes del objeto (desviación 0,0000° respecto a 30°), la interpretación por
  columnas valida en 20 de 20, los nudos internos `~n` no son `PointObj` (26) y el puente devuelve ejes para todos.
- B1 (V13, V23, M11) PASS con Q y con P: V13 igual, V23 opuesto y M11 opuesto en L3 respecto a L1 (384 de 384 pares).
- B2 FAIL con Q: ΣF3 global L1 = 48, L2 = 48, L3 = -48; con `frame: "element"` el valor es 48 en las tres. Con P pasa.
  Causa: `AreaJointForceShell` ya entrega las fuerzas en ejes globales y la conversión del puente las rota de más.
- C1 FAIL: 768 filas iguales por API y por tabla, diferencia máxima 4,97e-4 (parece redondeo de la tabla).
- Z PASS: unidades, bloqueo y selección de salida restaurados.

Nota: el tamaño máximo 0,5 de `AREA AUTO MESH ASSIGNMENTS` no genera 96 elementos en L2, sino 20.


## Actualización
La causa de que la malla automática no salga de 0,5 m está en `NOTA_AREA_AUTO_MESH.md`: el generador usa el valor `MeshType` y los campos `MaxSize1/MaxSize2` equivocados (SAP2000 usa `Maximum Size` y `Max1/Max2` y aplica 1,2192 m por defecto).
