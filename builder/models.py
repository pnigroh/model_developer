from django.db import models


# ─── Django field type choices ────────────────────────────────────────────────
FIELD_TYPES = [
    # Text
    ('CharField',           'CharField – short text'),
    ('TextField',           'TextField – long text'),
    ('SlugField',           'SlugField'),
    ('EmailField',          'EmailField'),
    ('URLField',            'URLField'),
    ('UUIDField',           'UUIDField'),
    ('GenericIPAddressField', 'GenericIPAddressField'),
    # Numbers
    ('IntegerField',        'IntegerField'),
    ('PositiveIntegerField','PositiveIntegerField'),
    ('BigIntegerField',     'BigIntegerField'),
    ('SmallIntegerField',   'SmallIntegerField'),
    ('FloatField',          'FloatField'),
    ('DecimalField',        'DecimalField'),
    # Boolean
    ('BooleanField',        'BooleanField'),
    ('NullBooleanField',    'NullBooleanField (deprecated, use BooleanField+null=True)'),
    # Date / Time
    ('DateField',           'DateField'),
    ('DateTimeField',       'DateTimeField'),
    ('TimeField',           'TimeField'),
    ('DurationField',       'DurationField'),
    # Files
    ('FileField',           'FileField'),
    ('ImageField',          'ImageField'),
    ('FilePathField',       'FilePathField'),
    # Relations
    ('ForeignKey',          'ForeignKey'),
    ('OneToOneField',       'OneToOneField'),
    ('ManyToManyField',     'ManyToManyField'),
    # Other
    ('JSONField',           'JSONField'),
    ('BinaryField',         'BinaryField'),
    ('AutoField',           'AutoField'),
    ('BigAutoField',        'BigAutoField'),
]

ON_DELETE_CHOICES = [
    ('CASCADE',     'CASCADE'),
    ('PROTECT',     'PROTECT'),
    ('SET_NULL',    'SET_NULL'),
    ('SET_DEFAULT', 'SET_DEFAULT'),
    ('DO_NOTHING',  'DO_NOTHING'),
]


# ─── Clazz ────────────────────────────────────────────────────────────────────
class Clazz(models.Model):
    """Represents a Django model definition."""
    name                = models.CharField(max_length=100, unique=True,
                                           help_text='PascalCase class name, e.g. PropertyImage')
    verbose_name        = models.CharField(max_length=200, blank=True)
    verbose_name_plural = models.CharField(max_length=200, blank=True)
    app_label           = models.CharField(max_length=100, blank=True,
                                           help_text='Optional – override app_label in Meta')
    db_table            = models.CharField(max_length=100, blank=True,
                                           help_text='Optional – override table name')
    ordering            = models.CharField(max_length=200, blank=True,
                                           help_text='Comma-separated fields, e.g. -created_at,name')
    abstract            = models.BooleanField(default=False)
    description         = models.TextField(blank=True, help_text='Internal notes / description')

    # List-view configuration
    list_display        = models.TextField(blank=True,
                                           help_text='Comma-separated field names to show in list view')
    search_fields       = models.TextField(blank=True,
                                           help_text='Comma-separated field names to search on')
    list_filter         = models.TextField(blank=True,
                                           help_text='Comma-separated field names for sidebar filter')
    date_hierarchy      = models.CharField(max_length=100, blank=True,
                                           help_text='Single DateField/DateTimeField for drill-down')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Clazz'
        verbose_name_plural = 'Clazzes'
        ordering            = ['name']

    def __str__(self):
        return self.name

    # ── helpers ───────────────────────────────────────────────────────────────
    def get_list_display_list(self):
        return [f.strip() for f in self.list_display.split(',') if f.strip()]

    def get_search_fields_list(self):
        return [f.strip() for f in self.search_fields.split(',') if f.strip()]

    def get_list_filter_list(self):
        return [f.strip() for f in self.list_filter.split(',') if f.strip()]

    def get_ordering_list(self):
        return [f.strip() for f in self.ordering.split(',') if f.strip()]


# ─── Field ────────────────────────────────────────────────────────────────────
class Field(models.Model):
    """A single field definition belonging to a Clazz."""
    clazz        = models.ForeignKey(Clazz, on_delete=models.CASCADE, related_name='fields')
    name         = models.CharField(max_length=100, help_text='Python attribute name, e.g. first_name')
    verbose_name = models.CharField(max_length=200, blank=True)
    field_type   = models.CharField(max_length=50, choices=FIELD_TYPES, default='CharField')
    order        = models.PositiveIntegerField(default=0)

    # Common options
    null         = models.BooleanField(default=False)
    blank        = models.BooleanField(default=False)
    unique       = models.BooleanField(default=False)
    db_index     = models.BooleanField(default=False)
    primary_key  = models.BooleanField(default=False)
    editable     = models.BooleanField(default=True)
    max_length   = models.PositiveIntegerField(null=True, blank=True,
                                               help_text='Required for CharField/SlugField/etc.')
    default      = models.CharField(max_length=500, blank=True,
                                    help_text='Leave blank for no default; use "None" for explicit None')
    help_text    = models.CharField(max_length=500, blank=True)
    choices      = models.TextField(blank=True,
                                    help_text='One choice per line: VALUE,Label')

    # DecimalField options
    max_digits       = models.PositiveIntegerField(null=True, blank=True)
    decimal_places   = models.PositiveIntegerField(null=True, blank=True)

    # DateField / DateTimeField options
    auto_now         = models.BooleanField(default=False)
    auto_now_add     = models.BooleanField(default=False)

    # Relation options (ForeignKey, OneToOneField, ManyToManyField)
    related_clazz    = models.ForeignKey(Clazz, on_delete=models.SET_NULL,
                                         null=True, blank=True,
                                         related_name='incoming_relations',
                                         help_text='Target model for FK/O2O/M2M')
    related_name     = models.CharField(max_length=100, blank=True,
                                        help_text='related_name argument')
    on_delete        = models.CharField(max_length=20, choices=ON_DELETE_CHOICES,
                                        default='CASCADE', blank=True,
                                        help_text='on_delete for FK/O2O')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Field'
        verbose_name_plural = 'Fields'
        ordering            = ['clazz', 'order', 'name']
        unique_together     = [('clazz', 'name')]

    def __str__(self):
        return f'{self.clazz.name}.{self.name} ({self.field_type})'


# ─── Section ──────────────────────────────────────────────────────────────────
class Section(models.Model):
    """A named group of fields within a Clazz (maps to a fieldset in admin)."""
    clazz       = models.ForeignKey(Clazz, on_delete=models.CASCADE, related_name='sections')
    name        = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True,
                                   help_text='Optional description shown below the section title')
    order       = models.PositiveIntegerField(default=0)
    collapsed   = models.BooleanField(default=False,
                                      help_text='Render section collapsed by default')

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Section'
        verbose_name_plural = 'Sections'
        ordering            = ['clazz', 'order', 'name']
        unique_together     = [('clazz', 'name')]

    def __str__(self):
        return f'{self.clazz.name} › {self.name}'


# ─── SectionField ─────────────────────────────────────────────────────────────
class SectionField(models.Model):
    """Assigns a Field to a Section with an explicit ordering."""
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='section_fields')
    field   = models.ForeignKey(Field,   on_delete=models.CASCADE, related_name='section_fields')
    order   = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name        = 'Section Field'
        verbose_name_plural = 'Section Fields'
        ordering            = ['order']
        unique_together     = [('section', 'field')]

    def __str__(self):
        return f'{self.section} → {self.field.name} (pos {self.order})'


# ─── RelatedTable ─────────────────────────────────────────────────────────────
class RelatedTable(models.Model):
    """
    Inline-editable related model attached to a Section.
    Represents an inline admin (TabularInline / StackedInline) for a FK back-relation.
    """
    INLINE_STYLES = [
        ('tabular',  'Tabular (table rows)'),
        ('stacked',  'Stacked (form per row)'),
    ]

    section         = models.ForeignKey(Section, on_delete=models.CASCADE,
                                        related_name='related_tables')
    related_clazz   = models.ForeignKey(Clazz, on_delete=models.CASCADE,
                                        related_name='used_as_inline',
                                        help_text='The child model shown as inline')
    fk_field        = models.CharField(max_length=100,
                                       help_text='Name of the FK field on the child model '
                                                 'that points back to the parent Clazz')
    verbose_name    = models.CharField(max_length=200, blank=True,
                                       help_text='Override inline heading')
    inline_style    = models.CharField(max_length=10, choices=INLINE_STYLES, default='tabular')
    extra           = models.PositiveIntegerField(default=1,
                                                  help_text='Number of empty extra rows to show')
    max_num         = models.PositiveIntegerField(null=True, blank=True,
                                                  help_text='Max rows allowed (blank = unlimited)')
    fields_display  = models.TextField(blank=True,
                                       help_text='Comma-separated fields to show in the inline '
                                                 '(blank = all)')
    order           = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Related Table'
        verbose_name_plural = 'Related Tables'
        ordering            = ['order']

    def __str__(self):
        return f'{self.section} ↳ {self.related_clazz.name} (inline)'

    def get_fields_display_list(self):
        return [f.strip() for f in self.fields_display.split(',') if f.strip()]
