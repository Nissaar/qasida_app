from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import TemplateView

from . import account_views, views
from .forms import (StyledPasswordChangeForm, StyledPasswordResetForm,
                    StyledSetPasswordForm)

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
    path('qasida/<slug:slug>/favourite/', account_views.toggle_favourite, name='toggle_favourite'),
    path('suggestions/', views.suggestion_inbox, name='suggestion_inbox'),

    # Accounts. Django's own views handle the password flows, given this
    # project's forms and templates. Two things here are deliberate. The url
    # names are the ones Django reverses internally, so renaming one breaks the
    # link it emails. And every template is named explicitly: django.contrib
    # .admin ships password_change/password_reset templates under the same
    # default names and is listed before this app, so anything left to the
    # default is quietly served in admin styling instead of the site's.
    path('accounts/register/', account_views.register, name='register'),
    path('accounts/login/', account_views.SignInView.as_view(), name='login'),
    path('accounts/logout/',
         auth_views.LogoutView.as_view(template_name='core/account/signed_out.html'),
         name='logout'),
    path('accounts/password/change/',
         auth_views.PasswordChangeView.as_view(
             form_class=StyledPasswordChangeForm,
             template_name='core/account/password_change.html',
             success_url='/accounts/password/change/done/'),
         name='password_change'),
    path('accounts/password/change/done/',
         auth_views.PasswordChangeDoneView.as_view(
             template_name='core/account/password_changed.html'),
         name='password_change_done'),
    path('accounts/password/reset/',
         auth_views.PasswordResetView.as_view(
             form_class=StyledPasswordResetForm,
             template_name='core/account/password_reset.html',
             email_template_name='core/account/password_reset_email.txt',
             subject_template_name='core/account/password_reset_subject.txt',
             success_url='/accounts/password/reset/sent/'),
         name='password_reset'),
    path('accounts/password/reset/sent/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='core/account/password_reset_sent.html'),
         name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             form_class=StyledSetPasswordForm,
             template_name='core/account/password_reset_confirm.html',
             success_url='/accounts/reset/done/'),
         name='password_reset_confirm'),
    path('accounts/reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='core/account/password_reset_done.html'),
         name='password_reset_complete'),

    # One reader's own corner of the library.
    path('my/', account_views.my_library, name='my_library'),
    path('my/history/', account_views.my_history, name='my_history'),
    path('my/history/clear/', account_views.clear_history, name='clear_history'),
    path('my/corrections/', account_views.my_corrections, name='my_corrections'),
    path('my/settings/', account_views.account_settings, name='account_settings'),
    path('my/settings/delete/', account_views.delete_account, name='delete_account'),
    path('my/saved/<int:pk>/note/', account_views.favourite_note, name='favourite_note'),
]
