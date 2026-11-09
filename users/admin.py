from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import PerfilRisco, RespostaQuiz, User


@admin.register(User)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "email", "is_staff", "date_joined")


@admin.register(PerfilRisco)
class PerfilRiscoAdmin(admin.ModelAdmin):
    list_display = ("user", "tipo", "pontuacao", "atualizado_em")
    list_filter = ("tipo",)
    search_fields = ("user__username",)


@admin.register(RespostaQuiz)
class RespostaQuizAdmin(admin.ModelAdmin):
    list_display = ("user", "perfil", "pontuacao", "perfil_sugerido_home", "criado_em")
    list_filter = ("perfil", "criado_em")
    search_fields = ("user__username",)
    readonly_fields = ("user", "perfil", "pontuacao", "respostas", "por_dimensao", "vetos",
                       "perfil_sugerido_home", "criado_em")

    def has_add_permission(self, request):
        return False
