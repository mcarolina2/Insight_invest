from django.contrib import admin
from django.utils.html import format_html
from .models import Plano, Assinatura
 
 
@admin.register(Plano)
class PlanoAdmin(admin.ModelAdmin):
    list_display = ['nome_exibicao', 'tipo', 'preco_mensal', 'requer_quiz',
                     'acesso_otimizacao_markowitz', 'limite_ativos_visiveis']
 
 
@admin.register(Assinatura)
class AssinaturaAdmin(admin.ModelAdmin):
    list_display = ['user', 'plano_badge', 'status_badge', 'dias_restantes_display', 'valor_pago']
    list_filter  = ['status', 'plano__tipo']
    search_fields = ['user__username', 'user__email']
    actions = ['ativar_pro_30_dias']
 
    def plano_badge(self, obj):
        cor = '#1D9E75' if obj.plano.tipo == 'pro' else '#888780'
        return format_html('<b style="color:{}">{}</b>', cor, obj.plano.nome_exibicao)
    plano_badge.short_description = 'Plano'
 
    def status_badge(self, obj):
        cores = {'ativa': '#1D9E75', 'pendente': '#BA7517',
                 'expirada': '#D85A30', 'cancelada': '#999'}
        cor = cores.get(obj.status, '#999')
        return format_html('<span style="color:{}">{}</span>', cor, obj.get_status_display())
    status_badge.short_description = 'Status'
 
    def dias_restantes_display(self, obj):
        d = obj.dias_restantes
        return f"{d} dias" if d is not None else "—"
    dias_restantes_display.short_description = 'Vencimento'
 
    @admin.action(description='Ativar Pro por 30 dias (pagamento confirmado)')
    def ativar_pro_30_dias(self, request, queryset):
        for assinatura in queryset:
            assinatura.ativar_pro(dias=30, pagamento_id='admin-manual', valor=9.90)
        self.message_user(request, f"{queryset.count()} assinatura(s) ativada(s) como Pro.")
