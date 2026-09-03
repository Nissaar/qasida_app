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
    path('search/', views.search, name='search'),
    path('random/', views.random_qasida, name='random_qasida'),
    path('poet/<str:name>/', views.poet, name='poet'),
    path('qasida/<int:pk>/', views.qasida_detail, name='qasida_detail'),
    path('qasida/<int:pk>/edit/', views.qasida_edit, name='qasida_edit'),
    path('suggestions/', views.suggestion_inbox, name='suggestion_inbox'),
]
