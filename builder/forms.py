from django import forms
from .models import Clazz, Field, Section, SectionField, RelatedTable


class ClazzForm(forms.ModelForm):
    class Meta:
        model  = Clazz
        fields = [
            'name', 'verbose_name', 'verbose_name_plural',
            'app_label', 'db_table', 'ordering', 'abstract',
            'description', 'list_display', 'search_fields',
            'list_filter', 'date_hierarchy',
        ]
        widgets = {
            'description':   forms.Textarea(attrs={'rows': 3}),
            'list_display':  forms.TextInput(attrs={'placeholder': 'name, created_at, status'}),
            'search_fields': forms.TextInput(attrs={'placeholder': 'name, email'}),
            'list_filter':   forms.TextInput(attrs={'placeholder': 'status, created_at'}),
            'ordering':      forms.TextInput(attrs={'placeholder': '-created_at, name'}),
        }


class FieldForm(forms.ModelForm):
    class Meta:
        model  = Field
        fields = [
            'name', 'verbose_name', 'field_type', 'order',
            'null', 'blank', 'unique', 'db_index', 'primary_key', 'editable',
            'max_length', 'default', 'help_text', 'choices',
            'max_digits', 'decimal_places',
            'auto_now', 'auto_now_add',
            'related_clazz', 'related_name', 'on_delete',
        ]
        widgets = {
            'choices':   forms.Textarea(attrs={'rows': 4,
                                               'placeholder': 'active,Active\ndraft,Draft'}),
            'help_text': forms.TextInput(),
            'default':   forms.TextInput(),
        }

    def __init__(self, *args, clazz=None, **kwargs):
        super().__init__(*args, **kwargs)
        if clazz:
            self.fields['related_clazz'].queryset = Clazz.objects.exclude(pk=clazz.pk)
        self.fields['related_clazz'].required = False
        self.fields['on_delete'].required = False


class SectionForm(forms.ModelForm):
    class Meta:
        model  = Section
        fields = ['name', 'description', 'order', 'collapsed']
        widgets = {
            'description': forms.TextInput(attrs={'placeholder': 'Optional subtitle for this section'}),
        }


class SectionFieldForm(forms.ModelForm):
    class Meta:
        model  = SectionField
        fields = ['field', 'order']

    def __init__(self, *args, clazz=None, **kwargs):
        super().__init__(*args, **kwargs)
        if clazz:
            self.fields['field'].queryset = clazz.fields.all().order_by('order', 'name')


class RelatedTableForm(forms.ModelForm):
    class Meta:
        model  = RelatedTable
        fields = [
            'related_clazz', 'fk_field', 'verbose_name',
            'inline_style', 'extra', 'max_num',
            'fields_display', 'order',
        ]
        widgets = {
            'fields_display': forms.TextInput(
                attrs={'placeholder': 'field1, field2 (blank = all)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['max_num'].required = False
        self.fields['verbose_name'].required = False
