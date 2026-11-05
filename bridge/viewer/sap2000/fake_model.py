# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

from pathlib import Path
from typing import Any

_DEMO_LOAD_PATTERNS = ["Ds", "Dp", "EQX", "Dead", "Live"]


class FakeSapObject:
    def __init__(self) -> None:
        self.SapModel = FakeSapModel()

    def GetVersion(self) -> str:
        return "23.0-demo"


class FakeSapModel:
    def __init__(self) -> None:
        self.path = str(Path.cwd() / "demo_model.sdb")
        self.locked = False
        self.PropMaterial = _NameList(["CONC_280", "A992Fy50"])
        self.PropFrame = _FrameProp(["W18X35", "W21X44", "B30x60"])
        self.PropArea = _PropArea(["LOSA_15CM"])
        self.PropCable = _NameList([])
        self.PropTendon = _NameList([])
        self.GroupDef = _Groups()
        self.SectCut = _SectCut()
        # Dead/Live son los patrones de las tablas de carga batch (coherente con el modelo real).
        self.LoadPatterns = _LoadPatterns(list(_DEMO_LOAD_PATTERNS))
        self.LoadCases = _LoadCases(["MODAL", "DEAD", "LIVE"])
        self.RespCombo = _RespCombo(["ASD1.01", "ASD1.02", "ENVOLVENTE"])
        # ASD1.02 anida ASD1.01 (factor 1.2) y suma Live: Live queda en 1.2 + 1.0 = 2.2.
        self.RespCombo.SetCaseList("ASD1.01", [0, 0], ["Dead", "Live"], [1.0, 1.0])
        self.RespCombo.SetCaseList("ASD1.02", [1, 0], ["ASD1.01", "Live"], [1.2, 1.0])
        self.PointObj = _PointObj()
        self.FrameObj = _FrameObj()
        self.AreaObj = _AreaObj(["A1"])
        self.ConstraintDef = _ConstraintDef()
        self.MassSource = _MassSource()
        self.SelectObj = _Selection()
        self.Results = _Results()
        self.DatabaseTables = _DatabaseTables()
        self.Analyze = _Analyze()
        self.DesignSteel = _DesignSteel()
        self.DesignConcrete = _DesignConcrete()
        self.File = _File(self)

    def GetModelFilename(self) -> str:
        return self.path

    def GetPresentUnits(self) -> int:
        return 12

    def GetDatabaseUnits(self) -> int:
        return 12

    def SetPresentUnits(self, code: int) -> int:
        return 0

    def GetModelIsLocked(self) -> bool:
        return self.locked

    def SetModelIsLocked(self, locked: bool) -> int:
        self.locked = locked
        return 0


class _NameList:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def GetNameList(self, *args) -> tuple[int, list[str], int]:
        return (len(self.names), self.names, 0)


class _PropArea(_NameList):
    """Mock for area/shell section properties (thickness for 3D extrusion)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(names)
        self._thickness: dict[str, float] = {"LOSA_15CM": 0.15}
        # Material for area sections (demo slab uses concrete)
        self._area_materials: dict[str, str] = {"LOSA_15CM": "CONC_280"}

    def GetShell(self, name: str, *args) -> tuple:
        t = self._thickness.get(name, 0.15)
        return (name, "", t, 0, 0, 0.0, 0)

    def GetMaterial(self, name: str, *args) -> tuple:
        """Support material for area properties in 3D viewer coloring."""
        mat = self._area_materials.get(name, "CONC_280")
        return (0, mat)


class _LoadPatterns(_NameList):
    def Add(self, name: str, load_type: int = 8, self_weight: float = 0, add_case: bool = True) -> int:
        if name not in self.names:
            self.names.append(name)
        return 0


class _SectCut:
    """Mock for SapModel.SectCut (section cut definitions)."""

    def __init__(self) -> None:
        self.cuts: dict[str, tuple[str, int]] = {}  # name -> (group_name, my_type)

    def SetByGroup(self, name: str, group_name: str, my_type: int = 1) -> int:
        self.cuts[name] = (group_name, my_type)
        return 0

    def GetNameList(self, *args) -> tuple[int, list[str], int]:
        names = list(self.cuts.keys())
        return (len(names), names, 0)


class _RespCombo(_NameList):
    """Mock for RespCombo (load combinations)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(names)
        self.case_lists: dict[str, list[tuple[int, str, float]]] = {}

    def Add(self, name: str, combo_type: int = 0) -> int:
        if name not in self.names:
            self.names.append(name)
        return 0

    def SetCaseList(self, name: str, case_types, case_names, scale_factors) -> int:
        self.case_lists[name] = list(zip(case_types, case_names, scale_factors))
        return 0

    def GetCaseList(self, name: str, *args) -> tuple:
        """Mock de RespCombo.GetCaseList: (NumberItems, CNameType[], CName[], SF[], ret).
        Tipo 0 = caso de carga, 1 = otra combinación (anidada)."""
        items = self.case_lists.get(name, [])
        return (
            len(items),
            [t for t, _, _ in items],
            [n for _, n, _ in items],
            [f for _, _, f in items],
            0,
        )


class _ModalSubType:
    """Mock for cCaseModalEigen / cCaseModalRitz (LoadCases.ModalEigen/.ModalRitz)."""

    def __init__(self, owner: "_LoadCases") -> None:
        self._owner = owner
        self.cases: dict[str, int] = {}  # case name -> (max_modes, min_modes)[0]

    def SetCase(self, name: str) -> int:
        if name not in self._owner.names:
            self._owner.names.append(name)
        self.cases[name] = self.cases.get(name, 0)
        return 0

    def SetNumberModes(self, name: str, max_modes: int, min_modes: int = 1) -> int:
        if name not in self.cases:
            return 1
        self.cases[name] = max_modes
        return 0


class _LoadCases(_NameList):
    def __init__(self, names: list[str]) -> None:
        super().__init__(names)
        self.ModalEigen = _ModalSubType(self)
        self.ModalRitz = _ModalSubType(self)


class _FrameProp(_NameList):
    def __init__(self, names: list[str]) -> None:
        super().__init__(names)
        # Demo section definitions for 3D extruded viewer
        self._demo_sections: dict[str, dict] = {
            "W18X35": {"type": "i", "depth": 0.46, "flange_width": 0.19, "tf": 0.014, "tw": 0.009},
            "W21X44": {"type": "i", "depth": 0.53, "flange_width": 0.21, "tf": 0.016, "tw": 0.011},
            "B30x60": {"type": "rectangular", "width": 0.30, "depth": 0.60},
            "LOSA_15CM": {"type": "shell", "thickness": 0.15},
        }
        # Realistic material assignments per section (for "Material" coloring in 3D pro viewer)
        self._section_materials: dict[str, str] = {
            "W18X35": "A992Fy50",
            "W21X44": "A992Fy50",
            "B30x60": "CONC_280",
            "LOSA_15CM": "CONC_280",
        }

    def SetMaterial(self, section: str, material: str) -> int:
        if section in self._section_materials or section in self.names:
            self._section_materials[section] = material
        return 0

    def GetMaterial(self, name: str, *args) -> tuple:
        """Support reading material for a section (used by 3D viewer and readers)."""
        mat = self._section_materials.get(name, "A992Fy50")
        return (0, mat)

    # Frame section property getters used by professional 3D viewer.
    #
    # Each getter raises when `name` isn't actually a section of its shape,
    # mirroring real SAP2000 (PropFrame.GetRectangle on an I-section throws,
    # it doesn't silently hand back rectangle-shaped nonsense). This matters
    # because _read_one_frame_section() in reader_sections.py determines a
    # section's shape by probing these getters in order until one succeeds —
    # a fake that never throws would make every section look rectangular
    # (the first getter tried) and leave that probing path untested.
    def GetRectangle(self, name: str, *args) -> tuple:
        s = self._demo_sections.get(name)
        if not s or s.get("type") != "rectangular":
            raise Exception(f"GetRectangle: '{name}' is not a rectangular section")
        return (s["width"], s["depth"], 0.0, 0.0, 0.0, 0)

    def GetISection(self, name: str, *args) -> tuple:
        s = self._demo_sections.get(name)
        if not s or s.get("type") != "i":
            raise Exception(f"GetISection: '{name}' is not an I/Wide-flange section")
        return (
            s.get("depth", 0.4),
            s.get("flange_width", 0.15),
            s.get("tf", 0.012),
            s.get("tw", 0.008),
            0.0,
            0.0,
            0.0,
            0,
        )

    def GetTube(self, name: str, *args) -> tuple:
        s = self._demo_sections.get(name)
        if not s or s.get("type") != "tube":
            raise Exception(f"GetTube: '{name}' is not a tube/box section")
        return (s.get("height", 0.25), s.get("width", 0.15), s.get("tf", 0.008), s.get("tw", 0.008), 0.0, 0)

    def GetPipe(self, name: str, *args) -> tuple:
        s = self._demo_sections.get(name)
        if not s or s.get("type") != "pipe":
            raise Exception(f"GetPipe: '{name}' is not a pipe section")
        return (s.get("outer_diameter", 0.2), s.get("wall_thickness", 0.008), 0.0, 0)

    def GetSectProps(self, name: str, *args) -> tuple:
        # Last-resort fallback in the real API: works for virtually any
        # defined section (not shape-specific), so it only raises for a
        # name that isn't a section of this PropFrame at all.
        if name not in self.names and name not in self._demo_sections:
            raise Exception(f"GetSectProps: unknown section '{name}'")
        return (0.01, 1e-4, 2e-4, 3e-5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)


class _Groups(_NameList):
    def __init__(self) -> None:
        super().__init__(["ALL", "VIGAS_N2", "COLUMNAS"])

    def SetGroup(self, name: str) -> int:
        if name not in self.names:
            self.names.append(name)
        return 0

    def GetAssignments(self, name: str) -> tuple[int, list[int], list[str], int]:
        if name == "VIGAS_N2":
            return (2, [2, 2], ["F1", "F2"], 0)
        return (0, [], [], 0)


class _FrameObj(_NameList):
    def __init__(self) -> None:
        super().__init__(["F1", "F2", "F3"])
        # Pre-assign realistic sections for demo (enables frame_assigned_section + material lookup)
        self.assigned_sections: dict[str, str] = {
            "F1": "W21X44",
            "F2": "B30x60",
            "F3": "W18X35",
        }
        self.points = {
            "F1": ("J1", "J2"),
            "F2": ("J2", "J3"),
            "F3": ("J3", "J4"),
        }

    def SetSection(self, target: str, section: str, item_type: int = 0) -> int:
        self.assigned_sections[target] = section
        return 0

    def GetSection(self, name: str, *args) -> tuple:
        """Support frame_assigned_section used by MeshBuilder for material-per-frame."""
        sec = self.assigned_sections.get(name, "")
        return (sec, "", 0)

    def GetPoints(self, name: str, *args) -> tuple[str, str, int]:
        i_end, j_end = self.points[name]
        return (i_end, j_end, 0)

    def GetLoadPoint(self, name: str, *args) -> tuple:
        """Mock de FrameObj.GetLoadPoint para tests de cargas puntuales.
        Retorna una tupla de arrays como lo haria SAP2000 real:
        ([frame_names], [patterns], [type_vals], [dir_vals], [dist_vals], [val_vals], ret).
        F2 tiene una carga puntual Live Force en dist=0.5 val=15.0; F1 y F3 no tienen.
        """
        if name == "F2":
            return (["F2"], ["Live"], [1], [4], [0.5], [15.0], 0)
        return ([], [], [], [], [], [], 0)

    def GetLoadDistributed(self, name: str, *args) -> tuple:
        """Mock de FrameObj.GetLoadDistributed.
        F1 tiene una carga distribuida Dead Force Dir=4 (Gravity) de 0 a 1 con val 5.0 y 5.0.
        """
        if name == "F1":
            return (["F1"], ["Dead"], [1], [4], [0.0], [1.0], [5.0], [5.0], 0)
        return ([], [], [], [], [], [], [], [], 0)

    def GetLocalAxes(self, name: str, *args) -> tuple:
        """Mock de FrameObj.GetLocalAxes(Name, Ang, Advanced, ret).
        F1 sin rotacion (0 grados, tipico); F2 rotado 15 grados (tipico);
        F3 usa ejes locales "avanzados" — un solo angulo no lo describe, por
        eso el consumidor (reader_geometry._frame_local_axes_data) debe
        tratarlo como angulo no disponible, no como Ang=0."""
        axes = {
            "F1": (0.0, False),
            "F2": (15.0, False),
            "F3": (0.0, True),
        }
        angle, advanced = axes.get(name, (0.0, False))
        return (angle, advanced, 0)

    def GetReleases(self, name: str, *args) -> tuple:
        """Mock de FrameObj.GetReleases para tests de rótulas.
        Retorna ([I_releases], [J_releases], [I_stiffness], [J_stiffness], ret).
        F3 tiene release de M2 y M3 en extremo J (indices 4 y 5 = True).
        """
        if name == "F3":
            return (
                [False, False, False, False, False, False],
                [False, False, False, False, True, True],
                [0.0] * 6,
                [0.0] * 6,
                0,
            )
        return ([False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6, 0)


class _AreaObj(_NameList):
    """Mock for AreaObj supporting demo 3D viewer (points + property)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(names)
        # Demo area A1 using the same joints as the frames (a simple quad slab at z~1.5 avg)
        self.area_points: dict[str, list[str]] = {
            "A1": ["J1", "J2", "J3", "J4"],
        }
        self.area_props: dict[str, str] = {"A1": "LOSA_15CM"}

    def GetPoints(self, name: str, *args) -> tuple:
        pts = self.area_points.get(name, [])
        # Return format expected by reader_geometry: list of point names in first array
        return (pts, 0)

    def GetProperty(self, name: str, *args) -> tuple:
        prop = self.area_props.get(name, "LOSA_15CM")
        return (prop, 0)

    def GetShell(self, name: str, *args) -> tuple:
        # Some calls may use this
        prop = self.area_props.get(name, "LOSA_15CM")
        return (name, prop, 0.15, 0, 0, 0.0, 0)


class _ConstraintDef(_NameList):
    """Mock de ConstraintDef para tests de visualizacion de constraints.
    CB1 (Body) ata J1/J2/J3, DIAPH1 (Diaphragm) ata J3/J4 — sobre los
    joints ya existentes en _PointObj (J1-J4)."""

    def __init__(self) -> None:
        super().__init__(["CB1", "DIAPH1"])
        self._types = {"CB1": "Body", "DIAPH1": "Diaphragm"}
        self._joints = {"CB1": ["J1", "J2", "J3"], "DIAPH1": ["J3", "J4"]}

    def GetConstraint(self, name: str, *args) -> tuple:
        return (self._types.get(name, "Unknown"), 0)

    def GetJoints(self, name: str, *args) -> tuple:
        return (self._joints.get(name, []), 0)


class _PointObj(_NameList):
    def __init__(self) -> None:
        super().__init__(["J1", "J2", "J3", "J4"])
        self.coords = {
            "J1": (0.0, 0.0, 0.0),
            "J2": (6.0, 0.0, 0.0),
            "J3": (6.0, 4.0, 3.0),
            "J4": (0.0, 4.0, 3.0),
        }

    def GetCoordCartesian(self, name: str, *args) -> tuple[float, float, float, int]:
        x, y, z = self.coords[name]
        return (x, y, z, 0)

    def GetRestraint(self, name: str, *args) -> tuple:
        """Mock de PointObj.GetRestraint para tests de apoyos.
        Retorna ([U1, U2, U3, R1, R2, R3], ret).
        J1 = empotrado (6 True), J2 = articulado (3 True + 3 False),
        J3 y J4 = libres (6 False).
        """
        restraints = {
            "J1": [True, True, True, True, True, True],
            "J2": [True, True, True, False, False, False],
            "J3": [False, False, False, False, False, False],
            "J4": [False, False, False, False, False, False],
        }
        return (restraints.get(name, [False] * 6), 0)

    def GetLoadForce(self, name: str, *args) -> tuple:
        """Mock de PointObj.GetLoadForce para tests de cargas puntuales en joints.
        Retorna ([joint_names], [patterns], [F1_vals], [F2_vals], [F3_vals],
                 [M1_vals], [M2_vals], [M3_vals], ret).
        J3 tiene una carga Dead F3=-10.0 (fuerza vertical hacia abajo).
        """
        if name == "J3":
            return (["J3"], ["Dead"], [0.0], [0.0], [-10.0], [0.0], [0.0], [0.0], 0)
        return ([], [], [], [], [], [], [], [], 0)


class _Selection:
    def GetSelected(self) -> tuple[int, list[int], list[str], int]:
        return (2, [2, 2], ["F1", "F2"], 0)


class _Setup:
    def DeselectAllCasesAndCombosForOutput(self) -> int:
        return 0

    def SetCaseSelectedForOutput(self, name: str, selected: bool = True) -> int:
        return 0

    def SetComboSelectedForOutput(self, name: str, selected: bool = True) -> int:
        return 0

    def SelectAllSectionCutsForOutput(self, selected: bool = True) -> int:
        return 0

    def SetSectionCutSelectedForOutput(self, name: str, selected: bool = True) -> int:
        return 0


class _Results:
    def __init__(self) -> None:
        self.Setup = _Setup()

    def JointDispl(self, item: str, item_type: int) -> tuple[Any, ...]:
        result = (
            2,
            ["J1", "J2"],
            ["J1", "J2"],
            ["Ds", "Dp"],
            ["Max", "Max"],
            [0.0, 0.0],
            [0.001, 0.002],
            [0.0, 0.0],
            [-0.004, -0.006],
            [0.0, 0.0],
            [0.0, 0.0],
            [0.0, 0.0],
            0,
        )
        return _filter_result_tuple(result, item, item_type, object_column_index=0)

    def FrameForce(self, item: str, item_type: int) -> tuple[Any, ...]:
        result = (
            2,
            ["F1", "F2"],
            [0.0, 0.0],
            ["F1", "F2"],
            [0.0, 0.0],
            ["Ds", "ASD1.01"],
            ["Max", "Max"],
            [0.0, 0.0],
            [-12.5, -18.1],
            [1.2, 1.6],
            [0.4, 0.5],
            [0.0, 0.0],
            [3.2, 4.1],
            [-8.4, -9.7],
            0,
        )
        return _filter_result_tuple(result, item, item_type, object_column_index=0)

    def AreaForceShell(self, item: str, item_type: int) -> tuple[Any, ...]:
        # Column layout confirmed against real SAP2000 (see MCP_SAP2000 API
        # docs / scripts/post_proceso/backend_shells.py in the reference repo):
        # Obj, Elm, PointElm, LoadCase, StepType, StepNum,
        # F11, F22, F12, FMax, FMin, FAngle, FVM,
        # M11, M22, M12, MMax, MMin, MAngle, V13, V23, VMax, VAngle.
        result = (
            4,
            ["A1", "A1", "A1", "A1"],
            ["A1", "A1", "A1", "A1"],
            ["1", "2", "3", "4"],
            ["Ds", "Ds", "Ds", "Ds"],
            ["Max", "Max", "Max", "Max"],
            [0.0, 0.0, 0.0, 0.0],
            [12.0, 14.0, 10.0, 11.0],  # F11
            [-8.0, -6.0, -9.0, -7.0],  # F22
            [1.5, 1.2, 1.1, 1.3],  # F12
            [0.0] * 4,
            [0.0] * 4,
            [0.0] * 4,
            [0.0] * 4,  # FMax,FMin,FAngle,FVM
            [3.2, 3.6, 2.9, 3.0],  # M11
            [-1.5, -1.2, -1.6, -1.4],  # M22
            [0.4, 0.3, 0.5, 0.35],  # M12
            [0.0] * 4,
            [0.0] * 4,
            [0.0] * 4,  # MMax,MMin,MAngle
            [2.1, 2.4, 1.9, 2.0],  # V13
            [0.8, 0.9, 0.7, 0.75],  # V23
            [0.0] * 4,
            [0.0] * 4,  # VMax,VAngle
            0,
        )
        return _filter_result_tuple(result, item, item_type, object_column_index=0)

    def SectionCutAnalysis(self) -> tuple[Any, ...]:
        # No (Name, ItemTypeElm) input — reports every section cut currently
        # selected for output, matching the real SectionCutAnalysis signature.
        return (
            2,
            ["SCUT1", "SCUT1"],
            ["Ds", "ASD1.01"],
            ["Max", "Max"],
            [0.0, 0.0],
            [10.0, 14.0],
            [0.0, 0.0],
            [50.0, 62.0],
            [5.0, 6.0],
            [0.0, 0.0],
            [120.0, 140.0],
            0,
        )

    def JointReact(self, item: str, item_type: int) -> tuple[Any, ...]:
        result = (
            2,
            ["J1", "J2"],
            ["J1", "J2"],
            ["Ds", "ASD1.02"],
            ["Max", "Max"],
            [0.0, 0.0],
            [10.0, 11.0],
            [0.0, 0.0],
            [25.0, 28.0],
            [0.0, 0.0],
            [0.0, 0.0],
            [0.0, 0.0],
            0,
        )
        return _filter_result_tuple(result, item, item_type, object_column_index=0)


def _filter_result_tuple(
    result: tuple[Any, ...], item: str, item_type: int, object_column_index: int
) -> tuple[Any, ...]:
    if item_type != 0 or item == "ALL":
        return result
    arrays = [value for value in result if isinstance(value, list)]
    if not arrays or object_column_index >= len(arrays):
        return result
    indexes = [
        index
        for index, name in enumerate(arrays[object_column_index])
        if str(name).casefold() == str(item).casefold()
    ]
    filtered_arrays = [[array[index] for index in indexes if index < len(array)] for array in arrays]
    rebuilt: list[Any] = []
    array_index = 0
    for value in result:
        if isinstance(value, int) and not rebuilt:
            rebuilt.append(len(indexes))
        elif isinstance(value, list):
            rebuilt.append(filtered_arrays[array_index])
            array_index += 1
        else:
            rebuilt.append(value)
    return tuple(rebuilt)


class _DatabaseTables:
    # ── Overwrites de diseño de acero (factores K) ──────────────────────────
    # OJO: la estructura de esta tabla es una HIPÓTESIS sobre cómo la expone
    # SAP2000 (nombre de tabla, nombres de columna, "Program Determined" como
    # texto). Sirve para probar la lógica de sap2000/steel_k.py, NO demuestra
    # que SAP2000 real se comporte así: hay que validarlo contra un modelo
    # real (ver "Diagnóstico de factores K" en el visor).
    STEEL_K_TABLE = "Steel Design Overwrites 1 - General - AISC 360-16"
    STEEL_K_COLUMNS = ["Frame", "DesignSect", "K1Major", "K1Minor", "K2Major", "K2Minor"]
    # Relaciones de longitud no arriostrada: nombres HIPOTÉTICOS (sin verificar
    # contra SAP2000 real). Se leen y se pueden editar.
    STEEL_L_COLUMNS = ["LRatioMajor", "LRatioMinor", "LRatioLTB"]

    # ── Resultados de diseño de acero: K y L calculados (SOLO LECTURA) ───────
    # Estructura HIPOTÉTICA (sin verificar contra SAP2000 real), ver
    # sap2000/steel_k_design.py. Filas por barra x combinación x estación.
    DESIGN_PMM_TABLE = "Steel Details 2 - PMM Details - AISC 360-16"  # nombre REAL (sonda)
    DESIGN_PMM_COLUMNS = [
        "Frame", "DesignSect", "DesignType", "Combo", "Station", "Ratio",
        "K1Major", "K1Minor", "K2Major", "K2Minor", "KLTB", "XLMajor", "XLMinor", "XLLTB",
    ]

    @staticmethod
    def _default_design_rows() -> list[dict[str, Any]]:
        def row(frame, sect, combo, st, ratio, k1M, k1m, k2M, k2m, kltb, lM, lm, lltb):
            return {"Frame": frame, "DesignSect": sect, "DesignType": "Column", "Combo": combo,
                    "Station": st, "Ratio": ratio, "K1Major": k1M, "K1Minor": k1m, "K2Major": k2M,
                    "K2Minor": k2m, "KLTB": kltb, "XLMajor": lM, "XLMinor": lm, "XLLTB": lltb}
        return [
            row("F1", "W21X44", "COMB1", 0.0, 0.35, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 1.0),
            row("F1", "W21X44", "COMB2", 2.5, 0.82, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 1.0),
            row("F1", "W21X44", "COMB3", 5.0, 0.61, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 1.0),
            # F3: K2 y L difieren entre filas (p. ej. combinaciones con/sin desplazamiento lateral).
            row("F3", "W18X35", "COMB1", 0.0, 0.40, 0.8, 0.8, 1.2, 1.2, 1.2, 0.8, 1.0, 1.2),
            row("F3", "W18X35", "COMB2", 3.0, 0.75, 0.8, 0.8, 1.6, 1.6, 1.2, 0.8, 1.0, 1.2),
        ]

    # Lo VERIFICADO con la sonda en SAP2000 23.3.1 (diseño ejecutado): esta tabla NO figura en
    # GetAvailableTables pero se lee directamente, con 55 columnas y UNA fila por barra (la gobernante).
    REAL_DESIGN_TABLE = "Steel Design 2 - PMM Details - AISC 360-16"
    REAL_DESIGN_COLUMNS = [
        "Frame", "DesignSect", "DesignType", "Status", "Combo", "Location", "Pr", "MrMajor", "MrMinor",
        "VrMajor", "VrMinor", "Tr", "Equation", "TotalRatio", "PRatio", "MMajRatio", "MMinRatio",
        "VMajRatio", "VMinRatio", "TorRatio", "DCLimit", "PrDsgn", "PcComp", "PcTension", "MrMajorDsgn",
        "McMajor", "MrMinorDsgn", "McMinor", "XLMajor", "XLMinor", "XLLTB", "K1Major", "K1Minor",
        "K2Major", "K2Minor", "KLTB", "CmMajor", "CmMinor", "Cb", "B1Major", "B1Minor", "B2Major",
        "B2Minor", "Fy", "E", "Length", "MajAxisAng", "RLLF", "SectClass", "FramingType", "SDC",
        "Omega0", "SystemCd", "ErrMsg", "WarnMsg",
    ]

    def _real_design_table(self, key: str) -> tuple[Any, ...]:
        base = {c: "" for c in self.REAL_DESIGN_COLUMNS}

        def row(**kw: Any) -> dict[str, Any]:
            r = dict(base)
            r.update(kw)
            return r

        rows = [
            row(Frame="1", DesignSect="HN20X33.8", DesignType="Beam-Column", Status="Pass", Combo="DSTL8",
                Location=0, TotalRatio=0.845412, XLMajor=1, XLMinor=1, XLLTB=1, K1Major=1, K1Minor=1,
                K2Major=1.352689, K2Minor=1.142895, KLTB=1.142895, Fy=3500, E=2000000, Length=3.0,
                RLLF=1, SystemCd="SMF", SDC="D", Omega0=3),
            row(Frame="2", DesignSect="W21X44", DesignType="Beam-Column", Status="Pass", Combo="DSTL3",
                Location=2.5, TotalRatio=0.61, XLMajor=1, XLMinor=0.5, XLLTB=1, K1Major=1, K1Minor=1,
                K2Major=1.0, K2Minor=1.0, KLTB=1.0, Fy=3500, E=2000000, Length=5.0, RLLF=1, SystemCd="SMF",
                SDC="D", Omega0=3),
        ]
        data = [r[c] for r in rows for c in self.REAL_DESIGN_COLUMNS]
        return (key, list(self.REAL_DESIGN_COLUMNS), len(self.REAL_DESIGN_COLUMNS), len(rows), data, 0)

    REAL_OVERWRITES_TABLE = "Overwrites - Steel Design - AISC 360-16"

    def use_real_overwrites(self) -> None:
        """Tabla de overwrites con la forma observada con la sonda en un SAP2000 real:
        nombre «Overwrites - Steel Design - AISC 360-16», columnas XLMajor/XLMinor/
        XLLTB/K1Major.../KLTB y el valor 0 como «Program Determined»."""
        self.steel_key = self.REAL_OVERWRITES_TABLE
        self.STEEL_K_COLUMNS = ["Frame", "DesignSect", "FrameType", "XLMajor", "XLMinor", "XLLTB",
                                "K1Major", "K1Minor", "K2Major", "K2Minor", "KLTB"]
        self.STEEL_L_COLUMNS = []
        self.include_length_columns = True
        self.steel_k_rows = {
            "F1": {"DesignSect": "W21X44", "FrameType": "Program Determined", "XLMajor": 0, "XLMinor": 0.5,
                   "XLLTB": 0, "K1Major": 1.0, "K1Minor": 0, "K2Major": 0, "K2Minor": 0, "KLTB": 1.0},
            "F3": {"DesignSect": "W18X35", "FrameType": "Program Determined", "XLMajor": 0.8, "XLMinor": 0,
                   "XLLTB": 1.2, "K1Major": 0.8, "K1Minor": 0.8, "K2Major": 1.2, "K2Minor": 1.2, "KLTB": 0},
        }

    def _steel_columns(self) -> list[str]:
        cols = list(self.STEEL_K_COLUMNS)
        if self.include_length_columns:
            cols += [c for c in self.STEEL_L_COLUMNS if c not in cols]
        return cols

    def __init__(self) -> None:
        # Solo las barras de ACERO tienen overwrites: F2 (hormigón) no aparece.
        self.steel_k_rows: dict[str, dict[str, Any]] = {
            "F1": {
                "DesignSect": "W21X44",
                "K1Major": 1.0,
                "K1Minor": 1.0,
                "K2Major": "Program Determined",
                "K2Minor": "Program Determined",
                "LRatioMajor": 1.0,
                "LRatioMinor": 0.5,
                "LRatioLTB": "Program Determined",
            },
            "F3": {
                "DesignSect": "W18X35",
                "K1Major": 0.8,
                "K1Minor": 0.8,
                "K2Major": 1.2,
                "K2Minor": 1.2,
                "LRatioMajor": 0.8,
                "LRatioMinor": "",
                "LRatioLTB": 1.2,
            },
        }
        # Interruptores para probar los fallos: el modelo bloquea la
        # escritura, o SAP2000 dice "ok" sin escribir nada.
        self.locked_rejects_edits = False
        # False simula una tabla SIN columnas de longitud no arriostrada.
        self.include_length_columns = True
        self.apply_silently_ignores = False
        # True simula que SAP2000 sirve la tabla de overwrites por nombre
        # directo aunque GetAvailableTables no la liste (hipótesis).
        self.read_unlisted_tables = False
        # Selección de patrones «para mostrar» (Get/SetLoadPatternsSelectedForDisplay). Las tablas
        # batch de cargas solo devuelven filas de estos patrones, como SAP2000. Por defecto están
        # todos seleccionados, así que el comportamiento de las pruebas existentes no cambia.
        self.display_selected_patterns: list[str] = list(_DEMO_LOAD_PATTERNS)
        # False simula una versión sin esta API (Get y Set lanzan).
        self.display_selection_supported = True
        # True simula un rechazo de Set (p. ej. modelo bloqueado): devuelve código 1 y no cambia nada.
        self.display_selection_rejects = False
        # Filas extra (valores planos, mismo ancho que los campos) por tabla de carga, para pruebas.
        self.extra_table_rows: dict[str, list[Any]] = {}
        # False simula que el diseño de acero NO se ha ejecutado (la tabla de
        # resultados no figura en la lista y su lectura directa viene vacía).
        self.design_run = True
        # True simula lo visto en un SAP2000 real con el diseño sin ejecutar: la tabla de
        # resultados figura en la lista pero devuelve 17 columnas con nombre vacío y una
        # fila de otra tabla (Preferences), con código de retorno 1.
        self.design_garbage = False
        # True simula lo verificado en SAP2000 23.3.1 con el diseño ejecutado: «Steel Details 1» y
        # «Steel Details 2» figuran en la lista pero devuelven basura; «Steel Design 2 - PMM Details -
        # AISC 360-16» NO figura en la lista pero se lee directamente (55 columnas, una fila por barra).
        self.design_details_garbage_but_design_name_valid = False
        # Nombre con el que se sirve la tabla de resultados (probar la variante «Steel Design 2»).
        self.design_table_name = self.DESIGN_PMM_TABLE
        # Tabla de overwrites: nombre actual (ver `use_real_overwrites`).
        self.steel_key = self.STEEL_K_TABLE
        # True muestra las columnas con el sufijo «Unitless» de la interfaz.
        self.design_unitless_names = False
        self.design_rows = self._default_design_rows()
        self._pending_edit: tuple[list[str], list[Any]] | None = None

    def SetTableForEditingArray(self, key: str, version: int, fields: Any, nrec: int, data: Any):
        """Estricto a propósito: rechaza (código 1) tabla desconocida,
        campos que no existen o datos que no cuadran con fields × registros.
        Un simulador que aceptase cualquier cosa daría por buena una
        escritura mal formada."""
        self._pending_edit = None
        fields = list(fields)
        data = list(data)
        if key != self.steel_key:
            return (version, fields, data, 1)
        if any(f not in self._steel_columns() for f in fields):
            return (version, fields, data, 1)
        if nrec < 1 or len(data) != nrec * len(fields):
            return (version, fields, data, 1)
        self._pending_edit = (fields, data)
        return (version, fields, data, 0)

    def ApplyEditedTables(self, fill_log: bool = True, *args):
        """(NumFatalErrors, NumErrorMsgs, NumWarnMsgs, NumInfoMsgs, ImportLog, ret)."""
        pending, self._pending_edit = self._pending_edit, None
        if pending is None:
            return (1, 1, 0, 0, "No hay ediciones pendientes.", 1)
        if self.locked_rejects_edits:
            return (1, 1, 0, 0, "El modelo está bloqueado.", 1)
        fields, data = pending
        width = len(fields)
        records = [dict(zip(fields, data[i : i + width])) for i in range(0, len(data), width)]
        if any(r.get("Frame") not in self.steel_k_rows for r in records):
            return (1, 1, 0, 0, "Barra sin overwrites de acero.", 1)
        if self.apply_silently_ignores:
            return (0, 0, 0, 0, "", 0)
        for r in records:
            row = self.steel_k_rows[r["Frame"]]
            for fld, val in r.items():
                if fld == "Frame":
                    continue
                try:
                    row[fld] = float(val)
                except (TypeError, ValueError):
                    row[fld] = val
        return (0, 0, 0, 0, "", 0)

    def CancelTableEditing(self) -> int:
        self._pending_edit = None
        return 0

    def GetAvailableTables(self, *args) -> tuple[int, list[str], int]:
        return (
            4,
            [
                "Load Pattern Definitions",
                "Load Case Definitions - Summary",
                "Combination Definitions",
                "Joint Coordinates",
                "Connectivity - Frame",
                "Element Forces - Frames",
                "Joint Displacements",
                "Joint Reactions",
                "Material Properties - Basic Mechanical Properties",
                "Frame Section Properties 01 - General",
                "Connectivity - Area",
                "Joint Restraint Assignments",
                "Frame Release Assignments 1 - General",
                "Frame Section Assignments",
                "Frame Local Axes Assignments 1 - Typical",
                "Frame Loads - Distributed",
                "Frame Loads - Point",
                "Joint Loads - Force",
                self.steel_key,
            ]
            + self._design_listed(),
            0,
        )

    def _design_listed(self) -> list[str]:
        if self.design_details_garbage_but_design_name_valid:
            # Figuran las dos «Steel Details» (basura); la tabla de diseño real NO figura.
            return ["Steel Details 1 - Summary Data - AISC 360-16", self.DESIGN_PMM_TABLE]
        return [self.design_table_name] if self.design_run or self.design_garbage else []

    # Tablas de carga batch: como SAP2000, solo devuelven filas de los patrones «para mostrar».
    _LOAD_TABLE_KEYS = ("Frame Loads - Distributed", "Frame Loads - Point", "Joint Loads - Force")

    def GetLoadPatternsSelectedForDisplay(self, *args) -> tuple[int, list[str]]:
        """(ret, LoadPatternList), orden [ret, lista] como en el wrapper del repo."""
        if not self.display_selection_supported:
            raise AttributeError("GetLoadPatternsSelectedForDisplay no disponible (simulado)")
        return (0, list(self.display_selected_patterns))

    def SetLoadPatternsSelectedForDisplay(self, names: Any) -> int:
        """Devuelve el código de retorno; con ``display_selection_rejects`` no cambia nada (código 1)."""
        if not self.display_selection_supported:
            raise AttributeError("SetLoadPatternsSelectedForDisplay no disponible (simulado)")
        if self.display_selection_rejects:
            return 1
        self.display_selected_patterns = [str(name) for name in names]
        return 0

    def GetTableForDisplayArray(self, key: str, *args) -> tuple[Any, ...]:
        raw = self._raw_table(key, *args)
        if key not in self._LOAD_TABLE_KEYS or raw[5] != 0:
            return raw
        _, fields, _, _, data, ret = raw
        width = len(fields)
        flat = list(data) + list(self.extra_table_rows.get(key, []))
        pattern_index = fields.index("LoadPat")
        records = [flat[i : i + width] for i in range(0, len(flat), width)]
        records = [r for r in records if str(r[pattern_index]) in self.display_selected_patterns]
        flat = [value for record in records for value in record]
        return (key, fields, width, len(records), flat, ret)

    def _raw_table(self, key: str, *args) -> tuple[Any, ...]:
        if key == self.steel_key and (
            self.read_unlisted_tables or key in self.GetAvailableTables()[1]
        ):
            fields = self._steel_columns()
            data: list[Any] = []
            for frame, row in self.steel_k_rows.items():
                data.append(frame)
                data.extend(row.get(c, "") for c in fields[1:])
            return (key, fields, len(fields), len(self.steel_k_rows), data, 0)
        if self.design_details_garbage_but_design_name_valid:
            if key == self.REAL_DESIGN_TABLE and self.design_run:
                return self._real_design_table(key)
            if key.startswith("Steel Details"):
                fields = [""] * 17
                data = ["SAP2000", "23.3.1", "Ultimate", "3010*1HHZ5QQBUCVZ6D8", "Yes", "Yes", "", "No",
                        "Tonf, m, C", "AISC 360-16", "ACI 318-19", "AA 2015", "AISI-16", "", "", "", "Yes"]
                return (key, fields, len(fields), 1, data, 1)
        if self.design_garbage and key.startswith(("Steel Details", "Steel Design 2")):
            fields = [""] * 17
            data = ["SAP2000", "23.3.1", "Ultimate", "3010*1HHZ5QQBUCVZ6D8", "Yes", "Yes", "", "No",
                    "Tonf, m, C", "AISC 360-16", "ACI 318-19", "AA 2015", "AISI-16", "", "", "", "Yes"]
            return (key, fields, len(fields), 1, data, 1)
        if key == self.design_table_name and self.design_run:
            base = list(self.DESIGN_PMM_COLUMNS)
            fields = [
                c + " Unitless" if self.design_unitless_names and c.startswith(("K", "XL")) else c
                for c in base
            ]
            data = [r.get(c, "") for r in self.design_rows for c in base]
            return (key, fields, len(fields), len(self.design_rows), data, 0)
        if key == "Load Pattern Definitions":
            fields = ["LoadPat", "Type", "SelfWtMult"]
            data = ["DEAD", "Dead", 1.0, "LIVE", "Live", 0.0, "EQX", "Quake", 0.0]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Load Case Definitions - Summary":
            fields = ["LoadCase", "Type"]
            data = ["MODAL", "Modal", "DEAD", "Linear Static", "LIVE", "Linear Static"]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Combination Definitions":
            fields = ["Combo", "Type"]
            data = ["COMB1", "Linear Add", "ENVOLVENTE", "Envelope"]
            return (key, fields, len(fields), 2, data, 0)
        if key == "Joint Coordinates":
            fields = ["Joint", "X", "Y", "Z"]
            data = [
                "J1",
                0.0,
                0.0,
                0.0,
                "J2",
                6.0,
                0.0,
                0.0,
                "J3",
                6.0,
                4.0,
                3.0,
                "J4",
                0.0,
                4.0,
                3.0,
            ]
            return (key, fields, len(fields), 4, data, 0)
        if key == "Connectivity - Frame":
            fields = ["Frame", "JointI", "JointJ"]
            data = ["F1", "J1", "J2", "F2", "J2", "J3", "F3", "J3", "J4"]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Element Forces - Frames":
            fields = ["Frame", "Station", "OutputCase", "P", "V2", "V3", "T", "M2", "M3"]
            data = [
                "F1",
                0.0,
                "Ds",
                -12.5,
                1.2,
                0.4,
                0.0,
                3.2,
                -8.4,
                "F2",
                0.0,
                "ASD1.01",
                -18.1,
                1.6,
                0.5,
                0.0,
                4.1,
                -9.7,
            ]
            return (key, fields, len(fields), 1, data, 0)
        if key == "Joint Displacements":
            fields = ["Joint", "OutputCase", "U1", "U2", "U3", "R1", "R2", "R3"]
            data = [
                "J1",
                "Ds",
                0.001,
                0.0,
                -0.004,
                0.0,
                0.0,
                0.0,
                "J2",
                "Dp",
                0.002,
                0.0,
                -0.006,
                0.0,
                0.0,
                0.0,
            ]
            return (key, fields, len(fields), 1, data, 0)
        if key == "Joint Reactions":
            fields = ["Joint", "OutputCase", "F1", "F2", "F3", "M1", "M2", "M3"]
            data = [
                "J1",
                "Ds",
                10.0,
                0.0,
                25.0,
                0.0,
                0.0,
                0.0,
                "J2",
                "ASD1.02",
                11.0,
                0.0,
                28.0,
                0.0,
                0.0,
                0.0,
            ]
            return (key, fields, len(fields), 1, data, 0)
        if key == "Joint Restraint Assignments":
            # Matches PointObj.GetRestraint's fixture exactly (J1 fixed, J2
            # pinned, J3/J4 free) so the batch and per-object paths agree.
            fields = ["Joint", "U1", "U2", "U3", "R1", "R2", "R3"]
            data = [
                "J1",
                "Yes",
                "Yes",
                "Yes",
                "Yes",
                "Yes",
                "Yes",
                "J2",
                "Yes",
                "Yes",
                "Yes",
                "No",
                "No",
                "No",
                "J3",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "J4",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
            ]
            return (key, fields, len(fields), 4, data, 0)
        if key == "Frame Release Assignments 1 - General":
            # Matches FrameObj.GetReleases's fixture exactly (F3 releases
            # M2+M3 at J end, F1/F2 fully continuous).
            fields = ["Frame", "PI", "V2I", "V3I", "TI", "M2I", "M3I", "PJ", "V2J", "V3J", "TJ", "M2J", "M3J"]
            data = [
                "F1",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "F2",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "F3",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "No",
                "Yes",
                "Yes",
            ]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Frame Section Properties 01 - General":
            # Matches PropFrame's _demo_sections/_section_materials fixtures
            # exactly (shape text mirrors SAP2000's GUI wording) so the
            # batch and per-section probing paths agree. t3/t2/tf/tw follow
            # SAP2000's generic dimension columns: depth/height, width,
            # flange thickness, web thickness (unused per shape left 0.0).
            fields = ["SectionName", "Material", "Shape", "t3", "t2", "tf", "tw"]
            data = [
                "W18X35",
                "A992Fy50",
                "I/Wide Flange",
                0.46,
                0.19,
                0.014,
                0.009,
                "W21X44",
                "A992Fy50",
                "I/Wide Flange",
                0.53,
                0.21,
                0.016,
                0.011,
                "B30x60",
                "CONC_280",
                "Rectangular",
                0.60,
                0.30,
                0.0,
                0.0,
            ]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Connectivity - Area":
            # Matches _AreaObj.area_points's fixture exactly (A1 = J1..J4,
            # in polygon order) so the batch and per-object paths agree.
            # One row per (area, point) — deliberately out of order (J1, J3,
            # J2, J4) so a test can confirm the reader sorts by PointIndex
            # rather than trusting row order.
            fields = ["Area", "PointIndex", "Point"]
            data = [
                "A1",
                1,
                "J1",
                "A1",
                3,
                "J3",
                "A1",
                2,
                "J2",
                "A1",
                4,
                "J4",
            ]
            return (key, fields, len(fields), 4, data, 0)
        if key == "Frame Section Assignments":
            # Matches FrameObj.GetSection's fixture exactly (F1→W21X44,
            # F2→B30x60, F3→W18X35) so the batch and per-object paths agree.
            fields = ["Frame", "Section", "AutoSelect"]
            data = [
                "F1",
                "W21X44",
                "",
                "F2",
                "B30x60",
                "",
                "F3",
                "W18X35",
                "",
            ]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Frame Local Axes Assignments 1 - Typical":
            # Matches FrameObj.GetLocalAxes's fixture exactly (F1=0deg,
            # F2=15deg, both tipicos; F3 usa ejes avanzados) so the batch and
            # per-object paths agree. F3's Advanced="Yes" row is skipped by
            # _frame_local_axes_from_database, forcing the per-object
            # fallback for F3 specifically — which independently returns
            # "no disponible" too, via the same Advanced flag.
            fields = ["Frame", "Angle", "Advanced"]
            data = [
                "F1",
                0.0,
                "No",
                "F2",
                15.0,
                "No",
                "F3",
                0.0,
                "Yes",
            ]
            return (key, fields, len(fields), 3, data, 0)
        if key == "Frame Loads - Distributed":
            # F1 matches FrameObj.GetLoadDistributed's fixture exactly; F2's
            # row is extra (batch-only) test data, so this exercises real
            # multi-row batch parsing (the single-row case that used to be
            # ambiguous is fixed and covered separately in
            # tests/test_pick_flat_table_single_row.py — Fase 22).
            fields = ["Frame", "LoadPat", "Type", "Dir", "RelDist1", "RelDist2", "Val1", "Val2"]
            data = [
                "F1",
                "Dead",
                "Force",
                4,
                0.0,
                1.0,
                5.0,
                5.0,
                "F2",
                "Live",
                "Force",
                4,
                0.0,
                1.0,
                3.0,
                3.0,
            ]
            return (key, fields, len(fields), 2, data, 0)
        if key == "Frame Loads - Point":
            # F2 matches FrameObj.GetLoadPoint's fixture exactly; F3's row is
            # extra (batch-only) test data, see note above.
            fields = ["Frame", "LoadPat", "Type", "Dir", "RelDist", "Val"]
            data = [
                "F2",
                "Live",
                "Force",
                4,
                0.5,
                15.0,
                "F3",
                "Dead",
                "Force",
                4,
                0.25,
                6.0,
            ]
            return (key, fields, len(fields), 2, data, 0)
        if key == "Joint Loads - Force":
            # J3 matches PointObj.GetLoadForce's fixture exactly; J2's row is
            # extra (batch-only) test data, see note above.
            fields = ["Joint", "LoadPat", "F1", "F2", "F3", "M1", "M2", "M3"]
            data = [
                "J3",
                "Dead",
                0.0,
                0.0,
                -10.0,
                0.0,
                0.0,
                0.0,
                "J2",
                "Live",
                0.0,
                0.0,
                -5.0,
                0.0,
                0.0,
                0.0,
            ]
            return (key, fields, len(fields), 2, data, 0)
        return (key, [], 0, 0, [], 1)


class _Analyze:
    def RunAnalysis(self) -> int:
        return 0

    def RunCases(self, case_names: list[str]) -> int:
        if not case_names or not all(isinstance(c, str) and c for c in case_names):
            return 1  # SAP2000 error code
        return 0


class _File:
    def __init__(self, model: FakeSapModel) -> None:
        self.model = model

    def Save(self) -> int:
        path = Path(self.model.path)
        path.write_text("fake sap2000 model", encoding="utf-8")
        return 0


class _MassSource:
    def __init__(self) -> None:
        self.options = {
            "massFromSelf": True,
            "massFromMass": True,
            "massFromLoads": False,
            "dlMult": 1.0,
            "llMult": 0.25,
        }

    def SetOptions(
        self,
        massFromSelf: bool,
        massFromMass: bool,
        massFromLoads: bool,
        dlMult: float,
        llMult: float,
        otherMult: float,
    ) -> int:
        self.options["massFromSelf"] = massFromSelf
        self.options["massFromMass"] = massFromMass
        self.options["massFromLoads"] = massFromLoads
        self.options["dlMult"] = dlMult
        self.options["llMult"] = llMult
        return 0


class _DesignSteel:
    def __init__(self) -> None:
        self.code = "AISC-360-22"
        self.combos: list[str] = []
        self.overwrites: dict[str, float] = {}
        self.summary_results: dict[str, Any] = {
            "FrameNames": ["F1", "F2", "F3"],
            "Ratios": [0.85, 0.62, 0.43],
            "RatioTypes": ["PMM", "PMM", "Shear"],
            "Locations": [0.0, 0.5, 0.0],
        }

    def SetCode(self, code: str) -> int:
        self.code = code
        return 0

    def GetCode(self) -> tuple[str, int]:
        return (self.code, 0)

    def SetOverwrite(self, item_type: int, item_name: str, value: float) -> int:
        self.overwrites[item_name] = value
        return 0

    def StartDesign(self) -> int:
        # Populate fake summary results
        names = ["F1", "F2", "F3"]
        self.summary_results = {
            "FrameNames": names,
            "Ratios": [0.85, 0.62, 0.43],
            "RatioTypes": ["PMM", "PMM", "Shear"],
            "Locations": [0.0, 0.5, 0.0],
        }
        return 0

    def GetSummaryResults(self, name: str, *args) -> tuple[Any, ...]:
        """Mock of the REAL DesignSteel.GetSummaryResults(Name, NumberItems,
        FrameName(), Ratio(), RatioType(), Location(), ComboName(),
        ErrorSummary(), WarningSummary(), ItemType) — confirmed against the
        OAPI reference. Ignores `name`/ItemType filtering (always returns
        every frame) since the reader only ever asks for "All"/Group."""
        return (
            3,
            ["F1", "F2", "F3"],
            [0.85, 0.62, 0.43],
            [1, 1, 2],
            [0.0, 0.5, 0.0],
            ["Ds", "ASD1.01", "Ds"],
            ["", "", ""],
            ["", "", ""],
            0,
        )


class _DesignConcrete:
    def __init__(self) -> None:
        self.code = "ACI 318-19"
        self.combos: list[str] = []
        self.overwrites: dict[str, float] = {}
        self.summary_results: dict[str, Any] = {
            "FrameNames": ["B1", "B2"],
            "Ratios": [0.72, 0.55],
            "RatioTypes": ["Flexure", "Flexure"],
            "Locations": [0.0, 1.0],
        }

    def SetCode(self, code: str) -> int:
        self.code = code
        return 0

    def GetCode(self) -> tuple[str, int]:
        return (self.code, 0)

    def SetOverwrite(self, item_type: int, item_name: str, value: float) -> int:
        self.overwrites[item_name] = value
        return 0

    def StartDesign(self) -> int:
        # Populate fake summary results
        names = ["B1", "B2"]
        self.summary_results = {
            "FrameNames": names,
            "Ratios": [0.72, 0.55],
            "RatioTypes": ["Flexure", "Flexure"],
            "Locations": [0.0, 1.0],
        }
        return 0

    def GetSummaryResults(
        self,
        number_items: int,
        frame_names_out: Any,
        ratios_out: Any,
        ratio_types_out: Any,
        locations_out: Any,
    ) -> int:
        return 0

    def GetSummaryResultsColumn(self, name: str, *args) -> tuple[Any, ...]:
        """Mock of the REAL DesignConcrete.GetSummaryResultsColumn(Name,
        NumberItems, FrameName(), MyOption(), Location(), PMMCombo(),
        PMMArea(), PMMRatio(), VmajorCombo(), AVmajor(), VminorCombo(),
        AVminor(), ErrorSummary(), WarningSummary(), ItemType) — confirmed
        against the OAPI reference. MyOption: 1=Check (PMMRatio is a real
        D/C), 2=Design (no meaningful ratio). Ignores name/ItemType
        filtering (always returns every column) since the reader only ever
        asks for "All"/Group."""
        return (
            2,
            ["C1", "C2"],
            [1, 2],
            [0.0, 0.0],
            ["Dcon1.01", "Dcon1.02"],
            [0.0, 0.0],
            [0.72, 0.0],
            ["Dcon1.01", "Dcon1.02"],
            [0.0, 0.0],
            ["Dcon1.01", "Dcon1.02"],
            [0.0, 0.0],
            ["", ""],
            ["", ""],
            0,
        )

    def GetSummaryResultsBeam(self, name: str, *args) -> tuple[Any, ...]:
        """Mock of the REAL DesignConcrete.GetSummaryResultsBeam(Name,
        NumberItems, FrameName(), Location(), TopCombo(), TopArea(),
        BotCombo(), BotArea(), VmajorCombo(), VmajorArea(), TLCombo(),
        TLArea(), TTCombo(), TTArea(), ErrorSummary(), WarningSummary(),
        ItemType) — confirmed against the OAPI reference. No ratio field at
        all, only required rebar areas. Ignores name/ItemType filtering
        (always returns every beam) since the reader only ever asks for
        "All"/Group."""
        return (
            2,
            ["B1", "B2"],
            [0.0, 1.0],
            ["Dcon1.01", "Dcon1.02"],
            [12.5, 8.2],
            ["Dcon1.01", "Dcon1.02"],
            [6.3, 4.1],
            ["Dcon1.01", "Dcon1.02"],
            [2.1, 1.5],
            ["", ""],
            [0.0, 0.0],
            ["", ""],
            [0.0, 0.0],
            ["", ""],
            ["", ""],
            0,
        )


# --- Modelo simulado para resultados en bloque (reacciones, esfuerzos, envolventes) ----------
#
# A diferencia de ``FakeSapModel`` (tuplas fijas), este modelo guarda la selección de salida,
# genera las filas de los casos seleccionados (con pasos Max/Min para la combinación de tipo
# envolvente) y cuenta las llamadas COM, para probar que no se lee barra a barra.

RESULTS_FRAMES = ("F1", "F2", "F3")
RESULTS_STATIONS = (0.0, 2.0, 4.0)
RESULTS_SUPPORTS = {"J1": 10.0, "J2": 12.0, "J3": 8.0}  # reacción vertical de la carga muerta
_CASE_SCALE = {"Dead": 1.0, "Live": -0.5}


def _frame_values(case: str, frame_index: int, x: float) -> tuple[float, float, float, float, float, float]:
    """P, V2, V3, T, M2, M3 de un caso simple: determinista y con signos mezclados."""
    scale = _CASE_SCALE[case]
    k = (frame_index + 1) * scale
    return (-10 * k, 5 * k * (1 - x / 4), 0.5 * k, 0.1 * k, 2 * k, k * (x - 2))


def _combo_values(frame_index: int, x: float) -> tuple[float, ...]:
    dead = _frame_values("Dead", frame_index, x)
    live = _frame_values("Live", frame_index, x)
    return tuple(1.2 * d + 1.6 * lv for d, lv in zip(dead, live))


class _SetupStateful(_Setup):
    def __init__(self) -> None:
        self.cases: set[str] = set()
        self.combos: set[str] = set()

    def DeselectAllCasesAndCombosForOutput(self) -> int:
        self.cases.clear()
        self.combos.clear()
        return 0

    def SetCaseSelectedForOutput(self, name: str, selected: bool = True) -> int:
        (self.cases.add if selected else self.cases.discard)(name)
        return 0

    def SetComboSelectedForOutput(self, name: str, selected: bool = True) -> int:
        (self.combos.add if selected else self.combos.discard)(name)
        return 0

    def GetCaseSelectedForOutput(self, name: str) -> tuple[bool, int]:
        return (name in self.cases, 0)

    def GetComboSelectedForOutput(self, name: str) -> tuple[bool, int]:
        return (name in self.combos, 0)


class _ResultsStateful(_Results):
    """Results de ``FakeResultsModel``: filas de los casos y combinaciones seleccionados."""

    def __init__(self) -> None:
        self.Setup = _SetupStateful()
        self.calls: list[tuple[str, str, int]] = []

    def _selected(self) -> list[tuple[str, str]]:
        out = [(c, "") for c in ("Dead", "Live") if c in self.Setup.cases]
        out += [(c, "") for c in ("COMB1",) if c in self.Setup.combos]
        if "ENV" in self.Setup.combos:
            out += [("ENV", "Max"), ("ENV", "Min")]
        return out

    def _frame_rows(self) -> list[tuple]:
        rows = []
        for name, step in self._selected():
            for fi, frame in enumerate(RESULTS_FRAMES):
                for x in RESULTS_STATIONS:
                    if name == "ENV":
                        # Envolvente de Dead, Live y COMB1: máximo o mínimo por componente.
                        candidates = [
                            _frame_values("Dead", fi, x),
                            _frame_values("Live", fi, x),
                            _combo_values(fi, x),
                        ]
                        pick = max if step == "Max" else min
                        values = tuple(pick(col) for col in zip(*candidates))
                    elif name == "COMB1":
                        values = _combo_values(fi, x)
                    else:
                        values = _frame_values(name, fi, x)
                    rows.append((frame, x, name, step or "", *values))
        return rows

    def FrameForce(self, item: str, item_type: int) -> tuple[Any, ...]:
        self.calls.append(("FrameForce", item, item_type))
        rows = self._frame_rows()
        if item_type == 0 and item != "ALL":
            rows = [r for r in rows if r[0].casefold() == item.casefold()]
        if not rows:
            return (0, [], [], [], [], [], [], [], [], [], [], [], [], 0)
        cols = list(zip(*rows))
        n = len(rows)
        return (
            n,
            list(cols[0]),  # Obj
            list(cols[1]),  # ObjSta
            list(cols[0]),  # Elm
            list(cols[1]),  # ElmSta
            list(cols[2]),  # LoadCase
            list(cols[3]),  # StepType
            [0.0] * n,  # StepNum
            *[list(cols[4 + i]) for i in range(6)],  # P V2 V3 T M2 M3
            0,
        )

    def JointReact(self, item: str, item_type: int) -> tuple[Any, ...]:
        self.calls.append(("JointReact", item, item_type))
        rows = []
        for name, step in self._selected():
            for joint, base in RESULTS_SUPPORTS.items():
                scale = _CASE_SCALE.get(name, 1.2 - 0.8 if name == "COMB1" else 1.0)
                f3 = base * (scale if name != "ENV" else (1.0 if step == "Max" else -0.5))
                rows.append((joint, name, step or "", 0.0, 0.0, f3, 0.0, 0.0, 0.0))
        if item_type == 0 and item != "ALL":
            rows = [r for r in rows if r[0].casefold() == item.casefold()]
        if not rows:
            return (0, [], [], [], [], [], [], [], [], [], [], [], 0)
        cols = list(zip(*rows))
        n = len(rows)
        return (
            n,
            list(cols[0]),  # Obj
            list(cols[0]),  # Elm
            list(cols[1]),  # LoadCase
            list(cols[2]),  # StepType
            [0.0] * n,  # StepNum
            *[list(cols[3 + i]) for i in range(6)],  # F1 F2 F3 M1 M2 M3
            0,
        )


class FakeResultsModel(FakeSapModel):
    """Modelo con casos Dead/Live, combinaciones COMB1 y ENV (envolvente) y 3 apoyos."""

    present_units = 12  # tonf_m_C

    def __init__(self) -> None:
        super().__init__()
        self.LoadCases = _LoadCases(["Dead", "Live"])
        self.RespCombo = _RespCombo(["COMB1", "ENV"])
        self.Results = _ResultsStateful()

    def GetPresentUnits(self) -> int:
        return self.present_units
