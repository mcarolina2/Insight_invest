"""
Extrator de preços, volume e KPIs de timing via yfinance.

Responsável por:
  - Baixar histórico OHLCV (Open/High/Low/Close/Volume) para um ticker
  - Calcular indicadores técnicos: volatilidade, beta, RSI, médias móveis
  - Retornar um DataFrame pronto para ser salvo em KpiTime

Dependência: pip install yfinance pandas numpy
"""

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Download de dados históricos
# ---------------------------------------------------------------------------

def baixar_historico_ohlcv(
    ticker: str,
    anos: int = 8,
    sufixo_b3: str = ".SA",
) -> pd.DataFrame:
    """
    Baixa o histórico diário de preços e volume de um ativo da B3.

    Args:
        ticker:    Código do ativo (ex: 'PETR4', 'VALE3', 'ITUB4')
        anos:      Quantos anos de histórico baixar (padrão: 8)
        sufixo_b3: Sufixo para tickers brasileiros no Yahoo Finance (.SA)

    Returns:
        DataFrame com colunas: date, open, high, low, close, volume, adj_close
    """
    ticker_yf = ticker.upper() + sufixo_b3
    data_inicio = (date.today() - timedelta(days=anos * 365)).isoformat()

    logger.info(f"Baixando histórico: {ticker_yf} desde {data_inicio}")

    yf_ticker = yf.Ticker(ticker_yf)
    df = yf_ticker.history(start=data_inicio, auto_adjust=True)

    if df.empty:
        logger.warning(f"Sem dados para {ticker_yf}")
        return pd.DataFrame()

    df = df.reset_index()
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    df = df.rename(columns={"date": "data"})

    # Remove timezone do índice de data (problema comum no yfinance)
    df["data"] = pd.to_datetime(df["data"]).dt.date

    return df[["data", "open", "high", "low", "close", "volume"]]


def baixar_lote(tickers: list[str], anos: int = 8) -> dict[str, pd.DataFrame]:
    """
    Baixa histórico para uma lista de tickers de uma vez (mais eficiente).
    Retorna um dict {ticker: DataFrame}.
    """
    tickers_yf = [t.upper() + ".SA" for t in tickers]
    data_inicio = (date.today() - timedelta(days=anos * 365)).isoformat()

    logger.info(f"Download em lote: {len(tickers)} ativos...")

    raw = yf.download(
        tickers_yf,
        start=data_inicio,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
    )

    resultado = {}
    for ticker, ticker_yf in zip(tickers, tickers_yf):
        try:
            if len(tickers) == 1:
                df = raw.copy()
            else:
                df = raw[ticker_yf].copy()

            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df["data"] = pd.to_datetime(df["date"]).dt.date
            resultado[ticker] = df[["data", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            logger.warning(f"Falha ao processar {ticker}: {e}")

    return resultado


# ---------------------------------------------------------------------------
# Cálculo de indicadores técnicos
# ---------------------------------------------------------------------------

def calcular_retornos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona colunas de retorno (diário, 1m, 3m, 12m) ao DataFrame OHLCV.
    """
    df = df.sort_values("data").copy()
    df["retorno_diario"] = df["close"].pct_change()

    # Retornos acumulados em janelas móveis
    df["retorno_1m"]  = df["close"].pct_change(21)   # ~21 pregões = 1 mês
    df["retorno_3m"]  = df["close"].pct_change(63)
    df["retorno_12m"] = df["close"].pct_change(252)

    return df


def calcular_volatilidade(df: pd.DataFrame, janela: int = 30) -> pd.Series:
    """
    Volatilidade anualizada baseada no desvio padrão dos retornos diários.
    Padrão: janela de 30 pregões, anualizada (× √252).
    """
    retornos = df["close"].pct_change()
    return retornos.rolling(janela).std() * np.sqrt(252)


def calcular_beta(
    df_ativo: pd.DataFrame,
    df_ibov: pd.DataFrame,
    janela: int = 252,
) -> pd.Series:
    """
    Beta do ativo em relação ao Ibovespa numa janela móvel.

    Args:
        df_ativo: DataFrame do ativo com coluna 'close'
        df_ibov:  DataFrame do Ibovespa com coluna 'close'
        janela:   Número de pregões (padrão: 252 = 1 ano)
    """
    ret_ativo = df_ativo.set_index("data")["close"].pct_change()
    ret_ibov  = df_ibov.set_index("data")["close"].pct_change()

    combined = pd.concat([ret_ativo, ret_ibov], axis=1, keys=["ativo", "ibov"]).dropna()

    def beta_janela(janela_df):
        cov = janela_df["ativo"].cov(janela_df["ibov"])
        var = janela_df["ibov"].var()
        return cov / var if var != 0 else np.nan

    betas = combined.rolling(janela).apply(
        lambda x: x, raw=False  # placeholder — veja abaixo
    )

    # Cálculo rolling do beta via covariância / variância
    cov_rolling  = combined["ativo"].rolling(janela).cov(combined["ibov"])
    var_rolling  = combined["ibov"].rolling(janela).var()
    beta_series  = cov_rolling / var_rolling

    return beta_series


def calcular_rsi(df: pd.DataFrame, periodos: int = 14) -> pd.Series:
    """
    Relative Strength Index (RSI) — força relativa do ativo.
    Valores acima de 70 indicam sobrecompra; abaixo de 30, sobrevenda.
    """
    delta  = df["close"].diff()
    ganhos = delta.clip(lower=0)
    perdas = (-delta).clip(lower=0)

    media_ganhos = ganhos.ewm(span=periodos, adjust=False).mean()
    media_perdas = perdas.ewm(span=periodos, adjust=False).mean()

    rs  = media_ganhos / media_perdas.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calcular_media_movel(df: pd.DataFrame, janela: int) -> pd.Series:
    """Média móvel simples de fechamento."""
    return df["close"].rolling(janela).mean()


def calcular_volume_medio(df: pd.DataFrame, janela: int = 20) -> pd.Series:
    """Volume médio negociado nos últimos N pregões."""
    return df["volume"].rolling(janela).mean()


def calcular_todos_indicadores(df: pd.DataFrame, df_ibov: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica todos os cálculos de timing ao DataFrame de um ativo e retorna
    um DataFrame com os indicadores por data — pronto para popular KpiTime.

    Args:
        df:      DataFrame OHLCV do ativo (saída de baixar_historico_ohlcv)
        df_ibov: DataFrame OHLCV do Ibovespa (^BVSP)

    Returns:
        DataFrame com colunas compatíveis com o model KpiTime
    """
    df = calcular_retornos(df)

    df["volatilidade_30d"] = calcular_volatilidade(df, janela=30)
    df["rsi_14"]           = calcular_rsi(df)
    df["media_movel_50"]   = calcular_media_movel(df, 50)
    df["media_movel_200"]  = calcular_media_movel(df, 200)
    df["volume_medio_20d"] = calcular_volume_medio(df, 20)

    # Beta (requer df_ibov alinhado)
    beta = calcular_beta(df, df_ibov)
    if beta is not None:
        df["beta"] = beta.values

    # Renomeia para compatibilidade com o model
    df = df.rename(columns={
        "data":   "data_ref",
        "volume": "volume_diario",
    })

    colunas_model = [
        "data_ref", "volume_diario", "volume_medio_20d",
        "volatilidade_30d", "beta",
        "retorno_1m", "retorno_3m", "retorno_12m",
        "rsi_14", "media_movel_50", "media_movel_200",
    ]
    return df[[c for c in colunas_model if c in df.columns]]


# ---------------------------------------------------------------------------
# Dados do Ibovespa (benchmark)
# ---------------------------------------------------------------------------

def baixar_ibovespa(anos: int = 8) -> pd.DataFrame:
    """
    Baixa o histórico do Ibovespa (^BVSP) — usado como benchmark para beta.
    """
    data_inicio = (date.today() - timedelta(days=anos * 365)).isoformat()
    df = yf.download("^BVSP", start=data_inicio, auto_adjust=True, progress=False)
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    df["data"] = pd.to_datetime(df["date"]).dt.date
    return df[["data", "close"]]


# ---------------------------------------------------------------------------
# Informações estáticas do ativo (setor, nome, etc.)
# ---------------------------------------------------------------------------

def obter_info_ativo(ticker: str) -> dict:
    """
    Retorna informações cadastrais do ativo via yfinance.
    Útil para popular a tabela Ativo (nome, setor, subsetor).
    """
    yf_ticker = yf.Ticker(ticker.upper() + ".SA")
    info = yf_ticker.info

    return {
        "ticker":    ticker.upper(),
        "nome":      info.get("longName", ""),
        "setor":     info.get("sector", ""),
        "subsetor":  info.get("industry", ""),
        "tipo":      "acao",  # ajuste conforme necessário
    }