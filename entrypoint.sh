#!/bin/bash
set -e

echo "──────────────────────────────────────"
echo "  ModelDev – starting up"
echo "──────────────────────────────────────"

# Run migrations
echo "→ Running migrations…"
python manage.py migrate --noinput

# Collect static files
echo "→ Collecting static files…"
python manage.py collectstatic --noinput --clear

# Seed superuser + example data (idempotent)
echo "→ Running first-run setup…"
python manage.py setup_dev

echo "──────────────────────────────────────"
echo "  Starting Gunicorn on :8000"
echo "──────────────────────────────────────"

# Create output directory for generated scripts
mkdir -p /app/output

exec gunicorn modeldev.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
