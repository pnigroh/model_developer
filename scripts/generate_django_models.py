#!/usr/bin/env python3
"""
Django Models Generator from CSV export.

Usage:
    python generate_django_models.py -i <path/to/export.csv> -o <path/to/output_dir> [-a <app_name>]

Arguments:
    -i / --input      Path to the source CSV file (required)
    -o / --output     Path to the directory where the Django app will be written (required)
    -a / --app-name   Django app name used in apps.py (optional, defaults to the output folder name)

Example:
    python generate_django_models.py -i ~/Desktop/export_fields.csv -o ~/myproject/properties -a properties
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Data-type → Django field mapping
# ---------------------------------------------------------------------------
SIMPLE_TYPE_MAP = {
    "memo":             ("models.TextField", {"blank": "True", "null": "True"}),
    "blob":             ("models.FileField", {"upload_to": "'uploads/'", "blank": "True", "null": "True"}),
    "image":            ("models.ImageField", {"upload_to": "'images/'", "blank": "True", "null": "True"}),
    "boolean":          ("models.BooleanField", {"default": "False"}),
    "money":            ("models.DecimalField", {"max_digits": "14", "decimal_places": "2",
                                                  "blank": "True", "null": "True"}),
    "decimal number":   ("models.DecimalField", {"max_digits": "14", "decimal_places": "4",
                                                  "blank": "True", "null": "True"}),
    "whole number":     ("models.IntegerField", {"blank": "True", "null": "True"}),
    "email address":    ("models.EmailField", {"blank": "True", "null": "True"}),
    "date":             ("models.DateField", {"blank": "True", "null": "True"}),
    "date & time":      ("models.DateTimeField", {"blank": "True", "null": "True"}),
    "text":             ("models.CharField", {"max_length": "255", "blank": "True", "null": "True"}),
    "color":            ("models.CharField", {"max_length": "32", "blank": "True", "null": "True"}),
    # autonumber → Django auto-generates the pk; we skip Id fields entirely
    "autonumber":       None,
    # generic object refs handled separately
    "any class":        None,
    "any id":           None,
}


# Python / Django reserved words that cannot be used as field names.
# Django rules:
#   E001 – field names must not end with an underscore
#   E002 – field names must not contain "__"
#   We therefore use an "x_" PREFIX for collisions, not a suffix.
_RESERVED = {
    "class", "for", "object", "type", "id", "in", "is", "if", "or",
    "and", "not", "del", "def", "pass", "return", "import", "from",
    "with", "as", "try", "except", "raise", "global", "lambda",
    "yield", "while", "break", "continue", "elif", "else",
    # Django internals that would clash
    "pk", "objects", "save", "delete",
}


def to_snake_case(name: str) -> str:
    """Convert a human-readable field / class name to a valid Django field name.

    Rules applied (in order):
      1. Non-alphanumeric runs → single underscore
      2. Strip leading/trailing underscores
      3. Lower-case
      4. If the result is a Python/Django reserved word, prepend 'x_'
         (Django forbids trailing underscores – E001)
      5. Collapse any double-underscores introduced by step 1 into single
         ones (Django forbids '__' anywhere – E002)
    """
    s = name.strip()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_").lower()
    if s in _RESERVED:
        s = "x_" + s                    # prefix, never suffix (avoids E001)
    # Final guard: collapse any remaining __ sequences (avoids E002)
    s = re.sub(r"__+", "_", s)
    return s


def to_pascal_case(name: str) -> str:
    """Convert a human-readable class name to PascalCase Django model name."""
    s = name.strip()
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s)
    return "".join(word.capitalize() for word in s.split())


def pluralise(name: str) -> str:
    """Best-effort English plural for a class verbose name."""
    if name.endswith("y") and not name.endswith(("ay", "ey", "oy", "uy")):
        return name[:-1] + "ies"
    if name.lower().endswith(("s", "sh", "ch", "x", "z")):
        return name + "es"
    return name + "s"


def strip_html(value: str) -> str:
    """Remove any HTML tags that appear in the raw CSV (e.g. <font …>…</font>)."""
    return re.sub(r"<[^>]+>", "", value).strip()


# ---------------------------------------------------------------------------
# Parse CSV
# ---------------------------------------------------------------------------
def parse_csv(path: str):
    """
    Returns:
        classes      : dict[class_name_raw → list[dict]]  (ordered insertion)
        all_classes  : set of raw class names (for FK resolution)
    """
    classes = defaultdict(list)

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader)  # skip header

        for row in reader:
            if len(row) < 2:
                continue
            class_raw  = row[0].strip().strip('"')
            field_raw  = row[1].strip().strip('"')
            dtype_raw  = strip_html(row[2].strip().strip('"')) if len(row) > 2 else ""
            summary    = row[3].strip().strip('"')             if len(row) > 3 else ""

            if not class_raw or not field_raw:
                continue

            classes[class_raw].append({
                "field_raw":  field_raw,
                "dtype_raw":  dtype_raw,
                "summary":    summary,
            })

    return classes


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------
def resolve_field(field_raw: str, dtype_raw: str, summary: str,
                  all_classes: set, pascal_map: dict) -> dict | None:
    """
    Return a dict describing the Django field, or None to skip.

    Keys: name, field_type, kwargs, many_to_many, uses_content_type
    """
    dtype_lower  = dtype_raw.lower().strip()
    field_snake  = to_snake_case(field_raw)
    many         = "can have more values" in summary.lower()

    # -----------------------------------------------------------------------
    # 1. Skip "Id / Autonumber" – Django auto-creates the primary key
    # -----------------------------------------------------------------------
    if field_raw.strip().lower() == "id":
        return None

    # -----------------------------------------------------------------------
    # 2. "Any class" / "Any id" → GenericForeignKey via ContentType framework
    # -----------------------------------------------------------------------
    if dtype_lower in ("any class", "any id"):
        return {
            "name":              field_snake,
            "field_raw":         field_raw,
            "field_type":        "GENERIC_FK",
            "kwargs":            {},
            "many":              False,
            "uses_content_type": True,
        }

    # -----------------------------------------------------------------------
    # 3. dtype matches a known class → FK or M2M
    # -----------------------------------------------------------------------
    # Check both the raw dtype and the Pascal-cased version
    matched_class = None
    for cls_raw in all_classes:
        if dtype_raw.strip().lower() == cls_raw.lower():
            matched_class = cls_raw
            break

    if matched_class:
        target_model = pascal_map[matched_class]
        return {
            "name":              field_snake,
            "field_raw":         field_raw,
            "field_type":        "M2M" if many else "FK",
            "target":            target_model,
            "kwargs":            {"blank": "True", "null": "True"} if not many else {"blank": "True"},
            "many":              many,
            "uses_content_type": False,
        }

    # -----------------------------------------------------------------------
    # 4. Simple built-in type map
    # -----------------------------------------------------------------------
    if dtype_lower in SIMPLE_TYPE_MAP:
        mapped = SIMPLE_TYPE_MAP[dtype_lower]
        if mapped is None:
            return None          # explicitly skipped type
        field_type, kwargs = mapped
        return {
            "name":              field_snake,
            "field_raw":         field_raw,
            "field_type":        field_type,
            "kwargs":            dict(kwargs),
            "many":              False,
            "uses_content_type": False,
        }

    # -----------------------------------------------------------------------
    # 5. Empty dtype → plain CharField
    # -----------------------------------------------------------------------
    if dtype_lower == "":
        return {
            "name":              field_snake,
            "field_raw":         field_raw,
            "field_type":        "models.CharField",
            "kwargs":            {"max_length": "255", "blank": "True", "null": "True"},
            "many":              False,
            "uses_content_type": False,
        }

    # -----------------------------------------------------------------------
    # 6. Unknown / unrecognised → fall back to CharField with a comment
    # -----------------------------------------------------------------------
    return {
        "name":              field_snake,
        "field_raw":         field_raw,
        "field_type":        "models.CharField",
        "kwargs":            {"max_length": "255", "blank": "True", "null": "True"},
        "many":              False,
        "uses_content_type": False,
        "comment":           f"# NOTE: unknown source type '{dtype_raw}' – defaulted to CharField",
    }


# ---------------------------------------------------------------------------
# Code generation helpers
# ---------------------------------------------------------------------------
def kwargs_str(kwargs: dict) -> str:
    parts = []
    for k, v in kwargs.items():
        # Values that are already valid Python literals are emitted as-is;
        # plain strings (upload_to paths etc.) are already quoted inside the dict.
        parts.append(f"{k}={v}")
    return ", ".join(parts)


def _gfk_helper_names(base: str) -> tuple[str, str]:
    """
    Return (ct_field_name, id_field_name) for a GenericForeignKey whose
    snake_case base is *base*.

    Rules:
      • Must not end with '_'   (Django E001)
      • Must not contain '__'   (Django E002)
    Strategy: strip any trailing underscores from base, then append suffixes.
    The result is then collapsed with the same double-underscore guard.
    """
    clean = base.rstrip("_")                          # remove any trailing _
    ct = re.sub(r"__+", "_", f"{clean}_ct")           # e.g. x_class_ct
    oid = re.sub(r"__+", "_", f"{clean}_oid")         # e.g. x_class_oid
    return ct, oid


def render_model(class_raw: str, fields_info: list,
                 all_classes: set, pascal_map: dict) -> tuple[str, bool]:
    """Return (python_source, has_gfk) for a single Django model class."""
    model_name = pascal_map[class_raw]
    seen_names: set[str] = set()   # de-duplicate field names within the class
    fk_names:   set[str] = set()   # track FK field names to detect _id clashes
    has_gfk = False
    field_lines: list[str] = []

    # ── First pass: resolve all fields ───────────────────────────────────────
    resolved_fields: list[dict] = []
    for fi in fields_info:
        r = resolve_field(fi["field_raw"], fi["dtype_raw"], fi["summary"],
                          all_classes, pascal_map)
        if r is None:
            continue
        resolved_fields.append(r)
        if r["field_type"] == "FK":
            fk_names.add(r["name"])

    # ── Second pass: emit field lines ─────────────────────────────────────────
    for resolved in resolved_fields:
        name    = resolved["name"]
        ftype   = resolved["field_type"]
        comment = resolved.get("comment", "")

        # ── Django E006: ForeignKey named `foo` auto-creates `foo_id`.
        #    If a separate raw field also maps to `foo_id`, rename it.
        if ftype not in ("FK", "M2M", "GENERIC_FK"):
            # Check whether this plain field collides with any FK's shadow column
            for fk in fk_names:
                if name == f"{fk}_id":
                    name = f"{name}_raw"      # e.g. property_id → property_id_raw
                    break

        if name in seen_names:
            continue
        seen_names.add(name)

        if ftype == "GENERIC_FK":
            has_gfk = True
            ct_field, id_field = _gfk_helper_names(name)
            # Make sure helper names don't collide either
            while ct_field in seen_names:
                ct_field += "2"
            while id_field in seen_names:
                id_field += "2"
            seen_names.update({ct_field, id_field})

            field_lines.append(
                f"    {ct_field} = models.ForeignKey("
                f"'contenttypes.ContentType', on_delete=models.SET_NULL, "
                f"null=True, blank=True, related_name='+')"
            )
            field_lines.append(
                f"    {id_field} = models.PositiveIntegerField(null=True, blank=True)"
            )
            field_lines.append(
                f"    {name} = GenericForeignKey('{ct_field}', '{id_field}')"
            )

        elif ftype == "FK":
            target    = resolved["target"]
            on_delete = "models.SET_NULL"
            kw        = kwargs_str(resolved["kwargs"])
            rn        = "related_name='+'"
            sep       = ", " if kw else ""
            field_lines.append(
                f"    {name} = models.ForeignKey('{target}', "
                f"on_delete={on_delete}{sep}{kw}, {rn})"
            )

        elif ftype == "M2M":
            target = resolved["target"]
            kw     = kwargs_str(resolved["kwargs"])
            sep    = ", " if kw else ""
            field_lines.append(
                f"    {name} = models.ManyToManyField('{target}'{sep}{kw})"
            )

        else:
            kw     = kwargs_str(resolved["kwargs"])
            prefix = f"    {comment}\n" if comment else ""
            sep    = "" if not kw else ""   # kw already has all args
            if kw:
                field_lines.append(f"{prefix}    {name} = {ftype}({kw})")
            else:
                field_lines.append(f"{prefix}    {name} = {ftype}()")

    if not field_lines:
        field_lines.append("    pass")

    # ── __str__ candidate ─────────────────────────────────────────────────────
    str_candidates = [to_snake_case(f["field_raw"]) for f in fields_info
                      if f["field_raw"].lower() in ("name", "title", "full name")]
    str_field = str_candidates[0] if str_candidates else "pk"

    lines = [
        f"class {model_name}(models.Model):",
        f'    """Auto-generated model for "{class_raw}"."""',
        "",
        *field_lines,
        "",
        "    class Meta:",
        f"        verbose_name = '{class_raw}'",
        f"        verbose_name_plural = '{pluralise(class_raw)}'",
        "",
        "    def __str__(self):",
        f"        return str(self.{str_field})",
        "",
    ]
    return "\n".join(lines), has_gfk


# ---------------------------------------------------------------------------
# Generate models.py
# ---------------------------------------------------------------------------
def generate_models(classes: dict) -> str:
    all_classes = set(classes.keys())
    pascal_map  = {raw: to_pascal_case(raw) for raw in all_classes}

    # Collect all distinct fields per class (preserve first occurrence order)
    class_fields: dict[str, list] = {}
    for class_raw, rows in classes.items():
        seen = {}
        for row in rows:
            key = row["field_raw"].strip().lower()
            if key not in seen:
                seen[key] = row
        class_fields[class_raw] = list(seen.values())

    header = [
        '"""',
        "Django models auto-generated from CSV export.",
        "Review ForeignKey on_delete policies, blank/null constraints,",
        "and ManyToMany through tables before using in production.",
        '"""',
        "",
        "from django.db import models",
        "from django.contrib.contenttypes.fields import GenericForeignKey",
        "",
        "",
    ]

    body_parts = []
    any_gfk    = False

    for class_raw in sorted(all_classes, key=lambda c: to_pascal_case(c)):
        fields_info = class_fields[class_raw]
        model_src, has_gfk = render_model(class_raw, fields_info, all_classes, pascal_map)
        if has_gfk:
            any_gfk = True
        body_parts.append(model_src)

    # If no model actually uses GFK, drop that import
    src = "\n".join(header) + "\n".join(body_parts)
    if not any_gfk:
        src = src.replace(
            "from django.contrib.contenttypes.fields import GenericForeignKey\n", ""
        )

    return src


# ---------------------------------------------------------------------------
# Generate admin.py
# ---------------------------------------------------------------------------
def generate_admin(classes: dict) -> str:
    pascal_map = {raw: to_pascal_case(raw) for raw in classes}
    model_names = sorted(pascal_map.values())

    imports = ["from django.contrib import admin", ""]
    imports.append(
        "from .models import (\n    " +
        ",\n    ".join(model_names) +
        ",\n)"
    )
    imports.append("")
    imports.append("")

    registrations = []
    for name in model_names:
        registrations.append(f"@admin.register({name})")
        registrations.append(f"class {name}Admin(admin.ModelAdmin):")
        registrations.append(f"    pass")
        registrations.append("")

    return "\n".join(imports) + "\n".join(registrations)


# ---------------------------------------------------------------------------
# Generate apps.py
# ---------------------------------------------------------------------------
def generate_apps(app_name: str) -> str:
    pascal = to_pascal_case(app_name)
    return (
        "from django.apps import AppConfig\n"
        "\n"
        "\n"
        f"class {pascal}Config(AppConfig):\n"
        f"    default_auto_field = 'django.db.models.BigAutoField'\n"
        f"    name = '{app_name}'\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate_django_models",
        description="Generate a Django app (models.py, admin.py, apps.py) from a CSV class export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python generate_django_models.py -i export.csv -o ./myapp\n"
            "  python generate_django_models.py -i export.csv -o ./myapp -a listings\n"
        ),
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        metavar="CSV_FILE",
        help="Path to the source CSV file.",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        metavar="OUTPUT_DIR",
        help="Directory where the Django app files will be written (created if it does not exist).",
    )
    parser.add_argument(
        "-a", "--app-name",
        metavar="APP_NAME",
        default=None,
        help="Django app name used in apps.py. Defaults to the base name of OUTPUT_DIR.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    csv_path   = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    app_name   = args.app_name or os.path.basename(output_dir)

    # Validate input file
    if not os.path.isfile(csv_path):
        print(f"Error: input file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Input  : {csv_path}")
    print(f"Output : {output_dir}")
    print(f"App    : {app_name}")
    print()

    classes = parse_csv(csv_path)
    print(f"Found {len(classes)} classes: {', '.join(sorted(classes.keys()))}\n")

    os.makedirs(output_dir, exist_ok=True)

    # models.py
    models_src = generate_models(classes)
    models_path = os.path.join(output_dir, "models.py")
    with open(models_path, "w", encoding="utf-8") as fh:
        fh.write(models_src)
    print(f"✔  {models_path}")

    # admin.py
    admin_src = generate_admin(classes)
    admin_path = os.path.join(output_dir, "admin.py")
    with open(admin_path, "w", encoding="utf-8") as fh:
        fh.write(admin_src)
    print(f"✔  {admin_path}")

    # apps.py
    apps_src = generate_apps(app_name)
    apps_path = os.path.join(output_dir, "apps.py")
    with open(apps_path, "w", encoding="utf-8") as fh:
        fh.write(apps_src)
    print(f"✔  {apps_path}")

    # __init__.py
    init_path = os.path.join(output_dir, "__init__.py")
    with open(init_path, "w", encoding="utf-8") as fh:
        fh.write("")
    print(f"✔  {init_path}")

    print("\nDone. Next steps:")
    print("  1. Add 'contenttypes' to INSTALLED_APPS (needed for GenericForeignKey).")
    print(f"  2. Add '{app_name}' to INSTALLED_APPS.")
    print(f"  3. Run:  python manage.py makemigrations {app_name} && python manage.py migrate")


if __name__ == "__main__":
    main()
