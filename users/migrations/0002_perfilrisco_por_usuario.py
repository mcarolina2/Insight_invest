"""
Converte PerfilRisco de "tabela de categorias" (um registro por tipo, ligado
ao usuário por User.perfil_risco) para "um perfil por usuário" (PerfilRisco.user).

Os perfis já atribuídos são PRESERVADOS: para cada usuário que tinha
User.perfil_risco, cria-se um PerfilRisco(user=..., tipo=...) com pontuação 0.
Depois, as linhas antigas de categoria são apagadas e o campo
User.perfil_risco é removido.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copiar_perfis(apps, schema_editor):
    User = apps.get_model("users", "User")
    PerfilRisco = apps.get_model("users", "PerfilRisco")
    db = schema_editor.connection.alias
    antigos = list(PerfilRisco.objects.using(db).filter(user__isnull=True))
    for categoria in antigos:
        for u in User.objects.using(db).filter(perfil_risco=categoria):
            PerfilRisco.objects.using(db).create(user=u, tipo=categoria.tipo, pontuacao=0, respostas={})
    PerfilRisco.objects.using(db).filter(id__in=[c.id for c in antigos]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        # 1. tipo deixa de ser único (vários usuários podem ser "conservador")
        migrations.AlterField(
            model_name="perfilrisco", name="tipo",
            field=models.CharField(choices=[("conservador", "Conservador"),
                                            ("intermediario", "Intermediário"),
                                            ("arrojado", "Arrojado")], max_length=20),
        ),
        # 2. novos campos (user ainda opcional para permitir a cópia)
        migrations.AddField(
            model_name="perfilrisco", name="user",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name="perfil_risco_obj", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(model_name="perfilrisco", name="pontuacao",
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="perfilrisco", name="respostas",
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="perfilrisco", name="atualizado_em",
                            field=models.DateTimeField(auto_now=True)),
        # 3. remove os campos de categoria (obrigatórios, impediriam a cópia)
        migrations.RemoveField(model_name="perfilrisco", name="score_min"),
        migrations.RemoveField(model_name="perfilrisco", name="score_max"),
        migrations.RemoveField(model_name="perfilrisco", name="descricao"),
        migrations.RemoveField(model_name="perfilrisco", name="max_renda_variavel_pct"),
        # 4. copia os perfis existentes e apaga as categorias antigas
        migrations.RunPython(copiar_perfis, migrations.RunPython.noop),
        # 5. remove o vínculo antigo
        migrations.RemoveField(model_name="user", name="perfil_risco"),
        # 6. user passa a ser obrigatório
        migrations.AlterField(
            model_name="perfilrisco", name="user",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                       related_name="perfil_risco_obj", to=settings.AUTH_USER_MODEL),
        ),
    ]
