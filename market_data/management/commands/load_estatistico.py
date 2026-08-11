
import logging
from datetime import date
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Calcula KPIs estatísticos via yfinance e salva em KpiEstatistico"

    def add_arguments(self, parser):
        parser.add_argument("--tickers", nargs="+", type=str, default=None)
        parser.add_argument("--todos",   action="store_true")
        parser.add_argument("--janela",  type=int, default=252,
                            help="Pregões para calcular (252=1ano, 126=6m, 63=3m)")
        parser.add_argument("--anos",    type=int, default=3,
                            help="Anos de histórico para baixar do yfinance")

    def handle(self, *args, **options):
        try:
            from services.calculators.estatistico import (
                baixar_precos, baixar_ibovespa,
                calcular_retornos_log, calcular_estatisticas_ativo,
            )
        except ModuleNotFoundError as e:
            raise CommandError(f"Módulo não encontrado: {e}")

        from portfolio.models import Ativo
        from market_data.models import KpiEstatistico

        tickers = options.get("tickers") or []
        if options.get("todos"):
            tickers = list(Ativo.objects.filter(ativo=True)
                           .exclude(ticker__contains=" ")
                           .values_list("ticker", flat=True))

        if not tickers:
            raise CommandError("Use --tickers PETR4 VALE3 ou --todos")

        janela = options["janela"]
        anos   = options["anos"]
        hoje   = date.today()

        self.stdout.write(
            f"Calculando KPIs estatísticos | {len(tickers)} ativos "
            f"| janela={janela}d | histórico={anos}a\n"
        )

        # Baixa Ibovespa UMA vez como benchmark
        self.stdout.write("Baixando Ibovespa...")
        try:
            ibov    = baixar_ibovespa(anos=anos)
            ret_ibov = calcular_retornos_log(ibov.to_frame()).squeeze()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Ibovespa falhou ({e}) — beta não será calculado"))
            ret_ibov = None

        # Baixa preços em lote (mais eficiente)
        self.stdout.write("Baixando preços em lote...")
        try:
            precos   = baixar_precos(tickers, anos=anos)
            retornos = calcular_retornos_log(precos)
        except Exception as e:
            raise CommandError(f"Falha ao baixar preços: {e}")

        tickers_disponiveis = precos.columns.tolist()
        self.stdout.write(
            self.style.SUCCESS(f"    {len(tickers_disponiveis)} tickers com dados\n")
        )

        # Mapa ticker → objeto Ativo
        ativos_map = {
            a.ticker: a
            for a in Ativo.objects.filter(ticker__in=tickers_disponiveis)
        }

        salvos = 0
        erros  = []

        for ticker in tickers_disponiveis:
            ativo = ativos_map.get(ticker)
            if not ativo:
                continue

            try:
                preco_serie   = precos[ticker].iloc[-janela:]
                retorno_serie = retornos[ticker].iloc[-janela:]

                if len(retorno_serie.dropna()) < 30:
                    self.stdout.write(f"  {ticker}: poucos dados, pulando")
                    continue

                stats = calcular_estatisticas_ativo(
                    preco_serie, retorno_serie, ret_ibov
                )

                with transaction.atomic():
                    KpiEstatistico.objects.update_or_create(
                        ativo=ativo,
                        data_calculo=hoje,
                        janela_dias=janela,
                        defaults=stats,
                    )
                salvos += 1

                # Progresso a cada 10
                if salvos % 10 == 0:
                    self.stdout.write(f"  {salvos}/{len(tickers_disponiveis)}...", ending="\r")

            except Exception as e:
                logger.warning(f"{ticker}: {e}")
                erros.append(ticker)

        self.stdout.write(self.style.SUCCESS(
            f"\nConcluído!\n"
            f"  Salvos  : {salvos}\n"
            f"  Erros   : {len(erros)}"
            + (f" → {erros[:5]}" if erros else "")
        ))


# =============================================================================
# INTEGRAÇÃO COM O MOTOR DE SCORING
# =============================================================================
"""
Adicione este método ao motor_scoring.py para incluir o KPI Estatístico
no cálculo do score_final.

Como o score_estatistico é calculado (lógica de pontuação):

  Volatilidade baixa     → score alto (ativo mais estável = melhor para todos)
  Beta próximo de 1      → score neutro; muito alto = penaliza conservador
  Distribuição normal    → bônus (modelos funcionam melhor com normalidade)
  Skewness positiva      → bônus (caudas favoráveis para ganho)
  CV baixo               → score alto (risco relativo menor)
"""

PESOS_PERFIL_COM_ESTATISTICO = {
    "conservador": {
        "micro":       0.35,
        "macro":       0.25,
        "time":        0.10,
        "sentimento":  0.10,
        "estatistico": 0.20,   # ← peso do novo KPI
    },
    "intermediario": {
        "micro":       0.30,
        "macro":       0.20,
        "time":        0.20,
        "sentimento":  0.15,
        "estatistico": 0.15,
    },
    "arrojado": {
        "micro":       0.22,
        "macro":       0.13,
        "time":        0.35,
        "sentimento":  0.18,
        "estatistico": 0.12,
    },
}


def calcular_score_estatistico_serie(df_estat: "pd.DataFrame") -> "pd.Series":
    """
    Converte os KPIs estatísticos em score 0-100.

    Critérios de pontuação:
      - Volatilidade anual: quanto MENOR, melhor (invertemos o percentil)
      - CV: quanto MENOR, melhor (invertemos)
      - Beta: 1.0 = neutro; muito afastado de 1 = penaliza
      - Skewness positiva: bônus (até +10 pts)
      - JB normal: bônus +5 pts
      - Retorno médio positivo: bônus

    Args:
        df_estat: DataFrame com colunas = campos de KpiEstatistico,
                  indexado por ticker

    Returns:
        Series com score_estatistico (0-100) por ticker
    """
    import numpy as np
    import pandas as pd

    def norm_percentil(series, inverter=False):
        ranked = series.rank(pct=True, na_option="keep") * 100
        return (100 - ranked) if inverter else ranked

    scores = pd.DataFrame(index=df_estat.index)

    # Volatilidade: menor vol = score maior
    if "volatilidade_anual" in df_estat.columns:
        scores["s_vol"] = norm_percentil(df_estat["volatilidade_anual"], inverter=True)

    # CV: menor = melhor
    if "cv" in df_estat.columns:
        scores["s_cv"] = norm_percentil(df_estat["cv"].abs(), inverter=True)

    # Beta: penaliza desvio de 1 (|beta - 1| pequeno = melhor)
    if "beta" in df_estat.columns:
        distancia_beta = (df_estat["beta"] - 1).abs()
        scores["s_beta"] = norm_percentil(distancia_beta, inverter=True)

    # Retorno médio: maior = melhor
    if "media_retorno" in df_estat.columns:
        scores["s_retorno"] = norm_percentil(df_estat["media_retorno"])

    # Score base = média dos componentes
    score_base = scores.mean(axis=1)

    # Bônus por skewness positiva (cauda direita favorável)
    if "skewness" in df_estat.columns:
        bonus_skew = df_estat["skewness"].clip(-1, 1) * 5  # ±5 pontos
        score_base = score_base + bonus_skew

    # Bônus por distribuição normal (JB não rejeita)
    if "retorno_normal" in df_estat.columns:
        bonus_normal = df_estat["retorno_normal"].map({True: 5, False: 0}).fillna(0)
        score_base = score_base + bonus_normal

    return score_base.clip(0, 100).round(2)