import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Coleta manchetes, analisa sentimento com NLP e salva em SentimentoMercado"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tickers",
            nargs="+",
            type=str,
            default=None,
            help="Tickers para monitorar. Padrão: todos os ativos do banco.",
        )
        parser.add_argument(
            "--sem-modelo",
            action="store_true",
            help="Usa dicionário de palavras no lugar do FinBERT (mais rápido, menos preciso).",
        )
        parser.add_argument(
            "--apenas-exibir",
            action="store_true",
            help="Mostra os resultados sem salvar no banco.",
        )

    def handle(self, *args, **options):
        # -------------------------------------------------------------------
        # Imports locais
        # -------------------------------------------------------------------
        try:
            from services.extractors.sentiment_extractor import executar_pipeline_sentimento
        except ModuleNotFoundError as e:
            raise CommandError(
                f"Módulo não encontrado: {e}\n"
                "Verifique se services/extractors/sentiment_extractor.py existe."
            )

        from portfolio.models import Ativo

        tickers     = options.get("tickers") or []
        usar_modelo = not options.get("sem_modelo", False)
        salvar      = not options.get("apenas_exibir", False)

        # Se não informou tickers, usa todos os ativos ativos no banco
        if not tickers:
            tickers = list(
                Ativo.objects.filter(ativo=True).values_list("ticker", flat=True)
            )

        if not tickers:
            raise CommandError(
                "Nenhum ativo encontrado.\n"
                "Rode primeiro:\n"
                "  python manage.py load_price_data --todos --apenas-info"
            )

        self.stdout.write(
            f"\nIniciando pipeline de sentimento\n"
            f"  Ativos  : {len(tickers)} "
            f"({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})\n"
            f"  Modelo  : {'FinBERT-PT-BR' if usar_modelo else 'dicionário de palavras'}\n"
            f"  Salvar  : {'sim' if salvar else 'não (--apenas-exibir)'}\n"
        )

        # -------------------------------------------------------------------
        # Executa pipeline: coleta → NLP → (persistência)
        # -------------------------------------------------------------------
        try:
            resultados = executar_pipeline_sentimento(
                tickers         = tickers,
                salvar_no_banco = salvar,
                usar_modelo_nlp = usar_modelo,
            )
        except Exception as e:
            raise CommandError(f"Falha no pipeline de sentimento: {e}")

        if not resultados:
            self.stdout.write(self.style.WARNING(
                "\nNenhuma manchete coletada. Possíveis causas:\n"
                "  - Sites bloquearam o scraping (tente novamente mais tarde)\n"
                "  - Credenciais do Reddit não configuradas\n"
                "  - Sem conexão com a internet"
            ))
            return

        # -------------------------------------------------------------------
        # Exibe tabela de resultados
        # -------------------------------------------------------------------
        self.stdout.write(f"\nResultados ({len(resultados)} registros):\n")
        self.stdout.write(f"  {'Ticker':<10} {'Fonte':<14} {'Score':>7}  {'Menções':>8}")
        self.stdout.write(f"  {'-'*10} {'-'*14} {'-'*7}  {'-'*8}")

        for r in sorted(resultados, key=lambda x: x["score_sentimento"]):
            ticker_str = r.get("ticker") or "GERAL"
            score      = r["score_sentimento"]

            if score > 0.15:
                indicador = self.style.SUCCESS("▲ +")
            elif score < -0.15:
                indicador = self.style.ERROR("▼  ")
            else:
                indicador = "→  "

            self.stdout.write(
                f"  {ticker_str:<10} {r['fonte']:<14} "
                f"{indicador}{abs(score):.3f}  {r['volume_mencoes']:>8}"
            )

        # Sentimento médio geral
        scores = [r["score_sentimento"] for r in resultados]
        media  = sum(scores) / len(scores)
        humor  = "positivo" if media > 0.05 else ("negativo" if media < -0.05 else "neutro")

        self.stdout.write(
            self.style.SUCCESS(f"\nSentimento médio: {media:+.3f} ({humor})\n")
        )

        if salvar:
            self.stdout.write(
                self.style.SUCCESS(f"✓ {len(resultados)} registros salvos em SentimentoMercado")
            )
        else:
            self.stdout.write(
                self.style.WARNING("Modo --apenas-exibir ativo: nada foi salvo no banco.")
            )