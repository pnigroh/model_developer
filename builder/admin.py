from django.contrib import admin
from .models import Clazz, Field, Section, SectionField, RelatedTable


class FieldInline(admin.TabularInline):
    model   = Field
    fk_name = 'clazz'
    extra   = 1
    fields  = ['name', 'verbose_name', 'field_type', 'null', 'blank', 'order']


class SectionInline(admin.TabularInline):
    model  = Section
    extra  = 1
    fields = ['name', 'order', 'collapsed']


@admin.register(Clazz)
class ClazzAdmin(admin.ModelAdmin):
    list_display   = ['name', 'verbose_name', 'abstract', 'created_at']
    search_fields  = ['name', 'verbose_name']
    list_filter    = ['abstract']
    inlines        = [FieldInline, SectionInline]


class SectionFieldInline(admin.TabularInline):
    model  = SectionField
    extra  = 1
    fields = ['field', 'order']


class RelatedTableInline(admin.TabularInline):
    model  = RelatedTable
    extra  = 1
    fields = ['related_clazz', 'fk_field', 'inline_style', 'extra', 'order']


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display  = ['__str__', 'clazz', 'order', 'collapsed']
    list_filter   = ['clazz']
    inlines       = [SectionFieldInline, RelatedTableInline]


@admin.register(Field)
class FieldAdmin(admin.ModelAdmin):
    list_display  = ['name', 'clazz', 'field_type', 'null', 'blank', 'order']
    list_filter   = ['clazz', 'field_type']
    search_fields = ['name', 'verbose_name']


@admin.register(RelatedTable)
class RelatedTableAdmin(admin.ModelAdmin):
    list_display  = ['__str__', 'section', 'inline_style', 'extra']
    list_filter   = ['inline_style']
