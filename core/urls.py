from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search, name='search'),
    path('qasida/<int:pk>/', views.qasida_detail, name='qasida_detail'),
]
