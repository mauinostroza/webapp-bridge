"""Escritor IFC (STEP, ISO 10303-21) propio, en Python puro y sin ``ifcopenshell``.

``bridge/cad_ifc.py`` construye el IFC4 con esta clase. Solo escribe: no lee IFC ajenos ni calcula geometría (la geometría
la calcula el modelo ``estructura-v1`` antes). Los datos del esquema (orden de los atributos, enumeraciones, tipos) salen de
``bridge/ifc4_esquema.py``, generado desde el esquema IFC4 por ``scripts/generar_esquema_ifc4.py``.

Uso::

    f = ArchivoStep()
    p = f.crear_entidad("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    nombre = f.crear_entidad("IfcLabel", "Pilar")      # tipo definido: devuelve un ``Valor`` (se escribe en línea)
    texto = f.a_texto()                                # ASCII puro

Reglas de escritura (las del IFC4 / STEP edición 2):

* reales con punto y ``E`` (``1.E-05``), enteros sin punto; un ``int`` en un atributo REAL se escribe como real;
* cadenas ASCII; ``'`` y ``\\`` duplicados y todo carácter fuera de 0x20-0x7E como ``\\X2\\…\\X0\\`` (``\\X4\\…\\X0\\`` por
  encima de U+FFFF);
* valores de un SELECT envueltos (``IFCLABEL('x')``); atributos derivados como ``*``; ausentes como ``$``.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Any

from bridge import ifc4_esquema as _esq

_ENTIDADES = _esq.ENTIDADES
_TIPOS = _esq.TIPOS_DEFINIDOS
_ENUMS = _esq.ENUMERACIONES
_SELECTS = _esq.SELECTS


class ErrorEscritor(RuntimeError):
    """El IFC que se intenta escribir no respeta el esquema IFC4 (error de programación, no del modelo del usuario)."""


# ------------------------------------------------------------------ GUID ----

_BASE64_IFC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"


def _b64(valor: int, n: int) -> str:
    return "".join(_BASE64_IFC[(valor // 64**i) % 64] for i in range(n))[::-1]


def comprimir_guid(hex32: str) -> str:
    """GlobalId de IFC (22 caracteres) a partir de un UUID en hexadecimal (32 dígitos)."""
    b = bytes.fromhex(hex32)
    if len(b) != 16:
        raise ErrorEscritor(f"Un GlobalId se comprime desde 16 bytes, no {len(b)}")
    return _b64(b[0], 2) + "".join(
        _b64((b[i] << 16) | (b[i + 1] << 8) | b[i + 2], 4) for i in range(1, 16, 3)
    )


def guid_determinista(espacio: uuid.UUID, *partes: object) -> str:
    """GlobalId estable: ``uuid5`` del espacio de nombres y de las partes que identifican la entidad."""
    return comprimir_guid(uuid.uuid5(espacio, "/".join(str(p) for p in partes)).hex)


# ----------------------------------------------------------- formato STEP ---


def formato_real(x: float) -> str:
    """Real STEP: siempre con punto y, si lleva exponente, con ``E`` (``1.E-05``)."""
    x = float(x)
    if not math.isfinite(x):
        raise ErrorEscritor(f"Un real IFC no puede ser {x}")
    if x == 0.0:
        return "0."
    r = repr(x)
    if "e" in r:
        mantisa, exponente = r.split("e")
        if "." not in mantisa:
            mantisa += "."
        return f"{mantisa}E{exponente}"
    return r[:-1] if r.endswith(".0") else r


def formato_cadena(texto: str) -> str:
    """Cadena STEP entre apóstrofos, en ASCII puro (``\\X2\\`` y ``\\X4\\`` para lo demás)."""
    salida: list[str] = []
    bmp: list[int] = []
    amplios: list[int] = []

    def vaciar() -> None:
        if bmp:
            salida.append("\\X2\\" + "".join(f"{c:04X}" for c in bmp) + "\\X0\\")
            bmp.clear()
        if amplios:
            salida.append("\\X4\\" + "".join(f"{c:08X}" for c in amplios) + "\\X0\\")
            amplios.clear()

    for ch in texto:
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            vaciar()
            salida.append("''" if ch == "'" else "\\\\" if ch == "\\" else ch)
        elif 0xD800 <= o <= 0xDFFF:  # sustituto suelto: no es un carácter válido
            vaciar()
            salida.append("?")
        elif o <= 0xFFFF:
            if amplios:
                vaciar()
            bmp.append(o)
        else:
            if bmp:
                vaciar()
            amplios.append(o)
    vaciar()
    return "'" + "".join(salida) + "'"


# --------------------------------------------------------- tipos y valores --


def _resolver(tipo: tuple) -> tuple:
    """Sigue los tipos definidos (``DEF``) hasta su tipo base."""
    while tipo[0] == "DEF":
        try:
            tipo = _TIPOS[tipo[1]]
        except KeyError as exc:
            raise ErrorEscritor(f"Tipo IFC desconocido: {tipo[1]}") from exc
    return tipo


class Valor:
    """Valor de un tipo definido de IFC (``IfcLabel``, ``IfcLineIndex``…); se escribe en línea, sin ``#id``."""

    __slots__ = ("dato", "tipo")

    def __init__(self, tipo: str, dato: Any):
        object.__setattr__(self, "tipo", tipo)
        object.__setattr__(self, "dato", dato)

    def __setattr__(self, nombre: str, valor: Any) -> None:
        raise AttributeError("Valor es inmutable")

    def __repr__(self) -> str:
        return f"Valor({self.tipo}, {self.dato!r})"


class Entidad:
    """Entidad IFC creada en un ``ArchivoStep``; los atributos se leen y se asignan por su nombre IFC."""

    __slots__ = ("_archivo", "_id", "_tipo", "_vals")

    def __init__(self, archivo: ArchivoStep, tipo: str, ident: int, vals: list):
        object.__setattr__(self, "_archivo", archivo)
        object.__setattr__(self, "_tipo", tipo)
        object.__setattr__(self, "_id", ident)
        object.__setattr__(self, "_vals", vals)

    def id(self) -> int:
        return self._id

    def is_a(self, tipo: str | None = None) -> str | bool:
        """Sin argumento, el nombre del tipo; con él, si la entidad es de ese tipo o de un subtipo suyo."""
        if tipo is None:
            return self._tipo
        actual: str | None = self._tipo
        while actual is not None:
            if actual == tipo:
                return True
            actual = _ENTIDADES[actual][0] if actual in _ENTIDADES else None
        return False

    def __getattr__(self, nombre: str) -> Any:
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        indice = _indices(self._tipo).get(nombre)
        if indice is None:
            raise AttributeError(f"{self._tipo} no tiene el atributo {nombre}")
        valor = self._vals[indice]
        return list(valor) if isinstance(valor, tuple) else valor

    def __setattr__(self, nombre: str, valor: Any) -> None:
        indice = _indices(self._tipo).get(nombre)
        if indice is None:
            raise ErrorEscritor(f"{self._tipo} no tiene el atributo {nombre}")
        self._vals[indice] = self._archivo._validar_atributo(self._tipo, indice, valor)

    def __repr__(self) -> str:
        return f"#{self._id}={self._tipo}"


_CACHE_INDICES: dict[str, dict[str, int]] = {}


def _atributos(tipo: str) -> tuple:
    return _ENTIDADES[tipo][2]


def _indices(tipo: str) -> dict[str, int]:
    if tipo not in _CACHE_INDICES:
        _CACHE_INDICES[tipo] = {a[0]: i for i, a in enumerate(_atributos(tipo))}
    return _CACHE_INDICES[tipo]


# ----------------------------------------------------------------- archivo --


class ArchivoStep:
    """Un archivo IFC4 en construcción: crea entidades, comprueba cada valor contra el esquema y lo serializa."""

    def __init__(self) -> None:
        self._entidades: list[Entidad] = []
        self._cabecera: dict[str, Any] = {}

    # creación ---------------------------------------------------------------
    def crear_entidad(self, tipo: str, *args: Any, **kw: Any) -> Entidad | Valor:
        """Crea una entidad (devuelve ``Entidad``) o un valor de tipo definido (devuelve ``Valor``).

        Los atributos van por posición (en el orden del esquema) o por nombre. Los derivados no se pasan.
        """
        if tipo in _TIPOS and tipo not in _ENTIDADES:
            if len(args) != 1 or kw:
                raise ErrorEscritor(f"{tipo} es un tipo definido: se crea con un único valor")
            return Valor(tipo, self._validar_tipo(args[0], ("DEF", tipo), tipo))
        if tipo not in _ENTIDADES:
            raise ErrorEscritor(f"Entidad IFC desconocida o no incluida en el esquema del puente: {tipo}")
        _, abstracta, atributos = _ENTIDADES[tipo]
        if abstracta or not atributos:
            raise ErrorEscritor(f"{tipo} es abstracta: no se puede instanciar")
        if len(args) > len(atributos):
            raise ErrorEscritor(f"{tipo} tiene {len(atributos)} atributos y se pasaron {len(args)}")
        indices = _indices(tipo)
        valores: list[Any] = [None] * len(atributos)
        for i, v in enumerate(args):
            valores[i] = v
        for nombre, v in kw.items():
            if nombre not in indices:
                raise ErrorEscritor(f"{tipo} no tiene el atributo {nombre}")
            if indices[nombre] < len(args):
                raise ErrorEscritor(f"{tipo}.{nombre} se pasó por posición y por nombre")
            valores[indices[nombre]] = v
        valores = [self._validar_atributo(tipo, i, v) for i, v in enumerate(valores)]
        entidad = Entidad(self, tipo, len(self._entidades) + 1, valores)
        self._entidades.append(entidad)
        return entidad

    create_entity = crear_entidad  # nombre de la API de ifcopenshell, para el código que ya lo usa

    # validación ---------------------------------------------------------------
    def _validar_atributo(self, tipo: str, indice: int, valor: Any) -> Any:
        nombre, expr, _opcional, derivado = _atributos(tipo)[indice]
        if derivado:
            if valor is not None:
                raise ErrorEscritor(f"{tipo}.{nombre} es un atributo derivado: no se asigna")
            return None
        if valor is None:
            return None
        return self._validar_tipo(valor, expr, f"{tipo}.{nombre}")

    def _validar_tipo(self, valor: Any, expr: tuple, donde: str) -> Any:
        clase = expr[0]
        if clase == "DEF":
            if isinstance(valor, Valor):
                if valor.tipo != expr[1]:
                    raise ErrorEscritor(f"{donde}: se esperaba {expr[1]} y llegó {valor.tipo}")
                return valor.dato
            return self._validar_tipo(valor, _resolver(expr), donde)
        if clase == "SIMPLE":
            return _validar_simple(valor, expr[1], donde)
        if clase == "ENUM":
            if not isinstance(valor, str) or valor not in _ENUMS[expr[1]]:
                raise ErrorEscritor(f"{donde}: {valor!r} no es un valor de {expr[1]}")
            return valor
        if clase == "ENT":
            return self._validar_entidad(valor, (expr[1],), donde)
        if clase == "SELECT":
            miembros = _SELECTS[expr[1]]
            if isinstance(valor, Valor):
                if valor.tipo not in miembros:
                    raise ErrorEscritor(f"{donde}: {valor.tipo} no es un valor admitido por {expr[1]}")
                return valor
            return self._validar_entidad(valor, miembros, donde)
        # listas: (clase, mínimo, máximo, tipo_del_elemento)
        if isinstance(valor, (str, bytes)) or not hasattr(valor, "__iter__"):
            raise ErrorEscritor(f"{donde}: se esperaba una lista y llegó {valor!r}")
        elementos = tuple(valor)
        minimo, maximo, elemento = expr[1], expr[2], expr[3]
        if len(elementos) < minimo or (maximo is not None and len(elementos) > maximo):
            raise ErrorEscritor(
                f"{donde}: {len(elementos)} elementos fuera del rango [{minimo}:{maximo or '?'}]"
            )
        return tuple(self._validar_tipo(e, elemento, donde) for e in elementos)

    def _validar_entidad(self, valor: Any, admitidos: tuple[str, ...], donde: str) -> Entidad:
        if not isinstance(valor, Entidad):
            raise ErrorEscritor(
                f"{donde}: se esperaba una entidad ({', '.join(admitidos)}) y llegó {valor!r}"
            )
        if valor._archivo is not self:
            raise ErrorEscritor(f"{donde}: la entidad {valor!r} es de otro archivo")
        conocidos = [a for a in admitidos if a in _ENTIDADES]
        if conocidos and len(conocidos) == len(admitidos) and not any(valor.is_a(a) for a in conocidos):
            raise ErrorEscritor(f"{donde}: {valor._tipo} no es {' ni '.join(admitidos)}")
        return valor

    def admite_enum(self, entidad: str, atributo: str, valor: str) -> bool:
        """True si ``valor`` existe en la enumeración del atributo (p. ej. el ``PredefinedType`` de una clase)."""
        if entidad not in _ENTIDADES:
            raise ErrorEscritor(f"Entidad IFC desconocida: {entidad}")
        indice = _indices(entidad).get(atributo)
        if indice is None:
            raise ErrorEscritor(f"{entidad} no tiene el atributo {atributo}")
        expr = _atributos(entidad)[indice][1]
        if expr[0] != "ENUM":
            raise ErrorEscritor(f"{entidad}.{atributo} no es una enumeración")
        return valor in _ENUMS[expr[1]]

    # cabecera y serialización ---------------------------------------------------
    def cabecera(
        self,
        *,
        descripcion: tuple[str, ...] = ("ViewDefinition [ReferenceView_V1.2]",),
        nombre: str = "",
        marca_tiempo: str | None = None,
        sistema: str = "",
        preprocesador: str = "bridge.ifc_step",
    ) -> None:
        self._cabecera = {
            "descripcion": tuple(descripcion),
            "nombre": nombre,
            "marca_tiempo": marca_tiempo or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sistema": sistema,
            "preprocesador": preprocesador,
        }

    def a_texto(self) -> str:
        """El archivo STEP completo, en ASCII puro."""
        if not self._cabecera:
            self.cabecera()
        c = self._cabecera
        descripcion = ",".join(formato_cadena(d) for d in c["descripcion"])
        lineas = [
            "ISO-10303-21;",
            "HEADER;",
            f"FILE_DESCRIPTION(({descripcion}),'2;1');",
            "FILE_NAME("
            f"{formato_cadena(c['nombre'])},{formato_cadena(c['marca_tiempo'])},(''),(''),"
            f"{formato_cadena(c['preprocesador'])},{formato_cadena(c['sistema'])},'');",
            "FILE_SCHEMA(('IFC4'));",
            "ENDSEC;",
            "DATA;",
        ]
        for e in self._entidades:
            lineas.append(self._linea(e))
        lineas += ["ENDSEC;", "END-ISO-10303-21;", ""]
        texto = "\n".join(lineas)
        texto.encode("ascii")  # garantía: el escritor nunca produce bytes fuera de ASCII
        return texto

    def _linea(self, e: Entidad) -> str:
        partes: list[str] = []
        for (nombre, expr, opcional, derivado), valor in zip(_atributos(e._tipo), e._vals, strict=True):
            if derivado:
                partes.append("*")
            elif valor is None:
                if not opcional:
                    raise ErrorEscritor(f"{e._tipo} #{e._id}: falta el atributo obligatorio {nombre}")
                partes.append("$")
            else:
                partes.append(_texto(valor, expr))
        return f"#{e._id}={e._tipo.upper()}({','.join(partes)});"


def _validar_simple(valor: Any, tipo: str, donde: str) -> Any:
    if tipo == "REAL" or tipo == "NUMBER":
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ErrorEscritor(f"{donde}: se esperaba un número y llegó {valor!r}")
        if tipo == "REAL" or isinstance(valor, float):
            if not math.isfinite(valor):
                raise ErrorEscritor(f"{donde}: un real IFC no puede ser {valor}")
            return float(valor)
        return int(valor)
    if tipo == "INTEGER":
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ErrorEscritor(f"{donde}: se esperaba un entero y llegó {valor!r}")
        return valor
    if tipo == "STRING":
        if not isinstance(valor, str):
            raise ErrorEscritor(f"{donde}: se esperaba un texto y llegó {valor!r}")
        return valor
    if tipo in ("BOOLEAN", "LOGICAL"):
        if not isinstance(valor, bool):
            raise ErrorEscritor(f"{donde}: se esperaba un booleano y llegó {valor!r}")
        return valor
    raise ErrorEscritor(f"{donde}: tipo simple no soportado: {tipo}")


def _texto(valor: Any, expr: tuple) -> str:
    """Texto STEP de un valor ya validado."""
    clase = expr[0]
    if clase == "DEF":
        return _texto(valor, _resolver(expr))
    if clase == "SIMPLE":
        tipo = expr[1]
        if tipo == "REAL" or (tipo == "NUMBER" and isinstance(valor, float)):
            return formato_real(valor)
        if tipo in ("INTEGER", "NUMBER"):
            return str(valor)
        if tipo == "STRING":
            return formato_cadena(valor)
        return ".T." if valor else ".F."
    if clase == "ENUM":
        return f".{valor}."
    if clase in ("ENT", "SELECT"):
        if isinstance(valor, Valor):
            return f"{valor.tipo.upper()}({_texto(valor.dato, ('DEF', valor.tipo))})"
        return f"#{valor._id}"
    elemento = expr[3]
    return "(" + ",".join(_texto(e, elemento) for e in valor) + ")"
