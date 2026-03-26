# ModelDev — Django Model Builder

A self-contained web application for designing Django models visually.  
Define **Clazzes** (models), **Fields**, and **Sections** through a browser UI, then export ready-to-use Django source files or import existing app code.

---

## Table of Contents

1. [Features](#features)
2. [Quick Start — Docker (recommended)](#quick-start--docker-recommended)
3. [Quick Start — Local Development](#quick-start--local-development)
4. [Deploying to a Server](#deploying-to-a-server)
   - [Environment Variables](#environment-variables)
   - [Behind a Reverse Proxy (Nginx)](#behind-a-reverse-proxy-nginx)
   - [Using PostgreSQL instead of SQLite](#using-postgresql-instead-of-sqlite)
5. [Project Structure](#project-structure)
6. [Usage Guide](#usage-guide)
   - [Clazzes](#clazzes)
   - [Fields](#fields)
   - [Sections & Related Tables](#sections--related-tables)
   - [Import](#import)
   - [Export](#export)
   - [Reset Workspace](#reset-workspace)
7. [Running Tests](#running-tests)
8. [Contributing](#contributing)

---

## Features

| Feature | Description |
|---|---|
| **Clazz builder** | Create Django model definitions with full `Meta` support — `verbose_name`, `ordering`, `db_table`, `abstract`, `app_label` |
| **35+ field types** | Every Django field type with all common options (`null`, `blank`, `unique`, `max_length`, `choices`, `default`, relations, …) |
| **Sections** | Group fields into named fieldsets; drag-and-drop to reorder |
| **Related Tables** | Attach inline-editable child models (TabularInline / StackedInline) to sections |
| **List view config** | Define `list_display`, `search_fields`, `list_filter`, `date_hierarchy` per model |
| **Import wizard** | Upload `models.py` + `admin.py` + `apps.py` (or a zip archive) — AST parser reconstructs all models, fields, sections and inlines |
| **Export** | Download a zip containing `models.py`, `admin.py`, `apps.py`, `__init__.py`, optional `views.py`/`urls.py`/`forms.py`, and a README |
| **Code preview** | See the generated `models.py` snippet for any Clazz and copy it to the clipboard |
| **Reset workspace** | Clear everything and optionally reload the built-in `Property` + `PropertyImage` starter examples |
| **Dark UI** | Bootstrap 5 dark theme, responsive |

---

## Quick Start — Docker (recommended)

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) ≥ 24
- [Docker Compose](https://docs.docker.com/compose/install/) ≥ 2 (included with Docker Desktop)

### Run in one command

```bash
git clone git@github.com:pnigroh/model_developer.git
cd model_developer
docker compose up -d
```

Open **http://localhost:8080** and log in with `admin` / `admin`.

### Stop

```bash
docker compose down
```

Data is persisted in a named Docker volume (`modeldev_db`). It survives container restarts and rebuilds.

### Rebuild after code changes

```bash
docker compose up -d --build
```

---

## Quick Start — Local Development

### Prerequisites

- Python 3.11 or 3.12
- pip

### Setup

```bash
git clone git@github.com:pnigroh/model_developer.git
cd model_developer

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

mkdir -p db
python manage.py migrate
python manage.py setup_dev         # creates admin user + example data

python manage.py runserver
```

Open **http://localhost:8000** and log in with `admin` / `admin`.

---

## Deploying to a Server

### 1. Clone the repository

```bash
git clone git@github.com:pnigroh/model_developer.git
cd model_developer
```

### 2. Create your `.env` file

```bash
cp .env.example .env
nano .env          # fill in SECRET_KEY, ALLOWED_HOSTS, credentials
```

### 3. Point docker-compose at your `.env`

Edit `docker-compose.yml` and replace the inline `environment:` block with:

```yaml
services:
  web:
    env_file: .env
```

Or keep individual variables and edit them directly.

### 4. Build and start

```bash
docker compose up -d --build
```

The container automatically:
1. Runs database migrations
2. Collects static files
3. Creates the superuser (if it doesn't exist yet)
4. Seeds example data (if the database is empty)
5. Starts Gunicorn on port 8000 (mapped to 8080 on the host by default)

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(insecure dev key)* | Django secret key — **must** be changed in production |
| `DEBUG` | `True` | Set to `False` in production |
| `ALLOWED_HOSTS` | `*` | Comma-separated hostnames, e.g. `modeldev.example.com` |
| `DJANGO_SUPERUSER_USERNAME` | `admin` | Initial superuser username |
| `DJANGO_SUPERUSER_PASSWORD` | `admin` | Initial superuser password — **change this** |
| `DJANGO_SUPERUSER_EMAIL` | `admin@modeldev.local` | Initial superuser email |

### Behind a Reverse Proxy (Nginx)

Example Nginx config (`/etc/nginx/sites-available/modeldev`):

```nginx
server {
    listen 80;
    server_name modeldev.example.com;

    # Redirect HTTP → HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name modeldev.example.com;

    ssl_certificate     /etc/letsencrypt/live/modeldev.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/modeldev.example.com/privkey.pem;

    client_max_body_size 20M;   # allow zip uploads

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

Then update your `.env`:

```env
DEBUG=False
ALLOWED_HOSTS=modeldev.example.com
```

And add to `docker-compose.yml` under the `web` service:

```yaml
environment:
  - DJANGO_SETTINGS_MODULE=modeldev.settings
  # tell Django it's behind HTTPS
  - SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
```

### Using PostgreSQL instead of SQLite

For production workloads you may prefer PostgreSQL.

1. Add a `db` service to `docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: modeldev
      POSTGRES_USER: modeldev
      POSTGRES_PASSWORD: secret
    volumes:
      - modeldev_pg:/var/lib/postgresql/data

  web:
    build: .
    depends_on: [db]
    environment:
      - DATABASE_URL=postgres://modeldev:secret@db:5432/modeldev
      # … other vars …

volumes:
  modeldev_pg:
```

2. Add `dj-database-url` to `requirements.txt`:

```
dj-database-url==2.1.0
```

3. In `modeldev/settings.py` replace the `DATABASES` block with:

```python
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db" / "db.sqlite3"}'
    )
}
```

---

## Project Structure

```
model_developer/
├── builder/                        # Main Django app
│   ├── models.py                   # Clazz, Field, Section, SectionField, RelatedTable
│   ├── views.py                    # All CRUD views + reset
│   ├── import_export_views.py      # Import wizard + export download
│   ├── importer.py                 # AST parser (models.py / admin.py / apps.py)
│   ├── exporter.py                 # Code generator (models, admin, apps, views, …)
│   ├── forms.py                    # Django form classes
│   ├── urls.py                     # URL routing
│   ├── admin.py                    # Django admin registration
│   ├── migrations/                 # Database migrations
│   ├── management/
│   │   └── commands/
│   │       └── setup_dev.py        # First-run superuser + seed data
│   └── tests/
│       ├── test_importer.py        # Parser unit tests
│       └── test_exporter.py        # Code-gen unit tests
├── modeldev/                       # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── templates/
│   ├── base.html                   # Shared layout (navbar, messages)
│   ├── registration/login.html
│   └── builder/                   # Per-view templates
│       ├── dashboard.html
│       ├── clazz_form.html
│       ├── clazz_detail.html
│       ├── clazz_preview.html
│       ├── field_form.html
│       ├── section_form.html
│       ├── section_detail.html
│       ├── related_table_form.html
│       ├── export.html
│       ├── clear_all.html
│       ├── confirm_delete.html
│       └── import/
│           ├── step1.html
│           ├── step2.html
│           ├── step3.html
│           └── _steps.html
├── static/
│   ├── css/app.css                 # Custom dark-theme styles
│   └── js/app.js                   # Auto-fill helpers, drag-drop, clipboard
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh                   # migrate → collectstatic → setup_dev → gunicorn
├── manage.py
├── requirements.txt
├── .env.example                    # Environment variable template
└── README.md
```

---

## Usage Guide

### Clazzes

A **Clazz** is a Django model definition. Create one from the dashboard or the *New Clazz* nav link.

Fields available:

| Field | Purpose |
|---|---|
| Name | PascalCase class name, e.g. `PropertyImage` |
| Verbose name / plural | Human-readable labels |
| App label | Override `Meta.app_label` |
| DB table | Override `Meta.db_table` |
| Ordering | Comma-separated, prefix `-` for descending |
| Abstract | Marks `Meta.abstract = True` |
| List display | Fields shown in the admin list view |
| Search fields | Fields searched by the admin search box |
| List filter | Fields shown in the admin sidebar filter |
| Date hierarchy | Single date field for drill-down navigation |

### Fields

Add fields to a Clazz from its detail page. All 35+ Django field types are supported with their relevant options:

- **Text**: `CharField`, `TextField`, `SlugField`, `EmailField`, `URLField`, `UUIDField`
- **Numbers**: `IntegerField`, `DecimalField`, `FloatField`, `PositiveIntegerField`, …
- **Boolean**: `BooleanField`
- **Date/Time**: `DateField`, `DateTimeField`, `TimeField` (with `auto_now` / `auto_now_add`)
- **Files**: `FileField`, `ImageField`
- **Relations**: `ForeignKey`, `OneToOneField`, `ManyToManyField` (with `on_delete`, `related_name`)
- **Other**: `JSONField`, `UUIDField`, `BinaryField`

### Sections & Related Tables

**Sections** map to Django admin `fieldsets`. From a section's detail page you can:

- Add/remove fields
- Drag rows to reorder
- Add **Related Tables** — these become `TabularInline` or `StackedInline` classes pointing at a child model. Set the FK field name, number of extra rows, max rows, and which fields to display.

### Import

Go to **Import** in the navbar.

- **Step 1** — Upload `models.py` (required), `admin.py` and `apps.py` (optional) as individual files, or upload a zip archive of your app folder.
- **Step 2** — Review all parsed models. Each card shows fields, sections, inline tables, and admin config extracted from the source. Choose which models to import and whether to **skip** or **overwrite** any that already exist.
- **Step 3** — Confirmation summary: created / updated / skipped / error counts.

The parser handles:

- All standard field types and their keyword arguments
- `class Meta` — `verbose_name`, `verbose_name_plural`, `ordering`, `abstract`, `db_table`
- `fieldsets` → Sections with correct field ordering and collapsed state
- `TabularInline` / `StackedInline` classes → Related Tables
- `list_display`, `search_fields`, `list_filter`, `date_hierarchy`
- `AppConfig` label and verbose_name from `apps.py`
- `GenericForeignKey` (skipped — virtual field, no DB column)

### Export

Go to **Export** in the navbar (or the dashboard button).

1. Select which Clazzes to include
2. Enter an **app name** (used as the Python package name and zip directory)
3. Toggle optional files: `views.py` + `urls.py`, `forms.py`
4. Click **Download Zip**

The zip contains:

```
myapp/
├── __init__.py
├── models.py      ← all model class definitions
├── admin.py       ← @admin.register + ModelAdmin + Inline classes
├── apps.py        ← AppConfig
├── views.py       ← basic CRUD skeletons (optional)
├── urls.py        ← URL patterns (optional)
├── forms.py       ← ModelForm classes (optional)
└── README.md      ← installation instructions
```

Drop the folder into your Django project, add the app name to `INSTALLED_APPS`, then run:

```bash
python manage.py makemigrations myapp
python manage.py migrate
```

### Reset Workspace

**Username menu → Clear Workspace** (or the *Clear All* button on the dashboard).

Shows a count of everything that will be deleted. The **Load starter examples** toggle (on by default) will recreate the `Property` + `PropertyImage` example Clazzes with fields, sections, and a tabular inline immediately after clearing.

---

## Running Tests

```bash
# Parser tests (no database required)
DJANGO_SETTINGS_MODULE=modeldev.settings python builder/tests/test_importer.py

# Exporter / code-gen tests (requires a populated database)
DJANGO_SETTINGS_MODULE=modeldev.settings python builder/tests/test_exporter.py
```

Or inside the running Docker container:

```bash
docker exec modeldev python builder/tests/test_importer.py
docker exec modeldev python builder/tests/test_exporter.py
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes and run the tests
4. Commit with a clear message: `git commit -m "feat: describe what changed"`
5. Push and open a Pull Request

Code style: PEP 8, 4-space indentation, single quotes.
