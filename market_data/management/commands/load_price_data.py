import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Baixa preços via yfinance, calcula indicadores técnicos e salva em KpiTime"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tickers",
            nargs="+",
            type=str,
            default=None,
            help="Lista de tickers da B3 (ex: --tickers PETR4 VALE3 ITUB4)",
        )
        parser.add_argument(
            "--todos",
            action="store_true",
            help="Processa todos os ativos com ativo=True no banco.",
        )
        parser.add_argument(
            "--anos",
            type=int,
            default=8,
            help="Quantos anos de histórico baixar (padrão: 8).",
        )
        parser.add_argument(
            "--apenas-info",
            action="store_true",
            help="Só atualiza nome/setor/subsetor do ativo, sem baixar histórico de preços.",
        )

    def handle(self, *args, **options):
        # -------------------------------------------------------------------
        # Imports locais
        # -------------------------------------------------------------------
        try:
            from services.extractors.yfinance_extractor import (
                baixar_historico_ohlcv,
                baixar_ibovespa,
                calcular_todos_indicadores,
                obter_info_ativo,
            )
        except ModuleNotFoundError as e:
            raise CommandError(
                f"Módulo não encontrado: {e}\n"
                "Verifique se services/extractors/yfinance_extractor.py existe."
            )

        from portfolio.models import Ativo
        from market_data.models import KpiTime

        anos         = options["anos"]
        apenas_info  = options.get("apenas_info", False)
        tickers      = options.get("tickers") or []

        # Decide quais tickers processar
        if options.get("todos"):
            tickers = list(
                Ativo.objects.filter(ativo=True).values_list("ticker", flat=True)
            )

        if not tickers:
            raise CommandError(
                "Informe os tickers:\n"
                "  --tickers PETR4 VALE3\n"
                "  ou use --todos para processar todos os ativos cadastrados."
            )

        self.stdout.write(
            f"Ativos a processar: {len(tickers)}"
            f"  |  Histórico: {anos} anos"
            f"  |  Modo: {'apenas info' if apenas_info else 'preços + indicadores'}\n"
        )

        # -------------------------------------------------------------------
        # Baixa o Ibovespa uma única vez (benchmark para o cálculo do beta)
        # -------------------------------------------------------------------
        df_ibov = None
        if not apenas_info:
            self.stdout.write("Baixando Ibovespa como benchmark (^BVSP)...")
            try:
                df_ibov = baixar_ibovespa(anos=anos)
                self.stdout.write(
                    self.style.SUCCESS(f"    Ibovespa: {len(df_ibov):,} pregões\n")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(
                        f"    Aviso: falha ao baixar Ibovespa ({e}). "
                        "O beta não será calculado.\n"
                    )
                )

        # -------------------------------------------------------------------
        # Processa cada ticker
        # -------------------------------------------------------------------
        total_salvos = 0
        erros        = []

        for i, ticker in enumerate(tickers, 1):
            prefixo = f"[{i}/{len(tickers)}] {ticker}"
            self.stdout.write(f"{prefixo} ", ending="")

            # 1. Atualiza/cria o registro do ativo no banco
            try:
                info = obter_info_ativo(ticker)
                ativo, criado = Ativo.objects.update_or_create(
                    ticker=ticker.upper(),
                    defaults={
                        "nome":     info.get("nome", ""),
                        "setor":    info.get("setor", ""),
                        "subsetor": info.get("subsetor", ""),
                        "tipo":     info.get("tipo", "acao"),
                    },
                )
                status_ativo = "criado" if criado else "atualizado"
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"→ erro ao buscar info: {e}"))
                erros.append(ticker)
                continue

            if apenas_info:
                self.stdout.write(self.style.SUCCESS(f"→ info {status_ativo}"))
                continue

            # 2. Baixa histórico OHLCV
            try:
                df_ohlcv = baixar_historico_ohlcv(ticker, anos=anos)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"→ falha ao baixar preços: {e}"))
                erros.append(ticker)
                continue

            if df_ohlcv.empty:
                self.stdout.write(self.style.WARNING("→ sem dados de preço"))
                continue

            # 3. Calcula indicadores técnicos
            try:
                ibov_para_calc = df_ibov if df_ibov is not None else df_ohlcv
                df_kpis = calcular_todos_indicadores(df_ohlcv, ibov_para_calc)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"→ falha no cálculo: {e}"))
                erros.append(ticker)
                continue

            # 4. Monta lista para bulk_create
            registros = [
                KpiTime(
                    ativo            = ativo,
                    data_ref         = row["data_ref"],
                    volume_diario    = row.get("volume_diario"),
                    volume_medio_20d = row.get("volume_medio_20d"),
                    volatilidade_30d = row.get("volatilidade_30d"),
                    beta             = row.get("beta"),
                    retorno_1m       = row.get("retorno_1m"),
                    retorno_3m       = row.get("retorno_3m"),
                    retorno_12m      = row.get("retorno_12m"),
                    rsi_14           = row.get("rsi_14"),
                    media_movel_50   = row.get("media_movel_50"),
                    media_movel_200  = row.get("media_movel_200"),
                )
                for _, row in df_kpis.iterrows()
            ]

            # 5. Salva no banco (upsert)
            try:
                with transaction.atomic():
                    KpiTime.objects.bulk_create(
                        registros,
                        update_conflicts = True,
                        unique_fields    = ["ativo", "data_ref"],
                        update_fields    = [
                            "volume_diario", "volume_medio_20d", "volatilidade_30d",
                            "beta", "retorno_1m", "retorno_3m", "retorno_12m",
                            "rsi_14", "media_movel_50", "media_movel_200",
                        ],
                    )
                total_salvos += len(registros)
                self.stdout.write(
                    self.style.SUCCESS(f"→ {len(registros):,} registros salvos")
                )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"→ falha ao salvar: {e}"))
                erros.append(ticker)

        # -------------------------------------------------------------------
        # Resumo final
        # -------------------------------------------------------------------
        self.stdout.write(self.style.SUCCESS(
            f"\nConcluído!\n"
            f"  Total de registros KpiTime salvos: {total_salvos:,}\n"
            f"  Tickers com erro: {len(erros)}"
            + (f" → {erros}" if erros else "")
        ))