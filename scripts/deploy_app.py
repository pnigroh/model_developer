#!/usr/bin/env python3
"""
deploy_app.py — Django / Django-CMS App Deployer
=================================================

Takes a generated Django app folder OR a zip archive containing one
(output of generate_django_models.py) and wires it into an existing
Django or Django-CMS project.

  • First-time deploy  → copies the app, patches settings + urls, generates
                         views / urls / templates, runs migrations.
  • Re-deploy (update) → behaviour controlled by --mode:

      update   (default) Diff source vs target — add new models and new
                         fields, warn about changed definitions, keep
                         everything else intact.
      replace            Overwrite the entire models.py (and admin.py) with
                         the incoming version. Old content is backed up.
      new-only           Only add models that do not yet exist in the target;
                         skip all models that are already present.

Usage
-----
    python deploy_app.py -i <app_folder_or_zip> -t <django_project_root> [options]

Options
-------
  -i / --input      Path to the app folder or .zip archive      (required)
  -t / --target     Root folder of the target Django project     (required)
  -a / --app-name   Override the app name (default: input basename)
  -m / --mode       update | replace | new-only  (default: update)
  --dry-run         Print every action without writing anything
  --no-migrate      Skip makemigrations / migrate step

Examples
--------
    python deploy_app.py -i ./django_models -t ~/myproject
    python deploy_app.py -i ./django_models.zip -t ~/myproject --mode replace
    python deploy_app.py -i ./django_models -t ~/myproject --mode new-only --dry-run
"""

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from model_sync import (
    SyncReport,
    apply_sync,
    compute_sync,
    parse_models_file,
)

# ─────────────────────────────────────────────────────────────────────────────
# Terminal colours
# ─────────────────────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}✔  {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠  {msg}{RESET}")
def err(msg):  print(f"{RED}✘  {msg}{RESET}", file=sys.stderr)
def info(msg): print(f"{CYAN}→  {msg}{RESET}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="deploy_app",
        description="Deploy or update a generated Django app inside an existing project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python deploy_app.py -i ./django_models -t ~/myproject\n"
            "  python deploy_app.py -i ./django_models.zip -t ~/myproject --mode replace\n"
            "  python deploy_app.py -i ./django_models -t ~/myproject --mode new-only --dry-run\n"
        ),
    )
    parser.add_argument("-i", "--input",    required=True, metavar="APP_FOLDER_OR_ZIP",
                        help="Path to the generated app folder or a .zip archive of it.")
    parser.add_argument("-t", "--target",   required=True, metavar="PROJECT_ROOT",
                        help="Root folder of the target Django project.")
    parser.add_argument("-a", "--app-name", metavar="APP_NAME", default=None,
                        help="Override app name (default: basename of --input).")
    parser.add_argument("-m", "--mode",
                        choices=["update", "replace", "new-only"],
                        default="update",
                        help=(
                            "update   — add new models/fields, warn on changes (default)\n"
                            "replace  — overwrite models.py entirely with incoming version\n"
                            "new-only — only add models not yet present, skip existing ones"
                        ))
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print every action without writing anything.")
    parser.add_argument("--no-migrate", action="store_true",
                        help="Skip makemigrations and migrate.")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Zip extraction
# ─────────────────────────────────────────────────────────────────────────────
def extract_zip_input(zip_path: Path) -> Path:
    """
    Extract a zip archive that contains a Django app folder and return the
    path to the extracted app directory inside a temporary folder.

    Handles two common zip layouts:
      Layout A — files at the root of the zip:
                   models.py, admin.py, apps.py, __init__.py, ...
      Layout B — files inside a single top-level subdirectory:
                   django_models/models.py, django_models/admin.py, ...
    """
    if not zipfile.is_zipfile(zip_path):
        err(f"Not a valid zip file: {zip_path}")
        sys.exit(1)

    tmp_dir = Path(tempfile.mkdtemp(prefix="class_migrator_"))
    info(f"Extracting {zip_path.name} → {tmp_dir}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # Detect layout
    required = {"models.py", "apps.py", "admin.py", "__init__.py"}

    # Layout A: required files directly in tmp_dir
    if required.issubset({p.name for p in tmp_dir.iterdir()}):
        ok(f"Zip extracted (flat layout): {tmp_dir}")
        return tmp_dir

    # Layout B: required files inside a single subdirectory
    subdirs = [p for p in tmp_dir.iterdir() if p.is_dir()]
    for sub in subdirs:
        if required.issubset({p.name for p in sub.iterdir()}):
            ok(f"Zip extracted (subdirectory layout: {sub.name}): {sub}")
            return sub

    err(
        f"Could not find models.py / apps.py / admin.py / __init__.py "
        f"in the zip archive. Expected them at the root or inside a single "
        f"top-level folder."
    )
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def write_file(path: Path, content: str, dry_run: bool, overwrite: bool = False) -> bool:
    if path.exists() and not overwrite:
        warn(f"Already exists, skipping: {path}")
        return False
    action = "Would write" if dry_run else "Writing"
    info(f"{action}: {path}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True


def model_names_from_file(models_py: Path) -> list:
    src = models_py.read_text(encoding="utf-8")
    return re.findall(r"^class (\w+)\(models\.Model\)", src, re.M)


# ─────────────────────────────────────────────────────────────────────────────
# Project layout detection
# ─────────────────────────────────────────────────────────────────────────────
SKIP_DIRS = {"venv", ".venv", "env", ".env", "node_modules", "__pycache__", ".git"}


def find_file(root: Path, filename: str, must_contain: str = "") -> Path | None:
    for candidate in sorted(root.rglob(filename)):
        if any(p in SKIP_DIRS for p in candidate.parts):
            continue
        if must_contain and must_contain not in candidate.read_text(encoding="utf-8"):
            continue
        return candidate
    return None


def detect_project(root: Path) -> dict:
    manage   = find_file(root, "manage.py")
    settings = find_file(root, "settings.py", must_contain="INSTALLED_APPS")
    urls     = find_file(root, "urls.py",     must_contain="admin")

    if not manage:
        raise FileNotFoundError(f"No manage.py found under {root}")
    if not settings:
        raise FileNotFoundError(f"No settings.py (with INSTALLED_APPS) found under {root}")
    if not urls:
        raise FileNotFoundError(f"No project-level urls.py (with admin route) found under {root}")

    compose = (find_file(root, "docker-compose.yml") or
               find_file(root, "docker-compose.yaml"))

    rel = settings.relative_to(root)
    settings_module = ".".join(rel.with_suffix("").parts)

    return {
        "settings":        settings,
        "urls":            urls,
        "manage":          manage,
        "settings_module": settings_module,
        "is_docker":       compose is not None,
        "compose_file":    compose,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Validate input
# ─────────────────────────────────────────────────────────────────────────────
def validate_input(input_path: Path):
    required = ["models.py", "apps.py", "admin.py", "__init__.py"]
    missing  = [f for f in required if not (input_path / f).exists()]
    if missing:
        err(f"Input folder is missing required files: {', '.join(missing)}")
        sys.exit(1)
    ok(f"Input folder validated: {input_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3a — First-time copy
# ─────────────────────────────────────────────────────────────────────────────
def copy_app(input_path: Path, target_root: Path, app_name: str, dry_run: bool) -> Path:
    dest = target_root / app_name
    info(f"{'Would copy' if dry_run else 'Copying'} app → {dest}")
    if not dry_run:
        shutil.copytree(str(input_path), str(dest))
        _fix_apps_py(dest / "apps.py", app_name)
    ok(f"App folder ready: {dest}")
    return dest


def _fix_apps_py(apps_py: Path, app_name: str):
    if not apps_py.exists():
        return
    src = apps_py.read_text(encoding="utf-8")
    src = re.sub(r"name\s*=\s*['\"][^'\"]*['\"]", f"name = '{app_name}'", src)
    pascal = "".join(w.capitalize() for w in re.split(r"[_\s]+", app_name))
    src = re.sub(r"^class \w+Config", f"class {pascal}Config", src, flags=re.M)
    apps_py.write_text(src, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3b — Sync existing app (models + admin + views + urls)
# ─────────────────────────────────────────────────────────────────────────────
def sync_app(input_path: Path, app_dest: Path, app_name: str, dry_run: bool) -> SyncReport:
    """
    Diff incoming models.py against existing models.py and apply safe changes:
      - Add new model classes
      - Add new fields to existing models
      - Warn (annotate) changed field definitions
      - Never remove fields or models
    Also syncs admin.py, views.py, app urls.py for newly added models.
    """
    src_models_py = input_path   / "models.py"
    dst_models_py = app_dest     / "models.py"

    info("Parsing source models …")
    src_models = parse_models_file(src_models_py)
    info("Parsing target models …")
    dst_models = parse_models_file(dst_models_py)

    report = compute_sync(src_models, dst_models)

    # ── Print diff summary ────────────────────────────────────────────────────
    print()
    print(f"  {'Models in source':30s} {len(src_models)}")
    print(f"  {'Models in target':30s} {len(dst_models)}")
    print(f"  {'New models to add':30s} {len(report.new_models)}")
    total_new_fields = sum(len(v) for v in report.added_fields.values())
    print(f"  {'New fields to add':30s} {total_new_fields}")
    total_warn = sum(len(v) for v in report.changed_fields.values())
    print(f"  {'Changed fields (warn only)':30s} {total_warn}")
    total_removed = sum(len(v) for v in report.removed_fields.values())
    print(f"  {'Fields only in target (kept)':30s} {total_removed}")
    print(f"  {'Unchanged models':30s} {len(report.unchanged)}")
    print()

    if not report.has_changes():
        ok("Models are already up to date — nothing to sync.")
        return report

    # ── Apply model changes ───────────────────────────────────────────────────
    actions = apply_sync(report, dst_models_py, src_models_py, dry_run)

    for action in actions:
        if action.startswith("ADD"):
            ok(action)
        elif action.startswith("WARN"):
            warn(action)
        else:
            info(action)

    if not dry_run and actions:
        ok(f"models.py updated (backup saved as models.py.bak)")

    # ── Sync admin.py for new models ──────────────────────────────────────────
    if report.new_models:
        new_names = [m.name for m in report.new_models]
        _sync_admin(app_dest, new_names, dry_run)
        _sync_views(app_dest, app_name, new_names, dry_run)
        _sync_app_urls(app_dest, app_name, new_names, dry_run)
        _sync_templates(app_dest.parent, app_name, new_names, dry_run)

    return report


def replace_models(input_path: Path, app_dest: Path, app_name: str, dry_run: bool):
    """
    --mode replace
    Overwrite the target models.py (and admin.py) entirely with the incoming
    versions. Backs up the originals first as .bak files.
    Also syncs views, urls, and templates for any models that are new.
    """
    src_models_py = input_path / "models.py"
    dst_models_py = app_dest  / "models.py"
    src_admin_py  = input_path / "admin.py"
    dst_admin_py  = app_dest  / "admin.py"

    # Compute which models are new (for views/urls/templates sync)
    src_models = parse_models_file(src_models_py)
    dst_models = parse_models_file(dst_models_py) if dst_models_py.exists() else {}
    new_names  = [n for n in src_models if n not in dst_models]

    print()
    print(f"  {'Models in source':30s} {len(src_models)}")
    print(f"  {'Models already in target':30s} {len(dst_models)}")
    print(f"  {'Models to overwrite':30s} {len([n for n in src_models if n in dst_models])}")
    print(f"  {'New models added':30s} {len(new_names)}")
    print()

    for src_path, dst_path in [(src_models_py, dst_models_py),
                                (src_admin_py,  dst_admin_py)]:
        if not src_path.exists():
            continue
        action = "Would replace" if dry_run else "Replacing"
        info(f"{action}: {dst_path}")
        if not dry_run:
            if dst_path.exists():
                dst_path.with_suffix(".py.bak").write_text(
                    dst_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            shutil.copy2(str(src_path), str(dst_path))
        ok(f"{'Would replace' if dry_run else 'Replaced'}: {dst_path.name} (backup saved as {dst_path.stem}.py.bak)")

    # Sync views/urls/templates for brand-new models only
    if new_names:
        _sync_views(app_dest, app_name, new_names, dry_run)
        _sync_app_urls(app_dest, app_name, new_names, dry_run)
        _sync_templates(app_dest.parent, app_name, new_names, dry_run)


def new_only_sync(input_path: Path, app_dest: Path, app_name: str, dry_run: bool) -> list:
    """
    --mode new-only
    Only add models that do not yet exist in the target. All existing models
    (and their fields) are left completely untouched.
    Returns list of new model names added.
    """
    src_models_py = input_path / "models.py"
    dst_models_py = app_dest  / "models.py"

    src_models = parse_models_file(src_models_py)
    dst_models = parse_models_file(dst_models_py) if dst_models_py.exists() else {}

    new_model_infos = [mi for name, mi in src_models.items() if name not in dst_models]
    skipped         = [n for n in src_models if n in dst_models]

    print()
    print(f"  {'Models in source':30s} {len(src_models)}")
    print(f"  {'Already in target (skipped)':30s} {len(skipped)}")
    print(f"  {'New models to add':30s} {len(new_model_infos)}")
    print()

    if not new_model_infos:
        ok("No new models found — target is already up to date.")
        return []

    for mi in new_model_infos:
        info(f"{'Would add' if dry_run else 'Adding'} model: {mi.name}")

    if not dry_run:
        # Build a minimal SyncReport with only new_models populated
        from model_sync import SyncReport, apply_sync
        report = SyncReport(new_models=new_model_infos)
        dst_models_py.with_suffix(".py.bak").write_text(
            dst_models_py.read_text(encoding="utf-8"), encoding="utf-8"
        )
        apply_sync(report, dst_models_py, src_models_py, dry_run=False)
        ok(f"models.py updated (backup saved as models.py.bak)")

    new_names = [mi.name for mi in new_model_infos]
    _sync_admin(app_dest, new_names, dry_run)
    _sync_views(app_dest, app_name, new_names, dry_run)
    _sync_app_urls(app_dest, app_name, new_names, dry_run)
    _sync_templates(app_dest.parent, app_name, new_names, dry_run)

    return new_names


def _sync_admin(app_dest: Path, new_names: list, dry_run: bool):
    """Append @admin.register blocks for new models to admin.py."""
    admin_py = app_dest / "admin.py"
    if not admin_py.exists():
        warn("admin.py not found — skipping admin sync.")
        return

    content = admin_py.read_text(encoding="utf-8")
    blocks  = []

    for name in new_names:
        if f"@admin.register({name})" in content:
            continue
        blocks.append(
            f"\n\n@admin.register({name})\n"
            f"class {name}Admin(admin.ModelAdmin):\n"
            f"    pass\n"
        )
        info(f"{'Would add' if dry_run else 'Adding'} admin.register({name})")

    if not blocks:
        ok("admin.py already has all new models registered.")
        return

    # Also make sure new model names are in the import line
    import_match = re.search(r"from \.models import \((.*?)\)", content, re.DOTALL)
    if import_match:
        existing_imports = import_match.group(1)
        to_add = [n for n in new_names if n not in existing_imports]
        if to_add:
            new_imports = existing_imports.rstrip().rstrip(",") + ",\n    " + ",\n    ".join(to_add) + ",\n"
            content = content.replace(import_match.group(1), new_imports)

    new_content = content + "".join(blocks)
    action = "Would update" if dry_run else "Updating"
    info(f"{action}: {admin_py}")
    if not dry_run:
        admin_py.write_text(new_content, encoding="utf-8")


def _sync_views(app_dest: Path, app_name: str, new_names: list, dry_run: bool):
    """Append ListView/DetailView classes for new models to views.py."""
    views_py = app_dest / "views.py"
    if not views_py.exists():
        warn("views.py not found — skipping views sync.")
        return

    content = views_py.read_text(encoding="utf-8")
    blocks  = []
    import_additions = []

    for name in new_names:
        slug = name.lower()
        if f"class {name}ListView" in content:
            continue
        import_additions.append(name)
        blocks.append(
            f"\n\nclass {name}ListView(ListView):\n"
            f'    model = {name}\n'
            f'    template_name = "{app_name}/{slug}_list.html"\n'
            f'    context_object_name = "{slug}_list"\n'
            f'    paginate_by = 25\n'
            f"\n\n"
            f"class {name}DetailView(DetailView):\n"
            f'    model = {name}\n'
            f'    template_name = "{app_name}/{slug}_detail.html"\n'
            f'    context_object_name = "{slug}"\n'
        )
        info(f"{'Would add' if dry_run else 'Adding'} {name}ListView + {name}DetailView")

    if not blocks:
        ok("views.py already has all new model views.")
        return

    # Add new model names to the .models import
    if import_additions:
        imp_match = re.search(r"from \.models import \((.*?)\)", content, re.DOTALL)
        if imp_match:
            existing = imp_match.group(1)
            to_add   = [n for n in import_additions if n not in existing]
            if to_add:
                new_imp = existing.rstrip().rstrip(",") + ",\n    " + ",\n    ".join(to_add) + ",\n"
                content = content.replace(imp_match.group(1), new_imp)

    new_content = content + "".join(blocks)
    action = "Would update" if dry_run else "Updating"
    info(f"{action}: {views_py}")
    if not dry_run:
        views_py.write_text(new_content, encoding="utf-8")


def _sync_app_urls(app_dest: Path, app_name: str, new_names: list, dry_run: bool):
    """Append URL patterns for new models to the app-level urls.py."""
    urls_py = app_dest / "urls.py"
    if not urls_py.exists():
        warn("App urls.py not found — skipping URL sync.")
        return

    content = urls_py.read_text(encoding="utf-8")
    new_patterns = []
    import_additions = []

    for name in new_names:
        slug = name.lower()
        if f'name="{slug}-list"' in content:
            continue
        import_additions.append(f"{name}ListView, {name}DetailView")
        new_patterns.append(
            f'    path("{slug}/",          {name}ListView.as_view(),   name="{slug}-list"),'
        )
        new_patterns.append(
            f'    path("{slug}/<int:pk>/", {name}DetailView.as_view(), name="{slug}-detail"),'
        )
        info(f"{'Would add' if dry_run else 'Adding'} URL patterns for {name}")

    if not new_patterns:
        ok("app urls.py already has all new model routes.")
        return

    # Add imports
    if import_additions:
        imp_match = re.search(r"from \.views import \((.*?)\)", content, re.DOTALL)
        if imp_match:
            existing = imp_match.group(1)
            to_add   = [p for p in import_additions if p.split(",")[0].strip() not in existing]
            if to_add:
                new_imp = existing.rstrip().rstrip(",") + ",\n    " + ",\n    ".join(to_add) + ",\n"
                content = content.replace(imp_match.group(1), new_imp)

    # Insert new patterns before the closing ] of urlpatterns
    lines = content.splitlines(keepends=True)
    in_block = False
    depth    = 0
    insert_before = None
    for idx, line in enumerate(lines):
        if not in_block and re.match(r"\s*urlpatterns\s*=\s*\[", line):
            in_block = True
        if in_block:
            depth += line.count("[") - line.count("]")
            if depth <= 0:
                insert_before = idx
                break

    if insert_before is not None:
        for i, pat in enumerate(new_patterns):
            lines.insert(insert_before + i, pat + "\n")
        content = "".join(lines)

    action = "Would update" if dry_run else "Updating"
    info(f"{action}: {urls_py}")
    if not dry_run:
        urls_py.write_text(content, encoding="utf-8")


def _sync_templates(project_root: Path, app_name: str, new_names: list, dry_run: bool):
    """Create stub templates for new models."""
    tmpl_dir = project_root / "templates" / app_name
    if not dry_run:
        tmpl_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    for name in new_names:
        slug = name.lower()
        for suffix, title in [("list", f"{name} List"), ("detail", f"{name} Detail")]:
            tmpl_path = tmpl_dir / f"{slug}_{suffix}.html"
            if not tmpl_path.exists():
                content = (
                    '{% extends "base.html" %}\n'
                    "{% block content %}\n"
                    f"<h1>{title}</h1>\n"
                    "{# TODO: replace with real template content #}\n"
                    "{% endblock %}\n"
                )
                if not dry_run:
                    tmpl_path.write_text(content, encoding="utf-8")
                created += 1

    if created:
        ok(f"Created {created} template stubs in templates/{app_name}/")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Patch settings.py
# ─────────────────────────────────────────────────────────────────────────────
def patch_settings(settings_path: Path, app_name: str, dry_run: bool):
    content = settings_path.read_text(encoding="utf-8")

    if f'"{app_name}"' in content or f"'{app_name}'" in content:
        ok(f"'{app_name}' already in INSTALLED_APPS.")
        return

    lines    = content.splitlines(keepends=True)
    in_block = False
    depth    = 0
    insert_before = None

    for idx, line in enumerate(lines):
        if not in_block and re.match(r"\s*INSTALLED_APPS\s*=\s*\[", line):
            in_block = True
        if in_block:
            depth += line.count("[") - line.count("]")
            if depth <= 0:
                insert_before = idx
                break

    if insert_before is not None:
        prev = insert_before - 1
        while prev >= 0 and not lines[prev].strip():
            prev -= 1
        prev_line = lines[prev].rstrip("\n")
        if prev_line.rstrip() and not prev_line.rstrip().endswith(","):
            lines[prev] = prev_line + ",\n"
        lines.insert(insert_before, f'    "{app_name}",\n')
        info(f"{'Would patch' if dry_run else 'Patching'} INSTALLED_APPS: {settings_path}")
        if not dry_run:
            settings_path.write_text("".join(lines), encoding="utf-8")
        ok(f"Added '{app_name}' to INSTALLED_APPS")
    else:
        warn("Could not locate INSTALLED_APPS — manual edit required.")

    # MEDIA_ROOT
    content = settings_path.read_text(encoding="utf-8")
    if "MEDIA_ROOT" not in content:
        block = (
            "\n# Media files\n"
            'MEDIA_URL = "/media/"\n'
            'MEDIA_ROOT = BASE_DIR / "media"\n'
        )
        if not dry_run:
            with open(settings_path, "a", encoding="utf-8") as fh:
                fh.write(block)
        ok("Added MEDIA_ROOT / MEDIA_URL to settings.py")
    else:
        ok("MEDIA_ROOT already present.")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Patch project urls.py
# ─────────────────────────────────────────────────────────────────────────────
def patch_urls(urls_path: Path, app_name: str, dry_run: bool):
    content = urls_path.read_text(encoding="utf-8")

    if (f'"{app_name}.urls"' in content or
            f"'{app_name}.urls'" in content):
        ok(f"URL include for '{app_name}' already present.")
        return

    if "include" not in content:
        content = content.replace(
            "from django.urls import path",
            "from django.urls import path, include",
        )

    new_url_line = (
        f'    path("{app_name}/", include("{app_name}.urls",'
        f' namespace="{app_name}")),\n'
    )

    # Django-CMS: insert before cms.urls catch-all
    cms_marker = '    path("", include("cms.urls"))'
    if cms_marker in content:
        content = content.replace(cms_marker, new_url_line + cms_marker)
        note = "inserted before cms.urls"
    else:
        # Plain urlpatterns: insert before closing ]
        lines = content.splitlines(keepends=True)
        in_block = False
        depth    = 0
        insert_before = None
        for idx, line in enumerate(lines):
            if not in_block and re.match(r"\s*urlpatterns\s*=\s*\[", line):
                in_block = True
            if in_block:
                depth += line.count("[") - line.count("]")
                if depth <= 0:
                    insert_before = idx
                    break
        if insert_before is None:
            warn("Could not locate urlpatterns — manual URL edit required.")
            return
        lines.insert(insert_before, new_url_line)
        content = "".join(lines)
        note = "appended to urlpatterns"

    info(f"{'Would patch' if dry_run else 'Patching'} urls.py ({note}): {urls_path}")
    if not dry_run:
        urls_path.write_text(content, encoding="utf-8")
    ok(f"URL include added for '{app_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Generate views.py (first-time)
# ─────────────────────────────────────────────────────────────────────────────
def generate_views(app_dest: Path, app_name: str, dry_run: bool):
    views_path = app_dest / "views.py"
    if views_path.exists():
        warn("views.py already exists — skipping (use sync for updates).")
        return

    names       = model_names_from_file(app_dest / "models.py")
    import_list = ",\n    ".join(names)
    view_blocks = []
    for name in names:
        slug = name.lower()
        view_blocks.append(
            f"class {name}ListView(ListView):\n"
            f'    model = {name}\n'
            f'    template_name = "{app_name}/{slug}_list.html"\n'
            f'    context_object_name = "{slug}_list"\n'
            f'    paginate_by = 25\n'
            f"\n\n"
            f"class {name}DetailView(DetailView):\n"
            f'    model = {name}\n'
            f'    template_name = "{app_name}/{slug}_detail.html"\n'
            f'    context_object_name = "{slug}"\n'
        )
    header = (
        f'"""\nAuto-generated views for \'{app_name}\'.\n"""\n'
        f'from django.views.generic import ListView, DetailView\n'
        f'from .models import (\n    {import_list},\n)\n\n\n'
    )
    write_file(views_path, header + "\n\n".join(view_blocks) + "\n", dry_run)
    ok(f"Generated views.py ({len(names)} model pairs)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Generate app urls.py (first-time)
# ─────────────────────────────────────────────────────────────────────────────
def generate_app_urls(app_dest: Path, app_name: str, dry_run: bool):
    urls_path = app_dest / "urls.py"
    if urls_path.exists():
        warn("App urls.py already exists — skipping (use sync for updates).")
        return

    names      = model_names_from_file(app_dest / "models.py")
    view_pairs = []
    patterns   = []
    for name in names:
        slug = name.lower()
        view_pairs.append(f"{name}ListView, {name}DetailView")
        patterns.append(
            f'    path("{slug}/",          {name}ListView.as_view(),   name="{slug}-list"),'
        )
        patterns.append(
            f'    path("{slug}/<int:pk>/", {name}DetailView.as_view(), name="{slug}-detail"),'
        )

    content = (
        f'"""\nAuto-generated URL conf for \'{app_name}\'.\n"""\n'
        f'from django.urls import path\n'
        f'from .views import (\n    {",\n    ".join(view_pairs)},\n)\n\n'
        f'app_name = "{app_name}"\n\n'
        f'urlpatterns = [\n' + "\n".join(patterns) + "\n]\n"
    )
    write_file(urls_path, content, dry_run)
    ok(f"Generated app urls.py ({len(names) * 2} patterns)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — Generate template stubs (first-time)
# ─────────────────────────────────────────────────────────────────────────────
def generate_templates(target_root: Path, app_dest: Path, app_name: str, dry_run: bool):
    tmpl_dir = target_root / "templates" / app_name
    names    = model_names_from_file(app_dest / "models.py")
    info(f"{'Would create' if dry_run else 'Creating'} templates: {tmpl_dir}")
    if not dry_run:
        tmpl_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    for name in names:
        slug = name.lower()
        for suffix, title in [("list", f"{name} List"), ("detail", f"{name} Detail")]:
            tmpl = tmpl_dir / f"{slug}_{suffix}.html"
            if not tmpl.exists():
                content = (
                    '{% extends "base.html" %}\n{% block content %}\n'
                    f"<h1>{title}</h1>\n"
                    "{# TODO: replace with real template content #}\n"
                    "{% endblock %}\n"
                )
                if not dry_run:
                    tmpl.write_text(content, encoding="utf-8")
                created += 1

    ok(f"Templates ready ({created} stubs created)")


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — Syntax validation
# ─────────────────────────────────────────────────────────────────────────────
def validate_output(app_dest: Path):
    errors = []
    for py_file in sorted(app_dest.glob("*.py")):
        try:
            ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"{py_file.name}: {exc}")
    if errors:
        for e in errors:
            err(f"Syntax error — {e}")
        sys.exit(1)
    ok("Syntax check passed for all .py files")


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 — Docker service detection
# ─────────────────────────────────────────────────────────────────────────────
def detect_docker_service(compose_file: Path) -> str:
    src     = compose_file.read_text(encoding="utf-8")
    current = None
    blocks  = {}
    for line in src.splitlines():
        m = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", line)
        if m:
            current = m.group(1)
            blocks[current] = []
        elif current:
            blocks[current].append(line)
    for name, lines in blocks.items():
        if "manage.py" in "\n".join(lines) or "runserver" in "\n".join(lines):
            return name
    return "web"


# ─────────────────────────────────────────────────────────────────────────────
# Step 11 — Run migrations
# ─────────────────────────────────────────────────────────────────────────────
def run_migrations(layout: dict, app_name: str, target_root: Path, dry_run: bool):
    compose_file = layout.get("compose_file")
    if layout["is_docker"]:
        svc  = detect_docker_service(compose_file)
        ok(f"Docker web service: '{svc}'")
        base = [
            "docker", "compose", "-f", str(compose_file),
            "run", "--rm", svc, "python", "manage.py",
        ]
    else:
        base = [sys.executable, str(layout["manage"])]

    for cmd in [base + ["makemigrations", app_name], base + ["migrate"]]:
        display = " ".join(cmd)
        if dry_run:
            info(f"Would run: {display}")
            continue
        info(f"Running: {display}")
        result = subprocess.run(cmd, cwd=str(target_root))
        if result.returncode != 0:
            err(f"Command failed (exit {result.returncode}): {display}")
            sys.exit(result.returncode)
        ok(f"Done: {display}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    args        = parse_args()
    raw_input   = Path(args.input).resolve()
    target_root = Path(args.target).resolve()
    mode        = args.mode          # "update" | "replace" | "new-only"
    dry_run     = args.dry_run
    no_migrate  = args.no_migrate

    # ── Step 1 · Resolve input (folder or zip) ────────────────────────────────
    head("Step 1 · Resolving input")
    if raw_input.suffix.lower() == ".zip":
        info(f"Zip archive detected: {raw_input}")
        input_path = extract_zip_input(raw_input)
        # Default app name from the zip filename (strip .zip)
        default_app_name = raw_input.stem
    elif raw_input.is_dir():
        input_path       = raw_input
        default_app_name = raw_input.name
    else:
        err(f"Input must be a folder or a .zip file: {raw_input}")
        sys.exit(1)

    app_name = args.app_name or default_app_name

    head("Django App Deployer")
    print(f"  Input       : {raw_input}")
    if raw_input.suffix.lower() == ".zip":
        print(f"  Extracted to: {input_path}")
    print(f"  Target root : {target_root}")
    print(f"  App name    : {app_name}")
    print(f"  Mode        : {mode}")
    if dry_run:
        print(f"  {YELLOW}DRY RUN — no files will be written{RESET}")

    validate_input(input_path)
    if not target_root.is_dir():
        err(f"Target project root not found: {target_root}")
        sys.exit(1)

    # ── Step 2 · Detect project layout ───────────────────────────────────────
    head("Step 2 · Detecting project layout")
    try:
        layout = detect_project(target_root)
    except FileNotFoundError as exc:
        err(str(exc))
        sys.exit(1)
    ok(f"manage.py      : {layout['manage']}")
    ok(f"settings.py    : {layout['settings']}")
    ok(f"urls.py        : {layout['urls']}")
    ok(f"Settings module: {layout['settings_module']}")
    ok(f"Docker project : {'yes' if layout['is_docker'] else 'no'}")

    # ── Step 3 · Deploy / sync ────────────────────────────────────────────────
    app_dest = target_root / app_name
    is_fresh = not app_dest.exists()

    if is_fresh:
        head("Step 3 · FRESH DEPLOY — copying app")
        copy_app(input_path, target_root, app_name, dry_run)
        working_app = app_dest if (not dry_run and app_dest.exists()) else input_path

        head("Step 4 · Patching settings.py")
        patch_settings(layout["settings"], app_name, dry_run)

        head("Step 5 · Patching project urls.py")
        patch_urls(layout["urls"], app_name, dry_run)

        head("Step 6 · Generating views.py")
        generate_views(working_app, app_name, dry_run)

        head("Step 7 · Generating app urls.py")
        generate_app_urls(working_app, app_name, dry_run)

        head("Step 8 · Generating template stubs")
        generate_templates(target_root, working_app, app_name, dry_run)

    else:
        ok(f"App '{app_name}' already exists at {app_dest}")

        if mode == "replace":
            head(f"Step 3 · REPLACE — overwriting models.py entirely")
            replace_models(input_path, app_dest, app_name, dry_run)

        elif mode == "new-only":
            head(f"Step 3 · NEW-ONLY — adding missing models, skipping existing ones")
            new_only_sync(input_path, app_dest, app_name, dry_run)

        else:  # update (default)
            head(f"Step 3 · UPDATE — syncing models (add new, warn on changes)")
            sync_app(input_path, app_dest, app_name, dry_run)

        # settings + urls are idempotent — safe to call in every mode
        head("Step 4 · Verifying settings.py")
        patch_settings(layout["settings"], app_name, dry_run)

        head("Step 5 · Verifying project urls.py")
        patch_urls(layout["urls"], app_name, dry_run)

    # ── Step 9 · Syntax validation ────────────────────────────────────────────
    if not dry_run and (target_root / app_name).exists():
        head("Step 9 · Syntax validation")
        validate_output(target_root / app_name)

    # ── Step 10 · Migrations ──────────────────────────────────────────────────
    if no_migrate:
        warn("Skipping migrations (--no-migrate).")
    else:
        head("Step 10 · Running migrations")
        run_migrations(layout, app_name, target_root, dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    head("Done")
    verb = "deployed to" if is_fresh else f"synced ({mode}) in"
    ok(f"App '{app_name}' {verb} {target_root}")
    print()
    print("  Next steps:")
    if is_fresh or mode == "update":
        print(f"    1. Review {app_dest}/models.py")
        if mode == "update" and not is_fresh:
            print(f"       → fields marked '# ⚠ SYNC' need manual review")
            print(f"       → backup saved as models.py.bak")
        else:
            print(f"       → check on_delete, blank/null, field types")
    elif mode == "replace":
        print(f"    1. Verify the replaced models.py at {app_dest}/models.py")
        print(f"       → backup saved as models.py.bak and admin.py.bak")
    elif mode == "new-only":
        print(f"    1. New models added to {app_dest}/models.py")
        print(f"       → backup saved as models.py.bak")
    print(f"    2. Customise views and templates in templates/{app_name}/")
    if layout["is_docker"]:
        print(f"    3. docker compose -f {layout['compose_file']} up --build")
    else:
        print(f"    3. python manage.py runserver")
    print()


if __name__ == "__main__":
    main()
