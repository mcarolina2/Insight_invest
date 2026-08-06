import yfinance as yf  
import logging
from datetime import date, timedelta
import numpy as np
import pandas as pd
from scipy import stats
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.optimize import minimize
import matplotlib.pyplot as plt
import matplotlib.pyplot as plt



logger = logging.getLogger(__name__)

PREGOES_ANO = 252  # convenção de mercado para anualização


# =============================================================================
# DOWNLOAD DE PREÇOS (equivale ao GetBCBData / getSymbols do R)
# =============================================================================

def baixar_precos(tickers: list[str], anos: int = 3) -> pd.DataFrame:
    """
    Baixa séries de fechamento ajustado para uma lista de tickers da B3.
    Equivale ao merge(lwsa3, elet6, ...) do R.

    Args:
        tickers: lista sem sufixo .SA  (ex: ['LWSA3', 'ELET6', 'ITSA4'])
        anos:    janela histórica em anos

    Returns:
        DataFrame com colunas = tickers, linhas = datas de pregão
    """
    import yfinance as yf

    tickers_yf  = [t.upper() + ".SA" for t in tickers]
    data_inicio = (date.today() - timedelta(days=anos * 365)).isoformat()

    precos = yf.download(
        tickers_yf,
        start=data_inicio,
        auto_adjust=True,
        progress=False,
    )["Close"]

    if isinstance(precos, pd.Series):
        precos = precos.to_frame(tickers_yf[0])

    precos.columns = [c.replace(".SA", "").upper() for c in precos.columns]
    precos = precos.ffill().dropna()

    logger.info(f"Preços baixados: {precos.shape[0]} pregões x {precos.shape[1]} ativos")
    return precos


def baixar_ibovespa(anos: int = 3) -> pd.Series:
    """Baixa o Ibovespa (^BVSP) para cálculo do beta e correlação."""
    import yfinance as yf
    data_inicio = (date.today() - timedelta(days=anos * 365)).isoformat()
    df = yf.download("^BVSP", start=data_inicio, auto_adjust=True, progress=False)["Close"]
    return df.squeeze()


# =============================================================================
# RETORNOS LOGARÍTMICOS (equivale ao returns() do fPortfolio)
# =============================================================================

def calcular_retornos_log(precos: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula retornos logarítmicos diários.
    R: returns(dados)  →  Python: np.log(P_t / P_{t-1})

    O log-retorno tem propriedade de aditividade temporal e é o padrão
    em finanças quantitativas (diferente do retorno simples pct_change).
    """
    return np.log(precos / precos.shift(1)).dropna()


# =============================================================================
# CORRELAÇÃO DE PEARSON (equivale ao correlationTest + cor() do R)
# =============================================================================

def matriz_correlacao(retornos: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula a matriz de correlação de Pearson entre todos os ativos.
    R: cor(Retornos, method="pearson")
    """
    return retornos.corr(method="pearson")


def teste_correlacao_pearson(x: pd.Series, y: pd.Series) -> dict:
    """
    Teste de correlação de Pearson entre dois ativos com p-valor.
    R: correlationTest(x, y, method="pearson")

    Returns:
        {correlacao, p_valor, significativo_95}
    """
    r, p = stats.pearsonr(x.dropna(), y.dropna())
    return {
        "correlacao":      round(r, 6),
        "p_valor":         round(p, 6),
        "significativo_95": p < 0.05,
    }


def plotar_matriz_correlacao(retornos: pd.DataFrame, titulo: str = "Matriz de Correlação"):
    """
    Plota a matriz de correlação com heatmap.
    R: corrplot(matriz_cor, method="color", addCoef.col="black")
    """

    matriz = matriz_correlacao(retornos)

    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(
        matriz,
        annot=True,          # equivale a addCoef.col="black"
        fmt=".2f",
        cmap="RdYlGn",       # vermelho (negativo) → amarelo → verde (positivo)
        vmin=-1, vmax=1,
        square=True,
        linewidths=0.5,
        ax=ax,
    )
    ax.set_title(titulo, pad=16, fontsize=13)
    plt.tight_layout()
    return fig


def plotar_dispersao(x: pd.Series, y: pd.Series, nome_x: str, nome_y: str):
    """
    Gráfico de dispersão com linha de regressão.
    R: plot(x, y) + abline(lm(x~y), col="red", lwd=3)
    """

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter(x, y, alpha=0.4, s=15, color="#1D9E75")

    # Linha de regressão (equivale ao abline(lm(x~y)) do R)
    m, b, r, p, _ = stats.linregress(y, x)
    y_linha = np.linspace(y.min(), y.max(), 100)
    ax.plot(y_linha, m * y_linha + b, color="red", linewidth=2,
            label=f"r = {r:.3f}, p = {p:.4f}")

    ax.set_xlabel(nome_x)
    ax.set_ylabel(nome_y)
    ax.set_title(f"Correlação {nome_x} × {nome_y}")
    ax.legend(fontsize=10)
    plt.tight_layout()
    return fig


# =============================================================================
# ESTATÍSTICAS DESCRITIVAS (equivale ao fBasics no R)
# =============================================================================

def calcular_estatisticas_ativo(
    preco_serie: pd.Series,
    retorno_serie: pd.Series,
    retorno_ibov: pd.Series = None,
) -> dict:

    r = retorno_serie.dropna()
    p = preco_serie.dropna()

    if len(r) < 30:
        logger.warning(f"Poucos dados ({len(r)} obs) — estatísticas podem ser imprecisas.")

    # ── Estatísticas de retorno ───────────────────────────────────────
    media_ret    = float(np.mean(r))
    mediana_ret  = float(np.median(r))
    variancia    = float(np.var(r, ddof=1))        # ddof=1 = amostral (como R)
    dp           = float(np.std(r, ddof=1))
    vol_anual    = dp * np.sqrt(PREGOES_ANO)       # anualiza

    # Coeficiente de Variação: risco relativo ao retorno
    cv = abs(dp / media_ret) if media_ret != 0 else None

    # ── Distribuição (fBasics no R) ───────────────────────────────────
    skew_val  = float(stats.skew(r))               # scipy.stats.skew
    kurt_val  = float(stats.kurtosis(r))            # excess kurtosis (como R)
    jb_stat, jb_pvalue = stats.jarque_bera(r)      # jarqueberaTest
    normal    = jb_pvalue > 0.05                   # H0: normalidade

    # ── Beta e correlação vs Ibovespa ────────────────────────────────
    beta = None
    corr_ibov = None
    r_quad = None

    if retorno_ibov is not None and len(retorno_ibov) > 30:
        # Alinha as datas
        comum = r.index.intersection(retorno_ibov.index)
        if len(comum) > 30:
            r_alinhado    = r.loc[comum]
            ibov_alinhado = retorno_ibov.loc[comum]

            # Regressão: retorno_ativo = alpha + beta * retorno_ibov
            slope, intercept, r_val, p_val, _ = stats.linregress(
                ibov_alinhado, r_alinhado
            )
            beta      = float(slope)
            r_quad    = float(r_val ** 2)

            # Correlação de Pearson
            corr_val, _ = stats.pearsonr(r_alinhado, ibov_alinhado)
            corr_ibov   = float(corr_val)

    # ── Estatísticas de preço ─────────────────────────────────────────
    media_preco = float(np.mean(p))
    dp_preco    = float(np.std(p, ddof=1))
    preco_min   = float(p.min())
    preco_max   = float(p.max())

    return {
        # Retorno
        "media_retorno":     round(media_ret, 8),
        "mediana_retorno":   round(mediana_ret, 8),
        "variancia_retorno": round(variancia, 10),
        "dp_retorno":        round(dp, 8),

        # Risco
        "volatilidade_anual": round(vol_anual, 6),
        "cv":                 round(cv, 6) if cv else None,

        # Distribuição
        "skewness":           round(skew_val, 6),
        "curtose":            round(kurt_val, 6),
        "jarque_bera":        round(float(jb_stat), 4),
        "jarque_bera_pvalue": round(float(jb_pvalue), 6),
        "retorno_normal":     bool(normal),

        # Mercado
        "beta":               round(beta, 6)      if beta      is not None else None,
        "correlacao_ibov":    round(corr_ibov, 4) if corr_ibov is not None else None,
        "r_quadrado":         round(r_quad, 4)    if r_quad    is not None else None,

        # Preço
        "media_preco": round(media_preco, 4),
        "dp_preco":    round(dp_preco, 4),
        "preco_min":   round(preco_min, 4),
        "preco_max":   round(preco_max, 4),
        "n_observacoes": len(r),
    }


# =============================================================================
# FRONTEIRA EFICIENTE (equivale ao portfolioFrontier do fPortfolio)
# =============================================================================

def calcular_fronteira_eficiente(retornos: pd.DataFrame, n_pontos: int = 50, rf: float = None, ) -> list[dict]:
    if rf is None:
        from market_data.models import KpiMacro
        macro = KpiMacro.objects.order_by("-data_ref").first()
        rf = (macro.selic / 100) if macro and macro.selic else 0.1375 
    mu  = retornos.mean().values * PREGOES_ANO
    cov = retornos.cov().values  * PREGOES_ANO
    n   = len(mu)

    def variancia_port(w):
        return w @ cov @ w

    def retorno_port(w):
        return w @ mu

    restricoes = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    limites    = [(0, 1)] * n
    w0         = np.ones(n) / n

    # Varre alvos de retorno do mínimo ao máximo
    ret_min = mu.min()
    ret_max = mu.max()
    alvos   = np.linspace(ret_min, ret_max, n_pontos)

    fronteira = []
    for alvo in alvos:
        res_alvo = restricoes + [{"type": "eq", "fun": lambda w, a=alvo: retorno_port(w) - a}]
        res = minimize(variancia_port, w0, method="SLSQP",
                       bounds=limites, constraints=res_alvo,
                       options={"maxiter": 1000, "ftol": 1e-9})
        if res.success:
            vol    = np.sqrt(variancia_port(res.x))
            ret    = retorno_port(res.x)
            sharpe = (ret - rf) / vol if vol > 0 else 0
            fronteira.append({
                "volatilidade": round(vol, 6),
                "retorno":      round(ret, 6),
                "sharpe":       round(sharpe, 4),
                "pesos":        dict(zip(retornos.columns, res.x.round(4))),
            })

    return fronteira


def plotar_fronteira_eficiente(
    fronteira: list[dict],
    pontos_interesse: list[dict] = None,
) -> object:
    """
    Plota a fronteira eficiente.
    Equivale ao frontierPlot() + points() do R.

    Args:
        fronteira: saída de calcular_fronteira_eficiente()
        pontos_interesse: lista de {vol, retorno, cor, label}
          Equivale ao: points(0.0365, -0.0027, pch=19, col="green")
    """
    vols   = [p["volatilidade"] for p in fronteira]
    rets   = [p["retorno"]      for p in fronteira]
    sharpe = [p["sharpe"]       for p in fronteira]

    fig, ax = plt.subplots(figsize=(10, 6))

    # Fronteira colorida pelo Sharpe (quanto mais verde, maior o Sharpe)
    sc = ax.scatter(vols, rets, c=sharpe, cmap="RdYlGn", s=30, zorder=3)
    plt.colorbar(sc, ax=ax, label="Índice de Sharpe")

    # Destaca a carteira de máximo Sharpe
    idx_max_sharpe = np.argmax(sharpe)
    ax.scatter(
        vols[idx_max_sharpe], rets[idx_max_sharpe],
        color="blue", s=120, zorder=5, marker="*",
        label=f"Máx Sharpe ({sharpe[idx_max_sharpe]:.2f})"
    )

    # Pontos de interesse (equivale ao points() do R)
    if pontos_interesse:
        for pt in pontos_interesse:
            ax.scatter(
                pt["vol"], pt["retorno"],
                color=pt.get("cor", "green"), s=80, zorder=5,
                marker="o",
            )
            if pt.get("label"):
                ax.annotate(
                    pt["label"],
                    (pt["vol"], pt["retorno"]),
                    textcoords="offset points", xytext=(6, 4),
                    fontsize=9, color=pt.get("cor", "green"),
                )

    ax.set_xlabel("Risco (Volatilidade Anual)", fontsize=11)
    ax.set_ylabel("Retorno Esperado Anual", fontsize=11)
    ax.set_title("Fronteira Eficiente de Markowitz", fontsize=13, pad=14)
    ax.axhline(y=0, color="black", linewidth=0.5, alpha=0.4)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.15)
    plt.tight_layout()
    return fig


# =============================================================================
# PIPELINE COMPLETO (equivale a rodar o script R inteiro)
# =============================================================================

def pipeline_analise_completa(
    tickers: list[str],
    anos: int = 3,
    janela_dias: int = 252,
) -> dict:
    """
    Executa a análise completa igual ao script R do professor.

    Args:
        tickers:     ex: ['LWSA3', 'ELET6', 'ITSA4', 'RENT3', 'BRFS3']
        anos:        janela de histórico
        janela_dias: pregões para calcular estatísticas

    Returns:
        dict com: precos, retornos, matriz_cor, stats_por_ativo, fronteira
    """
    logger.info(f"Iniciando análise: {tickers}")

    # 1. Download (como GetBCBData / getSymbols)
    precos  = baixar_precos(tickers, anos=anos)
    ibov    = baixar_ibovespa(anos=anos)

    # 2. Retornos log (como returns() do fPortfolio)
    retornos     = calcular_retornos_log(precos)
    retornos_ibov = calcular_retornos_log(ibov.to_frame()).squeeze()

    # 3. Matriz de correlação (como cor(Retornos))
    matriz_cor = matriz_correlacao(retornos)

    # 4. Estatísticas por ativo (como fBasics)
    stats_por_ativo = {}
    for ticker in tickers:
        if ticker not in precos.columns:
            continue
        preco_serie   = precos[ticker].iloc[-janela_dias:]
        retorno_serie = retornos[ticker].iloc[-janela_dias:]
        stats_por_ativo[ticker] = calcular_estatisticas_ativo(
            preco_serie, retorno_serie, retornos_ibov
        )

    # 5. Fronteira eficiente (como portfolioFrontier)
    fronteira = calcular_fronteira_eficiente(retornos)

    return {
        "precos":         precos,
        "retornos":       retornos,
        "matriz_cor":     matriz_cor,
        "stats_por_ativo": stats_por_ativo,
        "fronteira":      fronteira,
    }