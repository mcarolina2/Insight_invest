import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db.models import F


@login_required
def carteira_view(request):
    """
    Renderiza a tela principal da carteira com os filtros de risco/retorno.
    Os dados iniciais são carregados com o perfil padrão do usuário.
    """
    user   = request.user
    perfil = user.perfil_risco.tipo if user.perfil_risco else "intermediario"

    # Carrega a recomendação mais recente (se existir)
    from scoring.models import Recomendacao
    rec = (Recomendacao.objects.filter(user=user, status="ativa")
           .prefetch_related("itens__ativo", "itens__score_ativo_ref")
           .order_by("-criado_em").first())

    itens = []
    if rec:
        for item in rec.itens.select_related("ativo", "score_ativo_ref").all():
            s = item.score_ativo_ref
            itens.append({
                "ticker":      item.ativo.ticker,
                "nome":        item.ativo.nome,
                "setor":       item.ativo.setor or "",
                "percentual":  float(item.percentual_ideal),
                "tipo":        item.tipo,
                "score_final": float(s.score_final)  if s else 50,
                "score_micro": float(s.score_micro)  if s else 50,
                "score_time":  float(s.score_time)   if s else 50,
            })

    from market_data.models import KpiMacro
    macro = KpiMacro.objects.order_by("-data_ref").first()

    return render(request, "scoring/carteira.html", {
        "usuario":    user,
        "perfil":     perfil,
        "itens_json": json.dumps(itens),
        "recomendacao": rec,
        "macro":      macro,
    })


@login_required
@require_POST
def otimizar_ajax(request):
    """
    Endpoint AJAX — recebe filtros e retorna nova alocação.

    Body JSON esperado:
    {
      "risco":   "baixo" | "medio" | "alto",
      "retorno": "baixo" | "medio" | "alto",
      "setor":   "todos" | "Financeiro" | "Energia" | ...
    }

    Resposta JSON:
    {
      "alocacao": [{"ticker": "PETR4", "pct": 14.2, "score": 68, "setor": "Energia"}, ...],
      "metricas": {"retorno": 0.12, "volatilidade": 0.18, "sharpe": 0.67, "var95": -0.08},
      "modelo":   "sharpe"
    }
    """
    try:
        body   = json.loads(request.body)
        risco  = body.get("risco",   "medio")
        retorno= body.get("retorno", "medio")
        setor  = body.get("setor",   "todos")
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({"erro": "JSON inválido"}, status=400)

    user   = request.user
    perfil = user.perfil_risco.tipo if user.perfil_risco else "intermediario"

    # Converte filtros para parâmetros numéricos
    RETORNO_MAP = {
        "baixo": 0.08,   # CDI + 2%
        "medio": 0.14,   # Selic + 2%
        "alto":  0.22,   # retorno agressivo
    }
    RISCO_MAP = {
        "baixo": 0.12,   # volatilidade anual máxima 12%
        "medio": 0.20,
        "alto":  None,   # sem limite de volatilidade
    }
    MODELO_MAP = {
        ("baixo",  "baixo"):  "risk_parity",
        ("baixo",  "medio"):  "markowitz",
        ("baixo",  "alto"):   "markowitz",
        ("medio",  "baixo"):  "markowitz",
        ("medio",  "medio"):  "sharpe",
        ("medio",  "alto"):   "sharpe",
        ("alto",   "baixo"):  "sharpe",
        ("alto",   "medio"):  "sharpe",
        ("alto",   "alto"):   "score",    # máximo risco/retorno: puro score
    }

    retorno_alvo        = RETORNO_MAP.get(retorno)
    volatilidade_maxima = RISCO_MAP.get(risco)
    modelo              = MODELO_MAP.get((risco, retorno), "sharpe")

    try:
        from scoring.services.motor_scoring_v2 import (
            calcular_scores_v2 as calcular_scores_todos_ativos,
            #calcular_scores_todos_ativos,
            montar_carteira_otimizada,
        )

        df_scores = calcular_scores_todos_ativos(perfil=perfil)
        resultado = montar_carteira_otimizada(
            df_scores           = df_scores,
            perfil              = perfil,
            modelo              = modelo,
            retorno_alvo        = retorno_alvo,
            volatilidade_maxima = volatilidade_maxima,
            setor_filtro        = setor if setor != "todos" else None,
        )

        alocacao_lista = [
            {
                "ticker":     ticker,
                "pct":        pct,
                "score":      resultado["scores_por_ativo"].get(ticker, {}).get("score_final", 50),
                "score_micro":resultado["scores_por_ativo"].get(ticker, {}).get("score_micro", 50),
                "score_time": resultado["scores_por_ativo"].get(ticker, {}).get("score_time", 50),
                "setor":      resultado["scores_por_ativo"].get(ticker, {}).get("setor", ""),
            }
            for ticker, pct in resultado["alocacao"].items()
        ]

        return JsonResponse({
            "alocacao": alocacao_lista,
            "metricas": resultado.get("metricas", {}),
            "modelo":   resultado.get("modelo_usado", modelo),
        })

    except Exception as e:
        return JsonResponse({"erro": str(e)}, status=500)