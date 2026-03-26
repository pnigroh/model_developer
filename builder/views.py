from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Clazz, Field, Section, SectionField, RelatedTable
from .forms import ClazzForm, FieldForm, SectionForm, SectionFieldForm, RelatedTableForm


# ─── Reset / Clear All ────────────────────────────────────────────────────────
@login_required
def clear_all(request):
    if request.method == 'POST':
        clazz_count   = Clazz.objects.count()
        field_count   = Field.objects.count()
        section_count = Section.objects.count()

        # Cascade-delete everything (Fields, Sections, SectionFields,
        # RelatedTables all go via on_delete=CASCADE on their Clazz FK).
        Clazz.objects.all().delete()

        seed = request.POST.get('seed_examples') == 'on'
        seed_msg = ''
        if seed:
            from builder.management.commands.setup_dev import seed_example_data
            seed_msg = ' ' + seed_example_data()

        messages.success(
            request,
            f'Workspace reset — removed {clazz_count} clazz'
            f'{"es" if clazz_count != 1 else ""}, '
            f'{field_count} field{"s" if field_count != 1 else ""}, '
            f'{section_count} section{"s" if section_count != 1 else ""}.'
            + seed_msg
        )
        return redirect('dashboard')

    # GET → confirmation page
    return render(request, 'builder/clear_all.html', {
        'clazz_count':   Clazz.objects.count(),
        'field_count':   Field.objects.count(),
        'section_count': Section.objects.count(),
        'inline_count':  RelatedTable.objects.count(),
    })


# ─── Dashboard ────────────────────────────────────────────────────────────────
@login_required
def dashboard(request):
    clazzes = Clazz.objects.prefetch_related('fields', 'sections').order_by('name')
    return render(request, 'builder/dashboard.html', {'clazzes': clazzes})


# ─── Clazz CRUD ───────────────────────────────────────────────────────────────
@login_required
def clazz_create(request):
    form = ClazzForm(request.POST or None)
    if form.is_valid():
        clazz = form.save()
        messages.success(request, f'Clazz "{clazz.name}" created.')
        return redirect('clazz_detail', pk=clazz.pk)
    return render(request, 'builder/clazz_form.html', {'form': form, 'title': 'New Clazz'})


@login_required
def clazz_detail(request, pk):
    clazz = get_object_or_404(Clazz, pk=pk)
    sections = clazz.sections.prefetch_related(
        'section_fields__field', 'related_tables__related_clazz'
    ).order_by('order', 'name')
    unassigned_fields = clazz.fields.exclude(
        section_fields__section__clazz=clazz
    ).order_by('order', 'name')
    return render(request, 'builder/clazz_detail.html', {
        'clazz': clazz,
        'sections': sections,
        'unassigned_fields': unassigned_fields,
    })


@login_required
def clazz_edit(request, pk):
    clazz = get_object_or_404(Clazz, pk=pk)
    form  = ClazzForm(request.POST or None, instance=clazz)
    if form.is_valid():
        form.save()
        messages.success(request, f'Clazz "{clazz.name}" updated.')
        return redirect('clazz_detail', pk=clazz.pk)
    return render(request, 'builder/clazz_form.html', {
        'form': form, 'clazz': clazz, 'title': f'Edit {clazz.name}'
    })


@login_required
def clazz_delete(request, pk):
    clazz = get_object_or_404(Clazz, pk=pk)
    if request.method == 'POST':
        name = clazz.name
        clazz.delete()
        messages.success(request, f'Clazz "{name}" deleted.')
        return redirect('dashboard')
    return render(request, 'builder/confirm_delete.html', {
        'object': clazz, 'cancel_url': f'/clazz/{pk}/'
    })


# ─── Field CRUD ───────────────────────────────────────────────────────────────
@login_required
def field_create(request, clazz_pk):
    clazz = get_object_or_404(Clazz, pk=clazz_pk)
    form  = FieldForm(request.POST or None, clazz=clazz)
    if form.is_valid():
        field       = form.save(commit=False)
        field.clazz = clazz
        field.save()
        messages.success(request, f'Field "{field.name}" added.')
        return redirect('clazz_detail', pk=clazz.pk)
    return render(request, 'builder/field_form.html', {
        'form': form, 'clazz': clazz, 'title': 'Add Field'
    })


@login_required
def field_edit(request, pk):
    field = get_object_or_404(Field, pk=pk)
    form  = FieldForm(request.POST or None, instance=field, clazz=field.clazz)
    if form.is_valid():
        form.save()
        messages.success(request, f'Field "{field.name}" updated.')
        return redirect('clazz_detail', pk=field.clazz.pk)
    return render(request, 'builder/field_form.html', {
        'form': form, 'clazz': field.clazz, 'title': f'Edit Field: {field.name}'
    })


@login_required
def field_delete(request, pk):
    field = get_object_or_404(Field, pk=pk)
    clazz = field.clazz
    if request.method == 'POST':
        field.delete()
        messages.success(request, 'Field deleted.')
        return redirect('clazz_detail', pk=clazz.pk)
    return render(request, 'builder/confirm_delete.html', {
        'object': field, 'cancel_url': f'/clazz/{clazz.pk}/'
    })


# ─── Section CRUD ─────────────────────────────────────────────────────────────
@login_required
def section_create(request, clazz_pk):
    clazz = get_object_or_404(Clazz, pk=clazz_pk)
    form  = SectionForm(request.POST or None)
    if form.is_valid():
        section       = form.save(commit=False)
        section.clazz = clazz
        section.save()
        messages.success(request, f'Section "{section.name}" created.')
        return redirect('section_detail', pk=section.pk)
    return render(request, 'builder/section_form.html', {
        'form': form, 'clazz': clazz, 'title': 'New Section'
    })


@login_required
def section_detail(request, pk):
    section = get_object_or_404(
        Section.objects.prefetch_related(
            'section_fields__field', 'related_tables__related_clazz'
        ),
        pk=pk
    )
    clazz = section.clazz
    # Fields already in this section
    assigned_ids = section.section_fields.values_list('field_id', flat=True)
    # Fields available to add (belong to same clazz, not yet in this section)
    available_fields = clazz.fields.exclude(pk__in=assigned_ids).order_by('order', 'name')
    sf_form  = SectionFieldForm(clazz=clazz)
    rt_form  = RelatedTableForm()
    return render(request, 'builder/section_detail.html', {
        'section':          section,
        'clazz':            clazz,
        'available_fields': available_fields,
        'sf_form':          sf_form,
        'rt_form':          rt_form,
    })


@login_required
def section_edit(request, pk):
    section = get_object_or_404(Section, pk=pk)
    form    = SectionForm(request.POST or None, instance=section)
    if form.is_valid():
        form.save()
        messages.success(request, f'Section "{section.name}" updated.')
        return redirect('section_detail', pk=section.pk)
    return render(request, 'builder/section_form.html', {
        'form': form, 'clazz': section.clazz, 'title': f'Edit Section: {section.name}',
        'section': section,
    })


@login_required
def section_delete(request, pk):
    section = get_object_or_404(Section, pk=pk)
    clazz   = section.clazz
    if request.method == 'POST':
        section.delete()
        messages.success(request, 'Section deleted.')
        return redirect('clazz_detail', pk=clazz.pk)
    return render(request, 'builder/confirm_delete.html', {
        'object': section, 'cancel_url': f'/section/{pk}/'
    })


# ─── SectionField management ──────────────────────────────────────────────────
@login_required
@require_POST
def section_field_add(request, section_pk):
    section = get_object_or_404(Section, pk=section_pk)
    form    = SectionFieldForm(request.POST, clazz=section.clazz)
    if form.is_valid():
        sf         = form.save(commit=False)
        sf.section = section
        sf.save()
        messages.success(request, f'Field "{sf.field.name}" added to section.')
    else:
        messages.error(request, 'Could not add field: ' + str(form.errors))
    return redirect('section_detail', pk=section.pk)


@login_required
@require_POST
def section_field_remove(request, pk):
    sf      = get_object_or_404(SectionField, pk=pk)
    section = sf.section
    sf.delete()
    messages.success(request, 'Field removed from section.')
    return redirect('section_detail', pk=section.pk)


@login_required
@require_POST
def section_field_reorder(request, section_pk):
    """AJAX: receive ordered list of SectionField PKs and save new order."""
    import json
    section = get_object_or_404(Section, pk=section_pk)
    try:
        data = json.loads(request.body)
        for item in data:
            SectionField.objects.filter(pk=item['id'], section=section).update(order=item['order'])
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# ─── RelatedTable CRUD ────────────────────────────────────────────────────────
@login_required
@require_POST
def related_table_add(request, section_pk):
    section = get_object_or_404(Section, pk=section_pk)
    form    = RelatedTableForm(request.POST)
    if form.is_valid():
        rt         = form.save(commit=False)
        rt.section = section
        rt.save()
        messages.success(request, f'Related table "{rt.related_clazz.name}" added.')
    else:
        messages.error(request, 'Could not add related table: ' + str(form.errors))
    return redirect('section_detail', pk=section.pk)


@login_required
def related_table_edit(request, pk):
    rt      = get_object_or_404(RelatedTable, pk=pk)
    section = rt.section
    form    = RelatedTableForm(request.POST or None, instance=rt)
    if form.is_valid():
        form.save()
        messages.success(request, 'Related table updated.')
        return redirect('section_detail', pk=section.pk)
    return render(request, 'builder/related_table_form.html', {
        'form': form, 'section': section, 'rt': rt
    })


@login_required
@require_POST
def related_table_delete(request, pk):
    rt      = get_object_or_404(RelatedTable, pk=pk)
    section = rt.section
    rt.delete()
    messages.success(request, 'Related table removed.')
    return redirect('section_detail', pk=section.pk)


# ─── Code preview ─────────────────────────────────────────────────────────────
@login_required
def clazz_preview(request, pk):
    clazz = get_object_or_404(
        Clazz.objects.prefetch_related(
            'fields', 'sections__section_fields__field',
            'sections__related_tables__related_clazz'
        ),
        pk=pk
    )
    code = _generate_model_code(clazz)
    return render(request, 'builder/clazz_preview.html', {'clazz': clazz, 'code': code})


def _generate_model_code(clazz):
    """Generate a Python models.py snippet for the Clazz."""
    lines = ['from django.db import models', '', '']

    # Class declaration
    lines.append(f'class {clazz.name}(models.Model):')

    if clazz.description:
        lines.append(f'    """{clazz.description}"""')
        lines.append('')

    # Fields
    for field in clazz.fields.order_by('order', 'name'):
        lines.append(_render_field_line(field))

    lines.append('')

    # Meta
    lines.append('    class Meta:')
    meta_lines = []
    if clazz.verbose_name:
        meta_lines.append(f"        verbose_name = '{clazz.verbose_name}'")
    if clazz.verbose_name_plural:
        meta_lines.append(f"        verbose_name_plural = '{clazz.verbose_name_plural}'")
    if clazz.app_label:
        meta_lines.append(f"        app_label = '{clazz.app_label}'")
    if clazz.db_table:
        meta_lines.append(f"        db_table = '{clazz.db_table}'")
    if clazz.ordering:
        order_list = [f"'{o}'" for o in clazz.get_ordering_list()]
        meta_lines.append(f"        ordering = [{', '.join(order_list)}]")
    if clazz.abstract:
        meta_lines.append('        abstract = True')
    if not meta_lines:
        meta_lines.append('        pass')
    lines.extend(meta_lines)

    lines.append('')
    lines.append('    def __str__(self):')
    lines.append(f'        return str(self.pk)')
    lines.append('')

    return '\n'.join(lines)


def _render_field_line(field):
    kwargs = []
    if field.verbose_name:
        kwargs.append(f"verbose_name='{field.verbose_name}'")
    if field.max_length and field.field_type in (
            'CharField', 'SlugField', 'EmailField', 'URLField', 'FilePathField'):
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
        val = field.default
        try:
            float(val)
            kwargs.append(f'default={val}')
        except ValueError:
            if val in ('True', 'False', 'None'):
                kwargs.append(f'default={val}')
            else:
                kwargs.append(f"default='{val}'")
    if field.help_text:
        kwargs.append(f"help_text='{field.help_text}'")
    if field.auto_now:
        kwargs.append('auto_now=True')
    if field.auto_now_add:
        kwargs.append('auto_now_add=True')
    if field.max_digits:
        kwargs.append(f'max_digits={field.max_digits}')
    if field.decimal_places is not None:
        kwargs.append(f'decimal_places={field.decimal_places}')
    if field.field_type in ('ForeignKey', 'OneToOneField', 'ManyToManyField'):
        target = f"'{field.related_clazz.name}'" if field.related_clazz else "'CHANGE_ME'"
        if field.field_type != 'ManyToManyField':
            kwargs_str = f'{target}, on_delete=models.{field.on_delete}'
            if kwargs:
                kwargs_str += ', ' + ', '.join(kwargs)
        else:
            kwargs_str = target
            if kwargs:
                kwargs_str += ', ' + ', '.join(kwargs)
        if field.related_name:
            kwargs_str += f", related_name='{field.related_name}'"
        return f"    {field.name} = models.{field.field_type}({kwargs_str})"

    kwargs_str = ', '.join(kwargs)
    return f"    {field.name} = models.{field.field_type}({kwargs_str})"
