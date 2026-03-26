from django.urls import path
from . import views
from . import import_export_views as ie
from . import scripts_views as sv

urlpatterns = [
    # Dashboard
    path('',                                        views.dashboard,             name='dashboard'),

    # Scripts (Class Migrator GUI)
    path('scripts/',                                sv.scripts_index,            name='scripts_index'),
    path('scripts/generate/',                       sv.generate_form,            name='scripts_generate'),
    path('scripts/deploy/',                         sv.deploy_form,              name='scripts_deploy'),
    path('scripts/run/<str:job_id>/',               sv.scripts_run,              name='scripts_run'),
    path('scripts/stream/<str:job_id>/',            sv.scripts_stream,           name='scripts_stream'),

    # Import wizard
    path('import/',                                 ie.import_step1,             name='import_step1'),
    path('import/preview/',                         ie.import_step2,             name='import_step2'),
    path('import/execute/',                         ie.import_execute,           name='import_execute'),

    # Export
    path('export/',                                 ie.export_page,              name='export_page'),
    path('export/download/',                        ie.export_download,          name='export_download'),

    # Clear all
    path('clear/',                                  views.clear_all,             name='clear_all'),

    # Clazz
    path('clazz/new/',                              views.clazz_create,          name='clazz_create'),
    path('clazz/<int:pk>/',                         views.clazz_detail,          name='clazz_detail'),
    path('clazz/<int:pk>/edit/',                    views.clazz_edit,            name='clazz_edit'),
    path('clazz/<int:pk>/delete/',                  views.clazz_delete,          name='clazz_delete'),
    path('clazz/<int:pk>/preview/',                 views.clazz_preview,         name='clazz_preview'),

    # Fields
    path('clazz/<int:clazz_pk>/field/new/',         views.field_create,          name='field_create'),
    path('field/<int:pk>/edit/',                    views.field_edit,            name='field_edit'),
    path('field/<int:pk>/delete/',                  views.field_delete,          name='field_delete'),

    # Sections
    path('clazz/<int:clazz_pk>/section/new/',       views.section_create,        name='section_create'),
    path('section/<int:pk>/',                       views.section_detail,        name='section_detail'),
    path('section/<int:pk>/edit/',                  views.section_edit,          name='section_edit'),
    path('section/<int:pk>/delete/',                views.section_delete,        name='section_delete'),

    # Section ↔ Field linkage
    path('section/<int:section_pk>/field/add/',     views.section_field_add,     name='section_field_add'),
    path('section-field/<int:pk>/remove/',          views.section_field_remove,  name='section_field_remove'),
    path('section/<int:section_pk>/reorder/',       views.section_field_reorder, name='section_field_reorder'),

    # Related tables
    path('section/<int:section_pk>/related/add/',   views.related_table_add,     name='related_table_add'),
    path('related/<int:pk>/edit/',                  views.related_table_edit,    name='related_table_edit'),
    path('related/<int:pk>/delete/',                views.related_table_delete,  name='related_table_delete'),
]
