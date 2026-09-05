from django.urls import path
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    # Both must sit at the site root: a service worker can only control paths
    # at or below its own URL.
    path('sw.js', TemplateView.as_view(
        template_name='core/pwa/sw.js',
        content_type='text/javascript'), name='service_worker'),
    path('manifest.webmanifest', TemplateView.as_view(
        template_name='core/pwa/manifest.webmanifest',
        content_type='application/manifest+json'), name='manifest'),
    path('offline/', TemplateView.as_view(template_name='core/offline.html'), name='offline'),
    path('browse/', views.browse, name='browse'),
    path('poets/', views.poets, name='poets'),
    path('categories/', views.categories, name='categories'),
    path('collections/', views.collections, name='collections'),
    path('collection/<slug:slug>/', views.collection, name='collection'),
    path('search/', views.search, name='search'),
    path('random/', views.random_qasida, name='random_qasida'),
    # `path` rather than `str`: some author fields hold two names joined with
    # a slash, which `str` refuses to match or reverse.
    path('poet/<path:name>/', views.poet, name='poet'),
    # The numeric route is declared first on purpose: the slug converter also
    # matches digits, so /qasida/2528/ would otherwise be looked up as a slug
    # and 404. Links already published, and pages held in the service worker
    # cache, use this shape.
    path('qasida/<int:pk>/', views.qasida_by_id, name='qasida_by_id'),
    path('qasida/<slug:slug>/', views.qasida_detail, name='qasida_detail'),
    path('qasida/<slug:slug>/edit/', views.qasida_edit, name='qasida_edit'),
    path('qasida/<slug:slug>/download/', views.qasida_download, name='qasida_download'),
    path('suggestions/', views.suggestion_inbox, name='suggestion_inbox'),
]
