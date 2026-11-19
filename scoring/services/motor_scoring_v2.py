import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PREGOES_ANO = 252


# =============================================================================
# CONFIGURAÇÃO POR PERFIL
# =============================================================================

# Valores padrão. A configuração em uso fica no banco (ConfiguracaoPerfil),
# editável no painel; estes valores servem para criá-la e como reserva.
# Os pesos ('pesos' e 'micro_pesos') foram definidos pelo método AHP (Saaty);
# julgamentos e cálculo em scoring/services/ahp.py (python manage.py pesos_ahp).
PERFIS = {
    'conservador': {
        'score_min':      35,    # threshold baixo: poucos dados no banco
        'qtd_ativos':     8,
        'max_pct_ativo':  0.20,

        # Pesos das camadas disponíveis
        'pesos': {'micro': 0.55, 'macro': 0.24, 'estat': 0.21},

        # Pesos internos do score_micro (o que cada sub-indicador vale)
        'micro_pesos': {
            'liquidez':       0.36,   # conservador prioriza liquidez
            'rentabilidade':  0.29,
            'divida':         0.25,   # e baixo endividamento
            'crescimento':    0.10,
        },

        # Filtros ADICIONAIS por perfil (colunas do KpiMicro)
        'filtros_kpi': {
            'liquidez_corrente__gte': 1.0,    # só aceita liquidez > 1
            'divida_ebitda__lte':     3.0,    # endividamento moderado
        },
    },

    'intermediario': {
        'score_min':      30,
        'qtd_ativos':     10,
        'max_pct_ativo':  0.15,
        'pesos': {'micro': 0.49, 'macro': 0.31, 'estat': 0.20},
        'micro_pesos': {
            'liquidez':       0.20,
            'rentabilidade':  0.40,   # intermediário equilibra, com ênfase em rentabilidade
            'divida':         0.20,
            'crescimento':    0.20,
        },
        'filtros_kpi': {},            # sem filtros extras
    },

    'arrojado': {
        'score_min':      25,
        'qtd_ativos':     15,
        'max_pct_ativo':  0.12,
        'pesos': {'micro': 0.50, 'macro': 0.25, 'estat': 0.25},
        'micro_pesos': {
            'liquidez':       0.10,   # arrojado foca em crescimento
            'rentabilidade':  0.40,
            'divida':         0.10,
            'crescimento':    0.40,   # e potencial de retorno
        },
        'filtros_kpi': {
            'roe__gte': 0.05,         # só aceita ROE positivo
        },
    },
}


# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

# Valor atribuído a indicador ausente (regra de neutralidade = 50).
# A análise de sensibilidade troca esse valor temporariamente com
# `valor_ausente(...)`; ContextVar mantém a troca isolada por thread/requisição.
_VALOR_AUSENTE: ContextVar[float] = ContextVar("valor_ausente", default=50.0)


@contextmanager
def valor_ausente(valor: float):
    token = _VALOR_AUSENTE.set(float(valor))
    try:
        yield
    finally:
        _VALOR_AUSENTE.reset(token)


def _norm(series: pd.Series, inverter: bool = False) -> pd.Series:
    """
    Normaliza uma série para 0-100 usando rank percentil.
    Robusto a outliers. Se inverter=True, menor valor = maior score.
    Valores ausentes recebem o valor neutro (50, salvo na análise de sensibilidade).
    """
    neutro = _VALOR_AUSENTE.get()
    if series.isna().all() or len(series) < 2:
        return pd.Series(neutro, index=series.index)
    ranked = series.rank(pct=True, na_option='keep') * 100
    return (100 - ranked if inverter else ranked).fillna(neutro)


# =============================================================================
# SCORE MICRO — configura os pesos por perfil
# =============================================================================

def dimensoes_micro(df: pd.DataFrame) -> pd.DataFrame:
    """Escore (0-100) de cada dimensão fundamentalista: liquidez, rentabilidade,
    divida e crescimento. Cada uma é a média dos percentis dos seus indicadores."""
    scores = pd.DataFrame(index=df.index)

    # ── LIQUIDEZ ──────────────────────────────────────────────
    cols_liq = ['liquidez_corrente', 'liquidez_seca', 'liquidez_imediata', 'liquidez_geral']
    liq_parts = [_norm(df[c]) for c in cols_liq if c in df.columns]
    scores['liquidez'] = pd.concat(liq_parts, axis=1).mean(axis=1) if liq_parts else pd.Series(50, index=df.index)

    # ── RENTABILIDADE ──────────────────────────────────────────
    rent_parts = [_norm(df[c]) for c in ['roe', 'roa', 'margem_liquida', 'margem_ebitda', 'giro_ativo'] if c in df.columns]
    scores['rentabilidade'] = pd.concat(rent_parts, axis=1).mean(axis=1) if rent_parts else pd.Series(50, index=df.index)

    # ── ENDIVIDAMENTO (menor = melhor) ─────────────────────────
    scores['divida'] = _norm(df['divida_ebitda'], inverter=True) if 'divida_ebitda' in df.columns else pd.Series(50, index=df.index)

    # ── CRESCIMENTO ────────────────────────────────────────────
    cresc_parts = [_norm(df[c]) for c in ['crescimento_receita', 'crescimento_lucro'] if c in df.columns]
    scores['crescimento'] = pd.concat(cresc_parts, axis=1).mean(axis=1) if cresc_parts else pd.Series(50, index=df.index)

    return scores


def calcular_score_micro_v2(df: pd.DataFrame, micro_pesos: dict) -> pd.Series:
    """
    Score fundamentalista com pesos configuráveis por perfil.

    O conservador dá peso 35% para liquidez.
    O arrojado dá peso 40% para crescimento.
    Isso garante que os mesmos dados geram rankings DIFERENTES por perfil.
    """
    scores = dimensoes_micro(df)
    # ── SCORE MICRO PONDERADO pelos pesos do perfil ────────────
    score = (
        scores['liquidez']      * micro_pesos.get('liquidez', 0.25) +
        scores['rentabilidade'] * micro_pesos.get('rentabilidade', 0.35) +
        scores['divida']        * micro_pesos.get('divida', 0.20) +
        scores['crescimento']   * micro_pesos.get('crescimento', 0.20)
    )
    return score.round(2)


# =============================================================================
# SCORE MACRO (igual para todos — contexto econômico geral)
# =============================================================================

def componentes_macro(kpi_macro) -> list[dict]:
    """
    Parcelas do escore macro, em pontos a partir da base 50:

        M = 50 - 2*max(0, Selic - 4) - 3*max(0, IPCA12m - 3)
               + (5*A se A > 0, senão 3*A) - 1,5*max(0, Desemprego - 8)

    Selic meta (% a.a.); IPCA12m = inflação acumulada em 12 meses (na falta,
    IPCA do mês x 12); A = atividade: variação do IBC-Br em 12 meses (na falta,
    variação real anual do PIB); desemprego PNAD (%). Resultado limitado a [0, 100].
    Valores ausentes usam os padrões abaixo (Selic 10, IPCA 0,3/mês, A 0, desemprego 10).
    """
    selic = float(kpi_macro.selic or 10)
    ipca12 = getattr(kpi_macro, 'ipca_12m', None)
    rot_ipca = "IPCA em 12 meses"
    if ipca12 is None:
        ipca12 = float(kpi_macro.ipca_mensal or 0.3) * 12
        rot_ipca = "IPCA do mês × 12"
    ativ = getattr(kpi_macro, 'ibc_br_12m', None)
    rot_ativ = "IBC-Br em 12 meses"
    if ativ is None:
        ativ = kpi_macro.pib_trimestral
        rot_ativ = "PIB no ano"
    ativ = float(ativ or 0)
    desemp = float(kpi_macro.desemprego or 10)
    return [
        {"nome": "Selic", "valor": selic, "unidade": "% a.a.", "regra": "−2 por ponto acima de 4%",
         "pontos": -max(0, (selic - 4) * 2)},
        {"nome": rot_ipca, "valor": float(ipca12), "unidade": "%", "regra": "−3 por ponto acima de 3%",
         "pontos": -max(0, (float(ipca12) - 3) * 3)},
        {"nome": rot_ativ, "valor": ativ, "unidade": "%", "regra": "+5 por ponto de alta; −3 por ponto de queda",
         "pontos": ativ * 5 if ativ > 0 else ativ * 3},
        {"nome": "Desemprego", "valor": desemp, "unidade": "%", "regra": "−1,5 por ponto acima de 8%",
         "pontos": -max(0, (desemp - 8) * 1.5)},
    ]


def calcular_score_macro(kpi_macro) -> float:
    """Escore macro (0-100), igual para todos os ativos de uma data (ver componentes_macro)."""
    if not kpi_macro:
        return 50.0
    p = 50.0 + sum(c["pontos"] for c in componentes_macro(kpi_macro))
    return round(max(0, min(100, p)), 2)


# =============================================================================
# SCORE ESTATÍSTICO — recompensa estabilidade calibrada por perfil
# =============================================================================

def _retorno_por_risco(df_estat: pd.DataFrame) -> pd.Series | None:
    """Inverso do coeficiente de variação com sinal: média / desvio padrão
    dos retornos diários. None se não houver dado para calcular."""
    cols = df_estat.columns
    if 'media_retorno' in cols and 'dp_retorno' in cols:
        dp = pd.to_numeric(df_estat['dp_retorno'], errors='coerce').replace(0, np.nan)
        rr = pd.to_numeric(df_estat['media_retorno'], errors='coerce') / dp
    elif 'cv' in cols and 'media_retorno' in cols:
        cv = pd.to_numeric(df_estat['cv'], errors='coerce').abs().replace(0, np.nan)
        rr = np.sign(pd.to_numeric(df_estat['media_retorno'], errors='coerce')) / cv
    elif 'cv' in cols:                       # CV já com sinal (média / dp invertido)
        cv = pd.to_numeric(df_estat['cv'], errors='coerce').replace(0, np.nan)
        rr = 1 / cv
    else:
        return None
    return rr.replace([np.inf, -np.inf], np.nan)


def calcular_score_estat(df_estat: pd.DataFrame, perfil: str) -> pd.Series:
    """
    Score estatístico com lógica diferente por perfil:
      Conservador → recompensa baixa volatilidade e distribuição normal
      Arrojado    → aceita alta volatilidade se skewness for positivo
    """
    if df_estat.empty:
        return pd.Series(dtype=float)
    scores, bonus = componentes_estat(df_estat, perfil)
    base = scores.mean(axis=1) if not scores.empty else pd.Series(50, index=df_estat.index)
    return (base + bonus).clip(0, 100).round(2)


def componentes_estat(df_estat: pd.DataFrame, perfil: str):
    """(DataFrame com os percentis de volatilidade, beta e retorno/risco, bônus)."""
    scores = pd.DataFrame(index=df_estat.index)

    # Volatilidade: conservador quer baixa, arrojado aceita alta
    if 'volatilidade_anual' in df_estat.columns:
        if perfil == 'arrojado':
            scores['vol'] = pd.Series(50, index=df_estat.index)   # neutro
        else:
            scores['vol'] = _norm(df_estat['volatilidade_anual'], inverter=True)

    # Beta: conservador prefere beta < 1
    if 'beta' in df_estat.columns:
        dist_beta = (df_estat['beta'] - (0.7 if perfil == 'conservador' else 1.0)).abs()
        scores['beta'] = _norm(dist_beta, inverter=True)

    # Risco relativo: todos preferem mais retorno médio por unidade de risco.
    # Usa-se o inverso do CV (média / desvio padrão), que preserva a ordem do CV
    # quando a média é positiva e põe no fim os ativos com média NEGATIVA.
    # (O |CV| antigo premiava ações em queda forte: média muito negativa
    # deixava o |CV| pequeno, como se o risco relativo fosse baixo.)
    rr = _retorno_por_risco(df_estat)
    if rr is not None:
        scores['cv'] = _norm(rr)

    # Skewness positivo: bônus para todos, mas mais para arrojado
    bonus = 0
    if 'skewness' in df_estat.columns:
        skew_bonus = df_estat['skewness'].clip(-1, 1) * (8 if perfil == 'arrojado' else 4)
        bonus += skew_bonus

    # Distribuição normal: conservador valoriza previsibilidade
    if 'retorno_normal' in df_estat.columns and perfil == 'conservador':
        bonus += df_estat['retorno_normal'].map({True: 8, False: 0}).fillna(0)

    if isinstance(bonus, int):
        bonus = pd.Series(float(bonus), index=df_estat.index)
    return scores, bonus


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def obter_perfis() -> dict:
    """Configuração dos perfis em uso: do banco (ConfiguracaoPerfil), com os
    valores padrão do código como reserva para perfis ainda não cadastrados."""
    perfis = {p: dict(cfg) for p, cfg in PERFIS.items()}
    try:
        from scoring.models import ConfiguracaoPerfil
        for c in ConfiguracaoPerfil.objects.all():
            perfis[c.perfil] = c.como_dict()
    except Exception as e:                       # banco indisponível ou sem migração
        logger.warning(f"Usando configuração padrão dos perfis: {e}")
    return perfis


def config_perfil(perfil: str) -> dict:
    perfis = obter_perfis()
    return perfis.get(perfil, perfis['intermediario'])


def _ultimo_por_ativo(rows: list[dict], campo_data: str) -> list[dict]:
    """Mantém apenas o registro mais recente de cada ativo (já ordenados)."""
    vistos, saida = set(), []
    for r in rows:
        if r['ativo_id'] not in vistos:
            vistos.add(r['ativo_id'])
            saida.append(r)
    return saida


# Setor financeiro: o balanço de bancos, seguradoras e similares não separa
# circulante e não circulante, e "dívida" é a matéria-prima do negócio. Para
# eles, liquidez, margens, giro e dívida/EBITDA não têm interpretação: ficam
# sem valor (neutros no escore) e os filtros de liquidez e dívida não se aplicam.
# Rentabilidade (ROE, ROA) e crescimento valem para todos.
SETOR_FINANCEIRO = r"banco|financeir|previd[eê]ncia|seguros|segurador|arrendamento mercantil|securitiza|factoring|bolsas? de valores|financial"
CAMPOS_NAO_FINANCEIROS = ['liquidez_corrente', 'liquidez_seca', 'liquidez_imediata', 'liquidez_geral',
                          'margem_liquida', 'margem_ebitda', 'giro_ativo', 'divida_ebitda']


def eh_financeiro(setor) -> bool:
    import re
    return bool(re.search(SETOR_FINANCEIRO, str(setor or '').lower()))


def _aplicar_filtros_kpi(df: pd.DataFrame, filtros: dict, isentos: pd.Series | None = None) -> pd.DataFrame:
    """Aplica filtros no formato do Django (campo__gte / campo__lte) ao
    DataFrame, DEPOIS de escolher o balanço mais recente de cada ativo.
    isentos: linhas (setor financeiro) dispensadas dos filtros que não se aplicam a elas."""
    for chave, limite in filtros.items():
        campo, op = chave.rsplit('__', 1)
        if campo not in df.columns:
            continue
        ok = df[campo] >= limite if op == 'gte' else df[campo] <= limite if op == 'lte' else True
        if isentos is not None and campo in CAMPOS_NAO_FINANCEIROS:
            ok = ok | isentos.reindex(df.index).fillna(False)
        df = df[ok]
    return df


def universo_investivel(data_ref: date | None = None) -> set | None:
    """
    Tickers com preço e negociação suficientes para entrar na comparação, ou
    None se ainda não houver indicador estatístico no banco (sem filtro).

    Regra (a mesma da carteira e dos rankings): ter o indicador estatístico
    mais recente com cotação em pelo menos 90% dos pregões da janela e, quando
    a liquidez estiver gravada, volume mediano >= LIQUIDEZ_MINIMA_DIARIA e
    negócios em pelo menos PREGOES_COM_NEGOCIO_MIN dos pregões. Os percentis
    são calculados só dentro desse universo, para que ações quase sem negócio
    (preço parado, volatilidade e beta perto de zero) não ocupem o topo da
    camada estatística.
    """
    from django.conf import settings
    from market_data.models import KpiEstatistico
    data_ref = data_ref or date.today()
    linhas = list(KpiEstatistico.objects.filter(data_calculo__lte=data_ref, ativo__ativo=True)
                  .order_by('ativo_id', '-data_calculo', '-n_observacoes')
                  .values('ativo_id', 'ativo__ticker', 'n_observacoes', 'janela_dias',
                          'volume_mediano', 'pregoes_com_negocio'))
    if not linhas:
        return None
    minimo = float(getattr(settings, 'LIQUIDEZ_MINIMA_DIARIA', 0) or 0)
    min_preg = float(getattr(settings, 'PREGOES_COM_NEGOCIO_MIN', 0) or 0)
    saida = set()
    for l in _ultimo_por_ativo(linhas, 'data_calculo'):
        n, jan = l['n_observacoes'], l['janela_dias'] or 252
        if n is not None and n < 0.9 * jan:
            continue
        vol, preg = l['volume_mediano'], l['pregoes_com_negocio']
        if vol is not None and float(vol) < minimo:
            continue
        if preg is not None and float(preg) < min_preg:
            continue
        saida.add(l['ativo__ticker'])
    return saida


def calcular_scores_v2(perfil: str = 'intermediario', data_ref: date | None = None,
                       cfg: dict | None = None,
                       estat_externo: pd.DataFrame | None = None,
                       universo: set | None = None) -> pd.DataFrame:
    """
    Calcula o escore de TODOS os ativos elegíveis para o perfil.

    data_ref: data de referência ("como se fosse hoje"). Usada pelo backtest
    para não olhar o futuro: só entram DFPs publicadas até data_ref (regra
    conservadora: exercício encerrado há pelo menos 3 meses), o registro
    macro e o estatístico mais recentes até data_ref. None = hoje.

    cfg: configuração do perfil a usar no lugar da do banco (análise de
    sensibilidade). estat_externo: indicadores estatísticos já calculados
    (índice = ticker; colunas volatilidade_anual, beta, skewness, curtose,
    retorno_normal, cv) — o backtest os recalcula a partir dos preços da janela
    de calibração em vez de ler KpiEstatistico.

    Retorna DataFrame ordenado por score_final (decrescente), com colunas:
      ticker, ativo_id, setor, score_micro, score_macro, score_estat,
      score_final, kpi_macro_id, data_calculo — em tipos nativos.
    A seleção dos candidatos e a alocação ficam em selecionar_candidatos()
    e em scoring.services.carteira.montar_carteira_otimizada().

    universo: tickers investíveis (histórico e liquidez). None = calcula pelo
    banco (universo_investivel); o backtest passa o universo da própria data.
    """
    from market_data.models import KpiMicro, KpiMacro, KpiEstatistico

    data_ref    = data_ref or date.today()
    cfg         = cfg or config_perfil(perfil)
    pesos       = cfg['pesos']

    # ── 1. Fundamentalistas: DFP mais recente já publicada ────────
    campos_micro = ['liquidez_corrente', 'liquidez_seca', 'liquidez_imediata',
                    'liquidez_geral', 'roe', 'roa', 'giro_ativo',
                    'margem_liquida', 'margem_ebitda', 'divida_ebitda',
                    'crescimento_receita', 'crescimento_lucro']
    limite_dfp = data_ref - timedelta(days=90)
    rows = list(KpiMicro.objects
                .filter(data_ref__lte=limite_dfp, ativo__ativo=True)
                .order_by('ativo_id', '-data_ref')
                .values('ativo_id', 'ativo__ticker', 'ativo__setor', 'ativo__cnpj', *campos_micro))
    rows = _ultimo_por_ativo(rows, 'data_ref')
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).rename(columns={'ativo__ticker': 'ticker', 'ativo__setor': 'setor',
                                            'ativo__cnpj': 'cnpj'})
    df['setor'] = df['setor'].fillna('')
    # chave da companhia: CNPJ só com dígitos (ON, PN e unit da mesma empresa compartilham)
    df['empresa'] = df['cnpj'].fillna('').str.replace(r'\D', '', regex=True)
    df.loc[df['empresa'] == '', 'empresa'] = 'T:' + df['ticker']
    for c in campos_micro:
        df[c] = pd.to_numeric(df[c], errors='coerce').astype(float)

    # Universo investível ANTES dos percentis (ver universo_investivel)
    if universo is None and estat_externo is None:
        universo = universo_investivel(data_ref)
    if universo is not None:
        df = df[df['ticker'].isin(universo)]

    # Setor financeiro: indicadores sem interpretação ficam neutros
    financeiro = df['setor'].map(eh_financeiro).astype(bool)
    df.loc[financeiro, CAMPOS_NAO_FINANCEIROS] = np.nan
    df = _aplicar_filtros_kpi(df, cfg.get('filtros_kpi', {}), isentos=financeiro)
    if df.empty:
        return pd.DataFrame()
    df = df.set_index('ticker')

    # ── 2. Escore fundamentalista (pesos das dimensões por perfil) ─
    # O ranking percentil é feito por COMPANHIA (uma linha por CNPJ), para que
    # empresas com várias classes de ação não pesem em dobro na normalização.
    uma_por_empresa = df[~df['empresa'].duplicated()]
    s_micro = calcular_score_micro_v2(uma_por_empresa, cfg['micro_pesos'])
    df['score_micro'] = df['empresa'].map(dict(zip(uma_por_empresa['empresa'], s_micro.reindex(uma_por_empresa.index))))
    dims = dimensoes_micro(uma_por_empresa)
    for dcol in dims.columns:
        df[f'dim_{dcol}'] = df['empresa'].map(dict(zip(uma_por_empresa['empresa'], dims[dcol].round(1))))

    # ── 3. Escore macro (igual para todos os ativos) ───────────────
    from scoring.services.macro import macro_consolidado
    macro = macro_consolidado(data_ref)          # último valor de CADA indicador
    df['score_macro'] = calcular_score_macro(macro)

    # ── 4. Escore estatístico (neutro = 50 se não houver dado) ─────
    df['score_estat'] = _VALOR_AUSENTE.get()
    if estat_externo is not None:
        estat_rows = []
        if not estat_externo.empty:
            estat_externo = estat_externo[estat_externo.index.isin(df.index)]
            s_estat = calcular_score_estat(estat_externo, perfil)
            df['score_estat'] = s_estat.reindex(df.index).fillna(_VALOR_AUSENTE.get())
    else:
        estat_rows = list(KpiEstatistico.objects
                      .filter(ativo_id__in=df['ativo_id'].tolist(), data_calculo__lte=data_ref)
                      .order_by('ativo_id', '-data_calculo')
                      .values('ativo_id', 'ativo__ticker', 'volatilidade_anual', 'beta',
                              'skewness', 'curtose', 'retorno_normal', 'cv',
                              'media_retorno', 'dp_retorno'))
        estat_rows = _ultimo_por_ativo(estat_rows, 'data_calculo')
    if estat_rows:
        df_estat = pd.DataFrame(estat_rows).set_index('ativo__ticker').drop(columns='ativo_id')
        for c in df_estat.columns:
            if c != 'retorno_normal':
                df_estat[c] = pd.to_numeric(df_estat[c], errors='coerce').astype(float)
        s_estat = calcular_score_estat(df_estat, perfil)
        df['score_estat'] = s_estat.reindex(df.index).fillna(_VALOR_AUSENTE.get())

    # ── 5. Escore final: média ponderada renormalizada ─────────────
    total = sum(pesos.values())
    df['score_final'] = (df['score_micro'] * pesos.get('micro', 0) +
                         df['score_macro'] * pesos.get('macro', 0) +
                         df['score_estat'] * pesos.get('estat', 0)) / total

    df = df.reset_index().sort_values('score_final', ascending=False)
    df['score_final'] = df['score_final'].round(2)
    df['ativo_id'] = df['ativo_id'].astype(int)
    df['kpi_macro_id'] = int(macro.id) if macro else None
    df['data_calculo'] = data_ref.isoformat()
    cols = ['ticker', 'ativo_id', 'setor', 'empresa', 'score_micro', 'score_macro',
            'score_estat', 'score_final', 'kpi_macro_id', 'data_calculo']
    cols += [c for c in df.columns if c.startswith('dim_')]
    return df[cols].reset_index(drop=True)


def selecionar_candidatos(df_scores: pd.DataFrame, perfil: str,
                          setor: str | None = None, cfg: dict | None = None,
                          um_por_empresa: bool = True) -> pd.DataFrame:
    """Aplica escore mínimo, filtro setorial opcional e número de ativos do perfil.
    Com um_por_empresa, fica só o ticker de maior escore de cada companhia
    (ITUB3 e ITUB4 juntos na carteira seriam o mesmo risco duas vezes)."""
    if df_scores is None or df_scores.empty:
        return pd.DataFrame()
    cfg = cfg or config_perfil(perfil)
    df = df_scores[df_scores['score_final'] >= cfg['score_min']]
    df = df[df['ticker'].str.len().between(4, 6) & ~df['ticker'].str.contains(' ')]
    if setor:
        # um setor (texto) ou vários (lista); vazio = todos
        setores = [setor] if isinstance(setor, str) else [x for x in setor if x]
        if setores:
            df = df[df['setor'].isin(setores)]
    df = df.sort_values('score_final', ascending=False)
    if um_por_empresa and 'empresa' in df.columns:
        df = df[~df['empresa'].duplicated()]
    return df.head(cfg['qtd_ativos'])


def calcular_scores_todos_ativos(data_ref=None, perfil="intermediario"):
    """Compatibilidade com código antigo."""
    return calcular_scores_v2(perfil=perfil, data_ref=data_ref)
