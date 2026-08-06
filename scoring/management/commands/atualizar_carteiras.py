# scoring/management/commands/atualizar_carteiras.py
from django.core.management.base import BaseCommand
from scoring.services.motor_scoring_v2 import calcular_scores_v2, PERFIS
from scoring.models import CarteiraRecomendada

class Command(BaseCommand):
    help = "Recalcula as carteiras recomendadas para todos os perfis de risco."

    def handle(self, *args, **options):
        for perfil in PERFIS.keys():
            self.stdout.write(f"Calculando carteira: {perfil}...")
            df = calcular_scores_v2(perfil)

            if df.empty:
                self.stdout.write(self.style.WARNING(f"  Nenhum ativo elegível para {perfil}"))
                continue

            composicao = df[['ticker', 'setor', 'score_final', 'pct_markowitz']].to_dict('records')

            # desativa a carteira anterior e cria a nova
            CarteiraRecomendada.objects.filter(perfil=perfil, ativo=True).update(ativo=False)
            CarteiraRecomendada.objects.create(perfil=perfil, composicao=composicao, ativo=True)

            self.stdout.write(self.style.SUCCESS(f"  {len(composicao)} ativos salvos"))