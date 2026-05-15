from django.contrib import admin
from .models import KpiMicro,KpiMacro,KpiTime,SentimentoMercado
# Register your models here.

admin.site.register(KpiTime)
admin.site.register(SentimentoMercado)


@admin.register(KpiMacro)
class KpiMacroAdmin(admin.ModelAdmin):
    # Escolha quais colunas você quer ver na lista
    list_display = ('data_ref', 'selic', 'ipca_mensal', 'dolar_brl', 'ibc_br')
    # Adiciona um filtro por data na lateral
    list_filter = ('data_ref',)
    # Permite ordenar do mais recente para o mais antigo
    ordering = ('-data_ref',)

@admin.register(KpiMicro)
class KpiMicroAdmin(admin.ModelAdmin):
    # Usando campos reais do seu models.py
    list_display = ('ativo', 'data_ref', 'roe','roa', 'margem_liquida','crescimento_receita', 'crescimento_lucro','margem_ebitda', 'pl','pvpa', 'divida_liquida','divida_ebitda','ev_ebitda')
    list_filter = ('data_ref', 'ativo')
    search_fields = ('ativo__ticker',)
    ordering = ('-data_ref',)