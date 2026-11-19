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
    ("divida_ebitda", "Dívida líquida / EBITDA", "divx", "Anos de geração de caixa para pagar a dívida líquida. \"Caixa líquido\": a empresa tem mais caixa que dívida. \"Pior caso\": tem dívida e EBITDA negativo."),
    ("crescimento_receita", "Crescimento da receita", "pct", "Variação da receita em relação ao ano anterior."),
    ("crescimento_lucro", "Crescimento do lucro", "pct", "Variação do lucro líquido em relação ao ano anterior."),
    # P/L, P/VPA e dividend yield foram retirados: dependem de preço e número
    # de ações de fonte externa, não entram no escore e não tinham conferência.
]

INDICADORES_ESTAT = [
    ("volatilidade_anual", "Volatilidade anual", "pct", "Desvio-padrão dos retornos, anualizado: medida de risco."),
    ("beta", "Beta", "num", "Sensibilidade ao Ibovespa: 1 acompanha o índice; abaixo de 1 oscila menos."),
    ("media_retorno", "Retorno médio diário", "pct3", "Média dos retornos diários na janela."),
    ("skewness", "Assimetria", "num", "Positiva: retornos extremos tendem a ser para cima."),
    ("curtose", "Excesso de curtose", "num", "Curtose menos 3. Acima de 0: mais dias extremos (para cima ou para baixo) que a distribuição normal."),
    ("_rr", "Retorno por unidade de risco", "num4", "Retorno médio diário ÷ desvio-padrão diário (inverso do coeficiente de variação, com sinal). Negativo: a ação caiu em média no período. É o que entra no escore estatístico."),
]

# Indicadores sem interpretação para bancos, seguradoras e afins
from scoring.services.motor_scoring_v2 import CAMPOS_NAO_FINANCEIROS, eh_financeiro  # noqa: E402

DIMENSOES = [("dim_liquidez", "Liquidez"), ("dim_rentabilidade", "Rentabilidade"),
             ("dim_divida", "Endividamento"), ("dim_crescimento", "Crescimento")]


def _fmt(valor, formato):
    if valor is None:
        return "—"
    v = float(valor)
    if formato == "pct":
        return _br(f"{v * 100:.1f}") + "%"
    if formato == "pct3":
        return _br(f"{v * 100:.3f}") + "%"
    if formato == "dy":                                  # DY pode vir como fração ou em %
        v = v * 100 if abs(v) <= 1 else v
        return f"{v:.2f}%" if abs(v) <= 100 else "—"
    if formato == "divx":
        if v >= 99:
            return "pior caso"
        if v < 0:
            return "caixa líquido"
        formato = "x"
    if formato == "x":
        return _br(f"{v:.2f}") + "x"
    if formato == "num4":
        return _br(f"{v:.3f}")
    return _br(f"{v:.2f}")


def _br(txt: str) -> str:
    """Número no formato brasileiro (vírgula decimal)."""
    return txt.replace(".", ",")


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
    from scoring.services.motor_scoring_v2 import universo_investivel
    universo = universo_investivel()
    if universo is not None and ativo.ticker not in universo:
        from django.conf import settings
        return ("Fora do universo investível: precisa de cotação em pelo menos 90% dos pregões dos "
                f"últimos 12 meses, volume mediano de pelo menos R$ {settings.LIQUIDEZ_MINIMA_DIARIA / 1e6:g} mi/dia "
                f"e negócios em {settings.PREGOES_COM_NEGOCIO_MIN * 100:.0f}% dos pregões.")
    falhas = []
    f = cfg.get("filtros_kpi", {})
    if eh_financeiro(ativo.setor):            # filtros de liquidez e dívida não se aplicam
        f = {k: v for k, v in f.items() if k.rsplit("__", 1)[0] not in CAMPOS_NAO_FINANCEIROS}
    sem = "sem dado (indicador ausente reprova o filtro)"
    if "liquidez_corrente__gte" in f and (micro.liquidez_corrente is None or float(micro.liquidez_corrente) < f["liquidez_corrente__gte"]):
        v = sem if micro.liquidez_corrente is None else _fmt(micro.liquidez_corrente, 'x')
        lim = _br(f"{f['liquidez_corrente__gte']:g}")
        falhas.append(f"liquidez corrente {v} (mínimo {lim}x)")
    if "divida_ebitda__lte" in f and (micro.divida_ebitda is None or float(micro.divida_ebitda) > f["divida_ebitda__lte"]):
        v = sem if micro.divida_ebitda is None else _fmt(micro.divida_ebitda, 'divx')
        lim = _br(f"{f['divida_ebitda__lte']:g}")
        falhas.append(f"dívida líquida/EBITDA {v} (máximo {lim}x)")
    if "roe__gte" in f and (micro.roe is None or float(micro.roe) < f["roe__gte"]):
        v = sem if micro.roe is None else _fmt(micro.roe, 'pct')
        falhas.append(f"ROE {v} (mínimo {f['roe__gte'] * 100:g}%)")
    if falhas:
        return "Não passa nos filtros do perfil: " + "; ".join(falhas) + "."
    return "Sem escore calculado para este perfil."


def _versao_dos_dados() -> str:
    """Muda sempre que entram dados novos: invalida o cache dos escores."""
    from django.db.models import Max
    from market_data.models import KpiEstatistico, KpiMacro, KpiMicro
    from scoring.services.motor_scoring_v2 import obter_perfis
    import hashlib
    partes = [KpiMicro.objects.aggregate(m=Max("id"))["m"],
              KpiEstatistico.objects.aggregate(m=Max("id"))["m"],
              KpiMacro.objects.aggregate(m=Max("id"))["m"],
              hashlib.md5(json.dumps(obter_perfis(), sort_keys=True, default=str).encode()).hexdigest()[:8]]
    return "-".join(str(p) for p in partes)


def scores_em_cache(perfil):
    """calcular_scores_v2 é a parte lenta da página do ativo (3 perfis por visita).
    O resultado fica 30 min em cache e é refeito quando entram dados novos."""
    from django.core.cache import cache
    from scoring.services.motor_scoring_v2 import calcular_scores_v2
    chave = f"scores_v2:{perfil}:{date.today()}:{_versao_dos_dados()}"
    df = cache.get(chave)
    if df is None:
        df = calcular_scores_v2(perfil)
        cache.set(chave, df, 60 * 30)
    return df


def _situacao_no_perfil(ativo, perfil) -> dict:
    from scoring.services.motor_scoring_v2 import config_perfil, selecionar_candidatos
    cfg = config_perfil(perfil)
    df = scores_em_cache(perfil)
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
    saida["dimensoes"] = [{"nome": nome, "valor": round(float(r[c]))}
                          for c, nome in DIMENSOES if c in r.index and r[c] == r[c]]
    candidatos = selecionar_candidatos(df, perfil)
    saida["entra"] = ativo.ticker in set(candidatos["ticker"])
    if saida["entra"]:
        saida["motivo"] = (f"Está entre os {cfg['qtd_ativos']} melhores escores elegíveis do perfil "
                           f"e entra na carteira teórica.")
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
        "minimo": round(float(s.min()), 2), "maximo": round(float(s.max()), 2),
        "fim": s.index[-1].strftime("%d/%m/%Y"),
        "ultimo": round(float(s.iloc[-1]), 2),
    }


def montar_contexto_ativo(ativo, perfil_usuario=None) -> dict:
    from market_data.models import KpiEstatistico, KpiMicro, KpiTime
    from scoring.models import CarteiraRecomendada

    # Fundamentos: até 5 últimas DFPs, da mais antiga para a mais recente
    micros = list(KpiMicro.objects.filter(ativo=ativo).order_by("-data_ref")[:5])[::-1]
    financeiro = eh_financeiro(ativo.setor)
    fundamentos = [{
        "rotulo": rot, "explicacao": exp,
        "valores": (["não se aplica"] * len(micros) if financeiro and campo in CAMPOS_NAO_FINANCEIROS
                    else [_fmt(getattr(m, campo, None), fmt) for m in micros]),
    } for campo, rot, fmt, exp in INDICADORES_FUND]

    est = (KpiEstatistico.objects.filter(ativo=ativo).order_by("-data_calculo", "-janela_dias").first())
    def _valor_estat(campo):
        if campo == "_rr":
            if est.media_retorno is None or not est.dp_retorno:
                return None
            return float(est.media_retorno) / float(est.dp_retorno)
        return getattr(est, campo, None)
    estatisticas = [{"rotulo": rot, "explicacao": exp, "valor": _fmt(_valor_estat(campo), fmt)}
                    for campo, rot, fmt, exp in INDICADORES_ESTAT] if est else []

    # Liquidez (o que decide se o ativo entra no universo investível)
    from django.conf import settings
    from scoring.services.motor_scoring_v2 import universo_investivel
    universo = universo_investivel()
    liquidez = None
    if est:
        vol = est.volume_mediano
        preg = est.pregoes_com_negocio
        liquidez = {
            "volume": (f"R$ {float(vol) / 1e6:.1f} mi/dia".replace(".", ",") if vol is not None else "não calculado"),
            "pregoes": (_br(f"{float(preg) * 100:.0f}") + "%" if preg is not None else "não calculado"),
            "observacoes": est.n_observacoes,
            "minimo_volume": _br(f"{settings.LIQUIDEZ_MINIMA_DIARIA / 1e6:g}"),
            "minimo_pregoes": f"{settings.PREGOES_COM_NEGOCIO_MIN * 100:.0f}",
            "elegivel": None if universo is None else ativo.ticker in universo,
        }

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
        "liquidez": liquidez,
        "financeiro": financeiro,
        "data_mercado": t.data_ref if t else None,
        "grafico_json": json.dumps(grafico) if grafico else None,
        "grafico": grafico,
    }
