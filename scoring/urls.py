# scoring/urls.py

from django.urls import path
from . import views

app_name = 'scoring'

urlpatterns = [
    path('',          views.carteira_view,  name='carteira'),
    path('otimizar/', views.otimizar_ajax,  name='otimizar'),
    path('similares/<str:ticker>/', views.similares_view, name='similares'),
]