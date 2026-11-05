# Sonda de secciones del Viewer

Comprueba en un SAP2000 real las llamadas de la API que usa `GET /v1/sap/actions/viewer/sections/props`
(`bridge/viewer/core/section_props.py`), que hoy solo están probadas con COM falso. Es de **solo lectura**.

```powershell
py -3.12 scripts\sonda_viewer_secciones.py
py -3.12 scripts\sonda_viewer_secciones.py --salida-dir C:\sondas
py -3.12 scripts\sonda_viewer_secciones.py --comparar-unidades
```

Con SAP2000 abierto y un modelo con secciones de barra. Deja `sonda_viewer_secciones_AAAAMMDD_HHMM.json` (informe
completo, con la salida cruda de cada llamada) y `.md` (tabla de veredictos). Devolver el `.json`.

| Prueba | Qué confirma |
|---|---|
| U1 | `GetPresentUnits` se reconoce y da los factores a mm y N |
| S1, S2 | Las firmas de todos los getters (incluidos los tipos que el modelo no tiene) se aceptan: se llaman con un nombre inexistente |
| N1 | `PropFrame.GetNameList` |
| T1, T2 | `GetTypeOAPI` y el getter tipado de cada tipo presente: disposición de las salidas, material y dimensiones |
| T3, T4 | `GetSectProps`: `R22`/`R33` coinciden con raíz(`I`/`A`) y, en I, el eje 3 es el fuerte |
| T5 | El área contra las dimensiones `h/b/tf/tw` (tolerancia 10 %, la convención de esbelteces) |
| M1–M3 | Materiales: tipo, `fy`/`fu` y `E` en MPa con valores plausibles |
| R1 | La ruta completa (`read_section_props`) sobre el modelo |
| C1 | (solo con `--comparar-unidades`) la conversión de la ruta contra SAP2000 en N_mm_C |

Veredictos: **PASS**, **FAIL** (la nota dice qué revisar) e **INCONCLUSO** (el modelo no permite decidirlo, p. ej. no tiene
ángulos dobles). Código de salida 0 sin FAIL, 1 con algún FAIL, 2 si no se pudo conectar.

`--comparar-unidades` es lo único que escribe: cambia las unidades activas a N_mm_C y las restaura siempre en un `finally`.
