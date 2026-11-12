"""``_name_list`` / ``_get_name_list``: todas las formas de respuesta de ``GetNameList`` y el reintento de firmas."""

from __future__ import annotations

import pytest

from bridge.actions import UnexpectedNameListResponse, _get_name_list, _name_list

NOMBRES = ["A", "B"]


@pytest.mark.parametrize(
    "respuesta",
    [
        (0, 2, ("A", "B")),  # (ret, count, names)
        (2, ("A", "B"), 0),  # (count, names, ret)
        (("A", "B"), 0),  # (names, ret)
        (0, ["A", "B"]),  # (ret, names) con lista
        (["A", "B"], 0),
        (("A", "B"),),  # (names,)
        ("A", "B"),  # tupla de str, sin ret
        ["A", "B"],  # lista de str, sin ret
    ],
)
def test_name_list_acepta_todas_las_formas(respuesta):
    assert _name_list(respuesta, "X.GetNameList") == NOMBRES


@pytest.mark.parametrize(
    "respuesta",
    [0, (0,), (0, 0, (), ()), (0, 0, ()), ((), 0), (0, None, 0), ([], 0)],
)
def test_name_list_vacia_no_es_error(respuesta):
    assert _name_list(respuesta, "X.GetNameList") == []


@pytest.mark.parametrize("respuesta", [(2, ("A", "B"), 1), (1, 2, ("A", "B")), 3, (("A",), 7)])
def test_name_list_con_ret_distinto_de_cero_sigue_siendo_error(respuesta):
    with pytest.raises(RuntimeError, match="returned"):
        _name_list(respuesta, "X.GetNameList")


@pytest.mark.parametrize("respuesta", [None, "texto", 1.5, (), [], (object(),), (object(), object())])
def test_name_list_forma_desconocida_levanta_la_excepcion_de_forma(respuesta):
    with pytest.raises(UnexpectedNameListResponse, match="Unexpected SAP2000 response"):
        _name_list(respuesta, "X.GetNameList")


class Miembro:
    """GetNameList con una respuesta por firma (``args`` -> respuesta o excepción)."""

    def __init__(self, respuestas, legacy=None):
        self.respuestas = respuestas
        self.llamadas = []
        if legacy is not None:
            self.GetNameList_1 = lambda *a: legacy

    def GetNameList(self, *args):
        args = tuple(
            tuple(a) if isinstance(a, list) else a for a in args
        )  # las listas ByRef no son hashables
        self.llamadas.append(args)
        r = self.respuestas.get(args, TypeError("firma no admitida"))
        if isinstance(r, Exception):
            raise r
        return r


def test_respuesta_inesperada_ya_no_aborta_y_se_prueba_la_siguiente_firma():
    # Antes: GetNameList() devolvía algo sin forma conocida → RuntimeError y nunca se probaba (0, ()).
    m = Miembro({(): None, (0, ()): (2, ("A", "B"), 0)})
    assert _get_name_list(m, "PropFrame.GetNameList") == NOMBRES
    assert m.llamadas == [(), (0, ())]


def test_propframe_prueba_tambien_la_firma_con_tipo_all():
    m = Miembro({(): "x", (0, ()): 5.5, (0, (), 0): (0, 2, ("A", "B"))})
    assert _get_name_list(m, "PropFrame.GetNameList") == NOMBRES
    assert m.llamadas == [(), (0, ()), (0, (), 0)]


def test_la_firma_con_tipo_all_solo_se_prueba_en_propframe():
    m = Miembro({(): None, (0, ()): None})
    with pytest.raises(RuntimeError):
        _get_name_list(m, "PropMaterial.GetNameList")
    assert (0, (), 0) not in m.llamadas


def test_cae_a_getnamelist_1_si_las_demas_fallan():
    m = Miembro({(): None, (0, ()): None, (0, (), 0): None}, legacy=(2, ("A", "B"), 0))
    assert _get_name_list(m, "PropFrame.GetNameList") == NOMBRES


def test_preferred_args_se_prueban_primero():
    m = Miembro({(0, ()): None, (): None, (1,): (2, ("A", "B"), 0)})
    assert _get_name_list(m, "GroupDef.GetNameList", 1) == NOMBRES
    assert m.llamadas[0] == (1,)


def test_si_fallan_todas_el_error_lleva_tipo_y_repr_truncado_de_la_ultima_respuesta():
    larga = "x" * 500
    m = Miembro({(): None, (0, ()): None, (0, (), 0): larga})
    with pytest.raises(RuntimeError) as info:
        _get_name_list(m, "PropFrame.GetNameList")
    msg = str(info.value)
    assert "SAP2000 rejected supported arguments for PropFrame.GetNameList" in msg
    assert "última respuesta: str 'xxx" in msg
    assert len(msg.split("última respuesta: ")[1]) <= 205  # repr truncado a ~200 caracteres


def test_si_todas_lanzan_typeerror_se_informa_que_no_hubo_respuesta():
    with pytest.raises(RuntimeError, match="ninguna"):
        _get_name_list(Miembro({}), "PropFrame.GetNameList")


def test_un_ret_distinto_de_cero_aborta_sin_seguir_probando():
    m = Miembro({(): (2, ("A", "B"), 1), (0, ()): (2, ("A", "B"), 0)})
    with pytest.raises(RuntimeError, match="returned 1"):
        _get_name_list(m, "PropFrame.GetNameList")
    assert m.llamadas == [()]
