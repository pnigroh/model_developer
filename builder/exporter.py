"""
builder/exporter.py
────────────────────
Generates Django source files from Clazz / Field / Section / RelatedTable
objects stored in the database.

Output files:
  models.py  – all model class definitions
  admin.py   – ModelAdmin + inline registrations
  apps.py    – AppConfig
  __init__.py
  views.py   – basic CRUD views skeleton (optional)
  urls.py    – URL patterns skeleton (optional)
"""

import io
import zipfile
import textwrap
from typing import Optional

from .models import Clazz, Field, Section, SectionField, RelatedTable


# ── Indentation helpers ────────────────────────────────────────────────────────
I1 = '    '   # 4 spaces
I2 = '        '  # 8 spaces


def _q(s: str) -> str:
    """Wrap in single quotes."""
    return f"'{s}'"


def _repr_default(val: str) -> str:
    """Render a default value as a Python literal."""
    if val in ('True', 'False', 'None'):
        return val
    try:
        float(val)
        return val
    except (ValueError, TypeError):
        return f"'{val}'"


# ── models.py ─────────────────────────────────────────────────────────────────
def generate_models_py(clazzes: list[Clazz]) -> str:
    lines = [
        'from django.db import models',
        '',
        '',
    ]

    for clazz in clazzes:
        lines.extend(_render_model_class(clazz))
        lines.append('')
        lines.append('')

    return '\n'.join(lines).rstrip() + '\n'


def _render_model_class(clazz: Clazz) -> list[str]:
    lines = [f'class {clazz.name}(models.Model):']

    if clazz.description:
        lines.append(f'{I1}"""{clazz.description}"""')
        lines.append('')

    fields = clazz.fields.order_by('order', 'name')
    if not fields:
        lines.append(f'{I1}pass')
    else:
        for field in fields:
            lines.append(_render_field_line(field))

    lines.append('')
    lines.extend(_render_meta(clazz))
    lines.append('')
    lines.append(f'{I1}def __str__(self):')

    # Pick the first CharField as __str__ candidate
    str_field = fields.filter(field_type__in=['CharField', 'TextField']).first()
    if str_field:
        lines.append(f"{I1}{I1}return self.{str_field.name}")
    else:
        lines.append(f"{I1}{I1}return f'{clazz.name} #{{self.pk}}'")

    return lines


def _render_meta(clazz: Clazz) -> list[str]:
    lines = [f'{I1}class Meta:']
    meta = []

    if clazz.verbose_name:
        meta.append(f"{I2}verbose_name = {_q(clazz.verbose_name)}")
    if clazz.verbose_name_plural:
        meta.append(f"{I2}verbose_name_plural = {_q(clazz.verbose_name_plural)}")
    if clazz.app_label:
        meta.append(f"{I2}app_label = {_q(clazz.app_label)}")
    if clazz.db_table:
        meta.append(f"{I2}db_table = {_q(clazz.db_table)}")
    if clazz.ordering:
        order_items = [_q(o.strip()) for o in clazz.ordering.split(',') if o.strip()]
        meta.append(f"{I2}ordering = [{', '.join(order_items)}]")
    if clazz.abstract:
        meta.append(f'{I2}abstract = True')

    if not meta:
        meta.append(f'{I2}pass')
    lines.extend(meta)
    return lines


def _render_field_line(field: Field) -> str:
    ft = field.field_type
    args: list[str] = []
    kwargs: list[str] = []

    # ── Relational first positional arg ──────────────────────────────────────
    if ft in ('ForeignKey', 'OneToOneField', 'ManyToManyField'):
        target = _q(field.related_clazz.name) if field.related_clazz else "'CHANGE_ME'"
        args.append(target)
        if ft != 'ManyToManyField':
            args.append(f'on_delete=models.{field.on_delete or "CASCADE"}')
        if field.related_name:
            kwargs.append(f'related_name={_q(field.related_name)}')

    # ── Common kwargs ─────────────────────────────────────────────────────────
    if field.verbose_name:
        kwargs.append(f'verbose_name={_q(field.verbose_name)}')
    if field.max_length and ft in (
        'CharField', 'SlugField', 'EmailField', 'URLField',
        'FilePathField', 'GenericIPAddressField',
    ):
        kwargs.append(f'max_length={field.max_length}')
    if field.null:
        kwargs.append('null=True')
    if field.blank:
        kwargs.append('blank=True')
    if field.unique:
        kwargs.append('unique=True')
    if field.db_index:
        kwargs.append('db_index=True')
    if field.primary_key:
        kwargs.append('primary_key=True')
    if not field.editable:
        kwargs.append('editable=False')
    if field.default not in ('', None):
        kwargs.append(f'default={_repr_default(field.default)}')
    if field.help_text:
        # Escape any single quotes in help_text
        ht = field.help_text.replace("'", "\\'")
        kwargs.append(f"help_text='{ht}'")
    if field.choices:
        lines = [
            ln.split(',', 1) for ln in field.choices.splitlines()
            if ',' in ln and not ln.startswith('#')
        ]
        if lines:
            choice_items = [f"({_q(v.strip())}, {_q(l.strip())})" for v, l in lines]
            kwargs.append(f'choices=[{", ".join(choice_items)}]')
    if field.max_digits:
        kwargs.append(f'max_digits={field.max_digits}')
    if field.decimal_places is not None:
        kwargs.append(f'decimal_places={field.decimal_places}')
    if field.auto_now:
        kwargs.append('auto_now=True')
    if field.auto_now_add:
        kwargs.append('auto_now_add=True')

    all_args = args + kwargs
    return f'{I1}{field.name} = models.{ft}({", ".join(all_args)})'


# ── admin.py ──────────────────────────────────────────────────────────────────
def generate_admin_py(clazzes: list[Clazz]) -> str:
    lines = [
        'from django.contrib import admin',
        '',
        f'from .models import {", ".join(c.name for c in clazzes)}',
        '',
        '',
    ]

    # Collect all unique inline pairs needed: (parent_clazz, RelatedTable)
    inline_classes: list[tuple[Clazz, RelatedTable]] = []
    for clazz in clazzes:
        for section in clazz.sections.order_by('order'):
            for rt in section.related_tables.order_by('order'):
                inline_classes.append((clazz, rt))

    # Render inline classes
    for parent_clazz, rt in inline_classes:
        lines.extend(_render_inline_class(parent_clazz, rt))
        lines.append('')
        lines.append('')

    # Render ModelAdmin classes
    for clazz in clazzes:
        lines.extend(_render_model_admin(clazz, inline_classes))
        lines.append('')
        lines.append('')

    return '\n'.join(lines).rstrip() + '\n'


def _inline_class_name(parent_clazz: Clazz, rt: RelatedTable) -> str:
    style = 'Tabular' if rt.inline_style == 'tabular' else 'Stacked'
    return f'{rt.related_clazz.name}{style}Inline'


def _render_inline_class(parent_clazz: Clazz, rt: RelatedTable) -> list[str]:
    style  = 'TabularInline' if rt.inline_style == 'tabular' else 'StackedInline'
    cls    = _inline_class_name(parent_clazz, rt)
    lines  = [f'class {cls}(admin.{style}):']
    lines.append(f'{I1}model = {rt.related_clazz.name}')

    if rt.fk_field:
        lines.append(f'{I1}fk_name = {_q(rt.fk_field)}')
    if rt.verbose_name:
        lines.append(f'{I1}verbose_name = {_q(rt.verbose_name)}')

    fields_list = rt.get_fields_display_list()
    if fields_list:
        fields_str = ', '.join(_q(f) for f in fields_list)
        lines.append(f'{I1}fields = [{fields_str}]')

    lines.append(f'{I1}extra = {rt.extra}')
    if rt.max_num is not None:
        lines.append(f'{I1}max_num = {rt.max_num}')

    return lines


def _render_model_admin(clazz: Clazz, all_inlines: list[tuple]) -> list[str]:
    lines = [f'@admin.register({clazz.name})']
    lines.append(f'class {clazz.name}Admin(admin.ModelAdmin):')

    body: list[str] = []

    # list_display
    if clazz.list_display:
        cols = [_q(c.strip()) for c in clazz.list_display.split(',') if c.strip()]
        body.append(f'{I1}list_display = [{", ".join(cols)}]')

    # search_fields
    if clazz.search_fields:
        sf = [_q(f.strip()) for f in clazz.search_fields.split(',') if f.strip()]
        body.append(f'{I1}search_fields = [{", ".join(sf)}]')

    # list_filter
    if clazz.list_filter:
        lf = [_q(f.strip()) for f in clazz.list_filter.split(',') if f.strip()]
        body.append(f'{I1}list_filter = [{", ".join(lf)}]')

    # date_hierarchy
    if clazz.date_hierarchy:
        body.append(f'{I1}date_hierarchy = {_q(clazz.date_hierarchy)}')

    # inlines – gather all RelatedTables under this clazz
    my_inlines = [
        _inline_class_name(pc, rt)
        for pc, rt in all_inlines
        if pc.pk == clazz.pk
    ]
    if my_inlines:
        body.append(f'{I1}inlines = [{", ".join(my_inlines)}]')

    # fieldsets from sections
    sections = list(clazz.sections.prefetch_related(
        'section_fields__field'
    ).order_by('order'))

    if sections:
        body.append(f'{I1}fieldsets = (')
        for sec in sections:
            field_names = [
                _q(sf.field.name)
                for sf in sec.section_fields.order_by('order')
            ]
            classes_line = ''
            if sec.collapsed:
                classes_line = f"'classes': ('collapse',), "
            desc_line = ''
            if sec.description:
                desc_line = f"'description': {_q(sec.description)}, "
            if field_names:
                body.append(
                    f"{I2}({_q(sec.name)}, "
                    f"{{{classes_line}{desc_line}'fields': ({', '.join(field_names)},)}}),"
                )
        body.append(f'{I1})')

    if not body:
        body.append(f'{I1}pass')

    lines.extend(body)
    return lines


# ── apps.py ───────────────────────────────────────────────────────────────────
def generate_apps_py(app_name: str, verbose_name: str = '') -> str:
    if not app_name:
        app_name = 'myapp'
    display = verbose_name or app_name.replace('_', ' ').title()
    return textwrap.dedent(f"""\
        from django.apps import AppConfig


        class {app_name.title().replace('_', '')}Config(AppConfig):
            default_auto_field = 'django.db.models.BigAutoField'
            name = '{app_name}'
            verbose_name = '{display}'
    """)


# ── views.py skeleton ─────────────────────────────────────────────────────────
def generate_views_py(clazzes: list[Clazz]) -> str:
    lines = [
        'from django.shortcuts import render, redirect, get_object_or_404',
        'from django.contrib.auth.decorators import login_required',
        'from django.contrib import messages',
        '',
        f'from .models import {", ".join(c.name for c in clazzes)}',
        '',
        '',
    ]

    for clazz in clazzes:
        name_lower = clazz.name.lower()
        vname      = clazz.verbose_name or clazz.name
        lines += [
            f'# ── {clazz.name} ────────────────────────────────────────────',
            '@login_required',
            f'def {name_lower}_list(request):',
            f'{I1}qs = {clazz.name}.objects.all()',
            f"{I1}return render(request, '{name_lower}/list.html', " + "{'object_list': qs})",
            '',
            '@login_required',
            f'def {name_lower}_detail(request, pk):',
            f'{I1}obj = get_object_or_404({clazz.name}, pk=pk)',
            f"{I1}return render(request, '{name_lower}/detail.html', " + "{'object': obj})",
            '',
            '@login_required',
            f'def {name_lower}_create(request):',
            f'{I1}# TODO: add form handling',
            f"{I1}return render(request, '{name_lower}/form.html', " + "{})",
            '',
            '@login_required',
            f'def {name_lower}_update(request, pk):',
            f'{I1}obj = get_object_or_404({clazz.name}, pk=pk)',
            f'{I1}# TODO: add form handling',
            f"{I1}return render(request, '{name_lower}/form.html', " + "{'object': obj})",
            '',
            '@login_required',
            f'def {name_lower}_delete(request, pk):',
            f'{I1}obj = get_object_or_404({clazz.name}, pk=pk)',
            f'{I1}if request.method == "POST":',
            f'{I1}{I1}obj.delete()',
            f'{I1}{I1}messages.success(request, f"{_q(vname)} deleted.")',
            f"{I1}{I1}return redirect('{name_lower}_list')",
            f"{I1}return render(request, '{name_lower}/confirm_delete.html', " + "{'object': obj})",
            '',
            '',
        ]

    return '\n'.join(lines).rstrip() + '\n'


# ── urls.py skeleton ──────────────────────────────────────────────────────────
def generate_urls_py(clazzes: list[Clazz]) -> str:
    lines = [
        'from django.urls import path',
        'from . import views',
        '',
        'urlpatterns = [',
    ]

    for clazz in clazzes:
        nl = clazz.name.lower()
        lines += [
            f"    # {clazz.name}",
            f"    path('{nl}/',             views.{nl}_list,   name='{nl}_list'),",
            f"    path('{nl}/<int:pk>/',    views.{nl}_detail, name='{nl}_detail'),",
            f"    path('{nl}/new/',         views.{nl}_create, name='{nl}_create'),",
            f"    path('{nl}/<int:pk>/edit/', views.{nl}_update, name='{nl}_update'),",
            f"    path('{nl}/<int:pk>/delete/', views.{nl}_delete, name='{nl}_delete'),",
            '',
        ]

    lines.append(']')
    return '\n'.join(lines) + '\n'


# ── __init__.py ───────────────────────────────────────────────────────────────
def generate_init_py() -> str:
    return ''


# ── forms.py skeleton ─────────────────────────────────────────────────────────
def generate_forms_py(clazzes: list[Clazz]) -> str:
    lines = [
        'from django import forms',
        '',
        f'from .models import {", ".join(c.name for c in clazzes)}',
        '',
        '',
    ]

    for clazz in clazzes:
        lines += [
            f'class {clazz.name}Form(forms.ModelForm):',
            f'{I1}class Meta:',
            f'{I1}{I1}model = {clazz.name}',
            f"{I1}{I1}fields = '__all__'",
            '',
            '',
        ]

    return '\n'.join(lines).rstrip() + '\n'


# ── Main export builder ────────────────────────────────────────────────────────
def build_export_zip(
    clazz_pks: list[int],
    app_name: str = 'myapp',
    include_views: bool = False,
    include_forms: bool = False,
) -> bytes:
    """
    Build an in-memory zip containing the generated source files.
    Returns the zip bytes.
    """
    clazzes = list(
        Clazz.objects.filter(pk__in=clazz_pks).prefetch_related(
            'fields',
            'sections__section_fields__field',
            'sections__related_tables__related_clazz',
        ).order_by('name')
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'{app_name}/__init__.py', generate_init_py())
        zf.writestr(f'{app_name}/models.py',   generate_models_py(clazzes))
        zf.writestr(f'{app_name}/admin.py',    generate_admin_py(clazzes))
        zf.writestr(f'{app_name}/apps.py',     generate_apps_py(app_name))

        if include_views:
            zf.writestr(f'{app_name}/views.py', generate_views_py(clazzes))
            zf.writestr(f'{app_name}/urls.py',  generate_urls_py(clazzes))

        if include_forms:
            zf.writestr(f'{app_name}/forms.py', generate_forms_py(clazzes))

        # Add a README
        zf.writestr(f'{app_name}/README.md', _generate_readme(app_name, clazzes))

    buf.seek(0)
    return buf.read()


def _generate_readme(app_name: str, clazzes: list[Clazz]) -> str:
    clazz_list = '\n'.join(f'- `{c.name}` ({c.fields.count()} fields)' for c in clazzes)
    return textwrap.dedent(f"""\
        # {app_name} – Generated by ModelDev

        This app was exported from the ModelDev builder.

        ## Models

        {clazz_list}

        ## Installation

        1. Copy this directory into your Django project.
        2. Add `'{app_name}'` to `INSTALLED_APPS` in `settings.py`.
        3. Run `python manage.py makemigrations {app_name}`
        4. Run `python manage.py migrate`

        ## Notes

        - Review all generated `TODO` comments before using in production.
        - Add proper `__str__` methods, validators and business logic as needed.
    """)
