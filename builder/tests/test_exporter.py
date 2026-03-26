"""
Smoke tests for builder.exporter — needs Django ORM (use manage.py shell or pytest-django).
Run with:
  DJANGO_SETTINGS_MODULE=modeldev.settings python builder/tests/test_exporter.py
"""
import os
import sys
import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'modeldev.settings')
django.setup()

from builder.models import Clazz, Field, Section, SectionField, RelatedTable
from builder.exporter import (
    generate_models_py, generate_admin_py, generate_apps_py,
    generate_views_py, generate_urls_py, generate_forms_py,
    build_export_zip,
)


def test_generate_models_py():
    clazzes = list(Clazz.objects.prefetch_related('fields').all())
    if not clazzes:
        print("  ⚠ No clazzes in DB – skipping generate_models_py test")
        return
    code = generate_models_py(clazzes)
    assert 'from django.db import models' in code
    assert 'class' in code
    print(f"✓ generate_models_py ({len(clazzes)} clazzes, {len(code)} chars)")


def test_generate_admin_py():
    clazzes = list(Clazz.objects.prefetch_related(
        'fields', 'sections__section_fields__field',
        'sections__related_tables__related_clazz'
    ).all())
    if not clazzes:
        print("  ⚠ No clazzes in DB – skipping generate_admin_py test")
        return
    code = generate_admin_py(clazzes)
    assert 'from django.contrib import admin' in code
    assert 'ModelAdmin' in code
    print(f"✓ generate_admin_py ({len(code)} chars)")


def test_generate_apps_py():
    code = generate_apps_py('myapp', 'My Application')
    assert 'AppConfig' in code
    assert 'myapp' in code
    assert 'My Application' in code
    print("✓ generate_apps_py")


def test_build_export_zip():
    clazz_pks = list(Clazz.objects.values_list('pk', flat=True))
    if not clazz_pks:
        print("  ⚠ No clazzes in DB – skipping build_export_zip test")
        return
    import zipfile, io
    raw = build_export_zip(clazz_pks, app_name='testapp', include_views=True, include_forms=True)
    assert len(raw) > 0
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    assert 'testapp/models.py'   in names, f"Missing models.py. Got: {names}"
    assert 'testapp/admin.py'    in names
    assert 'testapp/apps.py'     in names
    assert 'testapp/__init__.py' in names
    assert 'testapp/views.py'    in names
    assert 'testapp/forms.py'    in names
    assert 'testapp/README.md'   in names
    # Verify models.py is valid Python
    import ast
    models_src = zf.read('testapp/models.py').decode()
    ast.parse(models_src)  # Will raise SyntaxError if broken
    admin_src = zf.read('testapp/admin.py').decode()
    ast.parse(admin_src)
    print(f"✓ build_export_zip (zip={len(raw)} bytes, files={names})")


if __name__ == '__main__':
    test_generate_models_py()
    test_generate_admin_py()
    test_generate_apps_py()
    test_build_export_zip()
    print("\n✅ All exporter tests passed.")
