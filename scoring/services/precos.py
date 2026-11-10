"""
Preços ajustados para a otimização, com cache.

Baixar 3 anos de preços a cada clique no filtro deixaria a página lenta e
poderia esbarrar em limites do Yahoo Finance; por isso o resultado fica em
cache (padrão: 12 horas) por conjunto de tickers.
"""
import hashlib
import logging

import pandas as pd
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 12 * 60 * 60


def baixar_precos(tickers: list[str], anos: int = 3) -> pd.DataFrame:
    """Baixa preços de fechamento ajustados do Yahoo Finance (colunas = tickers sem .SA)."""
    import yfinance as yf

    dados = yf.download([f"{t}.SA" for t in tickers], period=f"{anos}y",
                        auto_adjust=True, progress=False, threads=True)
    if dados is None or dados.empty:
        return pd.DataFrame()
    fech = dados["Close"]
    if isinstance(fech, pd.Series):          # um único ticker
        fech = fech.to_frame(name=f"{tickers[0]}.SA")
    fech.columns = [str(c).replace(".SA", "") for c in fech.columns]
    # O volume (em ações) vem no mesmo download; vai junto em .attrs para o
    # filtro de liquidez, sem um segundo acesso ao Yahoo.
    if "Volume" in dados:
        vol = dados["Volume"]
        if isinstance(vol, pd.Series):
            vol = vol.to_frame(name=f"{tickers[0]}.SA")
        vol.columns = [str(c).replace(".SA", "") for c in vol.columns]
        fech.attrs["volume"] = vol
    return fech


def obter_precos(tickers, anos: int = 3) -> pd.DataFrame:
    tickers = sorted(set(tickers))
    if not tickers:
        return pd.DataFrame()
    chave = "precos:" + hashlib.md5(f"{anos}|{','.join(tickers)}".encode()).hexdigest()
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache
    try:
        precos = baixar_precos(tickers, anos)
    except Exception as e:          # sem rede, Yahoo fora do ar etc.
        logger.warning(f"Falha ao baixar preços: {e}")
        return pd.DataFrame()
    if not precos.empty:
        volume = precos.attrs.pop("volume", None)
        cache.set(chave, precos, CACHE_SEGUNDOS)
        if volume is not None:
            cache.set("vol" + chave, volume, CACHE_SEGUNDOS)
    return precos


def obter_volumes(tickers, anos: int = 3) -> pd.DataFrame | None:
    """Volume diário (ações) do último obter_precos com os mesmos tickers. Não baixa nada."""
    tickers = sorted(set(tickers))
    chave = "precos:" + hashlib.md5(f"{anos}|{','.join(tickers)}".encode()).hexdigest()
    return cache.get("vol" + chave)


def liquidez(precos: pd.DataFrame, volumes: pd.DataFrame | None, pregoes: int = 126) -> pd.DataFrame:
    """
    Liquidez recente de cada ticker (últimos `pregoes`, ~6 meses):
      volume_financeiro: mediana de preço × quantidade negociada (R$/dia);
      pregoes_com_negocio: fração dos dias com volume > 0.
    Ações quase sem negócios aparecem no Yahoo com o preço repetido dia após
    dia; parecem ter volatilidade baixíssima e enganam o otimizador.
    """
    if volumes is None or volumes.empty:
        return pd.DataFrame()
    v = volumes.reindex(columns=precos.columns).tail(pregoes)
    p = precos.reindex(v.index).ffill()
    financeiro = (v * p).where(v > 0)
    return pd.DataFrame({
        "volume_financeiro": financeiro.median().fillna(0.0),
        "pregoes_com_negocio": (v.fillna(0) > 0).mean(),
    })


def baixar_ibovespa(anos: int = 3) -> pd.Series:
    """Fechamento do Ibovespa (^BVSP) no Yahoo Finance."""
    import yfinance as yf

    dados = yf.download("^BVSP", period=f"{anos}y", auto_adjust=True, progress=False)
    if dados is None or dados.empty:
        return pd.Series(dtype=float)
    fech = dados["Close"]
    if isinstance(fech, pd.DataFrame):
        fech = fech.iloc[:, 0]
    return fech.rename("IBOV")


def obter_ibovespa(anos: int = 3) -> pd.Series:
    chave = f"ibov:{anos}"
    em_cache = cache.get(chave)
    if em_cache is not None:
        return em_cache
    try:
        serie = baixar_ibovespa(anos)
    except Exception as e:
        logger.warning(f"Falha ao baixar o Ibovespa: {e}")
        return pd.Series(dtype=float)
    if not serie.empty:
        cache.set(chave, serie, CACHE_SEGUNDOS)
    return serie
