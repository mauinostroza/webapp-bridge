"""Pruebas de la parte pura de las ventanas de la bandeja (sin Win32).

Cubre el banner de arranque, la ventana de estado rediseñada, el anclaje a la
esquina de la pantalla y la revocación múltiple de conexiones. Todo el código
Win32 que dibuja estas ventanas sólo se puede comprobar en Windows real; aquí
se prueban las funciones que deciden qué se dibuja.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from bridge import tray

SCALES = [1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _inside(rect, layout):
    x, y, w, h = rect
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= layout["width"] and y + h <= layout["height"]


def _check_no_overlap_inside(layout, names):
    for name in names:
        assert _inside(layout[name], layout), name
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not _overlap(layout[a], layout[b]), (a, b)


# --- anclar_esquina / centrar_en_area ----------------------------------------


def test_anclar_esquina_pantalla_simple_con_margen():
    # Área de trabajo de un 1920x1080 con barra de tareas de 40 px abajo.
    area = (0, 0, 1920, 1040)
    assert tray.anclar_esquina(area, 500, 300, 12) == (1920 - 500 - 12, 1040 - 300 - 12)


def test_anclar_esquina_barra_de_tareas_arriba_no_empieza_en_cero():
    # Barra superior de 40 px: el área de trabajo empieza en y=40.
    area = (0, 40, 1920, 1080)
    x, y = tray.anclar_esquina(area, 500, 300, 12)
    assert (x, y) == (1920 - 512, 1080 - 312)
    assert y >= 40


def test_anclar_esquina_barra_de_tareas_izquierda_desplaza_x():
    # Barra izquierda de 60 px: el área empieza en x=60; la ventana sigue en la esquina derecha.
    area = (60, 0, 1920, 1080)
    x, y = tray.anclar_esquina(area, 500, 300, 12)
    assert (x, y) == (1920 - 512, 1080 - 312)
    assert x >= 60


def test_anclar_esquina_monitor_con_coordenadas_negativas():
    # Monitor a la izquierda del principal: su área va de x=-1920 a x=0.
    area = (-1920, 0, 0, 1080)
    assert tray.anclar_esquina(area, 400, 200, 12) == (0 - 400 - 12, 1080 - 200 - 12)


def test_anclar_esquina_ventana_mas_grande_que_el_area_se_alinea_arriba_izquierda():
    area = (100, 50, 400, 300)  # 300 x 250
    assert tray.anclar_esquina(area, 800, 600, 12) == (100, 50)


def test_anclar_esquina_ventana_justo_del_tamano_del_area():
    area = (10, 20, 310, 220)  # 300 x 200
    assert tray.anclar_esquina(area, 300, 200, 0) == (10, 20)


def test_anclar_esquina_margen_cero_pega_al_borde():
    area = (0, 0, 800, 600)
    assert tray.anclar_esquina(area, 100, 50, 0) == (700, 550)


def test_centrar_en_area_centra_y_no_se_sale():
    area = (0, 0, 1920, 1040)
    assert tray.centrar_en_area(area, 480, 270) == ((1920 - 480) // 2, (1040 - 270) // 2)


def test_centrar_en_area_mas_grande_que_el_area_queda_arriba_izquierda():
    assert tray.centrar_en_area((50, 60, 150, 160), 400, 400) == (50, 60)


# --- splash_layout / splash_text / splash_should_close -----------------------


@pytest.mark.parametrize("scale", SCALES)
def test_splash_layout_sin_solapes_y_dentro(scale):
    _check_no_overlap_inside(tray.splash_layout(scale), ["icon", "title", "subtitle", "version"])


def test_splash_layout_tamanos_base_a_100_por_ciento():
    layout = tray.splash_layout(1.0)
    assert (layout["width"], layout["height"]) == (480, 270)


def test_splash_layout_escala_monotona():
    sizes = [(tray.splash_layout(s)["width"], tray.splash_layout(s)["height"]) for s in SCALES]
    assert sizes == sorted(sizes)
    assert len(set(sizes)) == len(sizes)


def test_splash_text_con_version():
    text = tray.splash_text("1.4.2")
    assert text == {
        "title": "SAP2000 Bridge",
        "subtitle": "Abriendo el puente…",
        "version": "Versión 1.4.2",
    }


def test_splash_text_sin_version_deja_la_linea_vacia():
    for version in (None, "", "   "):
        assert tray.splash_text(version)["version"] == ""


def test_splash_should_close_al_cumplirse_la_duracion():
    assert tray.splash_should_close(2500, ready=False) is True
    assert tray.splash_should_close(3000, ready=False) is True


def test_splash_should_close_antes_de_tiempo_sin_ready_sigue_abierto():
    assert tray.splash_should_close(0, ready=False) is False
    assert tray.splash_should_close(2499.9, ready=False) is False


def test_splash_should_close_cuando_el_puente_escucha_antes_de_los_2_5_s():
    assert tray.splash_should_close(1500, ready=True) is True
    assert tray.splash_should_close(2000, ready=True) is True


def test_splash_se_ve_al_menos_el_tiempo_minimo_aunque_el_puente_ya_escuche():
    assert tray.splash_should_close(0, ready=True) is False
    assert tray.splash_should_close(300, ready=True) is False
    assert tray.splash_should_close(1499, ready=True) is False


def test_splash_should_close_duracion_configurable():
    assert tray.splash_should_close(100, ready=False, duration_ms=100) is True
    assert tray.splash_should_close(50, ready=True, duration_ms=100, min_visible_ms=50) is True
    assert tray.splash_should_close(99, ready=False, duration_ms=100) is False


def test_splash_duration_es_la_documentada():
    assert tray.SPLASH_DURATION_MS == 2500


# --- status_rows / status_indicator / status_layout --------------------------

LABELS = list(tray.STATUS_LABELS)


def _values(rows):
    return dict(rows)


def test_status_rows_conectado_con_todos_los_datos():
    status = {"connected": True, "model_path": "C:\\m\\edificio.sdb", "version": "25.0.0"}
    rows = tray.status_rows(status, port=8765, paired_count=2, updated_at="12:03:45")
    assert [label for label, _ in rows] == LABELS
    assert _values(rows) == {
        "Estado": "Conectado",
        "Puerto": "8765",
        "Modelo abierto": "C:\\m\\edificio.sdb",
        "Versión SAP2000": "25.0.0",
        "Conexiones emparejadas": "2",
        "Última actualización": "12:03:45",
    }


def test_status_rows_sin_conexion_sin_motivo_muestra_guiones():
    rows = _values(tray.status_rows({"connected": False}, port=8765, paired_count=0, updated_at="09:00:00"))
    assert rows["Estado"] == "Sin conexión"
    assert rows["Modelo abierto"] == "—"
    assert rows["Versión SAP2000"] == "—"
    assert rows["Conexiones emparejadas"] == "0"


def test_status_rows_con_motivo_de_fallo():
    rows = _values(
        tray.status_rows(
            {"connected": False, "reason": "COM connection lost"},
            port=8765,
            paired_count=1,
            updated_at="10:00:00",
        )
    )
    assert rows["Estado"] == "Sin conexión (COM connection lost)"


def test_status_rows_conectado_sin_ruta_de_modelo():
    rows = _values(tray.status_rows({"connected": True}, port=8765, paired_count=1, updated_at="x"))
    assert rows["Modelo abierto"] == "(sin ruta)"
    assert rows["Versión SAP2000"] == "—"


def test_status_rows_con_error_capturado():
    # Excepción genérica: no importamos bridge.sap2000_agent (exige SAP2000_AGENT_TOKEN).
    rows = _values(
        tray.status_rows(
            None,
            RuntimeError("SAP2000 COM agent requires Windows."),
            port=8765,
            paired_count=3,
            updated_at="11:11:11",
        )
    )
    assert rows["Estado"] == "Error al consultar el estado (SAP2000 COM agent requires Windows.)"
    assert rows["Modelo abierto"] == "—"
    assert rows["Versión SAP2000"] == "—"
    # Puerto, emparejadas y hora siguen mostrándose aunque falle SAP2000.
    assert rows["Puerto"] == "8765"
    assert rows["Conexiones emparejadas"] == "3"


def test_status_rows_con_none_y_sin_error():
    rows = _values(tray.status_rows(None, port=8765, paired_count=None, updated_at=None))
    assert rows["Estado"] == "Error al consultar el estado"
    assert rows["Conexiones emparejadas"] == "—"
    assert rows["Última actualización"] == "—"


def test_status_rows_siempre_seis_filas():
    cases = [
        ({"connected": True, "model_path": "x"}, None),
        ({"connected": False}, None),
        (None, None),
        (None, RuntimeError("boom")),
    ]
    for status, error in cases:
        rows = tray.status_rows(status, error, port=1, paired_count=None, updated_at=None)
        assert len(rows) == 6


def test_status_indicator_verde_si_conectado():
    assert tray.status_indicator({"connected": True, "model_path": "x"}) == "verde"


def test_status_indicator_ambar_si_sin_conexion_sin_fallo():
    assert tray.status_indicator({"connected": False}) == "ambar"


def test_status_indicator_rojo_si_hay_motivo_de_fallo():
    assert tray.status_indicator({"connected": False, "reason": "COM connection lost"}) == "rojo"


def test_status_indicator_rojo_con_error_o_none():
    assert tray.status_indicator(None) == "rojo"
    assert tray.status_indicator({"connected": True}, RuntimeError("x")) == "rojo"
    assert tray.status_indicator(None, RuntimeError("x")) == "rojo"


def test_status_indicator_colores_definidos_para_cada_nombre():
    for name in ("verde", "ambar", "rojo"):
        assert name in tray.STATUS_INDICATOR_RGB
        r, g, b = tray.STATUS_INDICATOR_RGB[name]
        assert all(0 <= c <= 255 for c in (r, g, b))


def test_hora_actualizacion_formato():
    stamp = 1_700_000_000.0
    assert tray.hora_actualizacion(stamp) == time.strftime("%H:%M:%S", time.localtime(stamp))
    value = tray.hora_actualizacion(stamp)
    assert len(value) == 8 and value[2] == ":" and value[5] == ":"


@pytest.mark.parametrize("scale", SCALES)
def test_status_layout_sin_solapes_y_dentro(scale):
    layout = tray.status_layout(scale)
    names = ["icon_small", "title", "divider", "panel", "label_column", "value_column", "footer"]
    _check_no_overlap_inside(layout, names)


@pytest.mark.parametrize("scale", SCALES)
def test_status_watermark_dentro_del_panel(scale):
    layout = tray.status_layout(scale)
    px, py, pw, ph = layout["panel"]
    wx, wy, ww, wh = layout["watermark"]
    assert wx >= px and wy >= py and wx + ww <= px + pw and wy + wh <= py + ph


@pytest.mark.parametrize("scale", SCALES)
def test_status_value_column_tiene_seis_filas(scale):
    layout = tray.status_layout(scale)
    _, _, _, height = layout["value_column"]
    assert layout["row_height"] * len(LABELS) <= height + len(LABELS)


def test_status_layout_tamano_base():
    layout = tray.status_layout(1.0)
    assert (layout["width"], layout["height"]) == (520, 340)


# --- revocación múltiple ------------------------------------------------------

ROWS = [
    {"id": "a", "label": "Mi equipo", "origin": "http://localhost:5173"},
    {"id": "b", "label": "", "origin": "https://otro.example"},
    {"id": "c", "label": "Portátil", "origin": "https://tercero.example"},
]


def test_pairing_indices_to_revoke_todas():
    assert tray.pairing_indices_to_revoke(ROWS, [0, 1, 2]) == [0, 1, 2]
    assert tray.pairing_ids_to_revoke(ROWS, [0, 1, 2]) == ["a", "b", "c"]


def test_pairing_indices_to_revoke_ninguna():
    assert tray.pairing_indices_to_revoke(ROWS, []) == []
    assert tray.pairing_ids_to_revoke(ROWS, []) == []


def test_pairing_indices_to_revoke_algunas_en_orden_de_fila():
    # El orden de llegada de los índices no importa: se devuelve en orden de fila.
    assert tray.pairing_ids_to_revoke(ROWS, [2, 0]) == ["a", "c"]


def test_pairing_indices_to_revoke_ignora_fuera_de_rango_y_negativos():
    assert tray.pairing_indices_to_revoke(ROWS, [-1, 3, 99, 1]) == [1]


def test_pairing_indices_to_revoke_ignora_duplicados():
    assert tray.pairing_indices_to_revoke(ROWS, [0, 0, 0]) == [0]


def test_pairing_indices_to_revoke_ignora_placeholder_sin_id():
    placeholder = [{}]  # así queda la lista cuando no hay conexiones emparejadas
    assert tray.pairing_indices_to_revoke(placeholder, [0]) == []
    assert tray.pairing_ids_to_revoke(placeholder, [0]) == []


def test_pairing_indices_to_revoke_sin_filas():
    assert tray.pairing_indices_to_revoke([], [0, 1]) == []


def test_pairing_indices_to_revoke_ignora_no_enteros():
    assert tray.pairing_indices_to_revoke(ROWS, ["0", None, 1.0]) == []


def test_revoke_confirm_message_solo_para_varias():
    assert tray.revoke_confirm_message(0) is None
    assert tray.revoke_confirm_message(1) is None
    assert tray.revoke_confirm_message(2) == "¿Revocar 2 conexiones?"
    assert tray.revoke_confirm_message(5) == "¿Revocar 5 conexiones?"


# --- pairings_layout con los botones nuevos ----------------------------------


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("has_pairings", [True, False])
def test_pairings_layout_botones_nuevos_sin_solapes(scale, has_pairings):
    layout = tray.pairings_layout(scale, has_pairings)
    names = ["list", "select_all", "revoke"] + ([] if has_pairings else ["hint"])
    _check_no_overlap_inside(layout, names)


@pytest.mark.parametrize("scale", SCALES)
def test_pairings_layout_botones_en_la_misma_fila(scale):
    layout = tray.pairings_layout(scale, True)
    assert layout["select_all"][1] == layout["revoke"][1]
    assert layout["select_all"][0] < layout["revoke"][0]


# --- Anclaje a la esquina en la ventana (sin Win32) --------------------------


def test_anclar_esquina_acepta_tupla_de_area_tal_cual_del_monitor():
    # GetMonitorInfo()["Work"] devuelve (left, top, right, bottom); se usa tal cual.
    work = (0, 0, 1920, 1032)
    x, y = tray.anclar_esquina(work, 520, 340, 12)
    assert 0 <= x and 0 <= y and x + 520 <= 1920 - 12 + 1 and y + 340 <= 1032 - 12 + 1


# --- Módulo y capa Win32 ------------------------------------------------------


def test_tray_expone_las_funciones_nuevas():
    for name in (
        "anclar_esquina",
        "centrar_en_area",
        "splash_layout",
        "splash_text",
        "splash_should_close",
        "status_rows",
        "status_indicator",
        "hora_actualizacion",
        "pairing_indices_to_revoke",
        "pairing_ids_to_revoke",
        "revoke_confirm_message",
    ):
        assert callable(getattr(tray, name)), name


def test_show_splash_existe_y_es_metodo_de_trayapp():
    assert callable(tray.TrayApp.show_splash)


def test_pure_helpers_no_dependen_de_win32_al_importar():
    # Las funciones puras se llaman sin ningún símbolo Win32 disponible.
    request = SimpleNamespace(origin="https://x.example", confirm_code="123456")
    assert tray.pairing_dialog_fields(request)["code"] == "123456"
