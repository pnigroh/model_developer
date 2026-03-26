"""
builder/scripts_views.py
─────────────────────────
GUI wrappers for the two Class Migrator scripts (scripts/).

Job lifecycle
─────────────
  1. POST /scripts/generate/ or /scripts/deploy/
       → validates params, saves any uploaded file to UPLOAD_DIR
       → stores job dict in the Django session (DB-backed → worker-safe)
       → redirects to /scripts/run/<job_id>/

  2. GET /scripts/run/<job_id>/
       → reads job label from session, renders the terminal page
       → page opens an EventSource to /scripts/stream/<job_id>/

  3. GET /scripts/stream/<job_id>/
       → pops the job from session, spawns the subprocess
       → streams stdout+stderr line-by-line as SSE
       → sends event:done with the exit code when finished
       → cleans up any uploaded temp files
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / 'scripts'

# Where uploaded CSVs/zips are saved while the job is pending.
# This dir lives inside the container; cleaned up after streaming.
UPLOAD_DIR = Path('/tmp/modeldev_script_uploads')
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Default output dir (bind-mounted to host via docker-compose)
DEFAULT_OUTPUT_DIR = '/app/output'

SESSION_KEY = 'modeldev_script_jobs'


# ── Session helpers ────────────────────────────────────────────────────────────

def _store_job(request, job_id: str, argv: list, label: str):
    jobs = request.session.get(SESSION_KEY, {})
    jobs[job_id] = {'argv': argv, 'label': label}
    request.session[SESSION_KEY] = jobs
    request.session.modified = True


def _pop_job(request, job_id: str) -> dict | None:
    jobs = request.session.get(SESSION_KEY, {})
    job  = jobs.pop(job_id, None)
    request.session[SESSION_KEY] = jobs
    request.session.modified = True
    return job


def _peek_job(request, job_id: str) -> dict | None:
    return request.session.get(SESSION_KEY, {}).get(job_id)


def _save_upload(django_file) -> Path:
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{django_file.name}"
    with open(dest, 'wb') as fh:
        for chunk in django_file.chunks():
            fh.write(chunk)
    return dest


# ── Index ──────────────────────────────────────────────────────────────────────

@login_required
def scripts_index(request):
    return render(request, 'builder/scripts/index.html', {
        'scripts_dir':      str(SCRIPTS_DIR),
        'default_output':   DEFAULT_OUTPUT_DIR,
    })


# ── Step 1: generate_django_models.py ─────────────────────────────────────────

@login_required
def generate_form(request):
    TYPES = [
        'Text', 'Memo', 'Blob', 'Image', 'Boolean', 'Money',
        'Decimal number', 'Whole number', 'Email address',
        'Date', 'Date & Time', 'Color', 'Autonumber',
    ]

    if request.method == 'GET':
        return render(request, 'builder/scripts/generate.html', {
            'types':          TYPES,
            'default_output': DEFAULT_OUTPUT_DIR,
        })

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
            'post':           request.POST,
            'types':          TYPES,
            'default_output': DEFAULT_OUTPUT_DIR,
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

    job_id = uuid.uuid4().hex
    _store_job(request, job_id,
               argv=argv,
               label=f"Generate: {csv_file.name} → {output_dir}")
    return redirect('scripts_run', job_id=job_id)


# ── Step 2: deploy_app.py ─────────────────────────────────────────────────────

@login_required
def deploy_form(request):
    MODES = [
        ('update',   'Update',   'Add new models/fields, warn on changes, never delete'),
        ('replace',  'Replace',  'Overwrite models.py entirely — originals backed up as .bak'),
        ('new-only', 'New only', 'Only add models not yet present; skip all existing ones'),
    ]

    if request.method == 'GET':
        return render(request, 'builder/scripts/deploy.html', {'modes': MODES})

    # ── POST ──────────────────────────────────────────────────────────────────
    input_type   = request.POST.get('input_type', 'path')
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
            errors.append('Please upload an app folder zip.')
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
            'post': request.POST, 'modes': MODES,
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

    job_id = uuid.uuid4().hex
    _store_job(request, job_id,
               argv=argv,
               label=f"Deploy: {Path(resolved_input).name} → {target_dir} [{mode}]")
    return redirect('scripts_run', job_id=job_id)


# ── Run page ───────────────────────────────────────────────────────────────────

@login_required
def scripts_run(request, job_id):
    job = _peek_job(request, job_id)
    if not job:
        messages.error(request, 'Job not found — it may have already run or the session expired.')
        return redirect('scripts_index')
    return render(request, 'builder/scripts/run.html', {
        'job_id': job_id,
        'label':  job['label'],
    })


# ── SSE stream ─────────────────────────────────────────────────────────────────

@login_required
def scripts_stream(request, job_id):
    """
    Server-Sent Events endpoint.
    Pops the job from the session, spawns the subprocess,
    and streams every output line as an SSE data event.
    Sends event:done with the exit code when finished.
    """
    job = _pop_job(request, job_id)

    if not job:
        def _gone():
            yield "data: [error] Job not found or already consumed.\n\n"
            yield "event: done\ndata: 1\n\n"
        return StreamingHttpResponse(_gone(), content_type='text/event-stream')

    argv = job['argv']

    def _event_stream():
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            cwd=str(SCRIPTS_DIR),
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            safe = line.rstrip('\n').replace('\r', '')
            yield f"data: {safe}\n\n"

        proc.wait()
        yield f"event: done\ndata: {proc.returncode}\n\n"

        # Clean up temp uploaded files that belonged to this job
        for arg in argv:
            p = Path(arg)
            if p.is_file() and p.parent == UPLOAD_DIR:
                try:
                    p.unlink()
                except OSError:
                    pass

    response = StreamingHttpResponse(_event_stream(), content_type='text/event-stream')
    response['Cache-Control']      = 'no-cache'
    response['X-Accel-Buffering']  = 'no'
    return response
