# Sonda de shells: malla automática, signo de V y API/tabla

Modelo: C:\Users\<usuario>\Downloads\Pruebas\sonda_run\SONDA_F0_SHELL.sdb

| Id | Veredicto | Prueba | Nota |
|---|---|---|---|
| A0 | INCONCLUSO | L2 está mallada automáticamente | 96 área(s) con 96 elemento(s) de análisis: un elemento por área, es malla explícita; convierta L2 con Automatic Area Mesh. |
| A1 | PASS | Interpretación por columnas válida en los elementos | 96 de 96 elementos con matriz válida por columnas (det +1, v3·n = ±1). |
| A2 | INCONCLUSO | Nudos internos de la malla automática no son PointObj | No hay nudos '~…': ¿la malla es explícita? |
| A3 | PASS | Los elementos heredan los ejes del objeto (v1 de AreaElm = v1 de AreaObj) | Desviación máxima entre el v1 del elemento y el del objeto: 0.0000°. |
| A4 | PASS | La regla por defecto (+ ángulo del objeto) reproduce los ejes de los elementos | Desviación máxima: 0.0000° (es lo que daría el respaldo del puente si no hubiera matriz). |
| A5 | PASS | El puente devuelve ejes para todos los elementos de L2 | 96 de 96 elementos con ejes en 2.02 s. Avisos de ejes: ninguno. |
| C1 | FAIL | API y tabla dan los mismos valores (caso Q) | 768 filas por API (0.85 s) y 768 por tabla (1.40 s); diferencia máxima 4.974e-04; solo API: 0, solo tabla: 0. |
| B1V13 | PASS | Signo de V13 en L3 respecto a L1 bajo el caso Q (esperado: igual) | 384 pares elemento-esquina: 384 igual, 0 opuesto, 0 nulos; dominante: igual. |
| B1V23 | PASS | Signo de V23 en L3 respecto a L1 bajo el caso Q (esperado: opuesto) | 384 pares elemento-esquina: 0 igual, 384 opuesto, 0 nulos; dominante: opuesto. |
| B1M11 | PASS | Signo de M11 en L3 respecto a L1 bajo el caso Q (esperado: opuesto) | 384 pares elemento-esquina: 0 igual, 384 opuesto, 0 nulos; dominante: opuesto. |
| B2 | FAIL | ΣF3 de AreaJointForceShell en ejes globales coincide en L1, L2 y L3 (caso Q) | ΣF3 global: {'L1': 48.0000000000211, 'L2': 48.00000107100418, 'L3': -48.00000000002712}. No coinciden: la conversión con los ejes del elemento no es la correcta (compare con 'elemento'). |
| B1V13 | PASS | Signo de V13 en L3 respecto a L1 bajo el caso P (esperado: igual) | 384 pares elemento-esquina: 384 igual, 0 opuesto, 0 nulos; dominante: igual. |
| B1V23 | PASS | Signo de V23 en L3 respecto a L1 bajo el caso P (esperado: opuesto) | 384 pares elemento-esquina: 0 igual, 384 opuesto, 0 nulos; dominante: opuesto. |
| B1M11 | PASS | Signo de M11 en L3 respecto a L1 bajo el caso P (esperado: opuesto) | 384 pares elemento-esquina: 0 igual, 384 opuesto, 0 nulos; dominante: opuesto. |
| B2 | PASS | ΣF3 de AreaJointForceShell en ejes globales coincide en L1, L2 y L3 (caso P) | ΣF3 global: {'L1': 5.453071371189222e-12, 'L2': 0.0, 'L3': -7.244355831360905e-12}.  |
| Z | PASS | Unidades, bloqueo y selección de salida restaurados | Iguales a los de antes. |
