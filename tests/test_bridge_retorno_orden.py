"""El código de retorno de SAP2000 puede venir al final (comtypes) o al inicio (pywin32).

Cada lectura del puente debe dar el mismo resultado con ambos órdenes. Se reutilizan los
dobles COM de las pruebas de cada módulo envueltos en un proxy que mueve el código al inicio.
"""

from __future__ import annotations

import pytest

from bridge import actions
from bridge.espectro import get_modal_periods_at_max_mass, list_load_cases
from bridge.hormigon import get_frame_forces, get_frames, list_combos
from tests.test_bridge_esbelteces import _RetFirst
from tests.test_bridge_espectro import FakeModel as EspectroModel
from tests.test_bridge_hormigon import FakeModel as HormigonModel


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        ((2, ("A", "B"), 0), (0, [2, ("A", "B")])),  # comtypes
        ((0, 2, ("A", "B")), (0, [2, ("A", "B")])),  # pywin32
        (("W1", "", 0), (0, ["W1", ""])),
        ((0, "W1", ""), (0, ["W1", ""])),
        ((3, 1), (1, [3])),  # ambiguo: ambos enteros y sin 0 inicial, el código va al final
        ((0, 5), (0, [5])),  # ambiguo: 0 al inicio y no al final, el código va al inicio
        (7, (7, [])),
    ],
)
def test_split_return_infiere_el_lado_del_codigo(raw, esperado):
    assert actions.split_return(raw) == esperado


def test_split_return_con_pista_resuelve_el_caso_ambiguo():
    assert actions.split_return((0, 2, 0), ret_first=True) == (0, [2, 0])
    assert actions.split_return((2, 0, 0), ret_first=False) == (0, [2, 0])


def test_espectro_load_cases_y_modal_con_codigo_al_inicio():
    kwargs = dict(
        load_cases=["D", "MODAL1"],
        case_types={"MODAL1": 3, "D": 1},
        modal={"MODAL1": ([0.3, 0.2], [0.1, 0.8], [0.7, 0.2])},
    )
    normal, invertido = EspectroModel(**kwargs), EspectroModel(**kwargs)

    def modal_real(*_args):
        # Salidas reales: n y arreglos (el doble de espectro rellena con enteros).
        return (2, (), (), (), (0.3, 0.2), (0.1, 0.8), (0.7, 0.2), (), (), (), (), (), (), (), (), (), 0)

    normal.Results.ModalParticipatingMassRatios = modal_real
    invertido.Results.ModalParticipatingMassRatios = lambda *a: (0, *modal_real()[:-1])
    invertido = _RetFirst(invertido)
    assert list_load_cases(invertido) == list_load_cases(normal) == ["D", "MODAL1"]
    assert list_load_cases(invertido, only_modal=True) == ["MODAL1"]
    assert get_modal_periods_at_max_mass(invertido, "MODAL1") == get_modal_periods_at_max_mass(
        normal, "MODAL1"
    )


def test_hormigon_frames_combos_y_esfuerzos_con_codigo_al_inicio():
    normal, invertido = HormigonModel(), _RetFirst(HormigonModel())
    assert list_combos(invertido) == list_combos(normal)
    assert [(f["nombre"], f["longitud_m"]) for f in get_frames(invertido)] == [
        (f["nombre"], f["longitud_m"]) for f in get_frames(normal)
    ]
    assert get_frames(invertido)[0]["seccion"] == "V30x60"
    assert get_frame_forces(invertido, "B1", ["C1", "C2"]) == get_frame_forces(normal, "B1", ["C1", "C2"])
