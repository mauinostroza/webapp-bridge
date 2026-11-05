"""
Primitivas de dibujo CAD propias del puente.

Contiene SOLO lo que las rutas del puente (`bridge/cad_drwr.py`,
`bridge/cad_com_live.py`) necesitan para enviar un dibujo ya aplanado al CAD
vivo: capas con su color ACI, las clases de primitivas, `Drawing`,
`translate` e `inferir_th`. El puente no depende del motor de cálculo.

Unidades: milímetros, coordenadas X-Y matemáticas (Y hacia arriba).
La paridad con el motor se vigila en tests/test_cad_prims_paridad.py.
"""

from dataclasses import dataclass, field

PT = tuple[float, float]

# ---------------------------------------------------------------- capas ---
L_EJE = "EJE"  # líneas de eje (center lines)
L_CONC = "CONCRETO"  # contornos de concreto / placa / perfil
L_ACERO = "ACERO"  # barras de refuerzo, pernos
L_ACOT = "ACOTADO"  # cotas y flechas
L_TXT = "TEXTOS"  # rótulos, notas, etiquetas
L_PERF = "PERFORACIONES"  # perforaciones de placas
L_SOLD = "SOLDADURA"  # símbolos de soldadura
L_HACH = "HACHURADO"  # hachurado de concreto
L_TABLA = "TABLAS"  # cuadros / tablas
L_OCULTO = "OCULTO"  # líneas ocultas

# Colores ACI (AutoCAD Color Index) por capa
LAYER_COLORS = {
    L_EJE: 1,  # rojo
    L_CONC: 7,  # blanco
    L_ACERO: 2,  # amarillo
    L_ACOT: 3,  # verde
    L_TXT: 4,  # cian
    L_PERF: 5,  # azul
    L_SOLD: 30,  # naranja
    L_HACH: 8,  # gris
    L_TABLA: 7,  # blanco
    L_OCULTO: 8,  # gris
}

# ------------------------------------------------------------ primitivas ---


@dataclass
class Line:
    p1: PT
    p2: PT
    layer: str = L_CONC
    width: float = 0.0


@dataclass
class Circle:
    c: PT
    r: float
    layer: str = L_ACERO
    filled: bool = False


@dataclass
class Arc:
    c: PT
    r: float
    a1: float  # ángulo inicial (grados)
    a2: float  # ángulo final (grados)
    ccw: bool = True  # barrido antihorario de a1 -> a2
    layer: str = L_ACERO


@dataclass
class Poly:
    pts: list[PT]
    closed: bool = False
    layer: str = L_CONC
    width: float = 0.0


@dataclass
class Filled:
    """Polígono relleno: flechas de cota, puntos de barra, símbolos."""

    pts: list[PT]
    layer: str = L_ACOT


@dataclass
class Text:
    pos: PT
    s: str
    h: float  # altura (en mm de modelo, ya escalada)
    rot: float = 0.0  # grados antihorario
    layer: str = L_TXT
    ha: str = "c"  # 'l' | 'c' | 'r'
    va: str = "m"  # 'b' | 'm' | 't'


@dataclass
class Dim:
    """Cota lineal asociativa. p1/p2 sobre la geometría, `base` sobre la
    línea de cota. vertical=False -> cota horizontal."""

    p1: PT
    p2: PT
    base: PT
    vertical: bool = False
    layer: str = L_ACOT
    txt: str | None = None  # texto ya formateado (unidades incluidas)
    text_height: float = 0.0  # altura propia; 0 conserva lectura legacy


@dataclass
class Drawing:
    title: str = ""
    ents: list[object] = field(default_factory=list)


# ------------------------------------------------------------- utilidades ---


def translate(dwg: Drawing, dx: float, dy: float) -> Drawing:
    """Devuelve una copia de `dwg` con todas las entidades desplazadas
    (dx, dy) mm, para ubicar el dibujo en el punto elegido por el usuario
    (clic en pantalla dentro del CAD) antes de enviarlo."""

    def tp(p: PT) -> PT:
        return (p[0] + dx, p[1] + dy)

    def te(e):
        if isinstance(e, Line):
            return Line(tp(e.p1), tp(e.p2), e.layer, e.width)
        if isinstance(e, Circle):
            return Circle(tp(e.c), e.r, e.layer, e.filled)
        if isinstance(e, Arc):
            return Arc(tp(e.c), e.r, e.a1, e.a2, e.ccw, e.layer)
        if isinstance(e, Poly):
            return Poly([tp(p) for p in e.pts], e.closed, e.layer, e.width)
        if isinstance(e, Filled):
            return Filled([tp(p) for p in e.pts], e.layer)
        if isinstance(e, Text):
            return Text(tp(e.pos), e.s, e.h, e.rot, e.layer, e.ha, e.va)
        if isinstance(e, Dim):
            return Dim(tp(e.p1), tp(e.p2), tp(e.base), e.vertical, e.layer, e.txt, e.text_height)
        return e

    return Drawing(dwg.title, [te(e) for e in dwg.ents])


def inferir_th(dwg: Drawing) -> float:
    """Altura de texto de cota: la de la primera cota con altura propia, si
    no la del primer texto, si no 15 mm."""
    for e in dwg.ents:
        if isinstance(e, Dim) and e.text_height > 0:
            return e.text_height
    for e in dwg.ents:
        if isinstance(e, Text) and e.h > 0:
            return e.h
    return 15.0
