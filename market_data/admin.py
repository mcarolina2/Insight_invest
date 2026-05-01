from django.contrib import admin
from .models import KpiMicro,KpiMacro,KpiTime,SentimentoMercado
# Register your models here.

admin.site.register(KpiMicro)
admin.site.register(KpiMacro)
admin.site.register(KpiTime)
admin.site.register(SentimentoMercado)