"""
Montagem da carteira recomendada: escores -> candidatos -> preços -> otimização.

Usado pela página interativa (otimizar_ajax) e pelo cálculo em lote
(comando atualizar_carteiras), para que os dois fluxos sejam consistentes.
"""
import pandas as pd

from scoring.services.motor_scoring_v2 import config_perfil, selecionar_candidatos
from scoring.services.otimizador import MIN_OBSERVACOES, otimizar
from django.conf import settings

from scoring.services.precos import liquidez, obter_precos, obter_volumes
from scoring.services.renda_fixa import PERDA_MAX_POR_PERFIL, montar_carteira_completa

# Filtros da página -> parâmetros (Quadro "Modelo de alocação e restrições" do TCC)
RETORNO_MAP = {"baixo": 0.08, "medio": 0.14, "alto": 0.22}
RISCO_MAP = {"baixo": 0.12, "medio": 0.20, "alto": None}
MODELO_MAP = {
    ("baixo", "baixo"): "risk_parity",
    ("baixo", "medio"): "markowitz",
    ("baixo", "alto"):  "markowitz",
    ("medio", "baixo"): "markowitz",
    ("medio", "medio"): "sharpe",
    ("medio", "alto"):  "sharpe",
    ("alto", "baixo"):  "sharpe",
    ("alto", "medio"):  "sharpe",
    ("alto", "alto"):   "score",
}

TAXA_LIVRE_RISCO_PADRAO = 0.10


class CarteiraVazia(Exception):
    """Nenhum ativo atende aos critérios do perfil/filtros."""


def taxa_livre_risco() -> float:
    """Selic meta mais recente (% a.a.) convertida para fração."""
    from market_data.models import KpiMacro
    macro = KpiMacro.objects.exclude(selic__isnull=True).order_by("-data_ref").first()
    return float(macro.selic) / 100 if macro else TAXA_LIVRE_RISCO_PADRAO


def parametros_dos_filtros(risco: str, retorno: str) -> dict:
    return {
        "modelo": MODELO_MAP.get((risco, retorno), "sharpe"),
        "retorno_alvo": RETORNO_MAP.get(retorno),
        "volatilidade_maxima": RISCO_MAP.get(risco),
    }



def _tem_historico(precos: pd.DataFrame | None, ticker: str) -> bool:
    return (precos is not None and ticker in precos.columns
            and int(precos[ticker].notna().sum()) >= MIN_OBSERVACOES)


def _iliquido(liq: pd.DataFrame, ticker: str) -> bool:
    """True se o ticker não atinge a liquidez mínima (sem dado de volume, não filtra)."""
    if liq is None or liq.empty or ticker not in liq.index:
        return False
    linha = liq.loc[ticker]
    return (linha["volume_financeiro"] < settings.LIQUIDEZ_MINIMA_DIARIA
            or linha["pregoes_com_negocio"] < settings.PREGOES_COM_NEGOCIO_MIN)


def _fmt_reais(v: float) -> str:
    return f"R$ {v / 1e6:.1f} mi".replace(".", ",") if v >= 1e6 else f"R$ {v / 1e3:.0f} mil"


def _candidatos_negociaveis(df_scores, perfil, setor_filtro, precos=None, rodadas: int = 4):
    """
    Seleciona os `qtd_ativos` melhores do ranking QUE TÊM histórico de preços.

    Muitas empresas com balanço na CVM não têm cotação no Yahoo (deslistadas,
    sem negociação, ticker diferente). Antes, elas eram escolhidas e depois
    descartadas pelo otimizador, e a carteira ficava com poucos ativos. Agora
    elas são puladas e o próximo do ranking entra no lugar.
    Retorna (candidatos, precos, tickers_sem_preco).
    """
    cfg = config_perfil(perfil)
    qtd = cfg["qtd_ativos"]
    ranking = selecionar_candidatos(df_scores, perfil, setor_filtro, cfg={**cfg, "qtd_ativos": 10 ** 6},
                                    um_por_empresa=False)
    if ranking.empty:
        return ranking, precos, []
    empresa = (dict(zip(ranking["ticker"], ranking["empresa"])) if "empresa" in ranking.columns
               else {t: t for t in ranking["ticker"]})

    def um_por_empresa(tickers):
        vistos, saida = set(), []
        for t in tickers:
            if empresa[t] not in vistos:
                vistos.add(empresa[t])
                saida.append(t)
        return saida

    if precos is not None:          # preços fornecidos (testes, backtest)
        ok = um_por_empresa([t for t in ranking["ticker"] if _tem_historico(precos, t)])
        sem = [t for t in ranking["ticker"].head(qtd) if t not in ok]
        escolhidos = ok[:qtd]
        if not escolhidos:          # nenhum preço: mantém o ranking e o otimizador aloca pelo escore
            return ranking[ranking["ticker"].isin(um_por_empresa(ranking["ticker"])[:qtd])], precos, []
        return ranking[ranking["ticker"].isin(escolhidos)], precos[escolhidos], sem

    lote = max(qtd * 2, 20)
    testados, ok, sem, quadros, iliquidos = 0, [], [], [], []
    for _ in range(rodadas):
        bloco = ranking["ticker"].iloc[testados: testados + lote].tolist()
        if not bloco:
            break
        testados += len(bloco)
        p = obter_precos(bloco)
        liq = liquidez(p, obter_volumes(bloco)) if p is not None and not p.empty else None
        if p is not None and not p.empty:
            quadros.append(p)
        for t in bloco:
            if _tem_historico(p, t) and not _iliquido(liq, t):
                if empresa[t] not in {empresa[x] for x in ok}:
                    ok.append(t)
            elif len(ok) < qtd:          # só reporta os que teriam entrado na carteira
                if _tem_historico(p, t):
                    iliquidos.append(f"{t} ({_fmt_reais(liq.loc[t, 'volume_financeiro'])}/dia)")
                else:
                    sem.append(t)
        if len(ok) >= qtd:
            break
    escolhidos = ok[:qtd]
    if not escolhidos:              # Yahoo fora do ar: aloca pelo escore, sem descartar ninguém
        topo = ranking[ranking["ticker"].isin(um_por_empresa(ranking["ticker"])[:qtd])]
        return topo, (pd.concat(quadros, axis=1) if quadros else pd.DataFrame()), []
    precos = pd.concat(quadros, axis=1)
    precos = precos.loc[:, ~precos.columns.duplicated()][escolhidos]
    if iliquidos:
        sem = sem + ["__ILIQ__" + x for x in iliquidos]
    return ranking[ranking["ticker"].isin(escolhidos)], precos, sem


def montar_carteira_otimizada(
    df_scores: pd.DataFrame,
    perfil: str,
    modelo: str = "sharpe",
    retorno_alvo: float | None = None,
    volatilidade_maxima: float | None = None,
    setor_filtro: str | None = None,
    calcular_fronteira: bool = True,
    precos: pd.DataFrame | None = None,
    rf: float | None = None,
) -> dict:
    """
    Retorna:
      alocacao         : {ticker: % (0-100)}, ordenado do maior para o menor
      scores_por_ativo : {ticker: {score_final, score_micro, score_macro, score_estat, setor}}
      metricas         : {retorno, volatilidade, sharpe, var95}
      modelo_usado     : modelo efetivamente aplicado (pode cair para 'score')
      aviso            : texto para o usuário ou None
      fronteira        : [{vol, ret}, ...]
    """
    candidatos, precos, sem_preco = _candidatos_negociaveis(df_scores, perfil, setor_filtro, precos)
    if candidatos.empty:
        msg = "Nenhum ativo atende aos critérios do seu perfil"
        if sem_preco:
            msg += " com histórico de preços e liquidez suficientes"
        raise CarteiraVazia(msg + (f" no setor {setor_filtro}." if setor_filtro else "."))

    scores = candidatos.set_index("ticker")["score_final"]

    res = otimizar(
        precos, scores, modelo=modelo,
        w_max=config_perfil(perfil)["max_pct_ativo"],
        retorno_alvo=retorno_alvo,
        volatilidade_maxima=volatilidade_maxima,
        taxa_livre_risco=taxa_livre_risco() if rf is None else rf,
        calcular_fronteira=calcular_fronteira,
    )

    pesos = res.pesos[res.pesos > 0.0005].sort_values(ascending=False)
    alocacao = {t: round(float(p) * 100, 2) for t, p in pesos.items()}

    info = candidatos.set_index("ticker")[
        ["score_final", "score_micro", "score_macro", "score_estat", "setor"]]
    scores_por_ativo = {
        t: {k: (round(float(v), 1) if k != "setor" else v) for k, v in row.items()}
        for t, row in info.iterrows()
    }

    aviso = res.aviso
    sem_hist = sorted(t for t in sem_preco if not t.startswith("__ILIQ__"))
    pouca_liq = [t.replace("__ILIQ__", "") for t in sem_preco if t.startswith("__ILIQ__")]
    notas = []
    if sem_hist:
        notas.append("Sem histórico de preços no Yahoo Finance (substituídos pelos próximos do ranking): "
                     + ", ".join(sem_hist) + ".")
    if pouca_liq:
        notas.append(f"Pouca negociação, abaixo de {_fmt_reais(settings.LIQUIDEZ_MINIMA_DIARIA)}/dia "
                     "(substituídos): " + ", ".join(pouca_liq) + ".")
    for nota in notas:
        aviso = f"{aviso} {nota}" if aviso else nota

    return {
        "alocacao": alocacao,
        "scores_por_ativo": scores_por_ativo,
        "metricas": res.metricas,
        "modelo_usado": res.modelo_usado,
        "aviso": aviso,
        "fronteira": res.fronteira,
        "precos": precos,          # usado pela análise estatística/macro (não vai para o JSON)
    }


def recalcular_carteiras(perfis=None) -> dict:
    """
    Recalcula e salva a carteira recomendada (fluxo em lote da home) de cada perfil.
    Usado pelo comando atualizar_carteiras e pela ação do painel administrativo.
    Retorna {perfil: texto do resultado}.
    """
    from django.db import transaction

    from scoring.models import CarteiraRecomendada
    from scoring.services.motor_scoring_v2 import calcular_scores_v2, obter_perfis

    resultado = {}
    for perfil in perfis or list(obter_perfis()):
        df = calcular_scores_v2(perfil)
        try:
            res = montar_carteira_otimizada(df, perfil, modelo="sharpe", calcular_fronteira=False)
        except CarteiraVazia as e:
            resultado[perfil] = f"sem carteira: {e}"
            continue
        completa = montar_carteira_completa(res, PERDA_MAX_POR_PERFIL.get(perfil), taxa_livre_risco())
        composicao = [{**item, **res["scores_por_ativo"].get(item["ticker"], {})}
                      for item in completa["itens"]]
        metricas = {**completa["metricas_totais"], "pct_renda_fixa": completa["pct_renda_fixa"],
                    "pct_acoes": completa["pct_acoes"], "acoes": completa["metricas_acoes"]}
        with transaction.atomic():
            CarteiraRecomendada.objects.filter(perfil=perfil, ativo=True).update(ativo=False)
            CarteiraRecomendada.objects.create(perfil=perfil, composicao=composicao, ativo=True,
                                               metricas=metricas, modelo_usado=res["modelo_usado"])
        aviso = f" (aviso: {res['aviso']})" if res["aviso"] else ""
        n_acoes = sum(1 for i in completa["itens"] if i["tipo"] == "acao")
        resultado[perfil] = (f"{completa['pct_renda_fixa']:.0f}% renda fixa + {n_acoes} ações, "
                             f"modelo {res['modelo_usado']}{aviso}")
    return resultado
