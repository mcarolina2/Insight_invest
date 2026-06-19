# users/urls.py

from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('login/',           views.login_view,      name='login'),
    path('logout/',          views.logout_view,     name='logout'),
    path('quiz/',            views.quiz_view,        name='quiz'),
    path('quiz/processar/',  views.processar_quiz,   name='processar_quiz'),
    path('quiz/resultado/',  views.resultado_quiz,   name='resultado_quiz'),
    path('perfil/',          views.perfil_view,      name='perfil'),
]