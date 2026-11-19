#!/usr/bin/env python3
"""Sonda «¿se borran combinaciones?»: foto de las combinaciones antes y después de cada prueba.

Trabaja SIEMPRE sobre una COPIA del modelo abierto (guardar como ``<copia-dir>\\<modelo>_SONDA_<sello>.sdb``); nunca
escribe en el archivo original, y al final comprueba con un hash que sigue intacto. Distingue los fallos del puente de
los de SAP2000: además de llamar a las rutas del puente, ejecuta pruebas de control con la API cruda (desbloquear,
analizar, cambiar la selección de salida, ``RespCombo.Add/Delete``, diseño...).

Requisitos: el entorno de Python debe tener las dependencias del puente
(``py -3.12 -m pip install -r backend\\requirements.txt pywin32 comtypes``).

Uso (PowerShell, Windows, SAP2000 abierto con el modelo problemático, idealmente con análisis corrido y bloqueado):

    py -3.12 scripts\\sonda_combinaciones.py
    py -3.12 scripts\\sonda_combinaciones.py --solo R          # solo controles con la API cruda
    py -3.12 scripts\\sonda_combinaciones.py --sin-diseno      # sin las pruebas de diseño
    py -3.12 scripts\\sonda_combinaciones.py --si              # sin pedir la confirmación escrita

Seguridad: pide escribir SI antes de guardar la copia; aborta (código 3) si tras guardar como el modelo abierto no es la
copia; las combinaciones que crea se llaman ``ZZ_SONDA_*`` y solo esas se borran, salvo la prueba R7b que reproduce
a propósito el «Add existente → Delete → Add» de ``combinaciones/send`` sobre un clon ``ZZ_SONDA_CLON``.

Salida: sonda_combinaciones_AAAAMMDD_HHMM.json (informe completo) y .md (tabla de veredictos). Devuélveme el .json.
Veredictos: SIN_CAMBIO, CAMBIO_ESPERADO, CAMBIO_INESPERADO (se eliminó o modificó una combinación que no debía),
ERROR (la llamada falló; no es un fallo de la sonda), OMITIDO (el modelo no tiene lo necesario).
Código de salida: 0 sin cambios inesperados, 1 con alguno, 2 sin conexión, 3 rechazada por una guarda de seguridad.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[1]
if (
    "--raiz" in sys.argv[:-1]
):  # otra copia del repo con el código del puente a probar (p. ej. un commit anterior)
    RAIZ = Path(sys.argv[sys.argv.index("--raiz") + 1]).resolve()
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

SIN_CAMBIO, ESPERADO, INESPERADO, ERROR, OMITIDO = (
    "SIN_CAMBIO",
    "CAMBIO_ESPERADO",
    "CAMBIO_INESPERADO",
    "ERROR",
    "OMITIDO",
)
PREFIJO = "ZZ_SONDA_"
LIMITE_CRUDO = 300
COMBO_AUTO = re.compile(r"^(DSTL|DCON|DWAL|DALUM|DCFS)\d+")


def _recortar(valor: Any) -> str:
    texto = repr(valor)
    return texto if len(texto) <= LIMITE_CRUDO else texto[:LIMITE_CRUDO] + "…"


def _causa(exc: BaseException) -> str:
    """Excepción hoja de un ``ExceptionGroup`` con el archivo y la línea donde se produjo."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    tb = exc.__traceback__
    ultimo = None
    while tb is not None:
        ultimo = tb
        tb = tb.tb_next
    donde = f" en {Path(ultimo.tb_frame.f_code.co_filename).name}:{ultimo.tb_lineno}" if ultimo else ""
    return f"{type(exc).__name__}: {exc}{donde}"


def _sha_archivo(ruta: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with ruta.open("rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        return h.hexdigest()
    except OSError:
        return None


def _ret(valor: Any) -> Any:
    """Código de retorno de una llamada COM (primer o último entero de la tupla)."""
    if isinstance(valor, (tuple, list)):
        enteros = [v for v in valor if isinstance(v, int) and not isinstance(v, bool)]
        return enteros[-1] if enteros else None
    return valor


# --------------------------------------------------------------------------------------------------------------------
# Foto y diferencias
# --------------------------------------------------------------------------------------------------------------------


def _tuple(valor: Any) -> tuple:
    return tuple(valor) if isinstance(valor, (tuple, list)) else (valor,)


def _nombres(res: Any) -> list[str]:
    """Nombres de un ``GetNameList`` (devuelve ``(n, nombres, ret)`` o ``(ret, n, nombres)``)."""
    for v in _tuple(res):
        if isinstance(v, (tuple, list)) and all(isinstance(x, str) for x in v):
            return list(v)
    return []


def _casos_de_combo(model: Any, nombre: str) -> list[Any]:
    for args in ((nombre,), (nombre, 0, [], [], []), (nombre, 0, (), (), ())):
        try:
            res = _tuple(model.RespCombo.GetCaseList(*args))
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            return [f"error: {exc}"]
        return [_recortar(v) for v in res]
    return ["error: firma"]


def _bool_de(res: Any) -> bool | None:
    for v in _tuple(res):
        if isinstance(v, bool):
            return v
    return None


def foto(model: Any) -> dict[str, Any]:
    """Estado observable de las combinaciones (y de lo que podría alterarlas). No modifica nada."""

    def seguro(f: Callable[[], Any]) -> Any:
        try:
            return f()
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    combos: dict[str, Any] = {}
    for nombre in _nombres(model.RespCombo.GetNameList()):
        combos[nombre] = {
            "casos": _casos_de_combo(model, nombre),
            "sel_salida": seguro(lambda n=nombre: _bool_de(model.Results.Setup.GetComboSelectedForOutput(n))),
            "auto_diseno": bool(COMBO_AUTO.match(nombre)),
        }
    casos = _nombres(model.LoadCases.GetNameList())
    return {
        "archivo": seguro(model.GetModelFilename),
        "bloqueado": seguro(model.GetModelIsLocked),
        "unidades": seguro(model.GetPresentUnits),
        "n_combos": len(combos),
        "combos": combos,
        "casos": {
            c: seguro(lambda n=c: _bool_de(model.Results.Setup.GetCaseSelectedForOutput(n))) for c in casos
        },
        "codigo_acero": seguro(lambda: _recortar(model.DesignSteel.GetCode(""))),
    }


def _sha_combos(f: dict[str, Any]) -> str:
    texto = json.dumps(
        {n: [c["casos"], c["auto_diseno"]] for n, c in f["combos"].items()}, sort_keys=True, default=str
    )
    return hashlib.sha256(texto.encode()).hexdigest()[:12]


def diferencias(antes: dict[str, Any], despues: dict[str, Any]) -> dict[str, Any]:
    a, b = antes["combos"], despues["combos"]
    modificadas = [n for n in a if n in b and a[n]["casos"] != b[n]["casos"]]
    sel = [n for n in a if n in b and a[n]["sel_salida"] != b[n]["sel_salida"]]
    return {
        "eliminadas": sorted(set(a) - set(b)),
        "agregadas": sorted(set(b) - set(a)),
        "modificadas": sorted(modificadas),
        "seleccion_cambiada": sorted(sel),
        "bloqueo": [antes["bloqueado"], despues["bloqueado"]],
        "archivo": [antes["archivo"], despues["archivo"]],
    }


def veredicto(dif: dict[str, Any], espera: dict[str, Any]) -> tuple[str, str]:
    """``espera``: ``agregadas`` (nombres o prefijos) permitidas y ``bloqueo`` (True si se espera cambiar el bloqueo)."""
    eliminadas = dif["eliminadas"]
    # Una combinación que NO es de la sonda desaparecida o modificada nunca es esperada; una ZZ_SONDA sí puede serlo.
    permitidas = set(espera.get("eliminadas", ()))
    malas = [n for n in eliminadas if n not in permitidas and not n.startswith(PREFIJO)]
    malas += [n for n in dif["modificadas"] if not n.startswith(PREFIJO)]
    if malas:
        return INESPERADO, f"combinaciones del modelo eliminadas o modificadas: {malas}"
    inesperadas_zz = [
        n for n in eliminadas + dif["modificadas"] if n.startswith(PREFIJO) and n not in permitidas
    ]
    cambios = bool(
        dif["eliminadas"]
        or dif["agregadas"]
        or dif["modificadas"]
        or dif["seleccion_cambiada"]
        or dif["bloqueo"][0] != dif["bloqueo"][1]
    )
    if inesperadas_zz and not espera.get("zz_ok"):
        return (
            INESPERADO,
            f"combinaciones de la sonda eliminadas o modificadas sin esperarlo: {inesperadas_zz}",
        )
    if not cambios:
        return SIN_CAMBIO, ""
    return ESPERADO, ""


# --------------------------------------------------------------------------------------------------------------------
# Informe
# --------------------------------------------------------------------------------------------------------------------


class Informe:
    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.pruebas: list[dict[str, Any]] = []

    def agregar(self, **kw: Any) -> None:
        self.pruebas.append(kw)
        marca = {SIN_CAMBIO: "  ", ESPERADO: "~ ", INESPERADO: "!!", ERROR: "E ", OMITIDO: "- "}[
            kw["veredicto"]
        ]
        print(f"{marca} {kw['fase']}/{kw['id']:<28} {kw['veredicto']:<18} {kw.get('nota', '')[:90]}")

    def resumen(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.pruebas:
            out[p["veredicto"]] = out.get(p["veredicto"], 0) + 1
        return out

    def a_markdown(self) -> str:
        res = self.resumen()
        lineas = [
            "# Sonda de combinaciones",
            "",
            f"Resumen: {res}",
            f"Original intacto: {self.meta.get('original_intacto')}",
            "",
            "| Prueba | Fase | Veredicto | Eliminadas | Modificadas | Bloqueo | Nota |",
            "|---|---|---|---|---|---|---|",
        ]
        for p in self.pruebas:
            d = p.get("dif") or {}
            lineas.append(
                f"| {p['id']} | {p['fase']} | {p['veredicto']} | {d.get('eliminadas', '')} | "
                f"{d.get('modificadas', '')} | {d.get('bloqueo', '')} | {p.get('nota', '')} |"
            )
        malas = [p for p in self.pruebas if p["veredicto"] == INESPERADO]
        lineas += ["", "## Conclusión", ""]
        if not malas:
            lineas.append("Ninguna prueba eliminó ni modificó combinaciones del modelo.")
        for p in malas:
            lineas.append(f"- **{p['id']}** ({p['fase']}): {p['nota']}")
        return "\n".join(lineas) + "\n"


# --------------------------------------------------------------------------------------------------------------------
# Contexto de la sonda
# --------------------------------------------------------------------------------------------------------------------


class Sonda:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.informe = Informe()
        os.environ.setdefault("SAP2000_AGENT_TOKEN", secrets.token_hex(16))
        from fastapi.testclient import TestClient

        from bridge import sap2000_agent

        self.agente = sap2000_agent
        self.sesion = sap2000_agent.session
        self.cliente = TestClient(
            sap2000_agent.app,
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": f"Bearer {sap2000_agent.TOKEN}"},
        )
        self.copia: Path | None = None
        self.original: Path | None = None
        self.contexto: dict[str, Any] = {}

    # Todo acceso crudo al modelo pasa por el actor STA del agente (mismo apartamento COM que las rutas).
    def crudo(self, f: Callable[[Any], Any]) -> Any:
        return self.sesion.actor.call(lambda: f(self.sesion._model()))

    def foto(self) -> dict[str, Any]:
        return self.crudo(foto)

    def ejecutar(
        self, fase: str, id_: str, llamada: Callable[[], Any], espera: dict[str, Any] | None = None
    ) -> None:
        espera = espera or {}
        antes = self.foto()
        respuesta, error = None, None
        try:
            respuesta = llamada()
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        despues = self.foto()
        dif = diferencias(antes, despues)
        v, n = veredicto(dif, espera)
        if error and v in (SIN_CAMBIO, ESPERADO):
            v, n = ERROR, error
        elif error:
            n = f"{n} (la llamada además falló: {error})"
        if isinstance(respuesta, tuple) and len(respuesta) == 2 and isinstance(respuesta[0], int):
            if respuesta[0] >= 400 and v in (SIN_CAMBIO, ESPERADO):
                v, n = ERROR, f"HTTP {respuesta[0]}: {_recortar(respuesta[1])}"
            elif not n:
                n = f"HTTP {respuesta[0]}"
        self.informe.agregar(
            fase=fase,
            id=id_,
            veredicto=v,
            nota=n,
            dif=dif,
            antes_sha=_sha_combos(antes),
            despues_sha=_sha_combos(despues),
            respuesta=_recortar(respuesta),
        )

    def http(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None) -> tuple[int, Any]:
        """Llama a una ruta. Una excepción no controlada de la ruta se devuelve como 500 con su causa (no aborta la sonda)."""
        try:
            if cuerpo is not None:
                r = self.cliente.request(metodo, ruta, json=cuerpo)
            else:
                r = self.cliente.request(metodo, ruta)
        except BaseException as exc:  # noqa: BLE001 - incluye ExceptionGroup de Starlette
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return 500, "excepción no controlada: " + _causa(exc)
        try:
            datos = r.json()
        except ValueError:
            datos = r.text
        return r.status_code, datos

    # ---- Guardas de seguridad ----------------------------------------------------------------------------------

    def preparar_copia(self) -> None:
        a = self.args
        self.http("POST", "/v1/sap/attach", {})
        nombre = self.crudo(lambda m: m.GetModelFilename())
        if not nombre:
            if not a.modelo_borrador:
                raise Rechazo(
                    "El modelo abierto no tiene archivo guardado. Guárdelo o use --modelo-borrador."
                )
            self.original = None
        else:
            self.original = Path(nombre)
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        carpeta = Path(a.copia_dir) if a.copia_dir else Path(tempfile.gettempdir()) / f"sonda_combos_{sello}"
        carpeta.mkdir(parents=True, exist_ok=True)
        stem = self.original.stem if self.original else "modelo"
        self.copia = carpeta / f"{stem}_SONDA_{sello}.sdb"
        sha = _sha_archivo(self.original) if self.original else None
        self.informe.meta.update(
            original=str(self.original) if self.original else None,
            original_sha_antes=sha,
            copia=str(self.copia),
        )
        print(f"Modelo original : {self.original}")
        print(f"Copia de trabajo: {self.copia}")
        if (
            not a.si
            and input("Se guardará una copia y la sonda trabajará SOLO sobre ella. Escriba SI: ").strip()
            != "SI"
        ):
            raise Rechazo("No se confirmó.")
        ret = self.crudo(lambda m: _ret(m.File.Save(str(self.copia))))
        actual = self.crudo(lambda m: m.GetModelFilename())
        if ret not in (0, None) or not actual or Path(actual).resolve() != self.copia.resolve():
            raise Rechazo(
                f"Tras guardar como la copia, el modelo abierto es {actual!r}, no la copia. Se aborta."
            )
        if self.original and Path(actual).resolve() == self.original.resolve():
            raise Rechazo("El modelo abierto sigue siendo el original. Se aborta.")

    def asegurar_copia(self) -> None:
        actual = self.crudo(lambda m: m.GetModelFilename())
        if not actual or Path(actual).resolve() != self.copia.resolve():
            raise Rechazo(f"El modelo abierto dejó de ser la copia ({actual!r}). Se aborta.")

    def cerrar(self) -> None:
        if self.original:
            despues = _sha_archivo(self.original)
            self.informe.meta["original_sha_despues"] = despues
            self.informe.meta["original_intacto"] = despues == self.informe.meta.get("original_sha_antes")
        else:
            self.informe.meta["original_intacto"] = None

    # ---- Datos del modelo para armar las llamadas --------------------------------------------------------------

    def descubrir(self) -> None:
        f = self.foto()
        c = self.contexto
        c["combos"] = [n for n in f["combos"] if not n.startswith(PREFIJO)]
        c["combo"] = c["combos"][0] if c["combos"] else None
        c["casos"] = list(f["casos"])
        c["caso"] = c["casos"][0] if c["casos"] else None
        c["marco"] = self.crudo(_primer_marco)
        c["grupo"] = self.crudo(_primer_grupo)
        c["seccion"] = self.crudo(_primera_seccion)
        self.informe.meta["contexto"] = {k: v for k, v in c.items() if k not in ("combos", "casos")}
        self.informe.meta["n_combos_inicial"] = f["n_combos"]
        self.informe.meta["bloqueado_inicial"] = f["bloqueado"]


class Rechazo(Exception):
    """Una guarda de seguridad impide continuar."""


def _primer_marco(model: Any) -> str | None:
    try:
        nombres = _nombres(model.FrameObj.GetNameList())
        return nombres[0] if nombres else None
    except Exception:  # noqa: BLE001
        return None


def _primer_grupo(model: Any) -> str | None:
    try:
        nombres = [n for n in _nombres(model.GroupDef.GetNameList()) if n != "ALL"]
        return nombres[0] if nombres else None
    except Exception:  # noqa: BLE001
        return None


def _primera_seccion(model: Any) -> str | None:
    try:
        nombres = _nombres(model.PropFrame.GetNameList())
        return nombres[0] if nombres else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------------------------------------------------
# Pruebas del puente (B)
# --------------------------------------------------------------------------------------------------------------------

P = "/v1/sap/actions"


def _matriz(nombre: str, caso: str | None, factor: str = "1.0") -> dict[str, Any]:
    return {"headers": [], "combinations": [{"name": nombre, "factors_by_case": {caso or "DEAD": factor}}]}


def pruebas_puente(s: Sonda, fase: str) -> None:
    c = s.contexto
    ej = s.ejecutar
    # Lecturas
    for ruta in (
        f"{P}/combinaciones/snapshot",
        f"{P}/combinaciones/existing",
        f"{P}/esbelteces/selected-raw",
        f"{P}/esbelteces/groups",
        f"{P}/esbelteces/sections",
        f"{P}/hormigon/frames",
        f"{P}/hormigon/combos",
        f"{P}/hormigon/selected-frames",
        f"{P}/equilibrio/combos",
        f"{P}/equilibrio/groups",
        f"{P}/acero-aisc360/combos",
        f"{P}/acero-aisc360/selected",
        f"{P}/espectro/functions",
        f"{P}/espectro/load-cases",
        f"{P}/espectro/spectra",
        "/v1/sap/model",
    ):
        ej(fase, "GET " + ruta.removeprefix(P).removeprefix("/v1/sap"), lambda r=ruta: s.http("GET", r))
    if c["combo"] and c["marco"]:
        ej(
            fase,
            "hormigon/frame-forces",
            lambda: s.http(
                "POST", f"{P}/hormigon/frame-forces", {"frame": c["marco"], "combos": [c["combo"]]}
            ),
        )
        ej(
            fase,
            "equilibrio/frame-data",
            lambda: s.http(
                "POST", f"{P}/equilibrio/frame-data", {"frame": c["marco"], "combo_sismo": c["combo"]}
            ),
        )
        ej(
            fase,
            "acero/fuerzas",
            lambda: s.http(
                "POST", f"{P}/acero-aisc360/fuerzas", {"frames": [c["marco"]], "combos": [c["combo"]]}
            ),
        )
    else:
        ej(fase, "lecturas con combo y marco", lambda: None, {})
        s.informe.pruebas[-1].update(veredicto=OMITIDO, nota="el modelo no tiene combinaciones o marcos")
    if c["grupo"] and c["combo"]:
        ej(
            fase,
            "equilibrio/group-demands",
            lambda: s.http(
                "POST", f"{P}/equilibrio/group-demands", {"group": c["grupo"], "combo_sismo": c["combo"]}
            ),
        )
    if c["casos"]:
        ej(
            fase,
            "espectro/ps",
            lambda: s.http("POST", f"{P}/espectro/ps", {"rows": [{"case": c["caso"], "factor": 1.0}]}),
        )
    if c["marco"]:
        ej(
            fase,
            "esbelteces/highlight",
            lambda: s.http("POST", f"{P}/esbelteces/highlight", {"frames": [c["marco"]]}),
        )
    # Escrituras: combinaciones propias
    zz, clon = PREFIJO + "A", PREFIJO + "CLON"
    nuevo_zz = {"agregadas": {zz}, "zz_ok": True}
    ej(
        fase,
        "combinaciones/send (nueva)",
        lambda: s.http(
            "POST", f"{P}/combinaciones/send", {"matrix": _matriz(zz, c["caso"]), "confirmed": True}
        ),
        nuevo_zz,
    )
    ej(
        fase,
        "combinaciones/send (clon nuevo)",
        lambda: s.http(
            "POST", f"{P}/combinaciones/send", {"matrix": _matriz(clon, c["caso"]), "confirmed": True}
        ),
        {"zz_ok": True},
    )
    ej(
        fase,
        "combinaciones/send (mismo nombre)",
        lambda: s.http(
            "POST", f"{P}/combinaciones/send", {"matrix": _matriz(clon, c["caso"], "2.0"), "confirmed": True}
        ),
        {"zz_ok": True},
    )
    ej(
        fase,
        "combinaciones/delete (solo ZZ)",
        lambda: s.http("POST", f"{P}/combinaciones/delete", {"names": [zz, clon], "confirmed": True}),
        {"eliminadas": {zz, clon}, "zz_ok": True},
    )
    # Escrituras que desbloquean
    if c["marco"] and c["seccion"]:
        ej(
            fase,
            "esbelteces/section/apply (sin desbloquear)",
            lambda: s.http(
                "POST",
                f"{P}/esbelteces/section/apply",
                {"frame": c["marco"], "section": c["seccion"], "confirmed": True},
            ),
            {"zz_ok": True},
        )
        ej(
            fase,
            "esbelteces/section/apply (desbloquear)",
            lambda: s.http(
                "POST",
                f"{P}/esbelteces/section/apply",
                {"frame": c["marco"], "section": c["seccion"], "confirmed": True, "desbloquear": True},
            ),
        )


def pruebas_puente_ampliadas(s: Sonda, fase: str) -> None:
    """Rutas que desbloquean el modelo, editan tablas o guardan: viewer (k), cortes de fundaciones, perfiles, K de esbelteces."""
    c = s.contexto
    ej = s.ejecutar
    V = f"{P}/viewer"
    ej(fase, "viewer/connect", lambda: s.http("POST", f"{V}/connect", {"mode": "sap2000"}))
    for ruta in (
        "status",
        "results/cases",
        "results/design",
        "results/reactions",
        "results/frames",
        "k",
        "k/design",
        "k/effective",
        "k/diagnose",
        "sections/props",
    ):
        ej(fase, f"GET viewer/{ruta}", lambda r=ruta: s.http("GET", f"{V}/{r}"))
    if c["caso"]:
        q = f"?case={c['caso']}"
        ej(fase, "GET viewer/results/reactions", lambda: s.http("GET", f"{V}/results/reactions{q}"))
        ej(fase, "GET viewer/results/frames", lambda: s.http("GET", f"{V}/results/frames{q}"))
        if c["marco"]:
            ej(fase, "GET viewer/results/frame", lambda: s.http("GET", f"{V}/results/frame/{c['marco']}{q}"))
    ej(fase, "GET viewer/snapshot", lambda: s.http("GET", f"{V}/snapshot"))
    ej(fase, "GET foundations/section-cuts", lambda: s.http("GET", f"{P}/foundations/section-cuts"))
    ej(
        fase,
        "GET foundations/section-cuts/groups",
        lambda: s.http("GET", f"{P}/foundations/section-cuts/groups"),
    )
    ej(fase, "GET foundations/cases", lambda: s.http("GET", f"{P}/foundations/cases"))
    ej(fase, "GET placa-base/cargas", lambda: s.http("GET", f"{P}/placa-base/cargas"))
    ej(fase, "GET zapata-biaxial/estado", lambda: s.http("GET", f"{P}/zapata-biaxial/estado"))
    ej(fase, "GET profiles/catalog", lambda: s.http("GET", f"{P}/profiles/catalog"))
    ej(fase, "GET profiles/selected-frames", lambda: s.http("GET", f"{P}/profiles/selected-frames"))
    # Escrituras (sobre la copia). K del viewer: vista previa de un cambio nulo y aplicación con desbloqueo permitido.
    estado, datos = s.http("GET", f"{V}/k")
    cambio = _primer_cambio_k(datos) if estado == 200 else None
    if cambio:
        marco, campo, valor = cambio
        holder: dict[str, Any] = {}

        def previa() -> Any:
            r = s.http("POST", f"{V}/k/preview", {"frame": marco, "changes": {campo: valor}})
            if isinstance(r[1], dict):
                holder["nonce"] = r[1].get("nonce")
            return r

        ej(fase, "viewer/k/preview", previa)
        if holder.get("nonce"):
            ej(
                fase,
                "viewer/k/apply (unlock)",
                lambda: s.http("POST", f"{V}/k/apply", {"nonce": holder["nonce"], "unlock": True}),
            )
    else:
        s.ejecutar(fase, "viewer/k/apply", lambda: None)
        s.informe.pruebas[-1].update(veredicto=OMITIDO, nota="el modelo no tiene overwrites de K de acero")
    corte = PREFIJO + "CORTE"
    if c["marco"]:
        cuerpo = {"nombre": corte, "frames": [c["marco"]], "desbloquear": True, "confirmed": True}
        ej(
            fase,
            "foundations/section-cuts/preview",
            lambda: s.http("POST", f"{P}/foundations/section-cuts/preview", cuerpo),
        )
        ej(
            fase,
            "foundations/section-cuts/apply (desbloquear)",
            lambda: s.http("POST", f"{P}/foundations/section-cuts/apply", cuerpo),
        )
        ej(
            fase,
            "esbelteces/k-factors/apply",
            lambda: s.http(
                "POST",
                f"{P}/esbelteces/k-factors/apply",
                {"items": [{"frame": c["marco"], "kx": 1.0, "ky": 1.0}], "confirmed": True},
            ),
        )
    if c["marco"] and c["seccion"]:
        ej(
            fase,
            "profiles/section/apply",
            lambda: s.http(
                "POST",
                f"{P}/profiles/section/apply",
                {"frames": [c["marco"]], "section": c["seccion"], "confirmed": True},
            ),
        )


def _primer_cambio_k(datos: Any) -> tuple[str, str, float] | None:
    """(barra, campo, valor actual) de la primera barra con un K numérico, para un cambio nulo."""
    marcos = datos.get("frames") if isinstance(datos, dict) else None
    if isinstance(marcos, dict):
        for marco, campos in marcos.items():
            if isinstance(campos, dict):
                for campo, valor in campos.items():
                    v = valor.get("value") if isinstance(valor, dict) else valor
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        return str(marco), str(campo), float(v)
    return None


# --------------------------------------------------------------------------------------------------------------------
# Controles con la API cruda (R): ¿es el puente o es SAP2000?
# --------------------------------------------------------------------------------------------------------------------


def pruebas_crudas(s: Sonda, fase: str, con_diseno: bool) -> None:
    c = s.contexto
    ej = s.ejecutar
    zz, clon = PREFIJO + "R", PREFIJO + "RCLON"
    caso = c["caso"]

    def cruda(f: Callable[[Any], Any]) -> Callable[[], Any]:
        return lambda: s.crudo(f)

    ej(fase, "R0 foto sin efectos", lambda: s.foto())
    ej(fase, "R1 SetModelIsLocked(False)", cruda(lambda m: m.SetModelIsLocked(False)), {"bloqueo": True})
    ej(fase, "R3 selección de salida", cruda(_sel_salida), {"zz_ok": True})
    ej(fase, "R4 cambiar unidades y volver", cruda(_unidades))
    ej(fase, "R6 RespCombo.Add", cruda(lambda m: m.RespCombo.Add(zz, 0)), {"agregadas": {zz}, "zz_ok": True})
    ej(
        fase,
        "R6 RespCombo.SetCaseList",
        cruda(lambda m: m.RespCombo.SetCaseList(zz, 0, caso, 1.0) if caso else None),
        {"zz_ok": True},
    )
    ej(
        fase,
        "R6 Add del clon",
        cruda(
            lambda m: (
                m.RespCombo.Add(clon, 0),
                m.RespCombo.SetCaseList(clon, 0, caso, 1.0) if caso else None,
            )
        ),
        {"zz_ok": True},
    )
    ej(
        fase,
        "R7 Delete del clon",
        cruda(lambda m: m.RespCombo.Delete(clon)),
        {"eliminadas": {clon}, "zz_ok": True},
    )
    ej(fase, "R7b Add existente→Delete→Add", cruda(lambda m: _add_delete_add(m, zz, caso)), {"zz_ok": True})
    ej(
        fase,
        "R14 RefreshView y ClearSelection",
        cruda(lambda m: (m.View.RefreshView(0, False), m.SelectObj.ClearSelection())),
    )
    ej(fase, "R13 LoadPatterns.Add/ChangeName/Delete", cruda(lambda m: _patron(m, zz)), {"zz_ok": True})
    ej(
        fase,
        "limpieza ZZ",
        cruda(lambda m: [m.RespCombo.Delete(n) for n in (zz, clon, PREFIJO + "RC")]),
        {"zz_ok": True, "eliminadas": {zz, clon}},
    )
    if con_diseno:
        ej(fase, "R8 DesignSteel.StartDesign", cruda(lambda m: m.DesignSteel.StartDesign()), {"zz_ok": True})
        ej(
            fase,
            "R8 DesignConcrete.StartDesign",
            cruda(lambda m: m.DesignConcrete.StartDesign()),
            {"zz_ok": True},
        )
        ej(fase, "R9 SetCode otro y restaurar", cruda(_cambiar_codigo), {"zz_ok": True})
    ej(
        fase,
        "R2 Analyze.RunAnalysis",
        cruda(lambda m: m.Analyze.RunAnalysis()),
        {"bloqueo": True, "zz_ok": True},
    )
    ej(fase, "R5 Save + reabrir copia", cruda(lambda m: _guardar_y_reabrir(m, s.copia)), {"zz_ok": True})


def _add_delete_add(model: Any, nombre: str, caso: str | None) -> list[Any]:
    """La secuencia exacta de ``send_combinations_to_sap2000`` para un nombre que ya existe."""
    pasos = [("Add", _ret(model.RespCombo.Add(nombre, 0)))]
    pasos.append(("Delete", _ret(model.RespCombo.Delete(nombre))))
    pasos.append(("Add", _ret(model.RespCombo.Add(nombre, 0))))
    if caso:
        pasos.append(("SetCaseList", _ret(model.RespCombo.SetCaseList(nombre, 0, caso, 2.0))))
    return pasos


def _sel_salida(model: Any) -> None:
    setup = model.Results.Setup
    setup.DeselectAllCasesAndCombosForOutput()
    for n in _nombres(model.RespCombo.GetNameList())[:1]:
        setup.SetComboSelectedForOutput(n, True)


def _unidades(model: Any) -> None:
    actual = model.GetPresentUnits()
    try:
        model.SetPresentUnits(9)
    finally:
        model.SetPresentUnits(actual)


def _patron(model: Any, base: str) -> list[Any]:
    lp = PREFIJO + "LP"
    pasos = [("Add", _ret(model.LoadPatterns.Add(lp, 8, 0, True)))]
    pasos.append(("Delete", _ret(model.LoadPatterns.Delete(lp))))
    return pasos


def _cambiar_codigo(model: Any) -> list[Any]:
    codigo = model.DesignSteel.GetCode("")
    return [("code", _recortar(codigo))]  # solo lectura: cambiar el código no es reversible con seguridad


def _guardar_y_reabrir(model: Any, copia: Path | None) -> list[Any]:
    pasos = [("Save", _ret(model.File.Save()))]
    if copia:
        pasos.append(("Open", _ret(model.File.OpenFile(str(copia)))))
    return pasos


# --------------------------------------------------------------------------------------------------------------------


def _relock(s: Sonda, fase: str) -> None:
    """En la fase L se vuelve a analizar (re-bloquear) por si una prueba anterior desbloqueó el modelo."""
    if fase == "L":
        s.ejecutar(
            fase,
            "re-bloquear: RunAnalysis",
            lambda: s.crudo(lambda m: m.Analyze.RunAnalysis()),
            {"bloqueo": True},
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida-dir", default=".")
    ap.add_argument("--copia-dir")
    ap.add_argument("--modelo-borrador", action="store_true", help="el modelo abierto ya es de prueba")
    ap.add_argument("--si", action="store_true", help="no pedir la confirmación escrita")
    ap.add_argument("--sin-diseno", action="store_true")
    ap.add_argument(
        "--raiz",
        help="raíz de otra copia del repo cuyo código del puente se prueba (p. ej. un commit anterior)",
    )
    ap.add_argument(
        "--solo", choices=("B", "R"), help="solo pruebas del puente (B) o solo controles crudos (R)"
    )
    args = ap.parse_args(argv)

    try:
        s = Sonda(args)
        s.preparar_copia()
    except Rechazo as exc:
        print(f"RECHAZADO: {exc}")
        return 3
    except ImportError as exc:
        print(
            f"Faltan dependencias del puente ({exc}). Instálelas en este entorno y vuelva a ejecutar:\n"
            "    py -3.12 -m pip install -r backend\\requirements.txt pywin32 comtypes"
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo conectar con SAP2000: {exc}")
        return 2
    try:
        s.descubrir()
        s.informe.meta["sap2000"] = s.crudo(lambda m: _recortar(m.GetVersion()))
        # Fase L: con resultados (el modelo de la copia tal como se guardó; si no está bloqueado se analiza antes).
        if not s.informe.meta["bloqueado_inicial"]:
            s.ejecutar(
                "L",
                "preparar: RunAnalysis",
                lambda: s.crudo(lambda m: m.Analyze.RunAnalysis()),
                {"bloqueo": True},
            )
        for fase in ("L", "U"):
            s.asegurar_copia()
            if fase == "U":
                s.crudo(lambda m: m.SetModelIsLocked(False))
            if args.solo != "R":
                pruebas_puente(s, fase)
            s.asegurar_copia()
            if args.solo != "R":
                _relock(s, fase)
                pruebas_puente_ampliadas(s, fase)
                s.asegurar_copia()
            if fase == "L" and args.solo != "B":
                # section/apply(desbloquear) pudo desbloquear: se vuelve a analizar para que los controles crudos
                # también se ejecuten con el modelo bloqueado.
                s.ejecutar(
                    "L",
                    "re-bloquear: RunAnalysis",
                    lambda: s.crudo(lambda m: m.Analyze.RunAnalysis()),
                    {"bloqueo": True},
                )
            if args.solo != "B":
                pruebas_crudas(s, fase, not args.sin_diseno)
    except Rechazo as exc:
        print(f"ABORTADO: {exc}")
        s.cerrar()
        return 3
    finally:
        s.cerrar()
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    salida = Path(args.salida_dir)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"sonda_combinaciones_{sello}.json").write_text(
        json.dumps(
            {"meta": s.informe.meta, "pruebas": s.informe.pruebas, "resumen": s.informe.resumen()},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    (salida / f"sonda_combinaciones_{sello}.md").write_text(s.informe.a_markdown(), encoding="utf-8")
    print(f"\nResumen: {s.informe.resumen()}  Original intacto: {s.informe.meta.get('original_intacto')}")
    print(f"Informe: {salida / f'sonda_combinaciones_{sello}.json'}")
    print(f"Para volver al modelo original: abra {s.original} (la copia queda en {s.copia}).")
    return 1 if s.informe.resumen().get(INESPERADO) else 0


if __name__ == "__main__":
    raise SystemExit(main())
