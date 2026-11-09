"""
Reúne as informações exibidas na página de um ativo (/ativo/<ticker>/).
"""
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

NOMES_PERFIS = {"conservador": "Conservador", "intermediario": "Intermediário", "arrojado": "Arrojado"}

# (campo do KpiMicro, rótulo, formato, explicação curta)
INDICADORES_FUND = [
    ("roe", "ROE", "pct", "Lucro líquido / patrimônio líquido: quanto a empresa rende sobre o capital dos acionistas."),
    ("roa", "ROA", "pct", "Lucro líquido / ativo total: rentabilidade sobre tudo o que a empresa possui."),
    ("margem_liquida", "Margem líquida", "pct", "Quanto da receita vira lucro líquido."),
    ("margem_ebitda", "Margem EBITDA", "pct", "Geração de caixa operacional em relação à receita."),
    ("liquidez_corrente", "Liquidez corrente", "x", "Ativo circulante / passivo circulante: acima de 1, cobre as dívidas de curto prazo."),
    ("divida_ebitda", "Dívida líquida / EBITDA", "x", "Anos de geração de caixa para pagar a dívida líquida. Negativo indica caixa líquido ou EBITDA negativo."),
    ("crescimento_receita", "Crescimento da receita", "pct", "Variação da receita em relação ao ano anterior."),
    ("crescimento_lucro", "Crescimento do lucro", "pct", "Variação do lucro líquido em relação ao ano anterior."),
    ("pl", "P/L", "x", "Preço / lucro por ação: quantos anos de lucro o preço representa."),
    ("pvpa", "P/VPA", "x", "Preço / valor patrimonial por ação."),
    ("dy", "Dividend yield", "dy", "Dividendos pagos / preço da ação."),
]

INDICADORES_ESTAT = [
    ("volatilidade_anual", "Volatilidade anual", "pct", "Desvio-padrão dos retornos, anualizado: medida de risco."),
    ("beta", "Beta", "num", "Sensibilidade ao Ibovespa: 1 acompanha o índice; abaixo de 1 oscila menos."),
    ("media_retorno", "Retorno médio diário", "pct3", "Média dos retornos diários na janela."),
    ("skewness", "Assimetria", "num", "Positiva: retornos extremos tendem a ser para cima."),
    ("curtose", "Curtose", "num", "Acima de 3: mais eventos extremos que a distribuição normal."),
    ("cv", "Coeficiente de variação", "num", "Risco por unidade de retorno médio."),
]


def _fmt(valor, formato):
    if valor is None:
        return "—"
    v = float(valor)
    if formato == "pct":
        return f"{v * 100:.1f}%"
    if formato == "pct3":
        return f"{v * 100:.3f}%"
    if formato == "dy":                                  # DY pode vir como fração ou em %
        v = v * 100 if abs(v) <= 1 else v
        return f"{v:.2f}%" if abs(v) <= 100 else "—"
    if formato == "x":
        return f"{v:.2f}x"
    return f"{v:.2f}"


def _motivo_fora_do_scoring(ativo, cfg) -> str:
    from market_data.models import KpiMicro

    if ativo.tipo == "fii":
        return "Fundos imobiliários não publicam demonstrações financeiras padronizadas (DFP) e ficam fora do escore."
    micro = KpiMicro.objects.filter(ativo=ativo).order_by("-data_ref").first()
    if micro is None:
        return ("Não há demonstrações financeiras deste ativo na base (CNPJ sem correspondência com a CVM), "
                "por isso ele não recebe escore.")
    if micro.data_ref > date.today() - timedelta(days=90):
        anterior = KpiMicro.objects.filter(ativo=ativo, data_ref__lte=date.today() - timedelta(days=90)).exists()
        if not anterior:
            return "A única demonstração financeira disponível ainda está dentro do prazo de divulgação."
    falhas = []
    f = cfg.get("filtros_kpi", {})
    if "liquidez_corrente__gte" in f and (micro.liquidez_corrente is None or float(micro.liquidez_corrente) < f["liquidez_corrente__gte"]):
        falhas.append(f"liquidez corrente {_fmt(micro.liquidez_corrente, 'x')} (mínimo {f['liquidez_corrente__gte']:g}x)")
    if "divida_ebitda__lte" in f and (micro.divida_ebitda is None or float(micro.divida_ebitda) > f["divida_ebitda__lte"]):
        falhas.append(f"dívida líquida/EBITDA {_fmt(micro.divida_ebitda, 'x')} (máximo {f['divida_ebitda__lte']:g}x)")
    if "roe__gte" in f and (micro.roe is None or float(micro.roe) < f["roe__gte"]):
        falhas.append(f"ROE {_fmt(micro.roe, 'pct')} (mínimo {f['roe__gte'] * 100:g}%)")
    if falhas:
        return "Não passa nos filtros do perfil: " + "; ".join(falhas) + "."
    return "Sem escore calculado para este perfil."


def _situacao_no_perfil(ativo, perfil) -> dict:
    from scoring.services.motor_scoring_v2 import (calcular_scores_v2, config_perfil,
                                                   selecionar_candidatos)
    cfg = config_perfil(perfil)
    df = calcular_scores_v2(perfil)
    saida = {"perfil": perfil, "nome": NOMES_PERFIS.get(perfil, perfil), "scores": None,
             "entra": False, "posicao": None, "total": len(df)}
    linha = df[df["ticker"] == ativo.ticker] if not df.empty else df
    if df.empty or linha.empty:
        saida["motivo"] = _motivo_fora_do_scoring(ativo, cfg)
        return saida

    r = linha.iloc[0]
    saida["posicao"] = int(linha.index[0]) + 1
    saida["scores"] = {k: round(float(r[k]), 1)
                       for k in ("score_final", "score_micro", "score_macro", "score_estat")}
    candidatos = selecionar_candidatos(df, perfil)
    saida["entra"] = ativo.ticker in set(candidatos["ticker"])
    if saida["entra"]:
        saida["motivo"] = f"Está entre os {cfg['qtd_ativos']} melhores escores elegíveis do perfil."
    elif r["score_final"] < cfg["score_min"]:
        saida["motivo"] = f"Escore {r['score_final']:.0f} abaixo do mínimo do perfil ({cfg['score_min']:.0f})."
    else:
        saida["motivo"] = (f"Escore suficiente, mas está na posição {saida['posicao']}; "
                           f"o perfil leva os {cfg['qtd_ativos']} primeiros.")
    return saida


def _precos_grafico(ticker):
    from scoring.services.precos import obter_precos
    try:
        precos = obter_precos([ticker], anos=1)
    except Exception as e:
        logger.warning(f"Preços indisponíveis para {ticker}: {e}")
        return None
    if precos is None or precos.empty or ticker not in precos.columns:
        return None
    s = precos[ticker].dropna()
    if len(s) < 20:
        return None
    return {
        "datas": [d.strftime("%d/%m/%Y") for d in s.index],
        "valores": [round(float(v), 2) for v in s.values],
        "variacao": round(float(s.iloc[-1] / s.iloc[0] - 1) * 100, 1),
        "ultimo": round(float(s.iloc[-1]), 2),
    }


def montar_contexto_ativo(ativo, perfil_usuario=None) -> dict:
    from market_data.models import KpiEstatistico, KpiMicro, KpiTime
    from scoring.models import CarteiraRecomendada

    # Fundamentos: até 5 últimas DFPs, da mais antiga para a mais recente
    micros = list(KpiMicro.objects.filter(ativo=ativo).order_by("-data_ref")[:5])[::-1]
    fundamentos = [{
        "rotulo": rot, "explicacao": exp,
        "valores": [_fmt(getattr(m, campo, None), fmt) for m in micros],
    } for campo, rot, fmt, exp in INDICADORES_FUND]

    est = (KpiEstatistico.objects.filter(ativo=ativo).order_by("-data_calculo", "-janela_dias").first())
    estatisticas = [{"rotulo": rot, "explicacao": exp, "valor": _fmt(getattr(est, campo, None), fmt)}
                    for campo, rot, fmt, exp in INDICADORES_ESTAT] if est else []

    t = KpiTime.objects.filter(ativo=ativo).order_by("-data_ref").first()
    mercado = []
    if t:
        mercado = [
            ("Retorno em 1 mês", _fmt(t.retorno_1m, "pct")),
            ("Retorno em 3 meses", _fmt(t.retorno_3m, "pct")),
            ("Retorno em 12 meses", _fmt(t.retorno_12m, "pct")),
            ("RSI (14 dias)", _fmt(t.rsi_14, "num")),
            ("Média móvel 50 dias", f"R$ {float(t.media_movel_50):.2f}" if t.media_movel_50 else "—"),
            ("Média móvel 200 dias", f"R$ {float(t.media_movel_200):.2f}" if t.media_movel_200 else "—"),
            ("Volume médio (20 dias)", f"{float(t.volume_medio_20d):,.0f} ações/dia".replace(",", ".")
             if t.volume_medio_20d else "—"),
        ]

    # Situação nos três perfis (o do usuário primeiro)
    ordem = list(NOMES_PERFIS)
    if perfil_usuario in ordem:
        ordem.remove(perfil_usuario)
        ordem.insert(0, perfil_usuario)
    situacoes = [_situacao_no_perfil(ativo, p) for p in ordem]

    # Presença nas carteiras recomendadas (pré-calculadas) da home
    nas_carteiras = []
    for c in CarteiraRecomendada.objects.filter(ativo=True):
        item = next((a for a in c.composicao if a.get("ticker") == ativo.ticker), None)
        if item:
            nas_carteiras.append({"perfil": NOMES_PERFIS.get(c.perfil, c.perfil), "pct": item.get("pct")})

    grafico = _precos_grafico(ativo.ticker) if ativo.tipo in ("acao", "fii", "etf", "bdr") else None

    return {
        "ativo": ativo,
        "perfil_usuario": perfil_usuario,
        "situacao_principal": situacoes[0],
        "situacoes": situacoes,
        "nas_carteiras": nas_carteiras,
        "anos_dfp": [m.data_ref.year for m in micros],
        "data_dfp": micros[-1].data_ref if micros else None,
        "fundamentos": fundamentos if micros else [],
        "estatisticas": estatisticas,
        "data_estat": est.data_calculo if est else None,
        "mercado": mercado,
        "data_mercado": t.data_ref if t else None,
        "grafico_json": json.dumps(grafico) if grafico else None,
        "grafico": grafico,
    }
