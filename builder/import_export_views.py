"""
builder/import_export_views.py
───────────────────────────────
Import wizard (3 steps) and export views.

Import flow
───────────
  GET  /import/           → Step 1 — upload form
  POST /import/           → process files, store parsed result in session → redirect Step 2
  GET  /import/preview/   → Step 2 — review parsed clazzes, choose which to import
  POST /import/execute/   → Step 3 — persist chosen clazzes, show summary

Export flow
───────────
  GET  /export/           → selection page (choose clazzes + options)
  POST /export/download/  → build zip + stream to browser
"""

import io
import json
import zipfile
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .importer import ImportResult, ClazzData, parse_files, save_import_result
from .exporter import build_export_zip
from .models import Clazz


# ── Helpers ────────────────────────────────────────────────────────────────────
SESSION_KEY = 'modeldev_import_result'


def _result_to_session(result: ImportResult) -> dict:
    """Serialise ImportResult into a JSON-safe dict for the session."""
    def cd_to_dict(cd: ClazzData) -> dict:
        return {
            # ── Core fields ──────────────────────────────────────────────────
            'name':               cd.name,
            'verbose_name':       cd.verbose_name,
            'verbose_name_plural':cd.verbose_name_plural,
            'app_label':          cd.app_label,
            'db_table':           cd.db_table,
            'ordering':           cd.ordering,
            'abstract':           cd.abstract,
            'description':        cd.description,
            'list_display':       cd.list_display,
            'search_fields':      cd.search_fields,
            'list_filter':        cd.list_filter,
            'date_hierarchy':     cd.date_hierarchy,
            'warnings':           cd.warnings,
            'field_count':        len(cd.fields),
            'section_count':      len(cd.sections),
            'inline_count':       len(cd.related_tables),
            # ── Preview summaries (for the wizard UI) ────────────────────────
            'fields_preview': [
                {
                    'name':       f.name,
                    'field_type': f.field_type,
                    'null':       f.null,
                    'blank':      f.blank,
                    'related':    f.related_model,
                }
                for f in cd.fields
            ],
            'sections_preview': [
                {
                    'name':      s.name,
                    'fields':    s.field_names,
                    'collapsed': s.collapsed,
                }
                for s in cd.sections
            ],
            'inlines_preview': [
                {
                    'related_model': r.related_model,
                    'fk_field':      r.fk_field,
                    'inline_style':  r.inline_style,
                    'section_name':  r.section_name,
                }
                for r in cd.related_tables
            ],
            # ── Full data (for DB save in step 3) ────────────────────────────
            'fields_full': [
                {
                    'name':          f.name,
                    'field_type':    f.field_type,
                    'verbose_name':  f.verbose_name,
                    'null':          f.null,
                    'blank':         f.blank,
                    'unique':        f.unique,
                    'db_index':      f.db_index,
                    'primary_key':   f.primary_key,
                    'editable':      f.editable,
                    'max_length':    f.max_length,
                    'default':       f.default,
                    'help_text':     f.help_text,
                    'choices':       f.choices,
                    'max_digits':    f.max_digits,
                    'decimal_places':f.decimal_places,
                    'auto_now':      f.auto_now,
                    'auto_now_add':  f.auto_now_add,
                    'related_model': f.related_model,
                    'related_name':  f.related_name,
                    'on_delete':     f.on_delete,
                    'order':         f.order,
                }
                for f in cd.fields
            ],
            'sections_full': [
                {
                    'name':        s.name,
                    'description': s.description,
                    'order':       s.order,
                    'collapsed':   s.collapsed,
                    'field_names': s.field_names,
                }
                for s in cd.sections
            ],
            'inlines_full': [
                {
                    'related_model': r.related_model,
                    'fk_field':      r.fk_field,
                    'verbose_name':  r.verbose_name,
                    'inline_style':  r.inline_style,
                    'extra':         r.extra,
                    'max_num':       r.max_num,
                    'fields_display':r.fields_display,
                    'section_name':  r.section_name,
                    'order':         r.order,
                }
                for r in cd.related_tables
            ],
        }

    return {
        'app_label':       result.app_label,
        'app_verbose_name':result.app_verbose_name,
        'errors':          result.errors,
        'warnings':        result.warnings,
        'clazzes':         [cd_to_dict(cd) for cd in result.clazzes],
    }


def _session_to_names(session_data: dict) -> list[str]:
    return [cd['name'] for cd in session_data.get('clazzes', [])]


# ── Step 1 – Upload ────────────────────────────────────────────────────────────
@login_required
def import_step1(request):
    """Upload form: accept individual files or a zip archive."""
    if request.method == 'GET':
        return render(request, 'builder/import/step1.html')

    # ── POST: process uploaded files ──────────────────────────────────────────
    files: dict[str, str] = {}
    errors: list[str] = []

    uploaded_files = request.FILES.getlist('source_files')
    zip_file       = request.FILES.get('zip_file')

    # Process zip
    if zip_file:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_file.read())) as zf:
                for name in zf.namelist():
                    basename = Path(name).name
                    if basename in ('models.py', 'admin.py', 'apps.py') and basename not in files:
                        try:
                            files[basename] = zf.read(name).decode('utf-8', errors='replace')
                        except Exception as e:
                            errors.append(f'Could not read {name} from zip: {e}')
        except zipfile.BadZipFile:
            errors.append('The uploaded file is not a valid zip archive.')

    # Process individual files
    for f in uploaded_files:
        basename = f.name.split('/')[-1]
        if basename in ('models.py', 'admin.py', 'apps.py'):
            try:
                files[basename] = f.read().decode('utf-8', errors='replace')
            except Exception as e:
                errors.append(f'Could not read {f.name}: {e}')

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(request, 'builder/import/step1.html')

    if not files:
        messages.error(request, 'No valid files uploaded. Please upload models.py, admin.py and/or apps.py.')
        return render(request, 'builder/import/step1.html')

    if 'models.py' not in files:
        messages.error(request, 'models.py is required.')
        return render(request, 'builder/import/step1.html')

    # Parse
    result = parse_files(files)

    if result.errors:
        for e in result.errors:
            messages.error(request, e)
        if not result.clazzes:
            return render(request, 'builder/import/step1.html')

    # Store in session
    request.session[SESSION_KEY] = _result_to_session(result)
    request.session.modified = True

    return redirect('import_step2')


# ── Step 2 – Preview & Select ──────────────────────────────────────────────────
@login_required
def import_step2(request):
    """Show parsed results, let user choose which clazzes to import."""
    session_data = request.session.get(SESSION_KEY)
    if not session_data:
        messages.warning(request, 'Import session expired. Please start again.')
        return redirect('import_step1')

    clazzes_data = session_data['clazzes']
    existing_names = set(Clazz.objects.values_list('name', flat=True))

    # Annotate each clazz with conflict info
    for cd in clazzes_data:
        cd['exists'] = cd['name'] in existing_names

    return render(request, 'builder/import/step2.html', {
        'session_data':  session_data,
        'clazzes_data':  clazzes_data,
        'existing_names':existing_names,
        'total':         len(clazzes_data),
        'conflict_count':sum(1 for cd in clazzes_data if cd['name'] in existing_names),
    })


# ── Step 3 – Execute ───────────────────────────────────────────────────────────
@login_required
@require_POST
def import_execute(request):
    """Persist chosen clazzes. Show summary."""
    session_data = request.session.get(SESSION_KEY)
    if not session_data:
        messages.error(request, 'Import session expired.')
        return redirect('import_step1')

    selected_names    = request.POST.getlist('selected_clazzes')
    conflict_strategy = request.POST.get('conflict_strategy', 'skip')

    if not selected_names:
        messages.warning(request, 'No clazzes selected.')
        return redirect('import_step2')

    # Rebuild ImportResult from session data
    from .importer import (
        ImportResult, ClazzData, FieldData, SectionData, RelatedTableData
    )

    # We need the full parsed result — re-parse from source isn't available,
    # but we stored enough for the DB save. We reconstruct minimal objects.
    # For the actual DB save we stored full field/section/inline data earlier,
    # however for the preview we only stored previews. So we need the original
    # parsed data — store it properly in the session.

    # Fetch full data that was stored in session (we stored full field data)
    result = _session_to_import_result(session_data)

    summary = save_import_result(result, selected_names, conflict_strategy)

    # Clear session
    del request.session[SESSION_KEY]
    request.session.modified = True

    return render(request, 'builder/import/step3.html', {
        'summary':          summary,
        'selected_names':   selected_names,
        'conflict_strategy':conflict_strategy,
    })


def _session_to_import_result(session_data: dict) -> 'ImportResult':
    """Reconstruct a minimal ImportResult from session data for DB saving."""
    from .importer import (
        ImportResult, ClazzData, FieldData, SectionData, RelatedTableData
    )

    result = ImportResult(
        app_label       = session_data.get('app_label', ''),
        app_verbose_name= session_data.get('app_verbose_name', ''),
        errors          = session_data.get('errors', []),
        warnings        = session_data.get('warnings', []),
    )

    for cd_raw in session_data.get('clazzes', []):
        cd = ClazzData(
            name               = cd_raw['name'],
            verbose_name       = cd_raw.get('verbose_name', ''),
            verbose_name_plural= cd_raw.get('verbose_name_plural', ''),
            app_label          = cd_raw.get('app_label', ''),
            db_table           = cd_raw.get('db_table', ''),
            ordering           = cd_raw.get('ordering', ''),
            abstract           = cd_raw.get('abstract', False),
            description        = cd_raw.get('description', ''),
            list_display       = cd_raw.get('list_display', ''),
            search_fields      = cd_raw.get('search_fields', ''),
            list_filter        = cd_raw.get('list_filter', ''),
            date_hierarchy     = cd_raw.get('date_hierarchy', ''),
        )

        # Reconstruct FieldData objects from full stored data
        for fd_raw in cd_raw.get('fields_full', []):
            cd.fields.append(FieldData(**fd_raw))

        # Reconstruct SectionData objects
        for sd_raw in cd_raw.get('sections_full', []):
            sd = SectionData(
                name        = sd_raw['name'],
                description = sd_raw.get('description', ''),
                order       = sd_raw.get('order', 0),
                collapsed   = sd_raw.get('collapsed', False),
                field_names = sd_raw.get('field_names', []),
            )
            cd.sections.append(sd)

        # Reconstruct RelatedTableData objects
        for rt_raw in cd_raw.get('inlines_full', []):
            rt = RelatedTableData(
                related_model = rt_raw.get('related_model', ''),
                fk_field      = rt_raw.get('fk_field', ''),
                verbose_name  = rt_raw.get('verbose_name', ''),
                inline_style  = rt_raw.get('inline_style', 'tabular'),
                extra         = rt_raw.get('extra', 1),
                max_num       = rt_raw.get('max_num'),
                fields_display= rt_raw.get('fields_display', ''),
                section_name  = rt_raw.get('section_name', ''),
                order         = rt_raw.get('order', 0),
            )
            cd.related_tables.append(rt)

        result.clazzes.append(cd)

    return result


# ── Export ─────────────────────────────────────────────────────────────────────
@login_required
def export_page(request):
    """Export selection page."""
    clazzes = Clazz.objects.prefetch_related('fields', 'sections').order_by('name')
    return render(request, 'builder/export.html', {'clazzes': clazzes})


@login_required
@require_POST
def export_download(request):
    """Build and stream the zip file."""
    selected_pks_raw = request.POST.getlist('selected_clazzes')
    app_name         = request.POST.get('app_name', 'myapp').strip() or 'myapp'
    include_views    = request.POST.get('include_views') == 'on'
    include_forms    = request.POST.get('include_forms') == 'on'

    try:
        selected_pks = [int(pk) for pk in selected_pks_raw]
    except ValueError:
        messages.error(request, 'Invalid selection.')
        return redirect('export_page')

    if not selected_pks:
        messages.error(request, 'Please select at least one Clazz to export.')
        return redirect('export_page')

    zip_bytes = build_export_zip(
        clazz_pks     = selected_pks,
        app_name      = app_name,
        include_views = include_views,
        include_forms = include_forms,
    )

    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{app_name}.zip"'
    return response
