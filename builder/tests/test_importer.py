"""
Smoke tests for builder.importer — runs without Django DB.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ── Sample files ───────────────────────────────────────────────────────────────
SAMPLE_MODELS = """
from django.db import models


class Property(models.Model):
    \"\"\"A real-estate property listing.\"\"\"

    STATUS_CHOICES = [
        ('active',   'Active'),
        ('draft',    'Draft'),
        ('sold',     'Sold'),
        ('archived', 'Archived'),
    ]

    title       = models.CharField(max_length=200, verbose_name='Title')
    description = models.TextField(blank=True, verbose_name='Description')
    city        = models.CharField(max_length=100)
    address     = models.CharField(max_length=300, blank=True)
    price       = models.DecimalField(max_digits=12, decimal_places=2)
    bedrooms    = models.PositiveIntegerField(default=1)
    bathrooms   = models.PositiveIntegerField(default=1)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    area_sqm    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at  = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        verbose_name        = 'Property'
        verbose_name_plural = 'Properties'
        ordering            = ['-created_at', 'title']

    def __str__(self):
        return self.title


class PropertyImage(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='images')
    image    = models.ImageField(upload_to='properties/')
    caption  = models.CharField(max_length=200, blank=True)
    order    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'Image #{self.pk}'
"""

SAMPLE_ADMIN = """
from django.contrib import admin
from .models import Property, PropertyImage


class PropertyImageInline(admin.TabularInline):
    model  = PropertyImage
    fk_name = 'property'
    extra  = 2
    fields = ['image', 'caption', 'order']


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display  = ['title', 'city', 'price', 'status', 'created_at']
    search_fields = ['title', 'city', 'description']
    list_filter   = ['status', 'city']
    date_hierarchy = 'created_at'
    inlines       = [PropertyImageInline]
    fieldsets     = (
        ('General Information', {
            'fields': ('title', 'description', 'city', 'address', 'status'),
        }),
        ('Property Details', {
            'fields': ('price', 'bedrooms', 'bathrooms', 'area_sqm'),
        }),
        ('Metadata', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )
"""

SAMPLE_APPS = """
from django.apps import AppConfig


class PropertiesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'properties'
    verbose_name = 'Property Management'
"""


# ── Tests ──────────────────────────────────────────────────────────────────────
from builder.importer import parse_files, parse_models_file, parse_admin_file, parse_apps_file


def test_parse_models():
    clazz_map, errors = parse_models_file(SAMPLE_MODELS)
    assert not errors, f"Unexpected errors: {errors}"

    assert 'Property' in clazz_map
    assert 'PropertyImage' in clazz_map

    prop = clazz_map['Property']
    assert prop.verbose_name == 'Property'
    assert prop.verbose_name_plural == 'Properties'
    assert prop.ordering == '-created_at, title'
    assert prop.description == 'A real-estate property listing.'

    field_names = [f.name for f in prop.fields]
    assert 'title' in field_names
    assert 'price' in field_names
    assert 'created_at' in field_names

    title_f = next(f for f in prop.fields if f.name == 'title')
    assert title_f.field_type == 'CharField'
    assert title_f.max_length == 200
    assert title_f.verbose_name == 'Title'

    price_f = next(f for f in prop.fields if f.name == 'price')
    assert price_f.field_type == 'DecimalField'
    assert price_f.max_digits == 12
    assert price_f.decimal_places == 2

    created_f = next(f for f in prop.fields if f.name == 'created_at')
    assert created_f.auto_now_add is True
    assert created_f.editable is False

    img = clazz_map['PropertyImage']
    fk_f = next(f for f in img.fields if f.name == 'property')
    assert fk_f.field_type == 'ForeignKey'
    assert fk_f.related_model == 'Property'
    assert fk_f.on_delete == 'CASCADE'
    assert fk_f.related_name == 'images'
    print("✓ parse_models")


def test_parse_admin():
    clazz_map, _ = parse_models_file(SAMPLE_MODELS)
    warns = parse_admin_file(SAMPLE_ADMIN, clazz_map)

    prop = clazz_map['Property']
    assert 'title' in prop.list_display, f"list_display: {prop.list_display}"
    assert 'status' in prop.list_filter,  f"list_filter: {prop.list_filter}"
    assert prop.date_hierarchy == 'created_at'

    assert len(prop.sections) == 3
    general = next(s for s in prop.sections if s.name == 'General Information')
    assert 'title' in general.field_names
    assert 'description' in general.field_names

    meta_sec = next(s for s in prop.sections if s.name == 'Metadata')
    assert meta_sec.collapsed is True

    assert len(prop.related_tables) == 1
    rt = prop.related_tables[0]
    assert rt.related_model == 'PropertyImage'
    assert rt.fk_field == 'property'
    assert rt.extra == 2
    assert rt.inline_style == 'tabular'
    print("✓ parse_admin")


def test_parse_apps():
    label, verbose = parse_apps_file(SAMPLE_APPS)
    assert label == 'properties'
    assert verbose == 'Property Management'
    print("✓ parse_apps")


def test_parse_files_combined():
    result = parse_files({
        'models.py': SAMPLE_MODELS,
        'admin.py':  SAMPLE_ADMIN,
        'apps.py':   SAMPLE_APPS,
    })
    assert not result.errors, f"Errors: {result.errors}"
    assert result.app_label == 'properties'
    assert len(result.clazzes) == 2

    prop = next(c for c in result.clazzes if c.name == 'Property')
    assert prop.app_label == 'properties'
    assert len(prop.sections) == 3
    assert len(prop.related_tables) == 1
    print("✓ parse_files combined")


if __name__ == '__main__':
    test_parse_models()
    test_parse_admin()
    test_parse_apps()
    test_parse_files_combined()
    print("\n✅ All import parser tests passed.")
