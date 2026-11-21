# Sonda corta del espesor de las áreas

`scripts/sonda_espesor_areas.py` confirma en SAP2000 real la lectura de `thickness`, `thickness_bending` y `sections` de
`viewer/foundations/shell-forces` y `shell-joint-forces` (`bridge/viewer/shell_forces.py::espesores`). Solo lee; no hace falta que el
modelo esté analizado (la prueba E6 sí lo necesita y queda INCONCLUSA si no).

```powershell
py -3.12 -m pip install -r backend\requirements.txt pywin32 comtypes
py -3.12 scripts\sonda_espesor_areas.py --esperado 0.4
```

`--esperado` es el espesor conocido (m) de la primera sección que lea (opcional; para la losa de la sonda de shells son 0,40 m). `--max-areas`
(20 por defecto) y `--salida-dir`.

| Id | Qué comprueba |
|---|---|
| E1 | `AreaObj.GetProperty` devuelve el nombre de la sección (anote la forma cruda) |
| E2 | Salidas crudas de `PropArea.GetShell_1` y `GetShell`, con la firma corta y la larga (informativo: anote cuál responde) |
| E3 | Lo que lee el puente (por tipo) coincide con la posición documentada (`ShellType, IncludeDrillingDOF, MatProp, MatAngle, Thickness, Bending, …`) |
| E4 | El espesor sigue las unidades presentes: cociente 1000 entre N_mm_C y Ton_m_C, es decir, la ruta lo entrega en metros |
| E5 | Con `--esperado`: el espesor leído es el esperado |
| E6 | `shell-forces` devuelve `thickness` y `sections` (y avisos de espesor, si los hay) |
| Z | Las unidades quedan como estaban |

Devuelva el `.json` y anote: la firma de `GetShell_1` que respondió (E2), el veredicto de E3 y E4 y, si hay avisos de espesor en E6, cuáles.
Si E3 es FAIL, las salidas de `GetShell_1` no siguen el orden supuesto y hay que ajustar `_leer_espesor_seccion`.

## Resultado de la primera ejecución («FUNDACIONES STACKER v00.sdb», SAP2000)

E1, E3, E4 y Z: PASS; E2 informativa: `GetShell_1` responde con la firma corta (y la larga), `GetShell` solo con la corta; E6 inconclusa (modelo sin analizar).
E5 salió FAIL porque se pasó `--esperado 0.4` para un modelo cuya sección es «Losa e=3000» (3,0 m, que es lo que lee el puente): era un valor
esperado equivocado, no un fallo de la lectura. Pendiente: repetir con el modelo analizado (E6) y, si hay, con una sección layered.
