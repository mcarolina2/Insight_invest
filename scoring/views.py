import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from users.models import perfil_do_usuario
from users.views import perfil_obrigatorio

logger = logging.getLogger(__name__)

OPCOES = {"baixo", "medio", "alto"}


def _setores_disponiveis(df_scores, perfil):
    """Todos os setores com ativo elegível, em ordem alfabética, com a contagem.
    Como dá para escolher vários setores juntos, setor com 1 ativo também entra."""
    from scoring.services.motor_scoring_v2 import config_perfil
    if df_scores.empty:
        return []
    elegiveis = df_scores[(df_scores["score_final"] >= config_perfil(perfil)["score_min"])
                          & (df_scores["setor"] != "")]
    if "empresa" in elegiveis.columns:          # conta empresas, não tickers
        elegiveis = elegiveis.drop_duplicates("empresa")
    contagem = elegiveis["setor"].value_counts()
    return [{"nome": n, "qtd": int(q)} for n, q in sorted(contagem.items(), key=lambda x: x[0].lower())]


@perfil_obrigatorio
def carteira_view(request):
    """Página da carteira interativa. A composição é carregada via otimizar_ajax."""
    from market_data.models import KpiMacro
    from scoring.services.motor_scoring_v2 import calcular_scores_v2, config_perfil

    perfil = perfil_do_usuario(request.user)
    df = calcular_scores_v2(perfil)
    cfg = config_perfil(perfil)
    return render(request, "scoring/carteira.html", {
        "perfil": perfil,
        "setores": _setores_disponiveis(df, perfil),
        "qtd_ativos": cfg["qtd_ativos"],
        "max_pct": int(cfg["max_pct_ativo"] * 100),
        "qtd_opcoes": list(range(QTD_MIN, QTD_MAX + 1)),
        "horizontes": HORIZONTES,
        "horizonte_padrao": HORIZONTE_PADRAO,
        "macro": __import__("scoring.services.macro", fromlist=["x"]).macro_consolidado(),
    })


QTD_MIN, QTD_MAX = 3, 20
HORIZONTES = (1, 2, 3, 5)
HORIZONTE_PADRAO = 3


@perfil_obrigatorio
@require_POST
def otimizar_ajax(request):
    """
    POST JSON: {"risco": "baixo|medio|alto", "retorno": "baixo|medio|alto",
                "setores": [<setor>, ...] (vazio = todos; aceita também "setor": "<setor>"),
                "qtd_ativos": 3-20 (opcional), "horizonte": 1|2|3|5 anos de simulação}

    Resposta: {
      "alocacao": [{"ticker", "pct", "score", "score_micro", "score_estat", "setor"}, ...],
      "metricas": {"retorno", "volatilidade", "sharpe", "var95"},
      "modelo": "risk_parity|markowitz|sharpe|score",
      "aviso": str | null,
      "fronteira": [{"vol", "ret"}, ...]
    }
    """
    from scoring.services.carteira import (CarteiraVazia, montar_carteira_otimizada,
                                           parametros_dos_filtros)
    from scoring.services.motor_scoring_v2 import calcular_scores_v2

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erro": "JSON inválido."}, status=400)

    risco = body.get("risco", "medio")
    retorno = body.get("retorno", "medio")
    if risco not in OPCOES or retorno not in OPCOES:
        return JsonResponse({"erro": "Filtro de risco/retorno inválido."}, status=400)

    # Setores: lista (vários) ou o campo antigo "setor" (um só); vazio/"todos" = todos.
    setores = body.get("setores")
    if setores is None:
        setores = [body.get("setor")] if body.get("setor") not in (None, "", "todos") else []
    if not isinstance(setores, list) or not all(isinstance(x, str) for x in setores):
        return JsonResponse({"erro": "Filtro de setor inválido."}, status=400)
    setores = [x for x in setores if x and x != "todos"][:30]

    # Número máximo de ações (padrão do perfil) e período da simulação (anos).
    try:
        qtd_ativos = int(body["qtd_ativos"]) if body.get("qtd_ativos") not in (None, "") else None
    except (TypeError, ValueError):
        return JsonResponse({"erro": "Número de ações inválido."}, status=400)
    if qtd_ativos is not None and not QTD_MIN <= qtd_ativos <= QTD_MAX:
        return JsonResponse({"erro": f"Escolha entre {QTD_MIN} e {QTD_MAX} ações."}, status=400)
    try:
        horizonte = int(body.get("horizonte") or HORIZONTE_PADRAO)
    except (TypeError, ValueError):
        horizonte = 0
    if horizonte not in HORIZONTES:
        return JsonResponse({"erro": "Período inválido."}, status=400)

    perfil = perfil_do_usuario(request.user)
    params = parametros_dos_filtros(risco, retorno)

    try:
        df = calcular_scores_v2(perfil)
        res = montar_carteira_otimizada(
            df, perfil, setor_filtro=setores or None, qtd_ativos=qtd_ativos, **params)
    except CarteiraVazia as e:
        return JsonResponse({"erro": str(e)}, status=422)
    except Exception:
        logger.exception("Erro ao otimizar carteira")
        return JsonResponse({"erro": "Erro ao calcular a carteira. Tente novamente."}, status=500)

    from scoring.services.carteira import taxa_livre_risco
    from scoring.services.renda_fixa import montar_carteira_completa, perda_maxima_do_usuario
    from users.models import PerfilRisco

    rf = taxa_livre_risco()
    perda_max = perda_maxima_do_usuario(PerfilRisco.objects.filter(user=request.user).first(), perfil)
    completa = montar_carteira_completa(res, perda_max, rf)

    sp = res["scores_por_ativo"]
    alocacao = []
    for item in completa["itens"]:
        if item["tipo"] == "renda_fixa":
            alocacao.append({"ticker": item["ticker"], "tipo": "renda_fixa", "pct": item["pct"],
                             "setor": item["descricao"], "score": None,
                             "score_micro": None, "score_estat": None})
        else:
            t = item["ticker"]
            alocacao.append({"ticker": t, "tipo": "acao", "pct": item["pct"],
                             "pct_nas_acoes": item["pct_nas_acoes"],
                             "score": sp[t]["score_final"], "score_micro": sp[t]["score_micro"],
                             "score_estat": sp[t]["score_estat"], "setor": sp[t]["setor"]})

    from scoring.services.analise_carteira import analisar_carteira
    try:
        pesos_acoes = {a["ticker"]: a["pct"] / 100 for a in alocacao if a["tipo"] == "acao"}
        precos_an, ibov = _precos_para_analise(res.get("precos"), list(pesos_acoes), horizonte)
        analise = analisar_carteira(precos_an, pesos_acoes, completa["pct_renda_fixa"], rf, sp,
                                    ibovespa=ibov, horizonte_anos=horizonte)
    except Exception:
        logger.exception("Erro na análise estatística/macro da carteira")
        msg = "Não foi possível gerar esta análise agora."
        analise = {"estatistica": {"disponivel": False, "motivo": msg},
                   "macro": {"disponivel": False, "motivo": msg}}

    return JsonResponse({
        "perfil": perfil,
        "analise": analise,
        "alocacao": alocacao,
        "metricas": completa["metricas_totais"],
        "metricas_acoes": completa["metricas_acoes"],
        "pct_acoes": completa["pct_acoes"],
        "pct_renda_fixa": completa["pct_renda_fixa"],
        "perda_max": perda_max,
        "taxa_livre_risco": rf,
        "modelo": res["modelo_usado"],
        "aviso": res["aviso"],
        "fronteira": res["fronteira"],
        "qtd_ativos": res.get("qtd_ativos"),
        "max_pct": res.get("max_pct"),
        "horizonte": horizonte,
    })


def _precos_para_analise(precos, tickers: list, horizonte: int):
    """
    Preços e Ibovespa para a análise da carteira no período escolhido.
    A carteira é sempre estimada com 3 anos (metodologia); só a SIMULAÇÃO usa o
    período escolhido. Para 5 anos é preciso baixar um histórico mais longo.
    """
    from scoring.services.precos import obter_ibovespa, obter_precos
    anos = max(HORIZONTE_PADRAO, horizonte)
    if anos > HORIZONTE_PADRAO and tickers:
        longos = obter_precos(tickers, anos=anos)
        if longos is not None and not longos.empty:
            precos = longos
    try:
        ibov = obter_ibovespa(anos=anos)
    except Exception:
        logger.warning("Ibovespa indisponível para a comparação da carteira")
        ibov = None
    return precos, ibov
