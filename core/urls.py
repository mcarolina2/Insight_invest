from django.urls import path

from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home_view, name='home'),
    path('buscar-ativo/', views.buscar_ativo, name='buscar_ativo'),
    path('ativo/<str:ticker>/', views.ativo_view, name='ativo'),
    path('metodologia/', views.metodologia_view, name='metodologia'),
    path('sobre/', views.sobre_view, name='sobre'),
    path('desempenho/', views.desempenho_view, name='desempenho'),
]
