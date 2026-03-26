"""
builder/importer.py
───────────────────
AST-based parser that reads Django source files (models.py, admin.py, apps.py)
and produces a structured list of ClazzData dicts ready to be saved to the DB.

Parsing strategy
────────────────
models.py  → class Model(models.Model) → fields, Meta
admin.py   → class ModelAdmin → list_display, search_fields, list_filter,
             date_hierarchy, fieldsets (→ sections), inlines (→ RelatedTables)
apps.py    → AppConfig.label / verbose_name (used to enrich Clazz.app_label)
"""

import ast
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional


# ── Known Django field types (normalised set) ──────────────────────────────────
KNOWN_FIELD_TYPES = {
    'CharField', 'TextField', 'SlugField', 'EmailField', 'URLField', 'UUIDField',
    'GenericIPAddressField', 'IntegerField', 'PositiveIntegerField', 'BigIntegerField',
    'SmallIntegerField', 'FloatField', 'DecimalField', 'BooleanField', 'NullBooleanField',
    'DateField', 'DateTimeField', 'TimeField', 'DurationField', 'FileField',
    'ImageField', 'FilePathField', 'ForeignKey', 'OneToOneField', 'ManyToManyField',
    'JSONField', 'BinaryField', 'AutoField', 'BigAutoField', 'PositiveBigIntegerField',
    'PositiveSmallIntegerField', 'SmallAutoField',
}

# Fields that are NOT real database columns — skip them entirely during import
NON_DB_FIELD_TYPES = {
    'GenericForeignKey',      # virtual — backed by two real FK + int fields
    'GenericRelation',        # reverse accessor only, no column
    'property',               # Python property, not a Django field
}

ON_DELETE_VALUES = {'CASCADE', 'PROTECT', 'SET_NULL', 'SET_DEFAULT', 'DO_NOTHING', 'RESTRICT'}


# ── Data classes ───────────────────────────────────────────────────────────────
@dataclass
class FieldData:
    name: str
    field_type: str
    verbose_name: str = ''
    null: bool = False
    blank: bool = False
    unique: bool = False
    db_index: bool = False
    primary_key: bool = False
    editable: bool = True
    max_length: Optional[int] = None
    default: str = ''
    help_text: str = ''
    choices: str = ''          # "VALUE,Label\nVALUE2,Label2"
    max_digits: Optional[int] = None
    decimal_places: Optional[int] = None
    auto_now: bool = False
    auto_now_add: bool = False
    related_model: str = ''    # target model name (string)
    related_name: str = ''
    on_delete: str = 'CASCADE'
    order: int = 0


@dataclass
class SectionData:
    name: str
    description: str = ''
    order: int = 0
    collapsed: bool = False
    field_names: list = field(default_factory=list)  # ordered list of field names


@dataclass
class RelatedTableData:
    related_model: str          # child model name
    fk_field: str               # FK field name on child
    verbose_name: str = ''
    inline_style: str = 'tabular'
    extra: int = 1
    max_num: Optional[int] = None
    fields_display: str = ''
    section_name: str = ''      # which section to attach to
    order: int = 0


@dataclass
class ClazzData:
    name: str
    verbose_name: str = ''
    verbose_name_plural: str = ''
    app_label: str = ''
    db_table: str = ''
    ordering: str = ''
    abstract: bool = False
    description: str = ''
    list_display: str = ''
    search_fields: str = ''
    list_filter: str = ''
    date_hierarchy: str = ''
    fields: list = field(default_factory=list)         # List[FieldData]
    sections: list = field(default_factory=list)       # List[SectionData]
    related_tables: list = field(default_factory=list) # List[RelatedTableData]
    # warnings / notes accumulated during parsing
    warnings: list = field(default_factory=list)


@dataclass
class ImportResult:
    clazzes: list = field(default_factory=list)   # List[ClazzData]
    app_label: str = ''
    app_verbose_name: str = ''
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _ast_value(node) -> str:
    """Convert a simple AST constant / Name / Attribute to a string representation."""
    if node is None:
        return ''
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_ast_value(node.value)}.{node.attr}'
    if isinstance(node, ast.List):
        return ', '.join(_ast_value(e) for e in node.elts)
    if isinstance(node, ast.Tuple):
        return ', '.join(_ast_value(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return f'-{_ast_value(node.operand)}'
    if isinstance(node, ast.Call):
        # e.g. list('...') or something – just return empty
        return ''
    return ''


def _ast_bool(node) -> Optional[bool]:
    if isinstance(node, ast.Constant):
        if node.value is True:
            return True
        if node.value is False:
            return False
    if isinstance(node, ast.Name):
        if node.id == 'True':
            return True
        if node.id == 'False':
            return False
    return None


def _ast_int(node) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _get_kwarg(keywords: list, name: str):
    """Return the AST node for a keyword argument by name, or None."""
    for kw in keywords:
        if kw.arg == name:
            return kw.value
    return None


def _normalise_field_type(raw: str) -> str:
    """models.CharField → CharField, etc."""
    if '.' in raw:
        raw = raw.split('.')[-1]
    return raw if raw in KNOWN_FIELD_TYPES else raw


def _extract_choices_string(node) -> str:
    """
    Try to extract a simple tuple-of-tuples choices definition into
    VALUE,Label\\n... format.
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ''
    lines = []
    for elt in node.elts:
        if isinstance(elt, (ast.Tuple, ast.List)) and len(elt.elts) >= 2:
            val   = _ast_value(elt.elts[0])
            label = _ast_value(elt.elts[1])
            if val and label:
                lines.append(f'{val},{label}')
    return '\n'.join(lines)


def _extract_list_of_strings(node) -> list:
    """Extract list/tuple of string constants."""
    result = []
    if isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            v = _ast_value(elt)
            if v:
                result.append(v.strip('^').lstrip('^'))
    elif isinstance(node, ast.Constant):
        result.append(str(node.value))
    return result


def _docstring(class_node) -> str:
    """Extract docstring from a class body."""
    if (class_node.body
            and isinstance(class_node.body[0], ast.Expr)
            and isinstance(class_node.body[0].value, ast.Constant)):
        return textwrap.dedent(class_node.body[0].value.value).strip()
    return ''


# ── models.py parser ───────────────────────────────────────────────────────────
def parse_models_file(source: str) -> tuple[dict, list]:
    """
    Parse models.py source.
    Returns (clazz_map, errors) where clazz_map = {name: ClazzData}.
    """
    clazz_map: dict[str, ClazzData] = {}
    errors: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        errors.append(f'models.py syntax error: {e}')
        return clazz_map, errors

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Only pick up classes that inherit from models.Model / Model / AbstractModel etc.
        base_names = []
        for base in node.bases:
            base_names.append(_ast_value(base))
        is_model = any(
            'Model' in b or 'models.Model' in b or 'TimeStampedModel' in b
            for b in base_names
        )
        if not is_model:
            continue

        cd = ClazzData(name=node.name, description=_docstring(node))

        field_order = 0
        for item in node.body:
            # ── class Meta ──────────────────────────────────────────────────
            if isinstance(item, ast.ClassDef) and item.name == 'Meta':
                _parse_meta(item, cd)
                continue

            # ── field assignments: name = models.XField(...) ────────────────
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    fname = target.id
                    if fname.startswith('_'):
                        continue
                    fd = _parse_field_assignment(fname, item.value, field_order)
                    if fd:
                        cd.fields.append(fd)
                        field_order += 1
                continue

            if isinstance(item, ast.AnnAssign):
                # annotated assignments (less common in models but possible)
                if isinstance(item.target, ast.Name) and item.value:
                    fname = item.target.id
                    fd = _parse_field_assignment(fname, item.value, field_order)
                    if fd:
                        cd.fields.append(fd)
                        field_order += 1

        clazz_map[cd.name] = cd

    return clazz_map, errors


def _parse_meta(meta_node: ast.ClassDef, cd: ClazzData):
    for item in meta_node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name):
                continue
            attr = target.id
            val_node = item.value

            if attr == 'verbose_name':
                cd.verbose_name = _ast_value(val_node)
            elif attr == 'verbose_name_plural':
                cd.verbose_name_plural = _ast_value(val_node)
            elif attr == 'app_label':
                cd.app_label = _ast_value(val_node)
            elif attr == 'db_table':
                cd.db_table = _ast_value(val_node)
            elif attr == 'abstract':
                v = _ast_bool(val_node)
                if v is not None:
                    cd.abstract = v
            elif attr == 'ordering':
                parts = _extract_list_of_strings(val_node)
                cd.ordering = ', '.join(parts)


def _parse_field_assignment(fname: str, val_node, order: int) -> Optional[FieldData]:
    """Parse a single field assignment value node into a FieldData."""
    if not isinstance(val_node, ast.Call):
        return None

    # Resolve field type
    func = val_node.func
    if isinstance(func, ast.Attribute):
        raw_type = func.attr
    elif isinstance(func, ast.Name):
        raw_type = func.id
    else:
        return None

    field_type = _normalise_field_type(raw_type)

    # Skip virtual / non-DB field types explicitly
    if field_type in NON_DB_FIELD_TYPES:
        return None

    # We only care about known Django field types
    if field_type not in KNOWN_FIELD_TYPES:
        return None

    kws = val_node.keywords
    args = val_node.args

    fd = FieldData(name=fname, field_type=field_type, order=order)

    # ── positional args ──────────────────────────────────────────────────────
    # ForeignKey(Model, on_delete=...) / ManyToManyField(Model)
    if field_type in ('ForeignKey', 'OneToOneField', 'ManyToManyField') and args:
        target = _ast_value(args[0])
        # Strip quotes and module prefix
        target = target.strip("'\"")
        if '.' in target:
            target = target.split('.')[-1]
        fd.related_model = target

    # CharField('verbose name', max_length=...) — first positional arg is verbose_name
    if field_type not in ('ForeignKey', 'OneToOneField', 'ManyToManyField') and args:
        fd.verbose_name = _ast_value(args[0]).strip("'\"")

    # ── keyword args ─────────────────────────────────────────────────────────
    vn = _get_kwarg(kws, 'verbose_name')
    if vn:
        fd.verbose_name = _ast_value(vn).strip("'\"")

    ml = _get_kwarg(kws, 'max_length')
    if ml:
        fd.max_length = _ast_int(ml)

    for bool_attr in ('null', 'blank', 'unique', 'db_index', 'primary_key',
                      'auto_now', 'auto_now_add'):
        kw = _get_kwarg(kws, bool_attr)
        if kw is not None:
            v = _ast_bool(kw)
            if v is not None:
                setattr(fd, bool_attr, v)

    ed = _get_kwarg(kws, 'editable')
    if ed is not None:
        v = _ast_bool(ed)
        if v is not None:
            fd.editable = v

    md = _get_kwarg(kws, 'max_digits')
    if md:
        fd.max_digits = _ast_int(md)

    dp = _get_kwarg(kws, 'decimal_places')
    if dp:
        fd.decimal_places = _ast_int(dp)

    ht = _get_kwarg(kws, 'help_text')
    if ht:
        fd.help_text = _ast_value(ht).strip("'\"")

    # default — store as string
    dft = _get_kwarg(kws, 'default')
    if dft is not None:
        fd.default = _ast_value(dft).strip("'\"")

    ch = _get_kwarg(kws, 'choices')
    if ch:
        fd.choices = _extract_choices_string(ch)
        # If choices is a Name (variable ref), note it
        if not fd.choices and isinstance(ch, ast.Name):
            fd.choices = f'# ref: {ch.id}'

    # relation kwargs
    to_node = _get_kwarg(kws, 'to')
    if to_node and not fd.related_model:
        target = _ast_value(to_node).strip("'\"")
        if '.' in target:
            target = target.split('.')[-1]
        fd.related_model = target

    rn = _get_kwarg(kws, 'related_name')
    if rn:
        fd.related_name = _ast_value(rn).strip("'\"")

    od = _get_kwarg(kws, 'on_delete')
    if od:
        raw_od = _ast_value(od)
        if '.' in raw_od:
            raw_od = raw_od.split('.')[-1]
        if raw_od in ON_DELETE_VALUES:
            fd.on_delete = raw_od

    return fd


# ── admin.py parser ────────────────────────────────────────────────────────────
def parse_admin_file(source: str, clazz_map: dict[str, ClazzData]) -> list[str]:
    """
    Parse admin.py and enrich clazz_map with admin configuration.
    Returns list of warning strings.
    """
    warnings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f'admin.py syntax error: {e}']

    # Collect inline class definitions first: InlineName → child model name
    # e.g. class ImageInline(admin.TabularInline): model = Image
    inline_map: dict[str, RelatedTableData] = {}  # inline class name → data

    # Also map @admin.register(Model) decorators
    register_map: dict[str, str] = {}   # AdminClass name → Model name

    # Pass 1: find all class definitions
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        base_names = [_ast_value(b) for b in node.bases]
        is_inline = any(
            'TabularInline' in b or 'StackedInline' in b or 'GenericTabularInline' in b
            for b in base_names
        )
        is_admin = any(
            'ModelAdmin' in b or 'AdminModel' in b
            for b in base_names
        )

        if is_inline:
            rt = _parse_inline_class(node)
            if rt:
                inline_map[node.name] = rt

        if is_admin:
            # Check for @admin.register() decorator
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    fn = _ast_value(dec.func)
                    if 'register' in fn and dec.args:
                        model_name = _ast_value(dec.args[0]).strip("'\"")
                        if '.' in model_name:
                            model_name = model_name.split('.')[-1]
                        register_map[node.name] = model_name

    # Pass 2: find admin.site.register() calls at module level
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        fn = _ast_value(call.func)
        if 'register' not in fn:
            continue
        # admin.site.register(Model, AdminClass) or admin.site.register(Model)
        if len(call.args) >= 1:
            model_name = _ast_value(call.args[0]).strip("'\"")
            if '.' in model_name:
                model_name = model_name.split('.')[-1]
            admin_cls = None
            if len(call.args) >= 2:
                admin_cls = _ast_value(call.args[1])
            if admin_cls:
                register_map[admin_cls] = model_name

    # Pass 3: parse each ModelAdmin and enrich ClazzData
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [_ast_value(b) for b in node.bases]
        is_admin = any('ModelAdmin' in b or 'AdminModel' in b for b in base_names)
        if not is_admin:
            continue

        model_name = register_map.get(node.name, '')
        if not model_name:
            # Try to infer from class name: PropertyAdmin → Property
            guess = node.name.removesuffix('Admin')
            if guess in clazz_map:
                model_name = guess

        if not model_name or model_name not in clazz_map:
            warnings.append(
                f'admin.py: Could not map {node.name} to a known model – skipped.'
            )
            continue

        cd = clazz_map[model_name]
        _enrich_clazz_from_admin(node, cd, inline_map, warnings)

    return warnings


def _parse_inline_class(node: ast.ClassDef) -> Optional[RelatedTableData]:
    """Parse an Inline class into a RelatedTableData stub."""
    base_names = [_ast_value(b) for b in node.bases]
    style = 'tabular'
    if any('Stacked' in b for b in base_names):
        style = 'stacked'

    rt = RelatedTableData(related_model='', fk_field='', inline_style=style)

    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name):
                continue
            attr = target.id
            val  = item.value

            if attr == 'model':
                name = _ast_value(val).strip("'\"")
                if '.' in name:
                    name = name.split('.')[-1]
                rt.related_model = name
            elif attr == 'fk_name':
                rt.fk_field = _ast_value(val).strip("'\"")
            elif attr == 'extra':
                v = _ast_int(val)
                if v is not None:
                    rt.extra = v
            elif attr == 'max_num':
                v = _ast_int(val)
                rt.max_num = v
            elif attr == 'verbose_name':
                rt.verbose_name = _ast_value(val).strip("'\"")
            elif attr == 'fields':
                parts = _extract_list_of_strings(val)
                rt.fields_display = ', '.join(parts)

    # Derive fk_field from inline class name if not found:
    # PropertyImageInline → property (parent field guess; will be marked as unknown)
    if not rt.fk_field:
        rt.fk_field = '?'

    return rt if rt.related_model else None


def _enrich_clazz_from_admin(
    node: ast.ClassDef,
    cd: ClazzData,
    inline_map: dict[str, RelatedTableData],
    warnings: list[str],
):
    """Parse a ModelAdmin class body and write results into cd."""
    inlines_attr = []

    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name):
                continue
            attr = target.id
            val  = item.value

            if attr == 'list_display':
                parts = _extract_list_of_strings(val)
                # filter out non-field strings like '__str__'
                parts = [p for p in parts if p != '__str__']
                cd.list_display = ', '.join(parts)

            elif attr == 'search_fields':
                parts = _extract_list_of_strings(val)
                # strip __ lookups like name__icontains → name
                parts = [p.split('__')[0].lstrip('^=@') for p in parts]
                cd.search_fields = ', '.join(dict.fromkeys(parts))  # dedup

            elif attr == 'list_filter':
                parts = _extract_list_of_strings(val)
                cd.list_filter = ', '.join(parts)

            elif attr == 'date_hierarchy':
                cd.date_hierarchy = _ast_value(val).strip("'\"")

            elif attr == 'inlines':
                inlines_attr = _extract_list_of_strings(val)

            elif attr == 'fieldsets':
                _parse_fieldsets(val, cd, warnings)

            elif attr == 'fields' and not cd.sections:
                # Simple flat fields= without fieldsets → create one default section
                parts = [p for p in _extract_list_of_strings(val) if not p.startswith('(')]
                if parts:
                    sec = SectionData(name='General', order=0, field_names=parts)
                    cd.sections.append(sec)

    # Process inlines → RelatedTableData attached to first section (or create one)
    for inline_cls_name in inlines_attr:
        rt_stub = inline_map.get(inline_cls_name)
        if not rt_stub:
            warnings.append(
                f'admin.py: Inline "{inline_cls_name}" not found in inline definitions.'
            )
            continue
        import copy
        rt = copy.deepcopy(rt_stub)
        # Attach to first section, or mark as unattached
        rt.section_name = cd.sections[0].name if cd.sections else ''
        rt.order = len(cd.related_tables)
        cd.related_tables.append(rt)


def _parse_fieldsets(val_node, cd: ClazzData, warnings: list[str]):
    """
    Parse fieldsets = (
        ('Section Name', {'fields': ('f1', 'f2'), 'classes': ('collapse',)}),
        ...
    )
    """
    if not isinstance(val_node, (ast.List, ast.Tuple)):
        return

    for idx, fs in enumerate(val_node.elts):
        if not isinstance(fs, (ast.Tuple, ast.List)) or len(fs.elts) < 2:
            continue

        title_node = fs.elts[0]
        opts_node  = fs.elts[1]

        title = _ast_value(title_node)
        if title == 'None':
            title = 'General'
        title = title.strip("'\"") or f'Section {idx+1}'

        sec = SectionData(name=title, order=idx)

        if isinstance(opts_node, ast.Dict):
            for k_node, v_node in zip(opts_node.keys, opts_node.values):
                k = _ast_value(k_node).strip("'\"")
                if k == 'fields':
                    raw_fields = _extract_list_of_strings(v_node)
                    # fieldsets can contain tuples (for side-by-side) — flatten
                    flat = []
                    for f_item in raw_fields:
                        flat.extend([x.strip() for x in f_item.split(',') if x.strip()])
                    sec.field_names = flat
                elif k == 'description':
                    sec.description = _ast_value(v_node).strip("'\"")
                elif k == 'classes':
                    classes = _extract_list_of_strings(v_node)
                    sec.collapsed = any('collapse' in c for c in classes)

        cd.sections.append(sec)


# ── apps.py parser ─────────────────────────────────────────────────────────────
def parse_apps_file(source: str) -> tuple[str, str]:
    """Return (app_label, verbose_name) from apps.py."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return '', ''

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [_ast_value(b) for b in node.bases]
        if not any('AppConfig' in b for b in base_names):
            continue
        label = ''
        verbose = ''
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                attr = target.id
                if attr == 'name' or attr == 'label':
                    v = _ast_value(item.value).strip("'\"")
                    if attr == 'label':
                        label = v
                    else:
                        # name is 'myapp.subapp' → last part is label
                        label = v.split('.')[-1]
                elif attr == 'verbose_name':
                    verbose = _ast_value(item.value).strip("'\"")
        return label, verbose
    return '', ''


# ── Top-level entry point ──────────────────────────────────────────────────────
def parse_files(files: dict[str, str]) -> ImportResult:
    """
    files = {'models.py': '...', 'admin.py': '...', 'apps.py': '...'}
    Returns an ImportResult.
    """
    result = ImportResult()

    # 1. apps.py
    if 'apps.py' in files:
        app_label, app_verbose = parse_apps_file(files['apps.py'])
        result.app_label        = app_label
        result.app_verbose_name = app_verbose

    # 2. models.py — required
    if 'models.py' not in files:
        result.errors.append('No models.py provided.')
        return result

    clazz_map, errors = parse_models_file(files['models.py'])
    result.errors.extend(errors)

    if not clazz_map:
        result.warnings.append('No Django model classes found in models.py.')

    # Apply app_label from apps.py to all clazzes (if not already set)
    for cd in clazz_map.values():
        if result.app_label and not cd.app_label:
            cd.app_label = result.app_label

    # 3. admin.py — optional
    if 'admin.py' in files:
        admin_warns = parse_admin_file(files['admin.py'], clazz_map)
        result.warnings.extend(admin_warns)

    result.clazzes = list(clazz_map.values())
    return result


# ── DB persistence ─────────────────────────────────────────────────────────────
def save_import_result(
    result: ImportResult,
    selected_names: list[str],
    conflict_strategy: str = 'skip',  # 'skip' | 'overwrite'
) -> dict:
    """
    Persist selected clazzes into the database.
    Returns a summary dict with counts.
    """
    from .models import (
        Clazz, Field as FieldModel, Section as SectionModel,
        SectionField, RelatedTable,
    )

    summary = {
        'created': [], 'updated': [], 'skipped': [], 'errors': [],
        'fields_created': 0, 'sections_created': 0, 'related_created': 0,
    }

    selected = [cd for cd in result.clazzes if cd.name in selected_names]

    # Build a name → Clazz DB object map (for FK resolution)
    existing_clazz_db: dict[str, Clazz] = {
        c.name: c for c in Clazz.objects.all()
    }

    # ── Phase 1: create / update Clazz records ─────────────────────────────
    clazz_db_map: dict[str, Clazz] = {}

    for cd in selected:
        exists = cd.name in existing_clazz_db

        if exists and conflict_strategy == 'skip':
            summary['skipped'].append(cd.name)
            clazz_db_map[cd.name] = existing_clazz_db[cd.name]
            continue

        defaults = dict(
            verbose_name        = cd.verbose_name,
            verbose_name_plural = cd.verbose_name_plural,
            app_label           = cd.app_label,
            db_table            = cd.db_table,
            ordering            = cd.ordering,
            abstract            = cd.abstract,
            description         = cd.description,
            list_display        = cd.list_display,
            search_fields       = cd.search_fields,
            list_filter         = cd.list_filter,
            date_hierarchy      = cd.date_hierarchy,
        )

        try:
            clazz_obj, created = Clazz.objects.update_or_create(
                name=cd.name, defaults=defaults
            )
            clazz_db_map[cd.name] = clazz_obj
            if created:
                summary['created'].append(cd.name)
            else:
                summary['updated'].append(cd.name)
        except Exception as e:
            summary['errors'].append(f'{cd.name}: {e}')
            continue

    # ── Phase 2: fields ────────────────────────────────────────────────────
    # Refresh existing clazz map now that we've created new ones
    all_clazz_db: dict[str, Clazz] = {
        c.name: c for c in Clazz.objects.all()
    }

    for cd in selected:
        clazz_obj = clazz_db_map.get(cd.name)
        if not clazz_obj:
            continue

        # If overwriting, delete existing fields
        if conflict_strategy == 'overwrite':
            clazz_obj.fields.all().delete()
            clazz_obj.sections.all().delete()

        for fd in cd.fields:
            related_clazz_obj = None
            if fd.related_model:
                related_clazz_obj = all_clazz_db.get(fd.related_model)

            try:
                _, created = FieldModel.objects.update_or_create(
                    clazz=clazz_obj,
                    name=fd.name,
                    defaults=dict(
                        verbose_name  = fd.verbose_name,
                        field_type    = fd.field_type,
                        order         = fd.order,
                        null          = fd.null,
                        blank         = fd.blank,
                        unique        = fd.unique,
                        db_index      = fd.db_index,
                        primary_key   = fd.primary_key,
                        editable      = fd.editable,
                        max_length    = fd.max_length,
                        default       = fd.default,
                        help_text     = fd.help_text,
                        choices       = fd.choices,
                        max_digits    = fd.max_digits,
                        decimal_places= fd.decimal_places,
                        auto_now      = fd.auto_now,
                        auto_now_add  = fd.auto_now_add,
                        related_clazz = related_clazz_obj,
                        related_name  = fd.related_name,
                        on_delete     = fd.on_delete or 'CASCADE',
                    ),
                )
                if created:
                    summary['fields_created'] += 1
            except Exception as e:
                summary['errors'].append(f'{cd.name}.{fd.name}: {e}')

    # ── Phase 3: sections + SectionFields ──────────────────────────────────
    for cd in selected:
        clazz_obj = clazz_db_map.get(cd.name)
        if not clazz_obj:
            continue

        # Field name → DB object map for this clazz
        field_db = {f.name: f for f in clazz_obj.fields.all()}

        for sec_data in cd.sections:
            try:
                sec_obj, _ = SectionModel.objects.get_or_create(
                    clazz=clazz_obj,
                    name=sec_data.name,
                    defaults=dict(
                        description = sec_data.description,
                        order       = sec_data.order,
                        collapsed   = sec_data.collapsed,
                    ),
                )
                summary['sections_created'] += 1

                for sf_order, fname in enumerate(sec_data.field_names):
                    fobj = field_db.get(fname)
                    if fobj:
                        SectionField.objects.get_or_create(
                            section=sec_obj,
                            field=fobj,
                            defaults={'order': sf_order},
                        )
            except Exception as e:
                summary['errors'].append(f'{cd.name} section {sec_data.name}: {e}')

    # ── Phase 4: related tables ────────────────────────────────────────────
    for cd in selected:
        clazz_obj = clazz_db_map.get(cd.name)
        if not clazz_obj:
            continue

        for rt_data in cd.related_tables:
            child_clazz = all_clazz_db.get(rt_data.related_model)
            if not child_clazz:
                summary['errors'].append(
                    f'{cd.name}: inline child "{rt_data.related_model}" not in DB – skipped.'
                )
                continue

            # Find target section
            target_section = None
            if rt_data.section_name:
                target_section = clazz_obj.sections.filter(
                    name=rt_data.section_name
                ).first()
            if not target_section:
                target_section = clazz_obj.sections.first()
            if not target_section:
                # Create a default section to hold the inline
                target_section, _ = SectionModel.objects.get_or_create(
                    clazz=clazz_obj, name='General',
                    defaults={'order': 0}
                )

            fk_field = rt_data.fk_field if rt_data.fk_field != '?' else ''

            try:
                RelatedTable.objects.get_or_create(
                    section=target_section,
                    related_clazz=child_clazz,
                    defaults=dict(
                        fk_field       = fk_field,
                        verbose_name   = rt_data.verbose_name,
                        inline_style   = rt_data.inline_style,
                        extra          = rt_data.extra,
                        max_num        = rt_data.max_num,
                        fields_display = rt_data.fields_display,
                        order          = rt_data.order,
                    ),
                )
                summary['related_created'] += 1
            except Exception as e:
                summary['errors'].append(
                    f'{cd.name} inline {rt_data.related_model}: {e}'
                )

    return summary
