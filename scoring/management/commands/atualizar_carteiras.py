"""
Recalcula, em lote, a carteira recomendada de cada perfil (fluxo da home pública).

Uso:
    python manage.py atualizar_carteiras

Também disponível no painel administrativo (Configurações de perfil > ação
"Recalcular carteiras"), útil no Render gratuito, que não tem terminal.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Recalcula as carteiras recomendadas para todos os perfis de risco."

    def handle(self, *args, **options):
        from scoring.services.carteira import recalcular_carteiras

        for perfil, texto in recalcular_carteiras().items():
            estilo = self.style.WARNING if texto.startswith("sem carteira") else self.style.SUCCESS
            self.stdout.write(estilo(f"{perfil}: {texto}"))
