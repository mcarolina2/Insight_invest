"""
Divisão entre renda fixa e ações (separação em dois fundos de Tobin, 1958).

Todos os investidores recebem a MESMA carteira de ações otimizada para o perfil;
o que muda de um investidor para outro é quanto fica em renda fixa pós-fixada
(Tesouro Selic / CDB 100% do CDI), o ativo livre de risco.

A parcela em ações é a maior que mantém a perda provável em 12 meses, com 95% de
confiança, dentro do limite que o próprio usuário declarou no questionário:

    w_acoes = min(1, perda_max / (1,645 x volatilidade anual das ações))

Ex.: ações com volatilidade de 20% a.a. e perda máxima de 5%  -> 15% em ações.
     A mesma carteira com perda máxima de 15%               -> 46% em ações.
O retorno esperado das ações não entra na regra (seria otimista demais confiar
nele para proteger o investidor); só a volatilidade.
"""
from math import sqrt

Z_95 = 1.645
DIAS_UTEIS = 252
VOLATILIDADE_PADRAO = 0.25   # usada se não houver preços para estimar (≈ Ibovespa)

# Pergunta "perda_max" do questionário: alternativa -> perda máxima aceita (fração)
PERDA_MAX_POR_RESPOSTA = {0: 0.0, 1: 0.05, 2: 0.15, 3: None}   # None = sem limite

# Para quem ainda não respondeu (ex.: carteiras de exemplo da página inicial)
PERDA_MAX_POR_PERFIL = {"conservador": 0.05, "intermediario": 0.15, "arrojado": None}

NOME_RENDA_FIXA = "Renda fixa"
DESCRICAO_RENDA_FIXA = "Tesouro Selic / CDB 100% do CDI"


def perda_maxima_do_usuario(perfil_obj=None, perfil: str | None = None):
    """Perda máxima declarada no quiz; sem resposta, usa a referência do perfil."""
    if perfil_obj is not None and "perda_max" in (perfil_obj.respostas or {}):
        return PERDA_MAX_POR_RESPOSTA.get(int(perfil_obj.respostas["perda_max"]))
    return PERDA_MAX_POR_PERFIL.get(perfil or getattr(perfil_obj, "tipo", None), 0.15)


def parcela_em_acoes(volatilidade_acoes: float | None, perda_max: float | None) -> float:
    if perda_max is None:
        return 1.0
    if perda_max <= 0:
        return 0.0
    vol = volatilidade_acoes or VOLATILIDADE_PADRAO
    return round(min(1.0, perda_max / (Z_95 * vol)), 4)


def metricas_totais(metricas_acoes: dict, w_acoes: float, rf: float) -> dict:
    """Métricas da carteira completa (ações + renda fixa)."""
    ret_a = metricas_acoes.get("retorno")
    vol_a = metricas_acoes.get("volatilidade")
    if ret_a is None or vol_a is None:
        ret_a, vol_a = rf, VOLATILIDADE_PADRAO
    ret = w_acoes * ret_a + (1 - w_acoes) * rf
    vol = w_acoes * vol_a
    var95_dia = ret / DIAS_UTEIS - Z_95 * vol / sqrt(DIAS_UTEIS)
    return {
        "retorno": round(ret, 4),
        "volatilidade": round(vol, 4),
        # na separação de Tobin o Sharpe da carteira completa é o das ações
        "sharpe": round((ret - rf) / vol, 3) if vol > 0 else None,
        "var95": round(var95_dia, 4),
        "perda_provavel_12m": round(Z_95 * vol, 4),
    }


def montar_carteira_completa(res_acoes: dict, perda_max, rf: float) -> dict:
    """
    Recebe o resultado de montar_carteira_otimizada (só ações) e devolve a
    carteira completa, com a renda fixa como primeiro item.
    """
    w = parcela_em_acoes(res_acoes.get("metricas", {}).get("volatilidade"), perda_max)
    itens = []
    if w < 1:
        itens.append({"ticker": NOME_RENDA_FIXA, "tipo": "renda_fixa",
                      "descricao": DESCRICAO_RENDA_FIXA, "pct": round((1 - w) * 100, 2)})
    for t, pct in res_acoes["alocacao"].items():
        if w > 0:
            itens.append({"ticker": t, "tipo": "acao", "pct": round(pct * w, 2),
                          "pct_nas_acoes": pct})
    return {
        "itens": itens,
        "pct_acoes": round(w * 100, 2),
        "pct_renda_fixa": round((1 - w) * 100, 2),
        "perda_max": perda_max,
        "metricas_totais": metricas_totais(res_acoes.get("metricas", {}), w, rf),
        "metricas_acoes": res_acoes.get("metricas", {}),
    }
