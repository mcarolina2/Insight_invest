import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from users.models import perfil_do_usuario
from users.views import perfil_obrigatorio

logger = logging.getLogger(__name__)

OPCOES = {"baixo", "medio", "alto"}
MAX_SETORES_FILTRO = 8


def _setores_disponiveis(df_scores, perfil):
    """Setores com pelo menos 2 ativos elegíveis (mínimo para otimizar)."""
    from scoring.services.motor_scoring_v2 import config_perfil
    if df_scores.empty:
        return []
    elegiveis = df_scores[(df_scores["score_final"] >= config_perfil(perfil)["score_min"])
                          & (df_scores["setor"] != "")]
    contagem = elegiveis["setor"].value_counts()
    return contagem[contagem >= 2].index[:MAX_SETORES_FILTRO].tolist()


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
        "macro": KpiMacro.objects.order_by("-data_ref").first(),
    })


@perfil_obrigatorio
@require_POST
def otimizar_ajax(request):
    """
    POST JSON: {"risco": "baixo|medio|alto", "retorno": "baixo|medio|alto", "setor": "todos|<setor>"}

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
    setor = body.get("setor") or "todos"
    if risco not in OPCOES or retorno not in OPCOES:
        return JsonResponse({"erro": "Filtro de risco/retorno inválido."}, status=400)

    perfil = perfil_do_usuario(request.user)
    params = parametros_dos_filtros(risco, retorno)

    try:
        df = calcular_scores_v2(perfil)
        res = montar_carteira_otimizada(
            df, perfil, setor_filtro=None if setor == "todos" else setor, **params)
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
        analise = analisar_carteira(res.get("precos"), pesos_acoes,
                                    completa["pct_renda_fixa"], rf, sp)
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
    })
