"""Tests de bridge/caesar_cargas.py: transferencia de cargas nodales desde CAESAR II.

COM falso con el mismo patrón que tests/test_bridge_acero_enviar.py. La forma pywin32 (código de
retorno al inicio) se prueba con el proxy de tests/test_bridge_esbelteces.py.

Todo esto está validado con COM falso. Falta validarlo con SAP2000 real (ver docs/SAP_ACTIONS.md).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import bridge.caesar_cargas as caesar_cargas
from bridge.actions import install_routes
from bridge.caesar_cargas import EUNITS_POR_UNIDADES, TIPOS_PATRON, CargasBody, preview_cargas
from bridge.graph import UNITS
from tests.test_bridge_contracts import assert_matches_contract
from tests.test_bridge_esbelteces import _RetFirst

RAIZ = "/v1/sap/actions/caesar"
ORIGINAL = 6  # kN_m_C: unidades del modelo de prueba (la restauración debe devolverlas aquí)
TONF_M = 12  # Ton_m_C


class _Puntos:
    """PointObj: nombres de nodo, GetLoadForce (forma OAPI) y SetLoadForce con fallos programables."""

    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (0, len(self._m.puntos), tuple(self._m.puntos))

    def GetLoadForce(self, nombre, *_):
        if self._m.fallar_previas:
            raise RuntimeError("boom lectura")
        filas = self._m.cargas_previas.get(nombre, [])
        columnas = list(zip(*filas)) if filas else [()] * 8
        load_pat, csys, f1, f2, f3, m1, m2, m3 = columnas
        return (
            len(filas),
            tuple(nombre for _ in filas),
            load_pat,
            tuple(0.0 for _ in filas),
            csys,
            f1,
            f2,
            f3,
            m1,
            m2,
            m3,
            0,
        )

    def SetLoadForce(self, nombre, patron, fuerzas, reemplazar, csys):
        self._m.llamadas_carga.append((nombre, patron, tuple(fuerzas), reemplazar, csys))
        if nombre in self._m.nodos_que_fallan:
            raise RuntimeError("boom nodo")
        return 0


class _Patrones:
    """LoadPatterns: nombres, tipo, Add (un nombre existente devuelve 1, como SAP2000) y fallos programables."""

    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (0, len(self._m.patrones), tuple(self._m.patrones))

    def GetLoadType(self, nombre):
        return (0, self._m.tipos.get(nombre, 8))

    def Add(self, nombre, tipo, peso, caso):
        self._m.llamadas_add.append((nombre, tipo, peso, caso))
        if nombre == self._m.fallar_add:
            raise RuntimeError("boom add")
        if nombre in self._m.patrones:
            return 1
        self._m.patrones.append(nombre)
        self._m.tipos[nombre] = tipo
        return 0


class _Casos:
    def __init__(self, modelo):
        self._m = modelo

    def GetNameList(self, *_):
        return (0, len(self._m.casos), tuple(self._m.casos))


class _Seleccion:
    """SelectObj.GetSelected con la forma OAPI (retorno al final); ``_RetFirst`` la mueve al inicio."""

    def __init__(self, modelo):
        self._m = modelo

    def GetSelected(self, *_):
        tipos = tuple(tipo for tipo, _ in self._m.seleccion)
        nombres = tuple(nombre for _, nombre in self._m.seleccion)
        return (len(nombres), tipos, nombres, 0)


class _Modelo:
    def __init__(
        self,
        *,
        puntos=("1", "2", "3"),
        patrones=("DEAD",),
        casos=("DEAD",),
        bloqueado=False,
        cargas_previas=None,
    ):
        self.puntos = list(puntos)
        self.patrones = list(patrones)
        self.tipos = {"DEAD": 1}
        self.casos = list(casos)
        self.units = ORIGINAL
        self.unidades_tupla = False  # GetPresentUnits como (ret, unidades)
        self.unit_calls: list[int] = []
        self.fallar_restaurar = False
        self.locked = bloqueado
        self.bloqueo_tupla = False  # GetModelIsLocked como (ret, bool)
        self.lock_calls: list[bool] = []
        self.cargas_previas = dict(cargas_previas or {})
        self.fallar_previas = False
        self.llamadas_carga: list[tuple] = []
        self.llamadas_add: list[tuple] = []
        self.nodos_que_fallan: set[str] = set()
        self.fallar_add: str | None = None
        self.seleccion: list[tuple[int, str]] = []  # (eObjectType, nombre): 1 = punto, 2 = frame
        self.PointObj = _Puntos(self)
        self.LoadPatterns = _Patrones(self)
        self.LoadCases = _Casos(self)
        self.SelectObj = _Seleccion(self)

    def GetPresentUnits(self):
        return (0, self.units) if self.unidades_tupla else self.units

    def SetPresentUnits(self, unidad):
        self.unit_calls.append(unidad)
        if self.fallar_restaurar and unidad == ORIGINAL:
            return 1
        self.units = unidad
        return 0

    def GetModelIsLocked(self):
        return (0, self.locked) if self.bloqueo_tupla else self.locked

    def SetModelIsLocked(self, bloqueo):
        self.lock_calls.append(bloqueo)
        if not bloqueo:
            self.locked = False
        return 0

    def escrituras(self) -> int:
        """Nº de llamadas de escritura recibidas (cargas, patrones, unidades y bloqueo)."""
        return len(self.llamadas_carga) + len(self.llamadas_add) + len(self.unit_calls) + len(self.lock_calls)


class _Actor:
    def call(self, fn):
        return fn()


class _Sesion:
    def __init__(self, modelo):
        self.actor = _Actor()
        self.modelo = modelo

    def _model(self):
        return self.modelo


@pytest.fixture
def cliente():
    def make(modelo):
        app = FastAPI()
        install_routes(app, _Sesion(modelo), RuntimeError)
        return TestClient(app, base_url="http://127.0.0.1:8765")

    return make


@pytest.fixture(scope="module")
def contratos():
    raiz = Path(__file__).resolve().parent.parent
    return json.loads((raiz / "web" / "src" / "bridge_contracts.json").read_text("utf-8"))["routes"]


def _cuerpo(**cambios):
    cuerpo = {
        "unidades": {"fuerza": "kN", "longitud": "m"},
        "crearLoadPatterns": False,
        "loadPatterns": [],
        "cargas": [{"nodo": 1, "loadPattern": "DEAD", "fx": 1.0, "fy": 0.0, "fz": -2.0}],
        "modo": "reemplazar",
    }
    cuerpo.update(cambios)
    return cuerpo


def _carga(nodo, patron="DEAD", fx=0.0, fy=0.0, fz=0.0):
    return {"nodo": nodo, "loadPattern": patron, "fx": fx, "fy": fy, "fz": fz}


# ---------------------------------------------------------------------------
# GET modelo
# ---------------------------------------------------------------------------


def test_modelo_lista_nodos_patrones_tipos_y_unidades(cliente):
    cuerpo = cliente(_Modelo()).get(f"{RAIZ}/modelo").json()
    assert cuerpo["ok"] is True and cuerpo["locked"] is False
    assert cuerpo["unidadesModelo"] == "kN · m · °C"
    assert cuerpo["nPuntos"] == 3 and cuerpo["puntosTruncado"] is False
    assert cuerpo["puntos"] == ["1", "2", "3"]
    assert cuerpo["loadPatterns"] == [{"name": "DEAD", "type": "Dead"}]


def test_modelo_con_mas_de_50000_puntos_devuelve_solo_el_recuento(cliente):
    puntos = [str(i) for i in range(1, 50_002)]
    cuerpo = cliente(_Modelo(puntos=puntos)).get(f"{RAIZ}/modelo").json()
    assert cuerpo["nPuntos"] == 50_001
    assert cuerpo["puntosTruncado"] is True
    assert cuerpo["puntos"] == []


def test_modelo_con_unidades_y_bloqueo_como_tupla(cliente):
    modelo = _Modelo(bloqueado=True)
    modelo.unidades_tupla = True
    modelo.bloqueo_tupla = True
    cuerpo = cliente(modelo).get(f"{RAIZ}/modelo").json()
    assert cuerpo["locked"] is True
    assert cuerpo["unidadesModelo"] == "kN · m · °C"


# ---------------------------------------------------------------------------
# Preview: solo lectura
# ---------------------------------------------------------------------------


def test_preview_no_escribe_nada(cliente):
    modelo = _Modelo()
    respuesta = cliente(modelo).post(f"{RAIZ}/cargas/preview", json=_cuerpo())
    assert respuesta.status_code == 200
    assert modelo.escrituras() == 0
    assert modelo.patrones == ["DEAD"]


def test_preview_directo_no_llama_a_escritura():
    modelo = _Modelo()
    preview_cargas(modelo, CargasBody(**_cuerpo()))
    assert modelo.escrituras() == 0


def test_preview_clasifica_patrones_existentes_por_crear_y_faltantes(cliente):
    modelo = _Modelo()
    cuerpo = _cuerpo(
        crearLoadPatterns=True,
        loadPatterns=[{"name": "VIENTO"}],
        cargas=[_carga(1, "DEAD", fz=-1.0), _carga(2, "VIENTO", fx=3.0), _carga(3, "SISMO", fx=1.0)],
    )
    resp = cliente(modelo).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["patterns"] == {"existentes": ["DEAD"], "aCrear": ["VIENTO"], "faltantes": ["SISMO"]}
    assert resp["ok"] is False  # SISMO no existe y no se crea: apply daría 422


def test_preview_pattern_faltante_sin_crear_queda_en_faltantes(cliente):
    cuerpo = _cuerpo(cargas=[_carga(1, "VIENTO", fx=1.0)])
    resp = cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["patterns"] == {"existentes": [], "aCrear": [], "faltantes": ["VIENTO"]}


def test_preview_nodos_inexistentes_se_listan(cliente):
    cuerpo = _cuerpo(cargas=[_carga(2, fz=-1.0), _carga(9, fz=-1.0)])
    resp = cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["nodos"] == {"total": 2, "encontrados": 1, "faltantes": [9], "nFaltantes": 1}
    assert resp["ok"] is False
    assert any("no existen en el modelo" in aviso for aviso in resp["avisos"])


def test_preview_cuenta_ceros_y_avisa_de_que_solo_hay_fuerzas(cliente):
    cuerpo = _cuerpo(cargas=[_carga(1, fx=0.0), _carga(2, fz=-4.0)])
    resp = cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["cargas"] == {"total": 2, "conValor": 1, "ceros": 1}
    assert any("Solo se transfieren FX, FY y FZ" in aviso for aviso in resp["avisos"])
    assert any("a cero no se escriben" in aviso for aviso in resp["avisos"])


def test_preview_unidades_no_soportadas_son_422_con_la_lista(cliente):
    respuesta = cliente(_Modelo()).post(
        f"{RAIZ}/cargas/preview", json=_cuerpo(unidades={"fuerza": "kgf", "longitud": "ft"})
    )
    assert respuesta.status_code == 422
    texto = json.dumps(respuesta.json(), ensure_ascii=False)
    assert "kgf-ft" in texto and "kN-m" in texto and "tonf-m" in texto


def test_preview_fuerza_desconocida_es_422(cliente):
    respuesta = cliente(_Modelo()).post(
        f"{RAIZ}/cargas/preview", json=_cuerpo(unidades={"fuerza": "MN", "longitud": "m"})
    )
    assert respuesta.status_code == 422


def test_preview_cargas_previas_cuenta_pares_con_carga_en_sap2000(cliente):
    previas = {"1": [("DEAD", "Global", 5.0, 0.0, 0.0, 0.0, 0.0, 0.0)]}
    cuerpo = _cuerpo(cargas=[_carga(1, fz=-1.0), _carga(2, fz=-1.0), _carga(3, fz=-1.0)])
    resp = cliente(_Modelo(cargas_previas=previas)).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["cargasPrevias"] == 1


def test_preview_cargas_previas_es_null_con_demasiados_nodos(cliente):
    puntos = [str(i) for i in range(1, 2002)]
    cuerpo = _cuerpo(cargas=[_carga(i, fz=-1.0) for i in range(1, 2002)])
    resp = cliente(_Modelo(puntos=puntos)).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    assert resp["cargasPrevias"] is None


def test_preview_cargas_previas_es_null_si_falla_la_lectura(cliente):
    modelo = _Modelo()
    modelo.fallar_previas = True
    resp = cliente(modelo).post(f"{RAIZ}/cargas/preview", json=_cuerpo()).json()
    assert resp["cargasPrevias"] is None
    assert any("cargasPrevias es null" in aviso for aviso in resp["avisos"])


def test_preview_avisa_de_bloqueo_y_de_caso_con_el_mismo_nombre(cliente):
    modelo = _Modelo(bloqueado=True, casos=("DEAD", "VIENTO"))
    cuerpo = _cuerpo(
        crearLoadPatterns=True, loadPatterns=[{"name": "VIENTO"}], cargas=[_carga(1, "VIENTO", fx=1)]
    )
    resp = cliente(modelo).post(f"{RAIZ}/cargas/preview", json=cuerpo).json()
    textos = " | ".join(resp["avisos"])
    assert resp["locked"] is True and "bloqueado" in textos
    assert "tiene el nombre de un caso de carga" in textos


def test_preview_con_codigo_de_retorno_al_inicio_da_el_mismo_resultado(cliente):
    normal = cliente(_Modelo(cargas_previas={"1": [("DEAD", "Global", 1.0, 0, 0, 0, 0, 0)]})).post(
        f"{RAIZ}/cargas/preview", json=_cuerpo()
    )
    invertido = cliente(
        _RetFirst(_Modelo(cargas_previas={"1": [("DEAD", "Global", 1.0, 0, 0, 0, 0, 0)]}))
    ).post(f"{RAIZ}/cargas/preview", json=_cuerpo())
    assert normal.status_code == invertido.status_code == 200
    assert normal.json() == invertido.json()


# ---------------------------------------------------------------------------
# Apply: escritura
# ---------------------------------------------------------------------------


def test_apply_crea_patrones_y_aplica_fuerzas_con_replace(cliente):
    modelo = _Modelo()
    cuerpo = _cuerpo(
        crearLoadPatterns=True,
        loadPatterns=[{"name": "VIENTO"}],
        cargas=[_carga(1, "VIENTO", 1.0, 2.0, 3.0), _carga(2, "DEAD", 4.0, 0.0, 0.0)],
    )
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=cuerpo)
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["ok"] is True and datos["estado"] == "ok"
    assert datos["patternsCreados"] == ["VIENTO"]
    assert datos["aplicadas"] == 2 and datos["nFallidas"] == 0 and datos["fallidas"] == []
    assert modelo.llamadas_add == [("VIENTO", TIPOS_PATRON["Other"], 0.0, True)]
    assert modelo.llamadas_carga == [
        ("1", "VIENTO", (1.0, 2.0, 3.0, 0.0, 0.0, 0.0), True, "Global"),
        ("2", "DEAD", (4.0, 0.0, 0.0, 0.0, 0.0, 0.0), True, "Global"),
    ]


def test_apply_modo_sumar_usa_replace_false(cliente):
    modelo = _Modelo()
    cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(modo="sumar"))
    assert [llamada[3] for llamada in modelo.llamadas_carga] == [False]


def test_apply_no_envia_momentos_y_el_cuerpo_no_admite_mx(cliente):
    modelo = _Modelo()
    cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(cargas=[_carga(1, fx=1.0, fy=2.0, fz=3.0)]))
    assert len(modelo.llamadas_carga) == 1
    assert modelo.llamadas_carga[0][2][3:] == (0.0, 0.0, 0.0)
    cuerpo = _cuerpo()
    cuerpo["cargas"][0]["mx"] = 5.0
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/apply", json=cuerpo).status_code == 422


def test_apply_restaura_unidades_en_el_camino_feliz(cliente):
    modelo = _Modelo()
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/apply", json=_cuerpo(unidades={"fuerza": "tonf", "longitud": "m"})
    )
    assert resp.status_code == 200
    assert modelo.unit_calls == [TONF_M, ORIGINAL]
    assert modelo.units == ORIGINAL
    datos = resp.json()
    assert datos["unidadesRestauradas"] is True and datos["unidadesModelo"] == "kN · m · °C"


def test_apply_sin_cambio_de_unidades_no_las_toca(cliente):
    modelo = _Modelo()
    cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo())
    assert modelo.unit_calls == []


def test_apply_restaura_unidades_aunque_falle_a_mitad(cliente):
    modelo = _Modelo()
    modelo.fallar_add = "VIENTO"
    cuerpo = _cuerpo(
        crearLoadPatterns=True,
        loadPatterns=[{"name": "VIENTO"}],
        cargas=[_carga(1, "VIENTO", fx=1.0)],
        unidades={"fuerza": "tonf", "longitud": "m"},
    )
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=cuerpo)
    assert resp.status_code == 409
    detalle = resp.json()["detail"]
    assert detalle["code"] == "partial" and detalle["partial"] is True and detalle["estado"] == "partial"
    assert modelo.unit_calls == [TONF_M, ORIGINAL] and modelo.units == ORIGINAL
    assert modelo.llamadas_carga == []  # tras el fallo de un patrón no se escribe ninguna carga
    assert detalle["unidadesRestauradas"] is True


def test_apply_restaura_unidades_con_get_present_units_como_tupla(cliente):
    modelo = _Modelo()
    modelo.unidades_tupla = True
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/apply", json=_cuerpo(unidades={"fuerza": "tonf", "longitud": "m"})
    )
    assert resp.status_code == 200
    assert modelo.unit_calls == [TONF_M, ORIGINAL] and modelo.units == ORIGINAL


def test_apply_fallo_al_restaurar_unidades_se_avisa(cliente):
    modelo = _Modelo()
    modelo.fallar_restaurar = True
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/apply", json=_cuerpo(unidades={"fuerza": "tonf", "longitud": "m"})
    )
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["unidadesRestauradas"] is False
    assert any("No se pudieron restaurar las unidades" in aviso for aviso in datos["avisos"])


def test_apply_con_codigo_de_retorno_al_inicio_da_el_mismo_resultado(cliente):
    normal = cliente(_Modelo()).post(f"{RAIZ}/cargas/apply", json=_cuerpo())
    invertido = cliente(_RetFirst(_Modelo())).post(f"{RAIZ}/cargas/apply", json=_cuerpo())
    assert normal.status_code == invertido.status_code == 200
    assert normal.json() == invertido.json()


def test_apply_bloqueado_sin_confirmar_es_409_y_no_escribe(cliente):
    modelo = _Modelo(bloqueado=True)
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo())
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "model_locked"
    assert modelo.lock_calls == [] and modelo.unit_calls == [] and modelo.llamadas_carga == []


def test_apply_bloqueado_con_confirmar_desbloquea_y_avisa(cliente):
    modelo = _Modelo(bloqueado=True)
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(confirmarBloqueo=True))
    assert resp.status_code == 200
    datos = resp.json()
    assert modelo.lock_calls == [False] and modelo.locked is False
    assert datos["modeloDesbloqueado"] is True
    assert any("BORRA los resultados" in aviso for aviso in datos["avisos"])


def test_apply_nodo_inexistente_es_partial_sin_llamar_a_setloadforce(cliente):
    modelo = _Modelo()
    cuerpo = _cuerpo(cargas=[_carga(9, fz=-1.0), _carga(1, fz=-2.0)])
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=cuerpo)
    assert resp.status_code == 409
    datos = resp.json()["detail"]
    assert datos["estado"] == "partial" and datos["ok"] is False
    assert datos["aplicadas"] == 1 and datos["nFallidas"] == 1
    assert datos["fallidas"] == [{"nodo": 9, "loadPattern": "DEAD", "motivo": "nodo no existe en el modelo"}]
    assert [llamada[0] for llamada in modelo.llamadas_carga] == ["1"]
    assert modelo.units == ORIGINAL


def test_apply_fallo_de_una_carga_se_registra_y_sigue(cliente):
    modelo = _Modelo(puntos=("1", "2", "3"))
    modelo.nodos_que_fallan = {"2"}
    cuerpo = _cuerpo(cargas=[_carga(1, fz=-1.0), _carga(2, fz=-1.0), _carga(3, fz=-1.0)])
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=cuerpo)
    assert resp.status_code == 409
    datos = resp.json()["detail"]
    assert datos["aplicadas"] == 2 and datos["nFallidas"] == 1
    assert datos["fallidas"] == [{"nodo": 2, "loadPattern": "DEAD", "motivo": "boom nodo"}]
    assert [llamada[0] for llamada in modelo.llamadas_carga] == ["1", "2", "3"]


def test_apply_cargas_nulas_no_se_escriben(cliente):
    modelo = _Modelo()
    cuerpo = _cuerpo(cargas=[_carga(1), _carga(2, fz=-1.0)])
    datos = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=cuerpo).json()
    assert [llamada[0] for llamada in modelo.llamadas_carga] == ["2"]
    assert datos["aplicadas"] == 1
    assert any("a cero no se escriben" in aviso for aviso in datos["avisos"])


def test_apply_patron_faltante_sin_crear_es_422_sin_escribir(cliente):
    modelo = _Modelo()
    resp = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(cargas=[_carga(1, "VIENTO", fx=1.0)]))
    assert resp.status_code == 422
    assert modelo.escrituras() == 0


def test_apply_log_no_incluye_nombres_ni_valores(cliente, caplog):
    modelo = _Modelo()
    with caplog.at_level(logging.INFO, logger="bridge.caesar_cargas"):
        cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(cargas=[_carga(1, "DEAD", fz=-123.456)]))
    mensajes = [
        registro.getMessage() for registro in caplog.records if registro.name == "bridge.caesar_cargas"
    ]
    assert mensajes and all("aplicadas=" in mensaje for mensaje in mensajes)
    assert all("DEAD" not in mensaje and "123" not in mensaje for mensaje in mensajes)


# ---------------------------------------------------------------------------
# Validación del cuerpo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cambios",
    [
        {"cargas": []},
        {"cargas": [_carga(0, fz=1.0)]},
        {"cargas": [_carga(1, "A/B", fz=1.0)]},
        {"cargas": [_carga(1, "DEAD", fz=1.0), _carga(1, "dead", fx=1.0)]},
        {"modo": "anular"},
        {"loadPatterns": [{"name": "VIENTO"}, {"name": "viento"}], "crearLoadPatterns": True},
        {"loadPatterns": [{"name": "VIENTO", "type": "Live"}], "crearLoadPatterns": True},
        {"loadPatterns": [{"name": "A:B"}], "crearLoadPatterns": True},
        {"foo": 1},
    ],
)
def test_entradas_invalidas_son_422(cliente, cambios):
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/apply", json=_cuerpo(**cambios)).status_code == 422
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=_cuerpo(**cambios)).status_code == 422


def test_limite_de_cargas_y_de_patrones(cliente):
    muchas = [_carga(i, fz=-1.0) for i in range(1, 200_002)]
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=_cuerpo(cargas=muchas)).status_code == 422
    patrones = [{"name": f"P{i}"} for i in range(2_001)]
    cuerpo = _cuerpo(crearLoadPatterns=True, loadPatterns=patrones)
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=cuerpo).status_code == 422


def test_cuerpo_grande_de_200000_cargas_se_acepta_en_preview(cliente):
    puntos = [str(i) for i in range(1, 200_001)]
    cuerpo = _cuerpo(cargas=[_carga(i, fz=-1.0) for i in range(1, 200_001)])
    resp = cliente(_Modelo(puntos=puntos)).post(f"{RAIZ}/cargas/preview", json=cuerpo)
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["nodos"]["total"] == 200_000 and datos["cargas"]["conValor"] == 200_000
    assert datos["cargasPrevias"] is None


def test_claves_extra_en_apply_son_422(cliente):
    assert cliente(_Modelo()).post(f"{RAIZ}/cargas/apply", json=_cuerpo(confirmado=True)).status_code == 422
    assert (
        cliente(_Modelo()).post(f"{RAIZ}/cargas/preview", json=_cuerpo(confirmarBloqueo=True)).status_code
        == 422
    )


def test_unidad_no_soportada_en_apply_no_escribe(cliente):
    modelo = _Modelo()
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/apply", json=_cuerpo(unidades={"fuerza": "lb", "longitud": "m"})
    )
    assert resp.status_code == 422
    assert modelo.escrituras() == 0


# ---------------------------------------------------------------------------
# Tablas y contrato
# ---------------------------------------------------------------------------


def test_tabla_de_unidades_coincide_con_la_de_graph():
    assert len(EUNITS_POR_UNIDADES) == 16
    for (fuerza, longitud), codigo in EUNITS_POR_UNIDADES.items():
        # Los códigos 1 a 4 (lb e in/ft) son °F en graph.UNITS; el resto, °C.
        assert UNITS[codigo].startswith(f"{fuerza} · {longitud} · °")
    assert EUNITS_POR_UNIDADES[("kN", "m")] == 6
    assert EUNITS_POR_UNIDADES[("kgf", "m")] == 8
    assert EUNITS_POR_UNIDADES[("N", "m")] == 10
    assert EUNITS_POR_UNIDADES[("tonf", "m")] == 12


def test_tipos_de_patron_son_solo_los_verificados():
    assert TIPOS_PATRON == {"Dead": 1, "Other": 8}


def test_cuerpo_de_carga_rechaza_valores_no_finitos():
    with pytest.raises(ValidationError):
        CargasBody(**_cuerpo(cargas=[_carga(1, fx=float("inf"))]))


def test_contratos_de_modelo_preview_y_apply(cliente, contratos):
    modelo = _Modelo()
    lectura = cliente(modelo).get(f"{RAIZ}/modelo").json()
    assert_matches_contract(contratos, "GET /v1/sap/actions/caesar/modelo", lectura)
    vista = cliente(modelo).post(f"{RAIZ}/cargas/preview", json=_cuerpo()).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/caesar/cargas/preview", vista)
    aplicado = cliente(_Modelo()).post(f"{RAIZ}/cargas/apply", json=_cuerpo()).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/caesar/cargas/apply", aplicado)


def test_contrato_de_fallidas_en_el_apply_parcial(cliente, contratos):
    modelo = _Modelo()
    detalle = cliente(modelo).post(f"{RAIZ}/cargas/apply", json=_cuerpo(cargas=[_carga(9, fz=-1.0)])).json()
    detalle = detalle["detail"]
    claves = set(contratos["POST /v1/sap/actions/caesar/cargas/apply"]["keys"])
    assert claves <= set(detalle)
    assert set(detalle["fallidas"][0]) == set(
        contratos["POST /v1/sap/actions/caesar/cargas/apply"]["items"]["fallidas"]
    )


# ---------------------------------------------------------------------------
# GET seleccion y POST cargas/existentes (solo lectura)
# ---------------------------------------------------------------------------


def _existentes(**cambios):
    cuerpo = {"unidades": {"fuerza": "kN", "longitud": "m"}, "loadPatterns": ["DEAD"], "nodos": [1]}
    cuerpo.update(cambios)
    return cuerpo


def test_seleccion_vacia_es_ok_con_listas_vacias(cliente):
    datos = cliente(_Modelo()).get(f"{RAIZ}/seleccion").json()
    assert datos["ok"] is True
    assert datos["nPuntos"] == 0 and datos["puntos"] == [] and datos["nodos"] == []
    assert datos["noNumericos"] == [] and datos["nNoNumericos"] == 0
    assert datos["puntosTruncado"] is False and datos["unidadesModelo"] == "kN · m · °C"


def test_seleccion_solo_cuenta_puntos_y_no_escribe(cliente):
    modelo = _Modelo()
    modelo.seleccion = [(1, "2"), (2, "F5"), (1, "1"), (5, "S1")]
    datos = cliente(modelo).get(f"{RAIZ}/seleccion").json()
    assert datos["nPuntos"] == 2 and datos["puntos"] == ["2", "1"]
    assert datos["nodos"] == [2, 1] and datos["noNumericos"] == []
    assert modelo.escrituras() == 0


def test_seleccion_separa_nombres_no_numericos(cliente):
    modelo = _Modelo()
    modelo.seleccion = [(1, "A"), (1, "007"), (1, "0"), (1, "3"), (1, "10")]
    datos = cliente(modelo).get(f"{RAIZ}/seleccion").json()
    assert datos["nodos"] == [3, 10]
    assert datos["noNumericos"] == ["A", "007", "0"] and datos["nNoNumericos"] == 3


def test_seleccion_con_codigo_de_retorno_al_inicio_da_el_mismo_resultado(cliente):
    modelo = _Modelo()
    modelo.seleccion = [(1, "1"), (1, "B"), (2, "F1")]
    normal = cliente(modelo).get(f"{RAIZ}/seleccion")
    invertido = cliente(_RetFirst(modelo)).get(f"{RAIZ}/seleccion")
    assert normal.status_code == invertido.status_code == 200
    assert normal.json() == invertido.json()


def test_seleccion_puntos_se_truncan_pero_nodos_no(cliente, monkeypatch):
    monkeypatch.setattr(caesar_cargas, "MAX_PUNTOS", 2)
    modelo = _Modelo()
    modelo.seleccion = [(1, "1"), (1, "2"), (1, "3")]
    datos = cliente(modelo).get(f"{RAIZ}/seleccion").json()
    assert datos["nPuntos"] == 3 and datos["puntosTruncado"] is True
    assert datos["puntos"] == ["1", "2"]
    assert datos["nodos"] == [1, 2, 3]


def test_existentes_devuelve_fx_fy_fz_por_nodo_y_patron_en_orden(cliente):
    previas = {
        "1": [
            ("DEAD", "Global", 1.0, 2.0, 3.0, 0.0, 0.0, 0.0),
            ("VIENTO", "Global", 0.0, -4.0, 0.0, 0.0, 0.0, 0.0),
        ],
        "2": [("DEAD", "Global", 0.0, 0.0, -5.0, 0.0, 0.0, 0.0)],
    }
    modelo = _Modelo(patrones=("DEAD", "VIENTO"), cargas_previas=previas)
    cuerpo = _existentes(loadPatterns=["VIENTO", "DEAD"], nodos=[2, 1, 3])
    resp = cliente(modelo).post(f"{RAIZ}/cargas/existentes", json=cuerpo)
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["cargas"] == [
        {"nodo": 1, "loadPattern": "VIENTO", "fx": 0.0, "fy": -4.0, "fz": 0.0},
        {"nodo": 1, "loadPattern": "DEAD", "fx": 1.0, "fy": 2.0, "fz": 3.0},
        {"nodo": 2, "loadPattern": "DEAD", "fx": 0.0, "fy": 0.0, "fz": -5.0},
    ]
    assert datos["nCargas"] == 3 and datos["cargasTruncadas"] is False


def test_existentes_suma_entradas_del_mismo_nodo_y_patron(cliente):
    previas = {
        "1": [
            ("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            ("DEAD", "Global", 2.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            ("DEAD", "Local", 10.0, 0.0, -1.0, 0.0, 0.0, 0.0),
        ]
    }
    datos = (
        cliente(_Modelo(cargas_previas=previas)).post(f"{RAIZ}/cargas/existentes", json=_existentes()).json()
    )
    assert datos["cargas"] == [{"nodo": 1, "loadPattern": "DEAD", "fx": 13.0, "fy": 0.0, "fz": -1.0}]
    assert any("sistema de coordenadas distinto de Global" in aviso for aviso in datos["avisos"])


def test_existentes_nodos_y_patrones_inexistentes_no_abortan(cliente):
    previas = {"1": [("DEAD", "Global", 0.0, 0.0, -2.0, 0.0, 0.0, 0.0)]}
    cuerpo = _existentes(loadPatterns=["DEAD", "NOPE"], nodos=[1, 9, 9])
    resp = cliente(_Modelo(cargas_previas=previas)).post(f"{RAIZ}/cargas/existentes", json=cuerpo)
    assert resp.status_code == 200
    datos = resp.json()
    assert datos["ok"] is True
    assert datos["nodosInexistentes"] == [9] and datos["nNodosInexistentes"] == 1
    assert datos["patternsInexistentes"] == ["NOPE"]
    assert datos["cargas"] == [{"nodo": 1, "loadPattern": "DEAD", "fx": 0.0, "fy": 0.0, "fz": -2.0}]
    assert any("no existen en el modelo" in aviso for aviso in datos["avisos"])


def test_existentes_ignora_momentos_y_no_devuelve_cargas_nulas(cliente):
    previas = {
        "1": [("DEAD", "Global", 1.0, 0.0, 0.0, 5.0, 6.0, 7.0)],
        "2": [("DEAD", "Global", 0.0, 0.0, 0.0, 5.0, 6.0, 7.0)],
    }
    datos = (
        cliente(_Modelo(cargas_previas=previas))
        .post(f"{RAIZ}/cargas/existentes", json=_existentes(nodos=[1, 2]))
        .json()
    )
    assert datos["cargas"] == [{"nodo": 1, "loadPattern": "DEAD", "fx": 1.0, "fy": 0.0, "fz": 0.0}]
    assert set(datos["cargas"][0]) == {"nodo", "loadPattern", "fx", "fy", "fz"}
    assert any("Mx, My y Mz no se devuelven" in aviso for aviso in datos["avisos"])


def test_existentes_trunca_cargas_al_limite(cliente, monkeypatch):
    monkeypatch.setattr(caesar_cargas, "MAX_CARGAS", 2)
    previas = {str(i): [("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)] for i in range(1, 4)}
    datos = (
        cliente(_Modelo(puntos=("1", "2", "3"), cargas_previas=previas))
        .post(f"{RAIZ}/cargas/existentes", json=_existentes(nodos=[1, 2, 3]))
        .json()
    )
    assert len(datos["cargas"]) == 2 and datos["nCargas"] == 3 and datos["cargasTruncadas"] is True
    assert [carga["nodo"] for carga in datos["cargas"]] == [1, 2]


@pytest.mark.parametrize("unidades_tupla", [False, True])
def test_existentes_restaura_unidades_con_y_sin_tupla(cliente, unidades_tupla):
    modelo = _Modelo()
    modelo.unidades_tupla = unidades_tupla
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/existentes", json=_existentes(unidades={"fuerza": "tonf", "longitud": "m"})
    )
    assert resp.status_code == 200
    assert modelo.unit_calls == [TONF_M, ORIGINAL] and modelo.units == ORIGINAL
    datos = resp.json()
    assert datos["unidadesRestauradas"] is True and datos["unidadesEnvio"] == "tonf · m · °C"
    assert datos["unidadesModelo"] == "kN · m · °C"


def test_existentes_restaura_unidades_aunque_falle_la_lectura(cliente):
    modelo = _Modelo()
    modelo.fallar_previas = True
    resp = cliente(modelo).post(
        f"{RAIZ}/cargas/existentes", json=_existentes(unidades={"fuerza": "tonf", "longitud": "m"})
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "sap_error"
    assert modelo.unit_calls == [TONF_M, ORIGINAL] and modelo.units == ORIGINAL
    assert modelo.escrituras() == 2  # solo las dos llamadas de unidades


def test_existentes_fallo_al_restaurar_unidades_se_avisa(cliente):
    modelo = _Modelo()
    modelo.fallar_restaurar = True
    datos = (
        cliente(modelo)
        .post(f"{RAIZ}/cargas/existentes", json=_existentes(unidades={"fuerza": "tonf", "longitud": "m"}))
        .json()
    )
    assert datos["unidadesRestauradas"] is False
    assert any("No se pudieron restaurar las unidades" in aviso for aviso in datos["avisos"])


def test_existentes_sin_cambio_de_unidades_no_las_toca(cliente):
    modelo = _Modelo()
    cliente(modelo).post(f"{RAIZ}/cargas/existentes", json=_existentes())
    assert modelo.unit_calls == []


def test_existentes_y_seleccion_no_escriben_nada(cliente):
    modelo = _Modelo(cargas_previas={"1": [("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)]})
    modelo.seleccion = [(1, "1")]
    cliente(modelo).get(f"{RAIZ}/seleccion")
    cliente(modelo).post(f"{RAIZ}/cargas/existentes", json=_existentes(nodos=[1, 9]))
    assert modelo.escrituras() == 0
    assert modelo.patrones == ["DEAD"] and modelo.units == ORIGINAL


def test_existentes_con_codigo_de_retorno_al_inicio_da_el_mismo_resultado(cliente):
    previas = {"1": [("DEAD", "Global", 1.0, 2.0, 3.0, 0.0, 0.0, 0.0)]}
    normal = cliente(_Modelo(cargas_previas=previas)).post(f"{RAIZ}/cargas/existentes", json=_existentes())
    invertido = cliente(_RetFirst(_Modelo(cargas_previas=previas))).post(
        f"{RAIZ}/cargas/existentes", json=_existentes()
    )
    assert normal.status_code == invertido.status_code == 200
    assert normal.json() == invertido.json()


@pytest.mark.parametrize(
    "cambios",
    [
        {"nodos": []},
        {"nodos": [0]},
        {"nodos": [1.5]},
        {"nodos": list(range(1, 200_002))},
        {"loadPatterns": []},
        {"loadPatterns": [f"P{i}" for i in range(2_001)]},
        {"loadPatterns": ["DEAD", "dead"]},
        {"loadPatterns": ["A/B"]},
        {"unidades": {"fuerza": "kgf", "longitud": "ft"}},
        {"crearLoadPatterns": True},
        {"cargas": []},
        {"confirmarBloqueo": True},
        {"mx": 1.0},
    ],
)
def test_existentes_entradas_invalidas_son_422_sin_escribir(cliente, cambios):
    modelo = _Modelo()
    assert cliente(modelo).post(f"{RAIZ}/cargas/existentes", json=_existentes(**cambios)).status_code == 422
    assert modelo.escrituras() == 0


def test_contratos_de_seleccion_y_cargas_existentes(cliente, contratos):
    modelo = _Modelo(cargas_previas={"1": [("DEAD", "Global", 1.0, 0.0, 0.0, 0.0, 0.0, 0.0)]})
    modelo.seleccion = [(1, "1"), (1, "X")]
    seleccion = cliente(modelo).get(f"{RAIZ}/seleccion").json()
    assert_matches_contract(contratos, "GET /v1/sap/actions/caesar/seleccion", seleccion)
    existentes = cliente(modelo).post(f"{RAIZ}/cargas/existentes", json=_existentes()).json()
    assert_matches_contract(contratos, "POST /v1/sap/actions/caesar/cargas/existentes", existentes)
