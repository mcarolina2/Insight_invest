"""
Motor de scoring do Insight Invest.

Calcula um score de 0 a 100 para cada ativo em cada camada:
  - score_micro      : análise fundamentalista (CVM)
  - score_macro      : ambiente econômico (BCB)
  - score_time       : timing técnico (yfinance) — usa fallback se não houver dados
  - score_sentimento : NLP de notícias — usa fallback se não houver dados

O score_final é a média ponderada das 4 camadas, com pesos
diferentes por perfil de risco do usuário.

Uso:
  from services.scoring.motor_scoring import calcular_scores_todos_ativos
  calcular_scores_todos_ativos()
"""

import logging
from datetime import date, timedelta
from typing import Optional
from .motor_scoring_v2 import *

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)





def calcular_scores_todos_ativos(
    data_ref=None,
    perfil="intermediario"
):
    return calcular_scores_v2(perfil)


# ---------------------------------------------------------------------------
# Pesos por perfil de risco
# ---------------------------------------------------------------------------

PESOS_PERFIL = {
    "conservador": {
        "micro":      0.40,
        "macro":      0.30,
        "time":       0.15,
        "sentimento": 0.15,
    },
    "intermediario": {
        "micro":      0.35,
        "macro":      0.25,
        "time":       0.25,
        "sentimento": 0.15,
    },
    "arrojado": {
        "micro":      0.25,
        "macro":      0.15,
        "time":       0.40,
        "sentimento": 0.20,
    },
}

# Perfil padrão usado para calcular o score_final geral (sem perfil específico)
PESOS_PADRAO = PESOS_PERFIL["intermediario"]


# ---------------------------------------------------------------------------
# Normalização: converte valores brutos em score 0-100
# ---------------------------------------------------------------------------

def normalizar_percentil(series: pd.Series) -> pd.Series:
    """
    Normaliza uma série de valores para 0-100 usando rank percentil.
    Robusto a outliers — não sofre com empresas com valores extremos.
    """
    if series.isna().all():
        return pd.Series([50.0] * len(series), index=series.index)

    ranks = series.rank(pct=True, na_option="keep")
    return (ranks * 100).fillna(50)  # 50 = neutro para quem não tem dado


def inverter(series: pd.Series) -> pd.Series:
    """Inverte uma série normalizada. Usado para indicadores onde MENOR é melhor (ex: dívida)."""
    return 100 - series


# ---------------------------------------------------------------------------
# SCORE MICRO — análise fundamentalista
# ---------------------------------------------------------------------------

def calcular_score_micro(df_micro: pd.DataFrame) -> pd.Series:
    """
    Calcula o score_micro para cada ativo a partir do DataFrame de KpiMicro.

    Lógica de pontuação:
      Liquidez      (25%): corrente, seca, imediata — mais é melhor
      Rentabilidade (35%): ROE, ROA, margens — mais é melhor
      Endividamento (20%): dívida/EBITDA — MENOS é melhor
      Crescimento   (20%): crescimento receita e lucro — mais é melhor

    Args:
        df_micro: DataFrame com uma linha por ativo e colunas = campos KpiMicro

    Returns:
        Series com score_micro (0-100) indexada pelo ticker
    """
    scores = pd.DataFrame(index=df_micro.index)

    # --- LIQUIDEZ (peso 25%) ---
    liq_cols = ["liquidez_corrente", "liquidez_seca", "liquidez_imediata", "liquidez_geral"]
    for col in liq_cols:
        if col in df_micro.columns:
            scores[f"s_{col}"] = normalizar_percentil(df_micro[col])

    liq_cols_score = [c for c in scores.columns if c.startswith("s_liquidez")]
    score_liquidez = scores[liq_cols_score].mean(axis=1) if liq_cols_score else pd.Series(50, index=df_micro.index)

    # --- RENTABILIDADE (peso 35%) ---
    rent_map = {
        "roe":            1.0,   # positivo = melhor
        "roa":            1.0,
        "margem_liquida": 1.0,
        "margem_ebitda":  1.0,
        "giro_ativo":     1.0,
    }
    rent_scores = []
    for col in rent_map:
        if col in df_micro.columns:
            rent_scores.append(normalizar_percentil(df_micro[col]))

    score_rentabilidade = pd.concat(rent_scores, axis=1).mean(axis=1) if rent_scores else pd.Series(50, index=df_micro.index)

    # --- ENDIVIDAMENTO (peso 20%) ---
    score_divida = pd.Series(50, index=df_micro.index)
    if "divida_ebitda" in df_micro.columns:
        # Dívida/EBITDA: menor é melhor → inverte após normalizar
        score_divida = inverter(normalizar_percentil(df_micro["divida_ebitda"]))

    # --- CRESCIMENTO (peso 20%) ---
    cresc_scores = []
    for col in ["crescimento_receita", "crescimento_lucro"]:
        if col in df_micro.columns:
            cresc_scores.append(normalizar_percentil(df_micro[col]))

    score_crescimento = pd.concat(cresc_scores, axis=1).mean(axis=1) if cresc_scores else pd.Series(50, index=df_micro.index)

    # --- SCORE MICRO FINAL ---
    score_micro = (
        score_liquidez      * 0.25 +
        score_rentabilidade * 0.35 +
        score_divida        * 0.20 +
        score_crescimento   * 0.20
    ).round(2)

    return score_micro


# ---------------------------------------------------------------------------
# SCORE MACRO — ambiente econômico (igual para todos os ativos na mesma data)
# ---------------------------------------------------------------------------

def calcular_score_macro(kpi_macro) -> float:
    """
    Calcula um score_macro único baseado no cenário econômico atual.
    Retorna um número 0-100 — o mesmo para todos os ativos na mesma data.

    Lógica:
      Selic alta  → penaliza renda variável (Score menor)
      IPCA alto   → penaliza (corroe margens)
      PIB positivo→ beneficia
      Câmbio alto → penaliza empresas importadoras, beneficia exportadoras
                    (usamos valor neutro por simplificação)

    Args:
        kpi_macro: objeto KpiMacro (o registro mais recente)

    Returns:
        float: score_macro entre 0 e 100
    """
    if not kpi_macro:
        return 50.0  # neutro se não houver dados

    pontuacao = 50.0  # parte do neutro

    # Selic: referência histórica ~4% (neutro). Cada 1% acima penaliza 2 pts
    selic = float(kpi_macro.selic or 10)
    pontuacao -= max(0, (selic - 4) * 2)

    # IPCA: meta de 3%. Cada 1% acima da meta penaliza 3 pts
    ipca = float(kpi_macro.ipca_mensal or 0.3) * 12  # anualiza
    pontuacao -= max(0, (ipca - 3) * 3)

    # PIB: crescimento positivo beneficia
    pib = float(kpi_macro.pib_trimestral or 0)
    if pib > 0:
        pontuacao += pib * 5
    else:
        pontuacao += pib * 3  # recessão penaliza menos que crescimento ajuda

    # Desemprego: alto penaliza consumo
    desemprego = float(kpi_macro.desemprego or 10)
    pontuacao -= max(0, (desemprego - 8) * 1.5)

    return round(max(0, min(100, pontuacao)), 2)


# ---------------------------------------------------------------------------
# SCORE TIME — análise técnica / timing
# ---------------------------------------------------------------------------

def calcular_score_time(df_time: pd.DataFrame) -> pd.Series:
    """
    Calcula o score_time para cada ativo a partir do DataFrame de KpiTime.

    Lógica:
      RSI (30%): entre 30 e 70 é saudável; abaixo de 30 = sobrevenda (oportunidade),
                 acima de 70 = sobrecompra (risco)
      Momentum  (40%): retorno 1m, 3m, 12m — mais é melhor
      Volatilidade (30%): menor vol = melhor para conservadores

    Returns:
        Series com score_time (0-100) ou 50 se sem dados
    """
    if df_time.empty:
        return pd.Series(dtype=float)

    scores = pd.DataFrame(index=df_time.index)

    # RSI: converte para escala de qualidade
    # RSI entre 40-60 = ótimo (tendência neutra/saudável)
    # RSI < 30 = possível reversão (score médio-alto para compra)
    # RSI > 70 = sobrecomprado (score baixo)
    if "rsi_14" in df_time.columns:
        rsi = df_time["rsi_14"].fillna(50)
        scores["s_rsi"] = rsi.apply(lambda r: (
            80 if 35 <= r <= 65   else   # zona saudável
            65 if r < 35          else   # sobrevenda (oportunidade)
            30                           # sobrecomprado (risco)
        ))

    # Momentum: retornos positivos = melhor
    for col in ["retorno_1m", "retorno_3m", "retorno_12m"]:
        if col in df_time.columns:
            scores[f"s_{col}"] = normalizar_percentil(df_time[col])

    # Volatilidade: menor é melhor (mais estável)
    if "volatilidade_30d" in df_time.columns:
        scores["s_volatilidade"] = inverter(normalizar_percentil(df_time["volatilidade_30d"]))

    if scores.empty:
        return pd.Series(50.0, index=df_time.index)

    return scores.mean(axis=1).round(2)


# ---------------------------------------------------------------------------
# SCORE SENTIMENTO — NLP de notícias
# ---------------------------------------------------------------------------

def calcular_score_sentimento(df_sent: pd.DataFrame) -> pd.Series:
    """
    Converte o score_sentimento (-1 a +1) para escala 0-100.

    Args:
        df_sent: DataFrame com colunas [ticker, score_sentimento, volume_mencoes]

    Returns:
        Series com score_sentimento (0-100) por ticker
    """
    if df_sent.empty:
        return pd.Series(dtype=float)

    # Converte -1..+1 para 0..100
    df_sent = df_sent.copy()
    df_sent["score_0_100"] = ((df_sent["score_sentimento"].astype(float) + 1) / 2 * 100).round(2)

    # Agrega por ticker (média ponderada pelo volume de menções)
    resultado = (
        df_sent.groupby("ticker")
        .apply(lambda g: np.average(g["score_0_100"], weights=g["volume_mencoes"].clip(1)))
    )

    return resultado.round(2)


# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------

def calcular_scores_todos_ativos(data_ref: date = None, perfil: str = "intermediario") -> pd.DataFrame:
    """
    Pipeline completo: busca dados de todas as camadas e calcula scores.

    Args:
        data_ref: data de referência (padrão: hoje)
        perfil:   'conservador' | 'intermediario' | 'arrojado'

    Returns:
        DataFrame com colunas:
          ticker, score_micro, score_macro, score_time, score_sentimento, score_final
    """
    from market_data.models import KpiMicro, KpiMacro, KpiTime, SentimentoMercado
    from portfolio.models import Ativo

    if data_ref is None:
        data_ref = date.today()

    pesos = PESOS_PERFIL.get(perfil, PESOS_PADRAO)

    self_log = lambda msg: logger.info(msg)

    # ------------------------------------------------------------------
    # 1. Carrega KpiMicro (último registro por ativo)
    # ------------------------------------------------------------------
    self_log("Carregando KpiMicro...")
    micro_qs = (
        KpiMicro.objects
        .select_related("ativo")
        .order_by("ativo__ticker", "-data_ref")
        .distinct("ativo__ticker")  # PostgreSQL — pega o mais recente por ativo
    )

    micro_rows = []
    for kpi in micro_qs:
        row = {
            "ticker":             kpi.ativo.ticker,
            "ativo_id":           kpi.ativo.id,
            "setor":              kpi.ativo.setor or "",
            "liquidez_corrente":  float(kpi.liquidez_corrente or 0),
            "liquidez_seca":      float(kpi.liquidez_seca or 0),
            "liquidez_imediata":  float(kpi.liquidez_imediata or 0),
            "liquidez_geral":     float(kpi.liquidez_geral or 0),
            "roe":                float(kpi.roe or 0),
            "roa":                float(kpi.roa or 0),
            "giro_ativo":         float(kpi.giro_ativo or 0),
            "margem_liquida":     float(kpi.margem_liquida or 0),
            "margem_ebitda":      float(kpi.margem_ebitda or 0),
            "divida_ebitda":      float(kpi.divida_ebitda or 0),
            "crescimento_receita":float(kpi.crescimento_receita or 0),
            "crescimento_lucro":  float(kpi.crescimento_lucro or 0),
        }
        micro_rows.append(row)

    if not micro_rows:
        logger.warning("Nenhum KpiMicro encontrado.")
        return pd.DataFrame()

    df_micro = pd.DataFrame(micro_rows).set_index("ticker")
    self_log(f"  {len(df_micro)} ativos com KpiMicro")

    # ------------------------------------------------------------------
    # 2. Calcula score_micro por ativo (normalizado dentro do setor)
    # ------------------------------------------------------------------
    score_micro_total = pd.Series(dtype=float)

    for setor, grupo in df_micro.groupby("setor"):
        scores_setor = calcular_score_micro(grupo)
        score_micro_total = pd.concat([score_micro_total, scores_setor])

    # ------------------------------------------------------------------
    # 3. Score macro (único para todos, baseado no cenário atual)
    # ------------------------------------------------------------------
    self_log("Carregando KpiMacro...")
    kpi_macro_recente = KpiMacro.objects.order_by("-data_ref").first()
    score_macro_valor  = calcular_score_macro(kpi_macro_recente)
    self_log(f"  Score macro: {score_macro_valor}")

    # ------------------------------------------------------------------
    # 4. Score time (por ativo)
    # ------------------------------------------------------------------
    self_log("Carregando KpiTime...")
    time_qs = (
        KpiTime.objects
        .select_related("ativo")
        .filter(data_ref__gte=data_ref - timedelta(days=5))
        .order_by("ativo__ticker", "-data_ref")
        .distinct("ativo__ticker")
    )

    time_rows = [
        {
            "ticker":          kpi.ativo.ticker,
            "rsi_14":          float(kpi.rsi_14 or 50),
            "retorno_1m":      float(kpi.retorno_1m or 0),
            "retorno_3m":      float(kpi.retorno_3m or 0),
            "retorno_12m":     float(kpi.retorno_12m or 0),
            "volatilidade_30d":float(kpi.volatilidade_30d or 0),
            "beta":            float(kpi.beta or 1),
        }
        for kpi in time_qs
    ]

    df_time = pd.DataFrame(time_rows).set_index("ticker") if time_rows else pd.DataFrame()
    score_time_series = calcular_score_time(df_time) if not df_time.empty else pd.Series(dtype=float)
    self_log(f"  {len(score_time_series)} ativos com KpiTime")

    # ------------------------------------------------------------------
    # 5. Score sentimento (por ativo)
    # ------------------------------------------------------------------
    self_log("Carregando SentimentoMercado...")
    sent_qs = (
        SentimentoMercado.objects
        .select_related("ativo")
        .filter(
            data_ref__gte=data_ref - timedelta(days=30),
            ativo__isnull=False,
        )
    )

    sent_rows = [
        {
            "ticker":          s.ativo.ticker,
            "score_sentimento":float(s.score_sentimento),
            "volume_mencoes":  int(s.volume_mencoes),
        }
        for s in sent_qs
    ]

    df_sent = pd.DataFrame(sent_rows) if sent_rows else pd.DataFrame()
    score_sent_series = calcular_score_sentimento(df_sent) if not df_sent.empty else pd.Series(dtype=float)
    self_log(f"  {len(score_sent_series)} ativos com Sentimento")

    # ------------------------------------------------------------------
    # 6. Monta DataFrame final e calcula score_final ponderado
    # ------------------------------------------------------------------
    resultado = pd.DataFrame(index=df_micro.index)
    resultado["ativo_id"]         = df_micro["ativo_id"]
    resultado["score_micro"]      = score_micro_total.reindex(resultado.index).fillna(50)
    resultado["score_macro"]      = score_macro_valor  # mesmo para todos
    resultado["score_time"]       = score_time_series.reindex(resultado.index).fillna(50)
    resultado["score_sentimento"] = score_sent_series.reindex(resultado.index).fillna(50)

    resultado["score_final"] = (
        resultado["score_micro"]      * pesos["micro"]      +
        resultado["score_macro"]      * pesos["macro"]      +
        resultado["score_time"]       * pesos["time"]       +
        resultado["score_sentimento"] * pesos["sentimento"]
    ).round(2)

    resultado["kpi_macro_id"] = kpi_macro_recente.id if kpi_macro_recente else None
    resultado["data_calculo"] = data_ref

    self_log(f"Score calculado para {len(resultado)} ativos.")
    self_log(f"  Média geral: {resultado['score_final'].mean():.1f}")
    self_log(f"  Top 5: {resultado.nlargest(5, 'score_final').index.tolist()}")

    return resultado.reset_index()