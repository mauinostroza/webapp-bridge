# Sonda de combinaciones borradas

`scripts/sonda_combinaciones.py` averigua si alguna acción del puente (o SAP2000 mismo) elimina o modifica
combinaciones. Se ejecuta en el PC del usuario, con SAP2000 abierto, y trabaja **solo sobre una copia** del modelo.

Requiere las dependencias del puente en el entorno de Python:

```powershell
py -3.12 -m pip install -r backend\requirements.txt pywin32 comtypes
```

```powershell
py -3.12 scripts\sonda_combinaciones.py
py -3.12 scripts\sonda_combinaciones.py --solo R --sin-diseno
```

## Garantías

- Pide escribir `SI`, guarda el modelo como `<modelo>_SONDA_<sello>.sdb` y comprueba que el modelo abierto es la copia;
  si no, aborta (código 3). Antes de cada fase vuelve a comprobarlo.
- Al terminar compara el hash del original (`original_intacto`).
- Solo crea y borra combinaciones `ZZ_SONDA_*`.

## Qué hace

Foto de las combinaciones (nombres, casos y factores, selección de salida, bloqueo, unidades) antes y después de
**cada** prueba, en dos fases: con el modelo bloqueado (L) y desbloqueado (U).

- **B (puente):** lecturas (combinaciones, esbelteces, hormigón, equilibrio, acero, espectro), `combinaciones/send`
  (nueva, y mismo nombre) y `delete`, `esbelteces/section/apply` con y sin `desbloquear`.
- **R (API cruda de SAP2000):** desbloquear, analizar, selección de salida, unidades, `RespCombo.Add/Delete`,
  la secuencia «Add existente → Delete → Add» de `combinaciones/send` (R7b), diseño, guardar y reabrir.

Comparar B y R separa un fallo del puente de un comportamiento de SAP2000.

## Informe

`sonda_combinaciones_AAAAMMDD_HHMM.json` y `.md`. Veredictos: `SIN_CAMBIO`, `CAMBIO_ESPERADO`,
`CAMBIO_INESPERADO` (se eliminó o modificó una combinación del modelo), `ERROR`, `OMITIDO`. Código de salida: 0 sin
cambios inesperados, 1 con alguno, 2 sin conexión, 3 rechazada por una guarda. **Devolver el `.json`.**

## Sospecha principal

`send_combinations_to_sap2000` hace `Add` → si falla `Delete` + `Add` para un nombre existente, sin comprobar el
resultado de `Delete`. Si el segundo `Add` falla (p. ej. modelo bloqueado) la combinación queda eliminada. Lo reproducen
`combinaciones/send (mismo nombre)` y R7b.

## Reproducir el síntoma con el código anterior al arreglo

`--raiz` prueba el código del puente de otra copia del repo. Para comparar con la versión anterior a la restauración de
la selección de salida (commit `bbb1447`):

```powershell
git worktree add ..\bridge_anterior bbb1447
py -3.12 scripts\sonda_combinaciones.py --raiz ..\bridge_anterior
```

Con el código anterior, las lecturas deberían aparecer como `CAMBIO_ESPERADO` con `seleccion_cambiada` (la selección de
salida cambia); con el actual, `SIN_CAMBIO`. Limpiar después con `git worktree remove ..\bridge_anterior`.

## Rutas ampliadas

Además de las del primer informe, prueba: viewer (`connect`, lecturas, `k/preview` y `k/apply` con `unlock`),
cortes de fundaciones (`preview` y `apply` con `desbloquear`), `esbelteces/k-factors/apply` y `profiles/section/apply`.
