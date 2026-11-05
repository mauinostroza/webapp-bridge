# Copiado de SAP2000_AI_ASSISTANT@572b684 (solo se reescriben imports).
from __future__ import annotations

import json
from pathlib import Path


DEFAULT_ALIASES = {
    "load_patterns": [
        "Load Pattern Definitions",
        "Load Patterns",
        "Load Pattern Definitions - Auto Seismic User Coefficient",
        "Load Pattern Definitions - Auto Wind",
    ],
    "load_cases": [
        "Load Case Definitions - Summary",
        "Load Case Definitions",
        "Load Cases",
        "Case Definitions",
    ],
    "load_combinations": [
        "Combination Definitions",
        "Load Combination Definitions",
        "Response Combination Definitions",
        "Load Combinations",
        "Combo Definitions",
    ],
    "joint_coordinates": [
        "Joint Coordinates",
        "Point Coordinates",
        "Objects and Elements - Joints",
        "Joint Object Connectivity",
    ],
    "frame_geometry": [
        "Connectivity - Frame",
        "Frame Connectivity",
        "Objects and Elements - Frames",
        "Frame Object Connectivity",
        "Frame Assignments - Summary",
    ],
    "frame_forces": [
        "Element Forces - Frames",
        "Frame Output Station Forces",
        "Frame Forces",
        "Analysis Results - Frame Forces",
        "Object Forces - Frames",
    ],
    "joint_displacements": [
        "Joint Displacements",
        "Joint Displacements - Absolute",
        "Analysis Results - Joint Displacements",
    ],
    "joint_reactions": [
        "Joint Reactions",
        "Base Reactions",
        "Analysis Results - Joint Reactions",
    ],
    "joint_restraints": [
        "Joint Restraint Assignments",
        "Joint Restraints",
    ],
    "frame_releases": [
        "Frame Release Assignments 1 - General",
        "Frame Assignments - Releases",
        "Frame Releases",
    ],
    "frame_loads_distributed": [
        "Frame Loads - Distributed",
        "Frame Loads Assignments - Distributed",
        "Element Forces - Frame Distributed Loads",
    ],
    "frame_loads_point": [
        "Frame Loads - Point",
        "Frame Loads Assignments - Point",
    ],
    "joint_loads_force": [
        "Joint Loads - Force",
        "Joint Loads Assignments - Force",
        "Joint Loads - Forces",
    ],
    "frame_assigned_section": [
        "Frame Section Assignments",
        "Frame Assignments - Sections",
        "Frame Assignments - Section",
        "Frame Properties - Assignments",
    ],
    "frame_section_properties": [
        "Frame Section Properties 01 - General",
        "Frame Section Property Definitions - Summary",
        "Frame Sections",
        "Frame Section Properties",
        "Frame Section Property Definitions - General",
    ],
    "frame_local_axes": [
        # Candidatos de nombre de tabla para el angulo de rotacion de ejes
        # locales 2/3 de cada frame (FrameObj.GetLocalAxes en la API: Ang en
        # grados alrededor del eje local 1). Igual que el resto de este
        # fichero, son conjeturas por analogia con nombres de tabla ya vistos
        # (p.ej. "Frame Release Assignments 1 - General") — NO confirmadas
        # contra un SAP2000 real desde este repo.
        "Frame Local Axes Assignments 1 - Typical",
        "Frame Local Axes Assignments 1 - General",
        "Frame Assignments - Local Axes",
        "Frame Local Axes",
    ],
    "area_connectivity": [
        "Connectivity - Area",
        "Area Connectivity",
        "Objects and Elements - Areas",
        "Area Object Connectivity",
    ],
}


def alias_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "table_aliases.json"


def load_aliases() -> dict[str, list[str]]:
    path = alias_path()
    if not path.exists():
        save_aliases(DEFAULT_ALIASES)
        return dict(DEFAULT_ALIASES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_ALIASES)
    aliases = dict(DEFAULT_ALIASES)
    for key, values in data.items():
        if isinstance(values, list):
            aliases[str(key)] = [str(value) for value in values]
    return aliases


def save_aliases(aliases: dict[str, list[str]]) -> None:
    path = alias_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(aliases, indent=2, ensure_ascii=False), encoding="utf-8")


def aliases_for(kind: str) -> list[str]:
    return load_aliases().get(kind, DEFAULT_ALIASES.get(kind, []))


def remember_alias(kind: str, value: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    aliases = load_aliases()
    current = [item for item in aliases.get(kind, DEFAULT_ALIASES.get(kind, [])) if str(item).strip()]
    if text in current:
        return
    aliases[kind] = [text, *current]
    save_aliases(aliases)
