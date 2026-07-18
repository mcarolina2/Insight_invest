"""
Salve em: scoring/management/commands/calcular_scores.py

Execucao:
  python manage.py calcular_scores
  python manage.py calcular_scores --perfil conservador
  python manage.py calcular_scores --usuario carolina
  python manage.py calcular_scores --apenas-scores
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Calcula scores por camada e score_final para todos os ativos"

    def add_arguments(self, parser):
        parser.add_argument(
            "--perfil",
            type=str,
            default="intermediario",
            choices=["conservador", "intermediario", "arrojado"],
            help="Perfil de risco para ponderar os scores",
        )
        parser.add_argument(
            "--usuario",
            type=str,
            default=None,
            help="Username para gerar recomendacao personalizada",
        )
        parser.add_argument(
            "--apenas-scores",
            action="store_true",
            help="So calcula e salva ScoreAtivo, sem gerar recomendacao",
        )

    def handle(self, *args, **options):
        # Importa de scoring.services (services esta dentro do app scoring)
        try:
            from scoring.services.motor_scoring_v2 import calcular_scores_v2 as calcular_scores_todos_ativos
            #from scoring.services.motor_scoring import calcular_scores_todos_ativos
            from scoring.services.recomendacao import gerar_recomendacao
        except ModuleNotFoundError as e:
            raise CommandError(
                f"Modulo nao encontrado: {e}\n"
                "Verifique se scoring/services/motor_scoring.py existe\n"
                "e se ha __init__.py em scoring/services/"
            )

        from scoring.models import ScoreAtivo
        from market_data.models import KpiMacro

        perfil        = options["perfil"]
        username      = options.get("usuario")
        apenas_scores = options.get("apenas_scores", False)

        self.stdout.write(f"Calculando scores | perfil: {perfil}\n")

        # --- Passo 1: Calcula todos os scores ---
        self.stdout.write("[1/3] Calculando scores por camada...")
        df_scores = calcular_scores_todos_ativos(
            data_ref=date.today(),
            perfil=perfil,
        )

        if df_scores.empty:
            raise CommandError(
                "Nenhum ativo com dados suficientes.\n"
                "Rode antes: python manage.py load_cvm_data"
            )

        top5 = df_scores.nlargest(5, "score_final")["ticker"].tolist()
        self.stdout.write(self.style.SUCCESS(
            f"    {len(df_scores)} ativos calculados\n"
            f"    Score medio: {df_scores['score_final'].mean():.1f}\n"
            f"    Top 5: {top5}\n"
        ))

        # --- Passo 2: Salva ScoreAtivo no banco ---
        self.stdout.write("[2/3] Salvando ScoreAtivo...")
        kpi_macro = KpiMacro.objects.order_by("-data_ref").first()
        salvos = 0

        with transaction.atomic():
            for _, row in df_scores.iterrows():
                ScoreAtivo.objects.update_or_create(
                    ativo_id     = int(row["ativo_id"]),
                    data_calculo = date.today(),
                    defaults={
                        "score_micro":      row.get("score_micro"),
                        "score_macro":      row.get("score_macro"),
                        "score_time":       row.get("score_time"),
                        "score_sentimento": row.get("score_sentimento"),
                        "score_final":      row["score_final"],
                        "kpi_macro_ref":    kpi_macro,
                    },
                )
                salvos += 1

        self.stdout.write(self.style.SUCCESS(f"    {salvos} ScoreAtivo salvos\n"))

        if apenas_scores:
            self.stdout.write(self.style.SUCCESS("Concluido!"))
            return

        # --- Passo 3: Gera recomendacao para usuario ---
        if username:
            self.stdout.write(f"[3/3] Gerando recomendacao para '{username}'...")
            try:
                from users.models import User
                user = User.objects.get(username=username)
            except Exception:
                raise CommandError(f"Usuario '{username}' nao encontrado.")

            perfil_usuario = user.perfil_risco.tipo if user.perfil_risco else perfil
            rec = gerar_recomendacao(user, df_scores, perfil_usuario)

            if rec:
                self.stdout.write(self.style.SUCCESS(
                    f"    Recomendacao #{rec.id} gerada com {rec.itens.count()} ativos!\n"
                    f"    Admin: http://127.0.0.1:8000/admin/scoring/recomendacao/{rec.id}/"
                ))
            else:
                self.stdout.write(self.style.WARNING("    Falha ao gerar recomendacao."))
        else:
            self.stdout.write(
                "[3/3] Para gerar recomendacao:\n"
                "  python manage.py calcular_scores --usuario seu_username\n"
            )

        self.stdout.write(self.style.SUCCESS(
            "\nVer resultados:\n"
            "  http://127.0.0.1:8000/admin/scoring/scoreativo/\n"
        ))
