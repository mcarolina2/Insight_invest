"""
Otimização de carteiras do Insight Invest.

Módulo puro (sem Django): recebe preços e escores e devolve pesos.
É usado tanto pelo fluxo em lote (atualizar_carteiras) quanto pelo fluxo
interativo (otimizar_ajax) e pelo backtest, garantindo que os três usem
exatamente a mesma lógica.

Modelos (Quadro "Modelo de alocação e restrições" do TCC):
  - risk_parity : contribuição igual de risco (Maillard, Roncalli e Teïletche, 2010)
  - markowitz   : mínima variância sujeita a retorno esperado >= alvo
  - sharpe      : máximo Índice de Sharpe, opcionalmente com volatilidade <= teto
  - score       : pesos proporcionais ao escore final (sem covariância)

Em todos: long-only, soma = 1 e peso máximo por ativo w_max, com
w_max efetivo = max(w_max, 1/n) para manter o problema viável.
A covariância é estimada por encolhimento de Ledoit-Wolf (2004).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

DIAS_UTEIS_ANO = 252
MIN_OBSERVACOES = 252
MODELOS = ("risk_parity", "markowitz", "sharpe", "score")


# ─────────────────────────────────────────────────────────────────────
# Estimação dos parâmetros
# ─────────────────────────────────────────────────────────────────────

def preparar_precos(precos: pd.DataFrame, min_obs: int = MIN_OBSERVACOES) -> pd.DataFrame:
    """Mantém ativos com histórico suficiente, preenche lacunas pontuais e
    remove datas ainda incompletas (mesmas regras descritas na metodologia)."""
    if precos is None or precos.empty:
        return pd.DataFrame()
    precos = precos.sort_index().dropna(axis=1, how="all")
    precos = precos.loc[:, precos.notna().sum() >= min_obs]
    precos = precos.ffill().dropna()
    return precos if len(precos) >= min_obs else pd.DataFrame()


def estimar_parametros(precos: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Retorno esperado anual (média histórica composta) e covariância anual
    estimada por Ledoit-Wolf sobre retornos diários simples."""
    retornos = precos.pct_change().dropna()
    n = len(retornos)
    mu = (1 + retornos).prod() ** (DIAS_UTEIS_ANO / n) - 1
    lw = LedoitWolf().fit(retornos.values)
    cov = pd.DataFrame(lw.covariance_ * DIAS_UTEIS_ANO,
                       index=precos.columns, columns=precos.columns)
    return mu, cov


# ─────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────────────────────────────

def teto_viavel(w_max: float, n: int) -> float:
    return max(float(w_max), 1.0 / n) if n else float(w_max)


def aplicar_teto(pesos: np.ndarray, w_max: float) -> np.ndarray:
    """Limita cada peso a w_max e redistribui o excesso proporcionalmente
    entre os ativos ainda abaixo do teto (usado na alocação por escore)."""
    w = np.clip(np.asarray(pesos, dtype=float), 0, None)
    w = w / w.sum() if w.sum() > 0 else np.full(len(w), 1 / len(w))
    for _ in range(100):
        acima = w > w_max + 1e-12
        if not acima.any():
            break
        excesso = (w[acima] - w_max).sum()
        w[acima] = w_max
        livres = ~acima & (w < w_max - 1e-12)
        if not livres.any():
            break
        w[livres] += excesso * w[livres] / w[livres].sum()
    return w / w.sum()


def _vol(w, cov):
    return float(np.sqrt(max(w @ cov @ w, 0.0)))


def _resolver(objetivo, n, w_max, restricoes=(), inicios=None):
    """SLSQP com algumas inicializações; devolve a melhor solução viável."""
    limites = [(0.0, w_max)] * n
    base = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}, *restricoes]
    inicios = inicios or [np.full(n, 1.0 / n)]
    melhor = None
    for w0 in inicios:
        r = minimize(objetivo, w0, method="SLSQP", bounds=limites,
                     constraints=base, options={"maxiter": 500, "ftol": 1e-10})
        viavel = r.success and all(
            (c["fun"](r.x) >= -1e-6) if c["type"] == "ineq" else abs(c["fun"](r.x)) < 1e-6
            for c in base
        )
        if viavel and (melhor is None or r.fun < melhor.fun):
            melhor = r
    if melhor is None:
        return None
    w = np.clip(melhor.x, 0, None)
    return w / w.sum()


# ─────────────────────────────────────────────────────────────────────
# Modelos
# ─────────────────────────────────────────────────────────────────────

def pesos_score(scores: np.ndarray, w_max: float) -> np.ndarray:
    return aplicar_teto(np.asarray(scores, dtype=float), w_max)


def pesos_risk_parity(cov: np.ndarray, w_max: float):
    n = cov.shape[0]

    def objetivo(w):
        risco = w @ cov @ w
        contrib = w * (cov @ w) / risco          # contribuição relativa de cada ativo
        return float(((contrib - 1.0 / n) ** 2).sum()) * 1e4

    inv_vol = 1 / np.sqrt(np.diag(cov))
    return _resolver(objetivo, n, w_max, inicios=[aplicar_teto(inv_vol, w_max)])


def pesos_markowitz(mu: np.ndarray, cov: np.ndarray, w_max: float, retorno_alvo):
    n = len(mu)
    restricoes = []
    if retorno_alvo is not None:
        restricoes.append({"type": "ineq", "fun": lambda w: w @ mu - retorno_alvo})
    return _resolver(lambda w: w @ cov @ w * 1e3, n, w_max, restricoes)


def pesos_max_sharpe(mu: np.ndarray, cov: np.ndarray, w_max: float, rf: float, vol_max):
    n = len(mu)
    restricoes = []
    if vol_max is not None:
        restricoes.append({"type": "ineq", "fun": lambda w: vol_max - _vol(w, cov)})

    def objetivo(w):
        v = _vol(w, cov)
        return -(w @ mu - rf) / v if v > 0 else 0.0

    rng = np.random.default_rng(42)
    inicios = [np.full(n, 1.0 / n)] + [aplicar_teto(rng.dirichlet(np.ones(n)), w_max) for _ in range(4)]
    return _resolver(objetivo, n, w_max, restricoes, inicios)


# ─────────────────────────────────────────────────────────────────────
# Métricas e fronteira
# ─────────────────────────────────────────────────────────────────────

def metricas_carteira(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, rf: float) -> dict:
    ret = float(w @ mu)
    vol = _vol(w, cov)
    # VaR paramétrico diário a 95% (normal)
    var95 = ret / DIAS_UTEIS_ANO - 1.645 * vol / np.sqrt(DIAS_UTEIS_ANO)
    return {
        "retorno": round(ret, 4),
        "volatilidade": round(vol, 4),
        "sharpe": round((ret - rf) / vol, 3) if vol > 0 else None,
        "var95": round(float(var95), 4),
    }


def retorno_maximo(mu: np.ndarray, w_max: float) -> float:
    """Maior retorno atingível com pesos em [0, w_max]: preenche os ativos de
    maior retorno até o teto (solução exata desse problema linear)."""
    w = np.zeros(len(mu))
    restante = 1.0
    for i in np.argsort(-mu):
        w[i] = min(w_max, restante)
        restante -= w[i]
        if restante <= 1e-12:
            break
    return float(w @ mu)


def fronteira_eficiente(mu: np.ndarray, cov: np.ndarray, w_max: float, pontos: int = 15) -> list[dict]:
    """Pontos (volatilidade, retorno) da fronteira com as mesmas restrições."""
    n = len(mu)
    w_min = pesos_markowitz(mu, cov, w_max, None)
    if w_min is None:
        return []
    r_min = float(w_min @ mu)
    r_max = retorno_maximo(mu, w_max)
    resultado = []
    for alvo in np.linspace(r_min, r_max, pontos):
        w = pesos_markowitz(mu, cov, w_max, alvo)
        if w is not None:
            resultado.append({"vol": round(_vol(w, cov), 4), "ret": round(float(w @ mu), 4)})
    return resultado


# ─────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ResultadoOtimizacao:
    pesos: pd.Series                       # índice = ticker, soma = 1
    modelo_usado: str
    metricas: dict = field(default_factory=dict)
    fronteira: list = field(default_factory=list)
    aviso: str | None = None


def otimizar(
    precos: pd.DataFrame | None,
    scores: pd.Series,
    modelo: str = "sharpe",
    w_max: float = 0.20,
    retorno_alvo: float | None = None,
    volatilidade_maxima: float | None = None,
    taxa_livre_risco: float = 0.0,
    calcular_fronteira: bool = False,
) -> ResultadoOtimizacao:
    """
    precos : DataFrame (datas x tickers) de preços ajustados. Pode ser None/vazio,
             caso em que se usa a alocação por escore.
    scores : Series (ticker -> escore final) dos candidatos selecionados.
    """
    if modelo not in MODELOS:
        raise ValueError(f"Modelo desconhecido: {modelo}")
    scores = scores.astype(float)

    precos_ok = preparar_precos(precos[scores.index.intersection(precos.columns)]) \
        if precos is not None and not precos.empty else pd.DataFrame()
    tickers = list(precos_ok.columns)

    def _por_score(aviso=None):
        n = len(scores)
        w = pesos_score(scores.values, teto_viavel(w_max, n))
        res = ResultadoOtimizacao(pd.Series(w, index=scores.index), "score", aviso=aviso)
        if len(tickers) >= 2:  # métricas apenas se houver preços para todos
            mu, cov = estimar_parametros(precos_ok)
            if set(scores.index) <= set(tickers):
                ww = res.pesos.reindex(tickers).fillna(0).values
                res.metricas = metricas_carteira(ww, mu.values, cov.values, taxa_livre_risco)
        return res

    if modelo == "score":
        return _por_score()
    if len(tickers) < 2:
        return _por_score("Histórico de preços insuficiente; alocação feita pelo escore.")

    mu, cov = estimar_parametros(precos_ok)
    teto = teto_viavel(w_max, len(tickers))
    M, C = mu.values, cov.values

    if modelo == "risk_parity":
        w = pesos_risk_parity(C, teto)
    elif modelo == "markowitz":
        w = pesos_markowitz(M, C, teto, retorno_alvo)
    else:
        # Se nenhuma carteira possível supera a taxa livre de risco, todo Sharpe é
        # negativo e "maximizá-lo" empurraria o otimizador para MAIS volatilidade.
        # Mesmo comportamento do PyPortfolioOpt: não otimiza e usa o escore.
        if retorno_maximo(M, teto) <= taxa_livre_risco:
            return _por_score(
                f"Nenhuma combinação dos ativos candidatos supera a taxa livre de risco "
                f"(Selic de {taxa_livre_risco:.1%} a.a.) no histórico; o Índice de Sharpe "
                f"não se aplica e a alocação foi feita pelo escore.")
        w = pesos_max_sharpe(M, C, teto, taxa_livre_risco, volatilidade_maxima)

    if w is None:
        alvo = []
        if retorno_alvo is not None and modelo == "markowitz":
            alvo.append(f"retorno de {retorno_alvo:.0%} a.a.")
        if volatilidade_maxima is not None and modelo == "sharpe":
            alvo.append(f"volatilidade até {volatilidade_maxima:.0%} a.a.")
        texto = " e ".join(alvo) or "as restrições escolhidas"
        return _por_score(f"Não foi possível atingir {texto} com os ativos candidatos; "
                          "alocação feita pelo escore.")

    res = ResultadoOtimizacao(pd.Series(w, index=tickers), modelo)
    if len(tickers) < len(scores):
        fora = sorted(set(scores.index) - set(tickers))
        res.aviso = f"Sem histórico suficiente (excluídos): {', '.join(fora)}."
    res.metricas = metricas_carteira(w, M, C, taxa_livre_risco)
    if calcular_fronteira:
        res.fronteira = fronteira_eficiente(M, C, teto)
    return res
