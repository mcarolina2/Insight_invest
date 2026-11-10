"""
Backtest walk-forward do Insight Invest (seção "Avaliação e Validação das
Carteiras" do TCC).

Protocolo
  * janela de calibração de 504 pregões e janela de teste de 63 pregões;
  * a cada rebalanceamento (pregão t) o motor roda em modo point-in-time:
      - indicadores estatísticos recalculados só com os preços da janela que
        termina em t (não usa KpiEstatistico, que é calculado "hoje");
      - fundamentalistas: DFP com exercício encerrado há >= 90 dias;
      - macro: último registro até t;
  * os pesos decididos no fechamento de t são executados no fechamento de t+1;
  * entre rebalanceamentos os pesos variam com os preços;
  * custo proporcional c sobre o volume negociado: r_liq = r_bruto - c * sum|w - w^-|.

Carteiras: combinada, só fundamentalista, só estatística, 1/N (mesmos
candidatos da combinada) e Ibovespa. Todas só com ações: pela separação de
Tobin a parcela de renda fixa à taxa livre de risco não altera o Índice de
Sharpe, então a comparação entre critérios de seleção é feita na parte em ações.

Teste: diferença de Sharpe por bootstrap em blocos circulares studentizado
(LEDOIT; WOLF, 2008).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy import stats

from scoring.services.carteira import MODELO_MAP, RETORNO_MAP, RISCO_MAP
from scoring.services.motor_scoring_v2 import (calcular_scores_v2, config_perfil,
                                               selecionar_candidatos)
from scoring.services.otimizador import otimizar

logger = logging.getLogger(__name__)

DIAS_ANO = 252
JANELA_CALIBRACAO = 504
JANELA_TESTE = 63
CUSTOS_PADRAO = (0.0, 0.001, 0.003)
COBERTURA_MINIMA = 0.90        # fração de pregões com preço na janela de calibração

# filtros (risco, retorno) usados para cada perfil no backtest
FILTROS_POR_PERFIL = {
    "conservador": ("baixo", "baixo"),      # paridade de risco
    "intermediario": ("medio", "medio"),    # máximo Sharpe com vol <= 20%
    "arrojado": ("alto", "alto"),           # alocação por escore
}

ESTRATEGIAS = {
    "combinada": "Combinada (3 camadas, AHP)",
    "fundamentalista": "Só fundamentalista",
    "estatistica": "Só estatística",
    "igual": "1/N (candidatos da combinada)",
    "ibovespa": "Ibovespa",
}


# ─────────────────────────────────────────────────────────────────────
# Indicadores estatísticos point-in-time (mesmas definições de
# services/calculators/estatistico.py, vetorizadas)
# ─────────────────────────────────────────────────────────────────────

def estatisticas_janela(precos: pd.DataFrame, mercado: pd.Series | None = None,
                        cobertura: float = COBERTURA_MINIMA) -> pd.DataFrame:
    """precos: janela de calibração (datas x tickers). Retorna índice = ticker."""
    ok = precos.columns[precos.notna().mean() >= cobertura]
    if len(ok) == 0:
        return pd.DataFrame()
    p = precos[ok].ffill()
    r = np.log(p / p.shift(1)).iloc[1:]
    n = r.count()
    media = r.mean()
    dp = r.std(ddof=1)
    S = r.skew()
    K = r.kurt()                                   # curtose em excesso
    jb = n / 6 * (S ** 2 + K ** 2 / 4)
    p_jb = pd.Series(stats.chi2.sf(jb.values, 2), index=jb.index)

    if mercado is not None and not mercado.dropna().empty:
        m = np.log(mercado / mercado.shift(1)).reindex(r.index)
    else:                                          # sem Ibovespa: média do universo
        m = r.mean(axis=1)
    beta = r.apply(lambda c: c.cov(m) / m.var() if m.var() > 0 else np.nan)

    return pd.DataFrame({
        "volatilidade_anual": dp * np.sqrt(DIAS_ANO),
        "beta": beta,
        "skewness": S,
        "curtose": K,
        "retorno_normal": p_jb > 0.05,
        "cv": (dp / media.replace(0, np.nan)).abs(),
    }).astype({"retorno_normal": bool})


# ─────────────────────────────────────────────────────────────────────
# Métricas e teste
# ─────────────────────────────────────────────────────────────────────

def metricas(r: pd.Series, rf_d: pd.Series, turnovers: list[float] | None = None) -> dict:
    r = r.dropna()
    if len(r) < 2:
        return {}
    ex = r - rf_d.reindex(r.index).fillna(0)
    anos = len(r) / DIAS_ANO
    acum = (1 + r).cumprod()
    total = acum.iloc[-1] - 1
    vol = r.std(ddof=1) * np.sqrt(DIAS_ANO)
    neg = ex[ex < 0]
    desvio_neg = np.sqrt((neg ** 2).sum() / len(ex)) * np.sqrt(DIAS_ANO)
    q05 = r.quantile(0.05)
    return {
        "retorno_anual": (1 + total) ** (1 / anos) - 1 if total > -1 else -1.0,
        "volatilidade": vol,
        "sharpe": ex.mean() * DIAS_ANO / vol if vol > 0 else np.nan,
        "sortino": ex.mean() * DIAS_ANO / desvio_neg if desvio_neg > 0 else np.nan,
        "max_drawdown": float((acum / acum.cummax() - 1).min()),
        "cvar95": float(r[r <= q05].mean()),
        "turnover_medio": float(np.mean(turnovers)) if turnovers else np.nan,
        "retorno_total": total,
        "n_dias": int(len(r)),
    }


def _hac_parzen(y: np.ndarray) -> np.ndarray:
    """Matriz de covariância de longo prazo (kernel de Parzen, banda automática simples)."""
    T = y.shape[0]
    u = y - y.mean(axis=0)
    banda = max(1, int(np.floor(4 * (T / 100) ** (4 / 25))))
    psi = u.T @ u / T
    for j in range(1, banda + 1):
        z = j / (banda + 1)
        k = 1 - 6 * z ** 2 + 6 * z ** 3 if z <= 0.5 else 2 * (1 - z) ** 3
        g = u[j:].T @ u[:-j] / T
        psi += k * (g + g.T)
    return psi


def _delta_sharpe(y: np.ndarray):
    a, b, c, d = y.mean(axis=0)
    va, vb = c - a ** 2, d - b ** 2
    delta = a / np.sqrt(va) - b / np.sqrt(vb)
    grad = np.array([c / va ** 1.5, -d / vb ** 1.5, -a / (2 * va ** 1.5), b / (2 * vb ** 1.5)])
    return delta, grad


def teste_sharpe_ledoit_wolf(ri: pd.Series, rn: pd.Series, bloco: int = 5,
                             reamostragens: int = 2000, semente: int = 42) -> dict:
    """
    H0: Sharpe(i) = Sharpe(n). Bootstrap em blocos circulares studentizado
    (LEDOIT; WOLF, 2008), sobre retornos diários em excesso.
    Retorna diferença anualizada, erro-padrão e p-valor bicaudal.
    """
    par = pd.concat([ri, rn], axis=1, join="inner").dropna()
    x, z = par.iloc[:, 0].values, par.iloc[:, 1].values
    T = len(x)
    if T < 50 or x.std() == 0 or z.std() == 0:
        return {"delta_sharpe": np.nan, "p_valor": np.nan, "n": T}
    y = np.column_stack([x, z, x ** 2, z ** 2])
    delta, grad = _delta_sharpe(y)
    se = np.sqrt(max(grad @ _hac_parzen(y) @ grad / T, 0.0))

    rng = np.random.default_rng(semente)
    n_blocos = int(np.ceil(T / bloco))
    estat = np.empty(reamostragens)
    for m in range(reamostragens):
        inicios = rng.integers(0, T, n_blocos)
        idx = ((inicios[:, None] + np.arange(bloco)[None, :]) % T).ravel()[:T]
        yb = y[idx]
        d_b, g_b = _delta_sharpe(yb)
        # covariância do bootstrap: blocos não sobrepostos da própria amostra
        l = T // bloco
        u = yb[: l * bloco] - yb[: l * bloco].mean(axis=0)
        zeta = u.reshape(l, bloco, 4).sum(axis=1) / np.sqrt(bloco)
        psi_b = zeta.T @ zeta / l
        se_b = np.sqrt(max(g_b @ psi_b @ g_b / T, 0.0))
        estat[m] = abs(d_b - delta) / se_b if se_b > 0 else 0.0
    if not se > 1e-12:            # carteiras (quase) idênticas: diferença nula, teste não se aplica
        return {"delta_sharpe": float(delta * np.sqrt(DIAS_ANO)), "erro_padrao": 0.0, "p_valor": np.nan, "n": int(T)}
    p = (np.sum(estat >= abs(delta) / se) + 1) / (reamostragens + 1)
    return {"delta_sharpe": float(delta * np.sqrt(DIAS_ANO)), "erro_padrao": float(se * np.sqrt(DIAS_ANO)),
            "p_valor": float(p), "n": int(T)}


# ─────────────────────────────────────────────────────────────────────
# Simulação
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Trajetoria:
    bruto: pd.Series
    negociado: pd.Series                 # sum |w - w^-| em cada dia de execução
    turnovers: list[float] = field(default_factory=list)
    n_ativos: list[int] = field(default_factory=list)

    def liquido(self, c: float) -> pd.Series:
        return self.bruto - c * self.negociado


def simular(retornos: pd.DataFrame, agenda: dict[int, pd.Series], inicio: int) -> Trajetoria:
    """agenda: {índice do pregão de execução: pesos (somam 1)}. Pesos derivam com os preços."""
    R = retornos.fillna(0.0).values
    cols = {t: i for i, t in enumerate(retornos.columns)}
    w = np.zeros(R.shape[1])
    bruto = np.zeros(len(R))
    negociado = np.zeros(len(R))
    tr, nat = [], []
    for k in range(inicio, len(R)):
        port = float(w @ R[k])
        bruto[k] = port
        if w.sum() > 0 and 1 + port > 0:
            w = w * (1 + R[k]) / (1 + port)
        if k in agenda:
            novo = np.zeros_like(w)
            for t, p in agenda[k].items():
                novo[cols[t]] = p
            negociado[k] = np.abs(novo - w).sum()
            tr.append(negociado[k] / 2)
            nat.append(int((novo > 0).sum()))
            w = novo
    idx = retornos.index[inicio:]
    return Trajetoria(pd.Series(bruto[inicio:], idx), pd.Series(negociado[inicio:], idx), tr, nat)


def _selic_diaria(indice: pd.DatetimeIndex) -> pd.Series:
    from market_data.models import KpiMacro
    linhas = list(KpiMacro.objects.exclude(selic__isnull=True).order_by("data_ref").values("data_ref", "selic"))
    if not linhas:
        s = pd.Series(10.0, index=indice)
    else:
        s = pd.Series([float(l["selic"]) for l in linhas],
                      index=pd.to_datetime([l["data_ref"] for l in linhas]))
        s = s[~s.index.duplicated(keep="last")]
        s = s.reindex(s.index.union(indice)).ffill().bfill().reindex(indice)
    return (1 + s / 100) ** (1 / DIAS_ANO) - 1


def _pesos(candidatos: pd.DataFrame, coluna: str, janela: pd.DataFrame, perfil: str,
           cfg: dict, rf: float, modelo: str | None) -> pd.Series:
    risco, retorno = FILTROS_POR_PERFIL.get(perfil, ("medio", "medio"))
    scores = candidatos.set_index("ticker")[coluna]
    res = otimizar(janela[scores.index], scores, modelo=modelo or MODELO_MAP[(risco, retorno)],
                   w_max=cfg["max_pct_ativo"], retorno_alvo=RETORNO_MAP[retorno],
                   volatilidade_maxima=RISCO_MAP[risco], taxa_livre_risco=rf)
    w = res.pesos[res.pesos > 1e-6]
    return w / w.sum()


def _jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def executar_backtest(precos: pd.DataFrame, perfis=("conservador", "intermediario", "arrojado"),
                      ibovespa: pd.Series | None = None, inicio: date | None = None,
                      fim: date | None = None, janela_cal: int = JANELA_CALIBRACAO,
                      janela_teste: int = JANELA_TESTE, custos=CUSTOS_PADRAO,
                      modelo: str | None = None, reamostragens: int = 2000,
                      progresso=None, volumes: pd.DataFrame | None = None) -> dict:
    """volumes: volume diário (ações) por ticker. Se informado, aplica em cada
    data o mesmo filtro de liquidez da plataforma, com os 126 pregões até t."""
    from django.conf import settings as cfg_django
    from scoring.services.precos import liquidez
    precos = precos.sort_index()
    precos.index = pd.to_datetime(precos.index)
    if fim:
        precos = precos[precos.index <= pd.Timestamp(fim)]
    retornos = precos.pct_change(fill_method=None).iloc[1:]
    precos = precos.iloc[1:]
    datas = precos.index
    if ibovespa is not None and not ibovespa.empty:
        ibovespa = ibovespa.copy()
        ibovespa.index = pd.to_datetime(ibovespa.index)
        ibovespa = ibovespa.reindex(datas.union(ibovespa.index)).ffill().reindex(datas)

    t0 = janela_cal - 1
    if inicio:
        t0 = max(t0, int(datas.searchsorted(pd.Timestamp(inicio))))
    decisoes = list(range(t0, len(datas) - 1, janela_teste))
    if not decisoes:
        raise ValueError(f"Histórico insuficiente: são necessários mais de {janela_cal} pregões "
                         f"(há {len(datas)}).")
    rf_d = _selic_diaria(datas)

    saida = {"periodo": {"inicio": datas[decisoes[0] + 1].date().isoformat(),
                         "fim": datas[-1].date().isoformat(),
                         "rebalanceamentos": len(decisoes),
                         "janela_calibracao": janela_cal, "janela_teste": janela_teste},
             "perfis": {}}

    for perfil in perfis:
        cfg = config_perfil(perfil)
        cfg_sem_macro = {**cfg, "pesos": {**cfg["pesos"], "macro": 0.0}}
        agendas = {e: {} for e in ("combinada", "fundamentalista", "estatistica", "igual")}
        jaccard_macro, n_cand_macro = [], []
        for n, t in enumerate(decisoes):
            d = datas[t].date()
            janela = precos.iloc[t - janela_cal + 1: t + 1]
            mercado = ibovespa.iloc[t - janela_cal + 1: t + 1] if ibovespa is not None and not ibovespa.empty else None
            estat = estatisticas_janela(janela, mercado)
            negociaveis = set(estat.index) & set(precos.columns[precos.iloc[t].notna()])
            if volumes is not None and not volumes.empty:
                liq = liquidez(precos.iloc[: t + 1], volumes.loc[volumes.index <= datas[t]])
                if not liq.empty:
                    ok_liq = liq[(liq["volume_financeiro"] >= cfg_django.LIQUIDEZ_MINIMA_DIARIA)
                                 & (liq["pregoes_com_negocio"] >= cfg_django.PREGOES_COM_NEGOCIO_MIN)].index
                    negociaveis &= set(ok_liq)
            df = calcular_scores_v2(perfil, data_ref=d, estat_externo=estat)
            if df.empty:
                continue
            df = df[df["ticker"].isin(negociaveis)]
            rf = float((1 + rf_d.iloc[t]) ** DIAS_ANO - 1)
            exec_idx = t + 1

            cand = selecionar_candidatos(df, perfil, cfg=cfg)
            if not cand.empty:
                agendas["combinada"][exec_idx] = _pesos(cand, "score_final", janela, perfil, cfg, rf, modelo)
                agendas["igual"][exec_idx] = pd.Series(1 / len(cand), index=cand["ticker"].values)

            for estrategia, coluna in (("fundamentalista", "score_micro"), ("estatistica", "score_estat")):
                alt = df.assign(score_final=df[coluna])
                c2 = selecionar_candidatos(alt, perfil, cfg=cfg)
                if not c2.empty:
                    agendas[estrategia][exec_idx] = _pesos(c2, "score_final", janela, perfil, cfg, rf, modelo)

            # contribuição da camada macro: mesma seleção, com e sem ela
            df_sm = calcular_scores_v2(perfil, data_ref=d, cfg=cfg_sem_macro, estat_externo=estat)
            df_sm = df_sm[df_sm["ticker"].isin(negociaveis)]
            c_sm = selecionar_candidatos(df_sm, perfil, cfg=cfg_sem_macro)
            jaccard_macro.append(_jaccard(cand["ticker"] if not cand.empty else [], c_sm["ticker"]))
            n_cand_macro.append((len(cand), len(c_sm)))
            if progresso:
                progresso(perfil, n + 1, len(decisoes), d)

        inicio_sim = decisoes[0] + 1
        traj = {e: simular(retornos, ag, inicio_sim) for e, ag in agendas.items() if ag}
        if ibovespa is not None and not ibovespa.empty:
            r_ibov = ibovespa.pct_change(fill_method=None).fillna(0).iloc[inicio_sim:]
            traj["ibovespa"] = Trajetoria(r_ibov, pd.Series(0.0, index=r_ibov.index))

        res_perfil = {"metricas": {}, "testes": {}, "series": {},
                      "carteiras": {e: {datas[k].date().isoformat(): {t: round(float(w), 4) for t, w in pesos.items()}
                                        for k, pesos in ag.items()} for e, ag in agendas.items()},
                      "camada_macro": {"jaccard_medio": float(np.mean(jaccard_macro)) if jaccard_macro else None,
                                       "candidatos_com_e_sem": n_cand_macro},
                      "n_ativos_medio": {e: float(np.mean(tj.n_ativos)) for e, tj in traj.items() if tj.n_ativos}}
        for c in custos:
            chave = f"{c:.4f}"
            res_perfil["metricas"][chave] = {
                e: metricas(tj.liquido(c), rf_d, tj.turnovers if e != "ibovespa" else None)
                for e, tj in traj.items()}
            res_perfil["series"][chave] = {e: tj.liquido(c) for e, tj in traj.items()}
            if "combinada" in traj:
                ex = {e: tj.liquido(c) - rf_d.reindex(tj.bruto.index) for e, tj in traj.items()}
                res_perfil["testes"][chave] = {
                    e: teste_sharpe_ledoit_wolf(ex["combinada"], ex[e], reamostragens=reamostragens)
                    for e in ex if e != "combinada"}
        saida["perfis"][perfil] = res_perfil
    return saida
