# users/urls.py
from django.urls import path

from . import views

app_name = 'users'

urlpatterns = [
    path('login/',    views.login_view,  name='login'),
    path('logout/',   views.logout_view, name='logout'),
    path('cadastro/', views.registro,    name='registro'),
    path('quiz/',     views.quiz,        name='quiz'),
    path('perfil/',   views.perfil_view, name='perfil'),
    path('perfis/',   views.perfis_view, name='perfis'),
]
