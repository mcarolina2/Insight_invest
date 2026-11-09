from django.contrib import admin, messages

from .models import (PERFIS_CHOICES, CarteiraRecomendada, ConfiguracaoPerfil, HistoricoConfiguracaoPerfil,
                     ItemRecomendacao, Recomendacao, ScoreAtivo)


@admin.register(ConfiguracaoPerfil)
class ConfiguracaoPerfilAdmin(admin.ModelAdmin):
    list_display = ("perfil", "peso_fundamentalista", "peso_macro", "peso_estatistico",
                    "qtd_ativos", "max_pct_ativo", "score_min", "atualizado_em", "atualizado_por")
    readonly_fields = ("perfil", "atualizado_em", "atualizado_por")
    actions = ["recalcular_carteiras"]
    fieldsets = (
        (None, {"fields": ("perfil", "descricao")}),
        ("Pesos entre camadas (são renormalizados para somar 1)", {
            "fields": ("peso_fundamentalista", "peso_macro", "peso_estatistico")}),
        ("Pesos das dimensões fundamentalistas (devem somar 1)", {
            "fields": ("peso_liquidez", "peso_rentabilidade", "peso_endividamento", "peso_crescimento")}),
        ("Seleção e otimização", {
            "fields": ("score_min", "qtd_ativos", "max_pct_ativo")}),
        ("Filtros fundamentalistas (deixe em branco para não filtrar)", {
            "fields": ("liquidez_corrente_min", "divida_ebitda_max", "roe_min")}),
        ("Registro", {"fields": ("atualizado_em", "atualizado_por")}),
    )

    def has_add_permission(self, request):
        return False            # os três perfis são fixos; só se editam os parâmetros

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.atualizado_por = request.user
        super().save_model(request, obj, form, change)
        HistoricoConfiguracaoPerfil.objects.create(
            perfil=obj.perfil, configuracao=obj.como_dict(), alterado_por=request.user,
            motivo=f"Alterado no painel: {', '.join(form.changed_data) or 'sem mudanças'}")
        self.message_user(request, "Configuração salva. Use a ação 'Recalcular carteiras' "
                                   "para atualizar as carteiras da página inicial.")

    @admin.action(description="Recalcular carteiras dos perfis selecionados",
                  permissions=["recalcular"])
    def recalcular_carteiras(self, request, queryset):
        from scoring.services.carteira import recalcular_carteiras
        try:
            resultado = recalcular_carteiras([c.perfil for c in queryset])
        except Exception as e:
            self.message_user(request, f"Erro ao recalcular: {e}", messages.ERROR)
            return
        for perfil, texto in resultado.items():
            nivel = messages.WARNING if texto.startswith("sem carteira") else messages.SUCCESS
            self.message_user(request, f"{dict(PERFIS_CHOICES).get(perfil, perfil)}: {texto}", nivel)

    def has_recalcular_permission(self, request):
        return request.user.has_perm("scoring.recalcular_carteiras")


@admin.register(HistoricoConfiguracaoPerfil)
class HistoricoConfiguracaoPerfilAdmin(admin.ModelAdmin):
    list_display = ("perfil", "alterado_em", "alterado_por", "motivo")
    list_filter = ("perfil",)
    readonly_fields = ("perfil", "configuracao", "motivo", "alterado_por", "alterado_em")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CarteiraRecomendada)
class CarteiraRecomendadaAdmin(admin.ModelAdmin):
    list_display = ("perfil", "data_calculo", "modelo_usado", "ativo")
    list_filter = ("perfil", "ativo")
    readonly_fields = ("perfil", "data_calculo", "composicao", "metricas", "modelo_usado", "ativo")


@admin.register(ScoreAtivo)
class ScoreAtivoAdmin(admin.ModelAdmin):
    list_display = ("ativo", "data_calculo", "score_final", "score_micro", "score_macro", "score_estat")
    list_filter = ("data_calculo",)
    search_fields = ("ativo__ticker",)


admin.site.register(Recomendacao)
admin.site.register(ItemRecomendacao)
