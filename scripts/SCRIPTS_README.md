# Class Migrator

A two-script toolkit for migrating class/field definitions from a legacy system
export (CSV) into a fully wired Django / Django-CMS application.

---

## Files

| File | Purpose |
|---|---|
| `generate_django_models.py` | Step 1 — Reads the CSV export and produces a Django app folder |
| `deploy_app.py` | Step 2 — Installs the generated app into a target Django project |
| `model_sync.py` | Internal module used by `deploy_app.py` for field-level diffing |
| `django_models/` | Last output produced by `generate_django_models.py` |

---

## Step 1 — Generate Django models from CSV

```bash
python generate_django_models.py -i <csv_file> -o <output_dir> [-a <app_name>]
```

### Arguments

| Flag | Required | Description |
|---|---|---|
| `-i` / `--input` | ✅ | Path to the source CSV file |
| `-o` / `--output` | ✅ | Directory where the Django app files will be written (created if absent) |
| `-a` / `--app-name` | ❌ | Django app name used in `apps.py`. Defaults to the output folder name |

### Examples

```bash
# Basic
python generate_django_models.py \
    -i export_fields.csv \
    -o ./django_models

# With explicit app name
python generate_django_models.py \
    -i ~/Desktop/export_fields.csv \
    -o ~/myproject/properties \
    -a properties
```

### CSV format

The script expects a semicolon-delimited CSV with four columns:

| Column | Content |
|---|---|
| 1 | Class name |
| 2 | Field name |
| 3 | Data type (see mapping below) |
| 4 | Property summary (ignored) |

### Data type mapping

| CSV value | Django field |
|---|---|
| *(empty)* | `CharField(max_length=255)` |
| `Text` | `CharField(max_length=255)` |
| `Memo` | `TextField` |
| `Blob` | `FileField(upload_to='uploads/')` |
| `Image` | `ImageField(upload_to='images/')` |
| `Boolean` | `BooleanField` |
| `Money` | `DecimalField(max_digits=14, decimal_places=2)` |
| `Decimal number` | `DecimalField(max_digits=14, decimal_places=4)` |
| `Whole number` | `IntegerField` |
| `Email address` | `EmailField` |
| `Date` | `DateField` |
| `Date & Time` | `DateTimeField` |
| `Color` | `CharField(max_length=32)` |
| `Autonumber` / `Id` | Skipped (Django auto-creates `pk`) |
| `Any class` / `Any id` | `GenericForeignKey` via `ContentType` |
| *Matches a class name* | `ForeignKey` or `ManyToManyField` (when "Can have more values") |

### Output

The script produces a complete Django app folder containing:

```
<output_dir>/
├── __init__.py
├── apps.py
├── models.py      ← all model classes
├── admin.py       ← @admin.register stub for every model
```

---

## Step 2 — Deploy the app into a Django project

```bash
python deploy_app.py -i <app_folder_or_zip> -t <project_root> [options]
```

### Arguments

| Flag | Required | Description |
|---|---|---|
| `-i` / `--input` | ✅ | Path to the generated app folder **or a `.zip` archive** of it |
| `-t` / `--target` | ✅ | Root directory of the target Django / Django-CMS project |
| `-a` / `--app-name` | ❌ | Override app name (defaults to the folder or zip basename) |
| `-m` / `--mode` | ❌ | Sync mode for existing apps (see below). Default: `update` |
| `--dry-run` | ❌ | Preview all actions without writing any files |
| `--no-migrate` | ❌ | Skip `makemigrations` and `migrate` |

### Sync modes (`--mode`)

Used when the app already exists in the target project.

| Mode | Behaviour |
|---|---|
| `update` *(default)* | Diff source vs target — **add** new models and new fields automatically, **warn** (annotate) changed field definitions with `# ⚠ SYNC`, **never delete** anything |
| `replace` | Overwrite `models.py` and `admin.py` entirely with the incoming versions. Originals are backed up as `.bak` files |
| `new-only` | Only add models that **do not yet exist** in the target. All existing models are left completely untouched |

### Examples

```bash
# First deploy from a folder
python deploy_app.py \
    -i ./django_models \
    -t ~/myproject

# First deploy from a zip archive
python deploy_app.py \
    -i ./django_models.zip \
    -t ~/myproject \
    -a properties

# Update an existing app (default mode — safe, additive only)
python deploy_app.py \
    -i ./django_models \
    -t ~/myproject \
    --mode update

# Replace models entirely (use with caution)
python deploy_app.py \
    -i ./django_models.zip \
    -t ~/myproject \
    --mode replace

# Only add new models, leave existing ones alone
python deploy_app.py \
    -i ./django_models \
    -t ~/myproject \
    --mode new-only

# Preview any operation without touching files
python deploy_app.py \
    -i ./django_models \
    -t ~/myproject \
    --mode update \
    --dry-run
```

### What the script does automatically

#### Fresh deploy (app does not exist yet)

1. Copies the app folder into the target project root
2. Patches `settings.py` → adds app to `INSTALLED_APPS`
3. Patches project `urls.py` → adds `include()` (Django-CMS aware — inserts before `cms.urls`)
4. Generates `views.py` with `ListView` + `DetailView` per model
5. Generates app-level `urls.py` with list/detail URL patterns per model
6. Creates `templates/<app_name>/` with placeholder HTML templates
7. Syntax-checks all generated `.py` files
8. Runs `makemigrations <app_name>` + `migrate`

#### Re-deploy / update

| Mode | models.py | admin.py | views / urls / templates |
|---|---|---|---|
| `update` | Add new models + fields; annotate changed fields | Add new model registrations | Add new model views/routes/templates |
| `replace` | Fully overwritten from source (backed up) | Fully overwritten from source (backed up) | Add new model views/routes/templates |
| `new-only` | New model classes appended (backed up) | New model registrations appended | Add new model views/routes/templates |

### Docker support

The script auto-detects whether the target project uses `docker-compose.yml`.
If it does, migrations are run via:

```bash
docker compose -f docker-compose.yml run --rm <web_service> python manage.py ...
```

The web service name is detected by scanning the compose file for the service
that contains `manage.py` or `runserver` in its command.

### Backups

Before modifying any existing file, the script saves a `.bak` copy alongside it:

```
properties/models.py      ← updated
properties/models.py.bak  ← original preserved
```

---

## Full pipeline example

```bash
# 1. Generate from CSV
python generate_django_models.py \
    -i export_fields.csv \
    -o ./django_models \
    -a properties

# 2. Deploy into a project (first time)
python deploy_app.py \
    -i ./django_models \
    -t ~/myproject \
    -a properties

# 3. Later, after updating the CSV, regenerate and sync
python generate_django_models.py \
    -i export_fields_v2.csv \
    -o ./django_models_v2 \
    -a properties

python deploy_app.py \
    -i ./django_models_v2 \
    -t ~/myproject \
    --mode update
```

---

## Adding the app to an existing Django project manually

If you prefer to copy the files manually instead of using `deploy_app.py`:

**1. Copy the folder into your project:**
```bash
cp -r ./django_models/ myproject/properties/
```

**2. Add to `INSTALLED_APPS` in `settings.py`:**
```python
INSTALLED_APPS = [
    ...
    'django.contrib.contenttypes',  # required for GenericForeignKey
    'properties',
]
```

**3. Add media settings (if not present):**
```python
MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
```

**4. Add URL include in `urls.py`:**
```python
from django.urls import path, include

urlpatterns += [
    path('properties/', include('properties.urls', namespace='properties')),
]
```

**5. Install Pillow (required for `ImageField`):**
```bash
pip install Pillow
```

**6. Create and apply migrations:**
```bash
python manage.py makemigrations properties
python manage.py migrate
```

---

## Notes and cautions

| Topic | Note |
|---|---|
| `on_delete` | All `ForeignKey` fields use `SET_NULL`. Review and change to `CASCADE` or `PROTECT` where appropriate |
| `User` model | The generated `User` is a plain model. If you need Django auth, extend `AbstractUser` instead |
| `property_status` | Flagged `# NOTE` — source type was ambiguous; confirm what it should store |
| `for_`, `x_class`, `x_object`, `x_type` | Reserved Python/Django words were prefixed with `x_` (E001/E002 compliance) |
| `blank=True, null=True` | All fields are optional by default — tighten constraints as needed |
| Passwords | The `User.password` field is a plain `CharField`. Use Django's built-in password hashing |
| `--mode replace` | Destructive — existing model definitions are overwritten. Always review the `.bak` file before running migrations |
