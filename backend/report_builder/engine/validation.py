"""
Validacion cruzada entre el manifiesto y las anclas realmente presentes en un
.docx. Sin heuristica: solo se compara el conjunto de tags declarados en el
manifiesto contra el conjunto de tags report:* que existen en el documento.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.report_builder.engine.anchor_locator import find_all_report_tags, find_sdt_by_tag
from backend.report_builder.engine.manifest_schema import Manifest

STATUS_OK = "ok"
STATUS_ANCHOR_NOT_FOUND = "anchor_not_found"
STATUS_ANCHOR_DUPLICATED = "anchor_duplicated"
STATUS_DISABLED = "disabled"


@dataclass
class ModuleValidation:
    module_id: str
    tag: str
    status: str


@dataclass
class ValidationReport:
    modules: list = field(default_factory=list)
    extra_tags: set = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return all(m.status in (STATUS_OK, STATUS_DISABLED) for m in self.modules)

    def errors(self) -> list:
        return [m for m in self.modules if m.status not in (STATUS_OK, STATUS_DISABLED)]


def validate_against_docx(manifest: Manifest, document_element) -> ValidationReport:
    report = ValidationReport()
    declared_tags = set()

    for entry in manifest.modules:
        declared_tags.add(entry.tag)

        if not entry.enabled:
            report.modules.append(ModuleValidation(entry.module_id, entry.tag, STATUS_DISABLED))
            continue

        matches = find_sdt_by_tag(document_element, entry.tag)
        if len(matches) == 0:
            status = STATUS_ANCHOR_NOT_FOUND
        elif len(matches) > 1:
            status = STATUS_ANCHOR_DUPLICATED
        else:
            status = STATUS_OK
        report.modules.append(ModuleValidation(entry.module_id, entry.tag, status))

    report.extra_tags = find_all_report_tags(document_element) - declared_tags
    return report
