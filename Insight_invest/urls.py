"""
URL configuration for Insight_invest project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

admin.site.site_header = "Insight Invest — administração"
admin.site.site_title = "Insight Invest"
admin.site.index_title = "Painel do administrador e do analista"

urlpatterns = [
    path('admin/',    admin.site.urls),
    path('favicon.ico', lambda r: HttpResponse(status=204)),   # o admin pede este arquivo
    path('',          include('core.urls')),
    path('',          include('users.urls')),
    path('carteira/', include('scoring.urls')),
    path('billing/',  include('billing.urls')),
]
