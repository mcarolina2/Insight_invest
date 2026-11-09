"""
Análise estatística e sensibilidade macroeconômica da carteira do usuário.

Tudo aqui é DESCRITIVO: mostra como a carteira recomendada (pesos atuais,
incluindo a parcela de renda fixa) teria se comportado no histórico de preços e
como seus retornos mensais se relacionaram com as variáveis macroeconômicas.
Nada disso entra no escore nem no backtest.

  analisar_carteira(precos, pesos_acoes, pct_renda_fixa, rf, scores) -> dict
     "estatistica": distribuição dos retornos diários, VaR/CVaR históricos,
                    assimetria, curtose, Jarque-Bera, retorno acumulado,
                    drawdown e correlação entre as ações
     "macro"      : para cada variável (IPCA, variação da Selic, balança
                    comercial, atividade - IBC-Br, câmbio), correlação, beta,
                    p-valor e pontos do gráfico de dispersão; correlação de cada
                    ação com cada variável, ao lado dos escores
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

DIAS_UTEIS_ANO = 252
MIN_DIAS = 60
MIN_MESES = 12
NIVEL_SIGNIFICANCIA = 0.05

# campo calculado -> (rótulo, unidade da variável, frase do "a cada")
VARIAVEIS_MACRO = {
    "ipca":      ("Inflação (IPCA mensal)", "% no mês", "1 p.p. a mais de IPCA no mês"),
    "selic_var": ("Variação da Selic", "p.p. no mês", "1 p.p. de alta da Selic no mês"),
    "balanca":   ("Balança comercial", "US$ bi no mês", "US$ 1 bi a mais de saldo comercial"),
    "atividade": ("Atividade (IBC-Br)", "% no mês", "1% a mais de crescimento do IBC-Br"),
    "dolar":     ("Câmbio (R$/US$)", "% no mês", "1% de alta do dólar"),
}


def _f(x, casas=4):
    """float JSON-seguro (NaN/inf viram None)."""
    if x is None:
        return None
    x = float(x)
    return round(x, casas) if math.isfinite(x) else None


# ─────────────────────────────────────────────────────────────────────
# Séries da carteira
# ─────────────────────────────────────────────────────────────────────

def retornos_diarios(precos: pd.DataFrame, pesos_acoes: dict, pct_renda_fixa: float,
                     rf: float) -> tuple[pd.Series, pd.DataFrame]:
    """
    Retorno diário da carteira total com pesos constantes (rebalanceamento diário).
    pesos_acoes: {ticker: fração da carteira TOTAL}; pct_renda_fixa em 0-100.
    """
    tickers = [t for t in pesos_acoes if t in precos.columns]
    if not tickers:
        return pd.Series(dtype=float), pd.DataFrame()
    r = precos[tickers].sort_index().pct_change().iloc[1:]
    r = r.dropna(how="all").fillna(0.0)
    w = np.array([pesos_acoes[t] for t in tickers], dtype=float)
    w_rf = pct_renda_fixa / 100.0
    # renormaliza para somar 1 caso algum ticker não tenha preço
    total = w.sum() + w_rf
    if total <= 0:
        return pd.Series(dtype=float), r
    w, w_rf = w / total, w_rf / total
    rf_dia = (1 + (rf or 0.0)) ** (1 / DIAS_UTEIS_ANO) - 1
    carteira = r.values @ w + w_rf * rf_dia
    return pd.Series(carteira, index=r.index, name="carteira"), r


def _histograma(r: pd.Series, bins: int = 30) -> dict:
    x = r.values * 100
    cont, bordas = np.histogram(x, bins=bins)
    largura = bordas[1] - bordas[0]
    media, desvio = x.mean(), x.std(ddof=1)
    centros = (bordas[:-1] + bordas[1:]) / 2
    xs = np.linspace(bordas[0], bordas[-1], 80)
    normal = stats.norm.pdf(xs, media, desvio) * len(x) * largura if desvio > 0 else np.zeros_like(xs)
    return {
        "centros": [_f(c, 3) for c in centros],
        "contagens": [int(c) for c in cont],
        "largura": _f(largura, 4),
        "normal_x": [_f(v, 3) for v in xs],
        "normal_y": [_f(v, 3) for v in normal],
    }


def estatistica_carteira(r: pd.Series, r_ativos: pd.DataFrame, rf: float) -> dict:
    if len(r) < MIN_DIAS:
        return {"disponivel": False,
                "motivo": "Histórico de preços insuficiente para a análise estatística."}

    q05 = float(r.quantile(0.05))
    cauda = r[r <= q05]
    jb = stats.jarque_bera(r.values)
    media, desvio = float(r.mean()), float(r.std(ddof=1))
    var_normal = media - 1.645 * desvio

    acumulado = (1 + r).cumprod()
    pico = acumulado.cummax()
    drawdown = acumulado / pico - 1
    semanal = pd.DataFrame({"acum": acumulado, "dd": drawdown}).resample("W").last().dropna()
    rf_semana = (1 + (rf or 0.0)) ** (1 / 52) - 1
    so_rf = (1 + rf_semana) ** np.arange(1, len(semanal) + 1)

    pior_dia = r.idxmin()
    fundo = drawdown.idxmin()
    anos = len(r) / DIAS_UTEIS_ANO
    total = float(acumulado.iloc[-1] - 1)

    corr = r_ativos.corr() if r_ativos.shape[1] >= 2 else pd.DataFrame()
    return {
        "disponivel": True,
        "n_dias": int(len(r)),
        "inicio": r.index[0].date().isoformat(),
        "fim": r.index[-1].date().isoformat(),
        "media_diaria": _f(media, 5),
        "desvio_diario": _f(desvio, 5),
        "var95_hist": _f(q05, 4),
        "cvar95_hist": _f(cauda.mean(), 4),
        "var95_normal": _f(var_normal, 4),
        "assimetria": _f(stats.skew(r.values), 3),
        "curtose_excesso": _f(stats.kurtosis(r.values), 3),
        "jarque_bera_p": _f(jb.pvalue, 4),
        "normal_rejeitada": bool(jb.pvalue < NIVEL_SIGNIFICANCIA),
        "pior_dia": {"data": pior_dia.date().isoformat(), "retorno": _f(r.min(), 4)},
        "melhor_dia": {"data": r.idxmax().date().isoformat(), "retorno": _f(r.max(), 4)},
        "dias_positivos": _f((r > 0).mean(), 3),
        "retorno_total": _f(total, 4),
        "retorno_anualizado": _f((1 + total) ** (1 / anos) - 1 if anos > 0 and total > -1 else None, 4),
        "drawdown_max": _f(drawdown.min(), 4),
        "data_drawdown_max": fundo.date().isoformat(),
        "histograma": _histograma(r),
        "serie": {
            "datas": [d.date().isoformat() for d in semanal.index],
            "acumulado": [_f(v - 1, 4) for v in semanal["acum"]],
            "so_renda_fixa": [_f(v - 1, 4) for v in so_rf],
            "drawdown": [_f(v, 4) for v in semanal["dd"]],
        },
        "correlacao": {
            "tickers": list(corr.columns),
            "matriz": [[_f(v, 2) for v in linha] for linha in corr.values],
            "media": _f(corr.values[np.triu_indices(len(corr), 1)].mean(), 3) if len(corr) >= 2 else None,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Macroeconomia
# ─────────────────────────────────────────────────────────────────────

def macro_mensal() -> pd.DataFrame:
    """
    Variáveis macro em frequência mensal (índice = período mensal).
    KpiMacro mistura linhas diárias (Selic) e mensais (IPCA, balança, IBC-Br);
    o último valor disponível no mês representa o mês.
    """
    from market_data.models import KpiMacro

    campos = ["data_ref", "selic", "ipca_mensal", "exportacoes", "importacoes",
              "balanca_comercial", "ibc_br", "dolar_brl"]
    linhas = list(KpiMacro.objects.order_by("data_ref").values(*campos))
    if not linhas:
        return pd.DataFrame()
    df = pd.DataFrame(linhas)
    df["data_ref"] = pd.to_datetime(df["data_ref"])
    df = df.set_index("data_ref").apply(pd.to_numeric, errors="coerce")
    m = df.resample("ME").last()
    m.index = m.index.to_period("M")

    balanca = m["balanca_comercial"].copy()
    calculada = m["exportacoes"] - m["importacoes"]
    balanca = balanca.fillna(calculada)

    saida = pd.DataFrame(index=m.index)
    saida["ipca"] = m["ipca_mensal"]
    saida["selic_var"] = m["selic"].diff()
    saida["balanca"] = balanca / 1000.0          # BCB divulga em US$ milhões
    saida["atividade"] = m["ibc_br"].pct_change(fill_method=None) * 100
    saida["dolar"] = m["dolar_brl"].pct_change(fill_method=None) * 100
    return saida


def _mensal(r_diario: pd.Series | pd.DataFrame):
    m = (1 + r_diario).resample("ME").prod() - 1
    m.index = m.index.to_period("M")
    # descarta o mês corrente se estiver incompleto (menos de 15 pregões)
    contagem = r_diario.resample("ME").count()
    if isinstance(contagem, pd.DataFrame):
        contagem = contagem.max(axis=1)
    contagem.index = contagem.index.to_period("M")
    return m[contagem >= 15] * 100


def _br(x, casas=2):
    return f"{x:.{casas}f}".replace(".", ",")


def _mes_br(periodo) -> str:
    return f"{periodo.month:02d}/{periodo.year}"


def _interpretar(nome, frase, beta, p, r):
    if beta is None:
        return "Dados insuficientes."
    forca = abs(r)
    adjetivo = "fraca" if forca < 0.3 else "moderada" if forca < 0.6 else "forte"
    sentido = "maior" if beta > 0 else "menor"
    signif = (f"estatisticamente significativa (p = {_br(p, 3)})" if p < NIVEL_SIGNIFICANCIA
              else f"não significativa a 5% (p = {_br(p)}), podendo ser fruto do acaso")
    return (f"{frase[0].upper() + frase[1:]} esteve associado, em média, a um retorno da carteira "
            f"{_br(abs(beta))} p.p. {sentido} no mês. Relação {adjetivo} "
            f"(correlação {'+' if r >= 0 else '−'}{_br(abs(r))}), {signif}.")


def sensibilidade_macro(r_carteira: pd.Series, r_ativos: pd.DataFrame, scores: dict) -> dict:
    macro = macro_mensal()
    if macro.empty:
        return {"disponivel": False, "motivo": "Sem dados macroeconômicos carregados."}

    rc = _mensal(r_carteira)
    ra = _mensal(r_ativos) if not r_ativos.empty else pd.DataFrame()

    variaveis = []
    for campo, (rotulo, unidade, frase) in VARIAVEIS_MACRO.items():
        par = pd.concat([rc.rename("carteira"), macro[campo]], axis=1, join="inner").dropna()
        item = {"campo": campo, "rotulo": rotulo, "unidade": unidade, "n_meses": int(len(par))}
        if len(par) < MIN_MESES or par[campo].std() == 0:
            item.update({"disponivel": False,
                         "motivo": f"Só {len(par)} meses com dados (mínimo {MIN_MESES})."})
            variaveis.append(item)
            continue
        reg = stats.linregress(par[campo].values, par["carteira"].values)
        item.update({
            "disponivel": True,
            "beta": _f(reg.slope, 3),
            "intercepto": _f(reg.intercept, 3),
            "correlacao": _f(reg.rvalue, 3),
            "r2": _f(reg.rvalue ** 2, 3),
            "p_valor": _f(reg.pvalue, 4),
            "significativo": bool(reg.pvalue < NIVEL_SIGNIFICANCIA),
            "pontos": [{"mes": _mes_br(i), "x": _f(x, 3), "y": _f(y, 3)}
                       for i, x, y in zip(par.index, par[campo], par["carteira"])],
            "interpretacao": _interpretar(rotulo, frase, reg.slope, reg.pvalue, reg.rvalue),
        })
        variaveis.append(item)

    # correlação de cada ação com cada variável, ao lado dos escores
    por_ativo = []
    for t in ra.columns:
        linha = {"ticker": t,
                 "score_micro": (scores.get(t) or {}).get("score_micro"),
                 "score_estat": (scores.get(t) or {}).get("score_estat"),
                 "score_final": (scores.get(t) or {}).get("score_final"),
                 "setor": (scores.get(t) or {}).get("setor"),
                 "correlacoes": {}}
        for campo in VARIAVEIS_MACRO:
            par = pd.concat([ra[t].rename("a"), macro[campo]], axis=1, join="inner").dropna()
            if len(par) >= MIN_MESES and par[campo].std() > 0 and par["a"].std() > 0:
                c, p = stats.pearsonr(par[campo], par["a"])
                linha["correlacoes"][campo] = {"r": _f(c, 2), "p": _f(p, 3)}
            else:
                linha["correlacoes"][campo] = None
        por_ativo.append(linha)

    disponiveis = [v for v in variaveis if v.get("disponivel")]
    if not disponiveis:
        return {"disponivel": False, "variaveis": variaveis,
                "motivo": "Os meses com dados macro não coincidem com o histórico de preços "
                          f"(mínimo de {MIN_MESES} meses em comum)."}

    mais_relevante = max(disponiveis, key=lambda v: abs(v["correlacao"]))
    return {
        "disponivel": True,
        "variaveis": variaveis,
        "por_ativo": por_ativo,
        "destaque": mais_relevante["campo"],
        "nota": ("Correlação não é causalidade: os números descrevem como os retornos mensais "
                 "dos pesos atuais se moveram junto com cada indicador no mesmo mês. "
                 "Esta análise não altera o escore nem a alocação."),
    }


# ─────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────

def analisar_carteira(precos: pd.DataFrame | None, pesos_acoes: dict, pct_renda_fixa: float,
                      rf: float, scores: dict | None = None) -> dict:
    """pesos_acoes: {ticker: fração (0-1) da carteira total}."""
    if (precos is None or precos.empty) and pesos_acoes:
        msg = "Sem histórico de preços para analisar a carteira."
        return {"estatistica": {"disponivel": False, "motivo": msg},
                "macro": {"disponivel": False, "motivo": msg}}
    precos = precos.copy() if precos is not None else pd.DataFrame()
    if not pesos_acoes and pct_renda_fixa >= 99.99:
        msg = ("Sua carteira está 100% em renda fixa (Tesouro Selic / CDB): ela acompanha a Selic "
               "e não oscila com a bolsa, por isso não há distribuição de retornos nem sensibilidade "
               "macro das ações para mostrar.")
        return {"estatistica": {"disponivel": False, "motivo": msg},
                "macro": {"disponivel": False, "motivo": msg}}
    precos.index = pd.to_datetime(precos.index)
    r, r_ativos = retornos_diarios(precos, pesos_acoes, pct_renda_fixa, rf)
    if r.empty:
        msg = "Sem histórico de preços para os ativos da carteira."
        return {"estatistica": {"disponivel": False, "motivo": msg},
                "macro": {"disponivel": False, "motivo": msg}}
    return {
        "estatistica": estatistica_carteira(r, r_ativos, rf),
        "macro": sensibilidade_macro(r, r_ativos, scores or {}),
    }
