from django.contrib import admin
from portfolio.models import Ativo, Carteira, Posicao

admin.site.register(Ativo)
admin.site.register(Carteira)
admin.site.register(Posicao)