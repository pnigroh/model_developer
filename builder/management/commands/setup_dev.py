"""
Management command: setup_dev
Creates the superuser and loads example data on first run.

The seed logic is also importable as seed_example_data() so the
reset/clear view can call it directly without shelling out.
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


def seed_example_data():
    """
    Create the Property + PropertyImage example clazzes.
    Safe to call at any time — skips gracefully if either already exists.
    Returns a human-readable summary string.
    """
    from builder.models import Clazz, Field, Section, SectionField, RelatedTable

    created = []

    # ── Property ──────────────────────────────────────────────────────────────
    if not Clazz.objects.filter(name='Property').exists():
        prop = Clazz.objects.create(
            name='Property',
            verbose_name='Property',
            verbose_name_plural='Properties',
            ordering='-created_at',
            description='Real-estate property listing',
            list_display='title, city, price, status, created_at',
            search_fields='title, city, description',
            list_filter='status, city',
        )

        f_title   = Field.objects.create(clazz=prop, name='title',       field_type='CharField',          max_length=200,  order=0,  verbose_name='Title')
        f_desc    = Field.objects.create(clazz=prop, name='description',  field_type='TextField',          blank=True,      order=1,  verbose_name='Description')
        f_city    = Field.objects.create(clazz=prop, name='city',         field_type='CharField',          max_length=100,  order=2,  verbose_name='City')
        f_addr    = Field.objects.create(clazz=prop, name='address',      field_type='CharField',          max_length=300,  order=3,  verbose_name='Address', blank=True)
        f_price   = Field.objects.create(clazz=prop, name='price',        field_type='DecimalField',       max_digits=12,   decimal_places=2, order=4, verbose_name='Price')
        f_beds    = Field.objects.create(clazz=prop, name='bedrooms',     field_type='PositiveIntegerField',                order=5,  verbose_name='Bedrooms',  default='1')
        f_baths   = Field.objects.create(clazz=prop, name='bathrooms',    field_type='PositiveIntegerField',                order=6,  verbose_name='Bathrooms', default='1')
        f_stat    = Field.objects.create(clazz=prop, name='status',       field_type='CharField',          max_length=20,   order=7,  verbose_name='Status',
                                         choices='active,Active\ndraft,Draft\nsold,Sold\narchived,Archived',
                                         default='draft')
        f_area    = Field.objects.create(clazz=prop, name='area_sqm',     field_type='DecimalField',       max_digits=10,   decimal_places=2, order=8, verbose_name='Area (sqm)', null=True, blank=True)
        f_created = Field.objects.create(clazz=prop, name='created_at',   field_type='DateTimeField',      auto_now_add=True, order=9,  verbose_name='Created At', editable=False)
        f_updated = Field.objects.create(clazz=prop, name='updated_at',   field_type='DateTimeField',      auto_now=True,     order=10, verbose_name='Updated At', editable=False)

        s_general = Section.objects.create(clazz=prop, name='General Information', order=0)
        s_details = Section.objects.create(clazz=prop, name='Property Details',    order=1)
        s_meta    = Section.objects.create(clazz=prop, name='Metadata',            order=2, collapsed=True)

        SectionField.objects.create(section=s_general, field=f_title,   order=0)
        SectionField.objects.create(section=s_general, field=f_desc,    order=1)
        SectionField.objects.create(section=s_general, field=f_city,    order=2)
        SectionField.objects.create(section=s_general, field=f_addr,    order=3)
        SectionField.objects.create(section=s_general, field=f_stat,    order=4)
        SectionField.objects.create(section=s_details, field=f_price,   order=0)
        SectionField.objects.create(section=s_details, field=f_beds,    order=1)
        SectionField.objects.create(section=s_details, field=f_baths,   order=2)
        SectionField.objects.create(section=s_details, field=f_area,    order=3)
        SectionField.objects.create(section=s_meta,    field=f_created, order=0)
        SectionField.objects.create(section=s_meta,    field=f_updated, order=1)

        created.append('Property')
    else:
        prop = Clazz.objects.get(name='Property')
        s_details = prop.sections.filter(name='Property Details').first()

    # ── PropertyImage ─────────────────────────────────────────────────────────
    if not Clazz.objects.filter(name='PropertyImage').exists():
        img = Clazz.objects.create(
            name='PropertyImage',
            verbose_name='Property Image',
            verbose_name_plural='Property Images',
            ordering='order',
            description='Images attached to a Property',
            list_display='property, caption, order',
        )

        fi_prop    = Field.objects.create(clazz=img, name='property', field_type='ForeignKey',
                                          related_clazz=prop, on_delete='CASCADE',
                                          related_name='images', order=0, verbose_name='Property')
        fi_image   = Field.objects.create(clazz=img, name='image',   field_type='ImageField',          order=1, verbose_name='Image')
        fi_caption = Field.objects.create(clazz=img, name='caption', field_type='CharField', max_length=200, blank=True, order=2, verbose_name='Caption')
        fi_order   = Field.objects.create(clazz=img, name='order',   field_type='PositiveIntegerField', default='0', order=3, verbose_name='Order')

        s_img = Section.objects.create(clazz=img, name='Image Info', order=0)
        SectionField.objects.create(section=s_img, field=fi_image,   order=0)
        SectionField.objects.create(section=s_img, field=fi_caption, order=1)
        SectionField.objects.create(section=s_img, field=fi_order,   order=2)

        # Attach images as an inline to the Property's Details section (if it exists)
        if s_details:
            RelatedTable.objects.get_or_create(
                section=s_details,
                related_clazz=img,
                defaults=dict(
                    fk_field='property',
                    verbose_name='Property Images',
                    inline_style='tabular',
                    extra=2,
                    fields_display='image, caption, order',
                    order=0,
                ),
            )

        created.append('PropertyImage')

    if created:
        return f'Example clazzes created: {", ".join(created)}.'
    return 'Example clazzes already exist — nothing added.'


class Command(BaseCommand):
    help = 'Create default superuser and seed example Clazz data'

    def handle(self, *args, **options):
        User = get_user_model()

        # ── Superuser ──────────────────────────────────────────────────────
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'admin')
        email    = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@modeldev.local')

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(self.style.SUCCESS(
                f'Superuser created → username: {username}  password: {password}'
            ))
        else:
            self.stdout.write(self.style.WARNING(f'Superuser "{username}" already exists – skipped.'))

        # ── Example data ───────────────────────────────────────────────────
        from builder.models import Clazz
        if Clazz.objects.exists():
            self.stdout.write(self.style.WARNING('Example data already present – skipped.'))
            return

        self.stdout.write('Seeding example data…')
        msg = seed_example_data()
        self.stdout.write(self.style.SUCCESS(msg))
