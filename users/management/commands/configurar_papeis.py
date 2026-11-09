"""
Cria (ou atualiza) o grupo "Analista" e suas permissões. Idempotente: roda no build.

Papéis do Insight Invest:
  - Superusuário (administradora do sistema): acesso total ao painel /admin/.
  - Analista (ex.: economista): entra no painel e pode ajustar a configuração
    dos perfis, ver o histórico e recalcular as carteiras; vê dados de mercado
    e os perfis dos usuários, mas não altera usuários nem dados de mercado.
  - Investidor (usuário comum): não entra no painel; tem um perfil de risco
    (conservador, intermediário ou arrojado) definido pelo questionário.

Para tornar alguém analista:  python manage.py criar_analista <usuario>
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

PERMISSOES_ANALISTA = [
    ("scoring", "view_configuracaoperfil"),
    ("scoring", "change_configuracaoperfil"),
    ("scoring", "recalcular_carteiras"),
    ("scoring", "view_historicoconfiguracaoperfil"),
    ("scoring", "view_carteirarecomendada"),
    ("scoring", "view_scoreativo"),
    ("users", "view_perfilrisco"),
    ("users", "view_respostaquiz"),
    ("portfolio", "view_ativo"),
    ("market_data", "view_kpimicro"),
    ("market_data", "view_kpimacro"),
    ("market_data", "view_kpiestatistico"),
    ("market_data", "view_kpitime"),
]


class Command(BaseCommand):
    help = "Cria o grupo Analista com as permissões do economista."

    def handle(self, *args, **options):
        grupo, _ = Group.objects.get_or_create(name="Analista")
        perms = []
        for app, codename in PERMISSOES_ANALISTA:
            p = Permission.objects.filter(content_type__app_label=app, codename=codename).first()
            if p is None:
                self.stdout.write(self.style.WARNING(f"Permissão não encontrada: {app}.{codename}"))
                continue
            perms.append(p)
        grupo.permissions.set(perms)
        self.stdout.write(self.style.SUCCESS(f"Grupo Analista com {len(perms)} permissões."))
