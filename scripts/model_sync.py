"""
model_sync.py
=============
Parses Django models.py files and computes a safe field-level diff
between a source (incoming) and a target (existing on disk) models.py.

Rules
-----
  NEW model        → safe to add automatically
  NEW field        → safe to add automatically
  CHANGED field    → warn only — never silently alter an existing field
  REMOVED field    → ignore — never delete a field that exists in target
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldInfo:
    """Represents a single field line extracted from a model class."""
    name: str
    source_line: str        # the full indented assignment line, e.g. "    foo = models.CharField(...)"
    is_gfk_helper: bool = False   # True for the _ct / _oid helper fields of a GenericFK


@dataclass
class ModelInfo:
    """All field info extracted from one model class."""
    name: str
    fields: dict = field(default_factory=dict)   # field_name → FieldInfo
    str_field: str = "pk"                         # what __str__ returns


@dataclass
class SyncReport:
    """Result of comparing source vs target models.py."""
    new_models:     list = field(default_factory=list)   # ModelInfo objects to add wholesale
    added_fields:   dict = field(default_factory=dict)   # model_name → [FieldInfo]
    changed_fields: dict = field(default_factory=dict)   # model_name → [(FieldInfo_src, FieldInfo_dst)]
    removed_fields: dict = field(default_factory=dict)   # model_name → [FieldInfo]  (target only)
    unchanged:      list = field(default_factory=list)   # model names with no changes

    def has_changes(self) -> bool:
        return bool(self.new_models or self.added_fields or self.changed_fields)


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

def _is_model_class(node: ast.ClassDef) -> bool:
    """Return True if this AST ClassDef is a Django model (inherits models.Model)."""
    for base in node.bases:
        if isinstance(base, ast.Attribute):
            if base.attr == "Model":
                return True
        if isinstance(base, ast.Name):
            if base.id == "Model":
                return True
    return False


def _field_names_from_class(node: ast.ClassDef, src_lines: list[str]) -> dict:
    """
    Walk a ClassDef AST node and return:
        { field_name: FieldInfo }

    Only top-level assignments whose value is a models.* call,
    GenericForeignKey call, or PositiveIntegerField are collected.
    The raw source line is preserved verbatim.
    """
    fields = {}
    for child in node.body:
        if not isinstance(child, ast.Assign):
            continue
        if len(child.targets) != 1:
            continue
        target = child.targets[0]
        if not isinstance(target, ast.Name):
            continue

        fname = target.id

        # Grab the original source line(s) — strip leading whitespace for
        # comparison but keep it for re-insertion.
        lineno = child.lineno - 1          # 0-based
        # Some field defs span multiple lines; collect until the assignment ends
        raw_lines = []
        # Use col_offset to determine base indent
        indent = " " * child.col_offset
        raw_lines.append(src_lines[lineno].rstrip())
        end_lineno = getattr(child, "end_lineno", lineno + 1) - 1
        for extra in range(lineno + 1, end_lineno + 1):
            if extra < len(src_lines):
                raw_lines.append(src_lines[extra].rstrip())
        source_line = "\n".join(raw_lines)

        # Determine if it's a GFK helper (the _ct / _oid fields)
        is_helper = fname.endswith("_ct") or fname.endswith("_oid")

        fields[fname] = FieldInfo(
            name=fname,
            source_line=source_line,
            is_gfk_helper=is_helper,
        )

    return fields


def parse_models_file(path: Path) -> dict:
    """
    Parse a models.py file and return:
        { ModelName: ModelInfo }
    """
    src = path.read_text(encoding="utf-8")
    src_lines = src.splitlines()
    tree = ast.parse(src)

    models = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_model_class(node):
            continue

        fields = _field_names_from_class(node, src_lines)

        # Try to find the __str__ return field
        str_field = "pk"
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name == "__str__":
                for stmt in child.body:
                    if isinstance(stmt, ast.Return):
                        # return str(self.X)  →  X
                        try:
                            attr = stmt.value.args[0].attr
                            str_field = attr
                        except (AttributeError, IndexError):
                            pass

        models[node.name] = ModelInfo(
            name=node.name,
            fields=fields,
            str_field=str_field,
        )

    return models


# ─────────────────────────────────────────────────────────────────────────────
# Diff engine
# ─────────────────────────────────────────────────────────────────────────────

def _field_def_equal(a: FieldInfo, b: FieldInfo) -> bool:
    """
    Compare two field definitions ignoring pure whitespace differences.
    """
    def normalise(s: str) -> str:
        # Strip indentation and collapse internal spaces
        return re.sub(r"\s+", " ", s.strip())

    return normalise(a.source_line) == normalise(b.source_line)


def compute_sync(src_models: dict, dst_models: dict) -> SyncReport:
    """
    Compare source (incoming) models against destination (existing) models.

    src_models / dst_models: { ModelName: ModelInfo }
    """
    report = SyncReport()

    for model_name, src_info in src_models.items():

        # ── Brand new model ───────────────────────────────────────────────────
        if model_name not in dst_models:
            report.new_models.append(src_info)
            continue

        dst_info = dst_models[model_name]
        added    = []
        changed  = []

        for fname, src_field in src_info.fields.items():
            if fname not in dst_info.fields:
                # Field exists in source but not in target → add it
                added.append(src_field)
            else:
                dst_field = dst_info.fields[fname]
                if not _field_def_equal(src_field, dst_field):
                    # Field exists in both but definitions differ → warn
                    changed.append((src_field, dst_field))

        # Fields only in target (removed from source) → log, never touch
        removed = [
            dst_info.fields[fn]
            for fn in dst_info.fields
            if fn not in src_info.fields
        ]

        if added:
            report.added_fields[model_name] = added
        if changed:
            report.changed_fields[model_name] = changed
        if removed:
            report.removed_fields[model_name] = removed
        if not added and not changed:
            report.unchanged.append(model_name)

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Applier
# ─────────────────────────────────────────────────────────────────────────────

def _find_class_insert_point(src_lines: list[str], model_name: str) -> int | None:
    """
    Find the line index just before `class Meta:` inside *model_name*'s
    class body. This is where new fields should be inserted.
    Returns None if the class or Meta cannot be located.
    """
    in_class = False
    class_indent = 0

    for idx, line in enumerate(src_lines):
        # Detect start of the target class
        m = re.match(r'^(class\s+' + re.escape(model_name) + r'\s*\()', line)
        if m:
            in_class = True
            class_indent = 0
            continue

        if in_class:
            stripped = line.strip()
            # Detect `    class Meta:` — insert just before this line
            if re.match(r'\s+class Meta\s*:', line):
                return idx          # insert before this line
            # Detect the end of the class (next top-level class or EOF)
            if stripped and not line[0].isspace():
                break

    return None


def apply_sync(report: SyncReport, dst_path: Path, src_path: Path, dry_run: bool) -> list[str]:
    """
    Apply the sync report to the destination models.py.

    Returns a list of human-readable action strings (for the summary log).
    Backs up the original file as models.py.bak before any write.
    """
    actions = []
    src_text = src_path.read_text(encoding="utf-8")
    dst_text = dst_path.read_text(encoding="utf-8")
    dst_lines = dst_text.splitlines()

    # ── 1. Append brand-new model classes ────────────────────────────────────
    if report.new_models:
        # Extract each new model's full source block from src_text
        src_blocks = _extract_class_blocks(src_text)
        new_class_src = []
        for mi in report.new_models:
            block = src_blocks.get(mi.name, "")
            if block:
                new_class_src.append(block)
                actions.append(f"ADD   model  {mi.name}")

        if new_class_src and not dry_run:
            # Ensure the GFK import is present if any new model needs it
            needs_gfk = any(
                any(f.is_gfk_helper or "GenericForeignKey" in f.source_line
                    for f in mi.fields.values())
                for mi in report.new_models
            )
            if needs_gfk and "GenericForeignKey" not in dst_text:
                dst_lines = _ensure_gfk_import(dst_lines)

            dst_lines.append("")
            for block in new_class_src:
                dst_lines.extend(block.splitlines())
                dst_lines.append("")

    # ── 2. Add missing fields to existing models ──────────────────────────────
    # We do this in reverse-model order so that line indices stay valid
    # as we insert lines.  Collect (insert_idx, lines_to_insert) then apply.
    insertions = []   # list of (line_idx, [lines])

    for model_name, new_fields in report.added_fields.items():
        insert_idx = _find_class_insert_point(dst_lines, model_name)
        if insert_idx is None:
            actions.append(f"WARN  field  {model_name}: could not locate insertion point")
            continue

        lines_to_add = []
        for fi in new_fields:
            # Normalise indentation to 4 spaces
            normalised = _normalise_indent(fi.source_line, indent=4)
            lines_to_add.append(normalised)
            actions.append(f"ADD   field  {model_name}.{fi.name}")

        insertions.append((insert_idx, lines_to_add))

    # Apply insertions in reverse order so earlier insertions don't shift later indices
    for insert_idx, lines_to_add in sorted(insertions, key=lambda x: x[0], reverse=True):
        for i, l in enumerate(lines_to_add):
            dst_lines.insert(insert_idx + i, l)

    # ── 3. Annotate changed fields with a WARN comment ────────────────────────
    for model_name, pairs in report.changed_fields.items():
        for src_field, dst_field in pairs:
            actions.append(
                f"WARN  field  {model_name}.{src_field.name}: "
                f"definition differs — not changed automatically"
            )
            if not dry_run:
                # Insert a # UPDATED comment next to the existing field line
                comment = (
                    f"  # ⚠ SYNC: source has → "
                    + src_field.source_line.strip()
                )
                dst_lines = _annotate_field(dst_lines, model_name, dst_field.name, comment)

    # ── Write ─────────────────────────────────────────────────────────────────
    if not dry_run and actions:
        # Backup first
        dst_path.with_suffix(".py.bak").write_text(dst_text, encoding="utf-8")
        dst_path.write_text("\n".join(dst_lines) + "\n", encoding="utf-8")

    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_class_blocks(src_text: str) -> dict:
    """
    Return { ClassName: full_source_block } for every top-level class in src_text.
    The block includes the class header and all its body lines.
    """
    lines = src_text.splitlines()
    blocks = {}
    current_name = None
    current_lines = []

    for line in lines:
        m = re.match(r'^class (\w+)\s*\(', line)
        if m:
            if current_name:
                blocks[current_name] = "\n".join(current_lines)
            current_name = m.group(1)
            current_lines = [line]
        elif current_name:
            # End of class = next non-indented, non-blank line that isn't a decorator
            if line and not line[0].isspace() and not line.startswith("@") and not line.startswith("#"):
                blocks[current_name] = "\n".join(current_lines)
                current_name = None
                current_lines = []
                # This line might be the start of a new class — re-check
                m2 = re.match(r'^class (\w+)\s*\(', line)
                if m2:
                    current_name = m2.group(1)
                    current_lines = [line]
            else:
                current_lines.append(line)

    if current_name and current_lines:
        blocks[current_name] = "\n".join(current_lines)

    return blocks


def _normalise_indent(source_line: str, indent: int = 4) -> str:
    """Strip existing indentation and re-apply a fixed number of spaces."""
    return " " * indent + source_line.strip()


def _ensure_gfk_import(lines: list[str]) -> list[str]:
    """Insert the GenericForeignKey import after the `from django.db import models` line."""
    gfk_import = "from django.contrib.contenttypes.fields import GenericForeignKey"
    for idx, line in enumerate(lines):
        if "from django.db import models" in line:
            lines.insert(idx + 1, gfk_import)
            return lines
    # Fallback: prepend
    lines.insert(0, gfk_import)
    return lines


def _annotate_field(lines: list[str], model_name: str, field_name: str, comment: str) -> list[str]:
    """
    Find the line containing `    field_name = ...` inside model_name's class body
    and append *comment* to the end of that line.
    """
    in_class = False
    for idx, line in enumerate(lines):
        if re.match(r'^class\s+' + re.escape(model_name) + r'\s*\(', line):
            in_class = True
            continue
        if in_class:
            if re.match(r'^\s+' + re.escape(field_name) + r'\s*=', line):
                if comment not in line:
                    lines[idx] = line.rstrip() + comment
                return lines
            # Left the class
            if line and not line[0].isspace():
                break
    return lines
