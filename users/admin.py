from django.contrib import admin
from .models import PerfilRisco, User, Pergunta, OpcaoResposta, RespostaUsuario

# Register your models here.
admin.site.register(PerfilRisco)
admin.site.register(User)
admin.site.register(Pergunta)
admin.site.register(OpcaoResposta)
admin.site.register(RespostaUsuario)