"""
Calcula o escore de todos os ativos e salva em ScoreAtivo (usado nos rankings da home).

Uso:
    python manage.py calcular_scores                  # perfil intermediário (referência)
    python manage.py calcular_scores --perfil arrojado
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Calcula escores por camada e escore final e salva em ScoreAtivo"

    def add_arguments(self, parser):
        parser.add_argument("--perfil", default="intermediario",
                            choices=["conservador", "intermediario", "arrojado"])

    def handle(self, *args, **options):
        from scoring.models import ScoreAtivo
        from scoring.services.motor_scoring_v2 import calcular_scores_v2

        perfil = options["perfil"]
        self.stdout.write(f"Calculando escores | perfil: {perfil}")
        df = calcular_scores_v2(perfil=perfil)
        if df.empty:
            raise CommandError("Nenhum ativo com dados suficientes. "
                               "Rode antes: python manage.py load_cvm_data")

        hoje = date.today()
        with transaction.atomic():
            for row in df.itertuples(index=False):
                ScoreAtivo.objects.update_or_create(
                    ativo_id=int(row.ativo_id), data_calculo=hoje,
                    defaults={
                        "score_micro": round(float(row.score_micro), 2),
                        "score_macro": round(float(row.score_macro), 2),
                        "score_estat": round(float(row.score_estat), 2),
                        "score_final": round(float(row.score_final), 2),
                        "kpi_macro_ref_id": row.kpi_macro_id,
                    },
                )
        self.stdout.write(self.style.SUCCESS(
            f"{len(df)} escores salvos | média {df['score_final'].mean():.1f} | "
            f"top 5: {df.head(5)['ticker'].tolist()}"))
