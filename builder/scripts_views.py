"""
builder/scripts_views.py
─────────────────────────
GUI wrappers for the two Class Migrator scripts that live in scripts/.

generate_django_models.py  — reads a CSV and produces a Django app folder
deploy_app.py              — installs / updates a generated app in a project

Both script pages share the same run-output approach:
  1. A GET form lets the user fill in parameters.
  2. POST validates and builds the argv list, stores it in the session.
  3. The page connects to /scripts/stream/<job_id>/ via EventSource (SSE).
  4. The SSE view spawns the subprocess, streams stdout+stderr line-by-line.

Uploaded files (CSV, zip) are written to MEDIA_ROOT/script_uploads/ and
deleted automatically after the run completes.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.http import require_POST

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'
UPLOAD_DIR  = Path(tempfile.gettempdir()) / 'modeldev_script_uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory job registry: job_id → {'argv': [...], 'label': str}
# Good enough for a single-user dev tool; no Redis needed.
_JOB_REGISTRY: dict = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _save_upload(django_file) -> Path:
    """Save an InMemoryUploadedFile to UPLOAD_DIR and return its Path."""
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{django_file.name}"
    with open(dest, 'wb') as fh:
        for chunk in django_file.chunks():
            fh.write(chunk)
    return dest


def _register_job(argv: list, label: str) -> str:
    job_id = uuid.uuid4().hex
    _JOB_REGISTRY[job_id] = {'argv': argv, 'label': label}
    return job_id


# ── Index ──────────────────────────────────────────────────────────────────────

@login_required
def scripts_index(request):
    return render(request, 'builder/scripts/index.html', {
        'scripts_dir': str(SCRIPTS_DIR),
    })


# ── Step 1: generate_django_models.py ─────────────────────────────────────────

@login_required
def generate_form(request):
    """
    GET  → show the generate form
    POST → validate, save upload, build argv, redirect to run page
    """
    if request.method == 'GET':
        return render(request, 'builder/scripts/generate.html')

    # ── POST ──────────────────────────────────────────────────────────────────
    csv_file   = request.FILES.get('csv_file')
    output_dir = request.POST.get('output_dir', '').strip()
    app_name   = request.POST.get('app_name', '').strip()

    errors = []
    if not csv_file:
        errors.append('A CSV file is required.')
    if not output_dir:
        errors.append('An output directory path is required.')

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(request, 'builder/scripts/generate.html', {
            'post': request.POST,
        })

    csv_path = _save_upload(csv_file)

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / 'generate_django_models.py'),
        '-i', str(csv_path),
        '-o', output_dir,
    ]
    if app_name:
        argv += ['-a', app_name]

    job_id = _register_job(
        argv,
        label=f"Generate from {csv_file.name} → {output_dir}"
    )
    return redirect('scripts_run', job_id=job_id)


# ── Step 2: deploy_app.py ─────────────────────────────────────────────────────

@login_required
def deploy_form(request):
    """
    GET  → show the deploy form
    POST → validate, save upload if needed, build argv, redirect to run page
    """
    MODES = [
        ('update',   'Update (default)',  'Add new models/fields, warn on changed definitions, never delete anything'),
        ('replace',  'Replace',           'Overwrite models.py entirely with incoming version — originals backed up as .bak'),
        ('new-only', 'New only',          'Only add models not yet present; skip all existing models completely'),
    ]

    if request.method == 'GET':
        return render(request, 'builder/scripts/deploy.html', {'modes': MODES})

    # ── POST ──────────────────────────────────────────────────────────────────
    input_type   = request.POST.get('input_type', 'path')   # 'path' | 'upload'
    input_path   = request.POST.get('input_path', '').strip()
    input_upload = request.FILES.get('input_upload')
    target_dir   = request.POST.get('target_dir', '').strip()
    app_name     = request.POST.get('app_name', '').strip()
    mode         = request.POST.get('mode', 'update')
    dry_run      = request.POST.get('dry_run') == 'on'
    no_migrate   = request.POST.get('no_migrate') == 'on'

    errors = []
    resolved_input = None

    if input_type == 'upload':
        if not input_upload:
            errors.append('Please upload an app folder zip or select a path.')
        else:
            resolved_input = str(_save_upload(input_upload))
    else:
        if not input_path:
            errors.append('An input path (folder or zip) is required.')
        elif not Path(input_path).exists():
            errors.append(f'Input path not found: {input_path}')
        else:
            resolved_input = input_path

    if not target_dir:
        errors.append('A target Django project root path is required.')
    elif not Path(target_dir).is_dir():
        errors.append(f'Target project root not found: {target_dir}')

    if mode not in ('update', 'replace', 'new-only'):
        errors.append('Invalid mode.')

    if errors:
        for e in errors:
            messages.error(request, e)
        return render(request, 'builder/scripts/deploy.html', {
            'post': request.POST,
            'modes': MODES,
        })

    argv = [
        sys.executable,
        str(SCRIPTS_DIR / 'deploy_app.py'),
        '-i', resolved_input,
        '-t', target_dir,
        '-m', mode,
    ]
    if app_name:
        argv += ['-a', app_name]
    if dry_run:
        argv.append('--dry-run')
    if no_migrate:
        argv.append('--no-migrate')

    job_id = _register_job(
        argv,
        label=f"Deploy {Path(resolved_input).name} → {target_dir} [{mode}]"
    )
    return redirect('scripts_run', job_id=job_id)


# ── Run page (GET) ─────────────────────────────────────────────────────────────

@login_required
def scripts_run(request, job_id):
    """Render the run page — JS connects to the SSE stream endpoint."""
    job = _JOB_REGISTRY.get(job_id)
    if not job:
        messages.error(request, 'Job not found or already expired.')
        return redirect('scripts_index')
    return render(request, 'builder/scripts/run.html', {
        'job_id': job_id,
        'label':  job['label'],
    })


# ── SSE stream (GET) ───────────────────────────────────────────────────────────

@login_required
def scripts_stream(request, job_id):
    """
    Server-Sent Events endpoint.
    Spawns the subprocess and streams every output line as an SSE event.
    Sends a final  event:done  so the client knows it finished.
    """
    job = _JOB_REGISTRY.pop(job_id, None)
    if not job:
        def _gone():
            yield "data: [error] Job not found or already consumed.\n\n"
            yield "event: done\ndata: 1\n\n"
        return StreamingHttpResponse(_gone(), content_type='text/event-stream')

    argv = job['argv']

    def _event_stream():
        # Run script in scripts/ directory so relative imports (model_sync) work
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            cwd=str(SCRIPTS_DIR),
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            # Encode as SSE: escape any embedded newlines in the data value
            safe = line.rstrip('\n').replace('\n', ' ').replace('\r', '')
            yield f"data: {safe}\n\n"

        proc.wait()
        rc = proc.returncode
        yield f"event: done\ndata: {rc}\n\n"

        # Clean up temp uploaded files that were part of this job
        for arg in argv:
            p = Path(arg)
            if p.is_file() and UPLOAD_DIR in p.parents:
                try:
                    p.unlink()
                except OSError:
                    pass

    response = StreamingHttpResponse(_event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'   # disable Nginx buffering
    return response
