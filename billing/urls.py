from django.urls import path
from . import views
 
app_name = 'billing'
 
urlpatterns = [
    path('upgrade/',            views.upgrade_view,             name='upgrade'),
    path('solicitar/',          views.solicitar_upgrade,        name='solicitar'),
    path('confirmar/<int:user_id>/', views.confirmar_pagamento_manual, name='confirmar'),
]