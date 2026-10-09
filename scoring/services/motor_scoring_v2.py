import logging
from datetime import date, timedelta
from decimal import Decimal
from pypfopt import risk_models, expected_returns

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PREGOES_ANO = 252


# =============================================================================
# CONFIGURAÇÃO POR PERFIL
# =============================================================================

PERFIS = {
    'conservador': {
        'score_min':      35,    # threshold baixo: poucos dados no banco
        'qtd_ativos':     8,
        'max_pct_ativo':  0.20,

        # Pesos das camadas disponíveis
        'pesos': {'micro': 0.50, 'macro': 0.30, 'estat': 0.20},

        # Pesos internos do score_micro (o que cada sub-indicador vale)
        'micro_pesos': {
            'liquidez':       0.35,   # conservador prioriza liquidez
            'rentabilidade':  0.30,
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
        'pesos': {'micro': 0.40, 'macro': 0.30, 'estat': 0.15},
        'micro_pesos': {
            'liquidez':       0.25,
            'rentabilidade':  0.35,   # intermediário equilibra
            'divida':         0.20,
            'crescimento':    0.20,
        },
        'filtros_kpi': {},            # sem filtros extras
    },

    'arrojado': {
        'score_min':      25,
        'qtd_ativos':     15,
        'max_pct_ativo':  0.12,
        'pesos': {'micro': 0.30, 'macro': 0.20, 'estat': 0.20},
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

def _norm(series: pd.Series, inverter: bool = False) -> pd.Series:
    """
    Normaliza uma série para 0-100 usando rank percentil.
    Robusto a outliers. Se inverter=True, menor valor = maior score.
    """
    if series.isna().all() or len(series) < 2:
        return pd.Series(50.0, index=series.index)
    ranked = series.rank(pct=True, na_option='keep') * 100
    return (100 - ranked if inverter else ranked).fillna(50)


# =============================================================================
# SCORE MICRO — configura os pesos por perfil
# =============================================================================

def calcular_score_micro_v2(df: pd.DataFrame, micro_pesos: dict) -> pd.Series:
    """
    Score fundamentalista com pesos configuráveis por perfil.
    
    O conservador dá peso 35% para liquidez.
    O arrojado dá peso 40% para crescimento.
    Isso garante que os mesmos dados geram rankings DIFERENTES por perfil.
    """
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

def calcular_score_macro(kpi_macro) -> float:
    if not kpi_macro:
        return 50.0
    p = 50.0
    p -= max(0, (float(kpi_macro.selic or 10) - 4) * 2)
    p -= max(0, (float(kpi_macro.ipca_mensal or 0.3) * 12 - 3) * 3)
    pib = float(kpi_macro.pib_trimestral or 0)
    p += pib * 5 if pib > 0 else pib * 3
    p -= max(0, (float(kpi_macro.desemprego or 10) - 8) * 1.5)
    return round(max(0, min(100, p)), 2)


# =============================================================================
# SCORE ESTATÍSTICO — recompensa estabilidade calibrada por perfil
# =============================================================================

def calcular_score_estat(df_estat: pd.DataFrame, perfil: str) -> pd.Series:
    """
    Score estatístico com lógica diferente por perfil:
      Conservador → recompensa baixa volatilidade e distribuição normal
      Arrojado    → aceita alta volatilidade se skewness for positivo
    """
    if df_estat.empty:
        return pd.Series(dtype=float)

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

    # CV: todos preferem menor risco relativo
    if 'cv' in df_estat.columns:
        scores['cv'] = _norm(df_estat['cv'].abs(), inverter=True)

    # Skewness positivo: bônus para todos, mas mais para arrojado
    bonus = 0
    if 'skewness' in df_estat.columns:
        skew_bonus = df_estat['skewness'].clip(-1, 1) * (8 if perfil == 'arrojado' else 4)
        bonus += skew_bonus

    # Distribuição normal: conservador valoriza previsibilidade
    if 'retorno_normal' in df_estat.columns and perfil == 'conservador':
        bonus += df_estat['retorno_normal'].map({True: 8, False: 0}).fillna(0)

    base = scores.mean(axis=1) if not scores.empty else pd.Series(50, index=df_estat.index)
    return (base + bonus).clip(0, 100).round(2)


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def calcular_scores_v2(perfil: str = 'intermediario') -> pd.DataFrame:
    """
    Calcula scores diferenciados por perfil usando os dados disponíveis.

    Retorna DataFrame com colunas:
      ticker, ativo_id, score_micro, score_macro, score_estat,
      score_final, setor — tudo em float nativo (não np.float64)
    """
    from market_data.models import KpiMicro, KpiMacro
    from portfolio.models import Ativo

    cfg         = PERFIS.get(perfil, PERFIS['intermediario'])
    micro_pesos = cfg['micro_pesos']
    pesos       = cfg['pesos']
    filtros_kpi = cfg.get('filtros_kpi', {})

    # ── 1. Carrega KpiMicro (mais recente por ativo) ──────────
    campos = [
        'ativo_id', 'ativo__ticker', 'ativo__setor',
        'liquidez_corrente', 'liquidez_seca', 'liquidez_imediata', 'liquidez_geral',
        'roe', 'roa', 'giro_ativo', 'margem_liquida', 'margem_ebitda',
        'divida_ebitda', 'crescimento_receita', 'crescimento_lucro',
    ]

    qs = (KpiMicro.objects
          .select_related('ativo')
          .order_by('ativo_id', '-data_ref')
          .distinct('ativo_id'))

    # Aplica filtros específicos do perfil
    if filtros_kpi:
        qs = qs.filter(**filtros_kpi)

    rows = []
    for k in qs:
        row = {
            'ativo_id': k.ativo_id,
            'ticker':   k.ativo.ticker,
            'setor':    k.ativo.setor or '',
        }
        for campo in ['liquidez_corrente', 'liquidez_seca', 'liquidez_imediata',
                      'liquidez_geral', 'roe', 'roa', 'giro_ativo',
                      'margem_liquida', 'margem_ebitda', 'divida_ebitda',
                      'crescimento_receita', 'crescimento_lucro']:
            v = getattr(k, campo, None)
            row[campo] = float(v) if isinstance(v, Decimal) else (float(v) if v is not None else None)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index('ticker')

    # ── 2. Score micro com pesos do perfil ────────────────────
    df['score_micro'] = calcular_score_micro_v2(df, micro_pesos)

    # ── 3. Score macro (igual para todos) ────────────────────
    macro = KpiMacro.objects.order_by('-data_ref').first()
    df['score_macro'] = calcular_score_macro(macro)

    # ── 4. Score estatístico (se disponível) ──────────────────
    df['score_estat'] = 50.0  # default
    try:
        from market_data.models import KpiEstatistico
        estat_qs = (KpiEstatistico.objects
                    .order_by('ativo_id', '-data_calculo')
                    .distinct('ativo_id')
                    .values('ativo__ticker', 'volatilidade_anual', 'beta',
                            'skewness', 'curtose', 'retorno_normal', 'cv'))
        estat_rows = [
            {
                'ticker': e['ativo__ticker'],
                **{k: (float(v) if isinstance(v, Decimal) else
                       (float(v) if v is not None else None))
                   for k, v in e.items() if k != 'ativo__ticker'}
            }
            for e in estat_qs
        ]
        if estat_rows:
            df_estat = pd.DataFrame(estat_rows).set_index('ticker')
            s_estat  = calcular_score_estat(df_estat, perfil)
            df['score_estat'] = s_estat.reindex(df.index).fillna(50)
    except Exception as e:
        logger.warning(f"Score estatístico indisponível: {e}")

    # ── 5. Score final ponderado pelos pesos do perfil ────────
    total_peso = sum(pesos.values())
    df['score_final'] = (
        df['score_micro']  * pesos.get('micro', 0.40) / total_peso +
        df['score_macro']  * pesos.get('macro', 0.30) / total_peso +
        df['score_estat']  * pesos.get('estat', 0.15) / total_peso
    ).round(2)

    # Adiciona score_time e sentimento se disponíveis
    if 'time' in pesos:
        try:
            from scoring.services.motor_scoring import calcular_score_time
            from market_data.models import KpiTime
            time_qs = (KpiTime.objects.select_related('ativo')
                       .filter(data_ref__gte=date.today() - timedelta(days=5))
                       .order_by('ativo_id', '-data_ref').distinct('ativo_id'))
            time_rows = [{'ticker': k.ativo.ticker,
                          'rsi_14': float(k.rsi_14 or 50),
                          'retorno_12m': float(k.retorno_12m or 0),
                          'volatilidade_30d': float(k.volatilidade_30d or 0)}
                         for k in time_qs]
            if time_rows:
                df_time  = pd.DataFrame(time_rows).set_index('ticker')
                s_time   = calcular_score_time(df_time)
                df['score_time'] = s_time.reindex(df.index).fillna(50)
                df['score_final'] += (df.get('score_time', 50)
                                      * pesos.get('time', 0) / total_peso)
        except Exception:
            pass

    # ── 6. Converte tudo para float nativo ─────────────────────
    for col in df.select_dtypes(include=[np.floating, np.integer]).columns:
        df[col] = df[col].astype(float)

    df['ativo_id'] = df['ativo_id'].astype(int)
    df['kpi_macro_id'] = int(macro.id) if macro else None
    df['data_calculo'] = date.today().isoformat()

    # ─────────────────────────────────────────────────────────────
    # 7. Seleciona os melhores ativos
    # ─────────────────────────────────────────────────────────────
    # adicionar logo antes da linha "df = df.sort_values(by="score_final", ascending=False)"


    df = df.sort_values(
        by="score_final",
        ascending=False
    )

    qtd_ativos = cfg.get("qtd_ativos", 10)

    top_df = df.head(qtd_ativos).copy()

    # ─────────────────────────────────────────────────────────────
    # 8. Alocação via Markowitz
    # ─────────────────────────────────────────────────────────────
    try:
        import yfinance as yf
        from pypfopt import EfficientFrontier
        from pypfopt import risk_models, expected_returns

        tickers = top_df.index.tolist()

        if not tickers:
            raise Exception("Nenhum ativo selecionado pelo scoring")

        yahoo_tickers = [f"{ticker}.SA" for ticker in tickers]

        logger.info(
            f"Ativos selecionados para Markowitz: {yahoo_tickers}"
        )

        # ==========================================================
        # 1. DOWNLOAD DOS PREÇOS
        # ==========================================================

        precos = yf.download(
            yahoo_tickers,
            period="3y",
            auto_adjust=True,
            progress=False
        )["Close"]

        # Caso apenas um ticker seja retornado
        if isinstance(precos, pd.Series):
            precos = precos.to_frame()

        # ==========================================================
        # 2. REMOVER ATIVOS SEM HISTÓRICO
        # ==========================================================

        precos = precos.dropna(
            axis=1,
            how="all"
        )

        if precos.empty:
            raise Exception(
                "Yahoo Finance não retornou preços válidos"
            )

        # ==========================================================
        # 3. EXIGIR HISTÓRICO MÍNIMO
        # ==========================================================

        minimo_observacoes = 252

        precos = precos.dropna(
            axis=1,
            thresh=minimo_observacoes
        )

        if precos.shape[1] < 2:
            raise Exception(
                "Menos de dois ativos possuem histórico suficiente"
            )

        # ==========================================================
        # 4. PREENCHER PEQUENAS LACUNAS
        # ==========================================================

        precos = precos.ffill()

        # Remove datas que ainda possuem valores ausentes
        precos = precos.dropna()

        if len(precos) < 252:
            raise Exception(
                "Histórico temporal insuficiente para otimização"
            )

        logger.info(
            f"Ativos válidos para Markowitz: "
            f"{list(precos.columns)}"
        )

        logger.info(
            f"Observações utilizadas: {len(precos)}"
        )

        # ==========================================================
        # 5. RETORNO ESPERADO
        # ==========================================================

        mu = expected_returns.mean_historical_return(
            precos,
            compounding=True
        )

        # ==========================================================
        # 6. COVARIÂNCIA COM LEDOIT-WOLF
        # ==========================================================

        cov = risk_models.CovarianceShrinkage(
            precos
        ).ledoit_wolf()

        # ==========================================================
        # 7. MARKOWITZ
        # ==========================================================
        max_pct = cfg.get(
            "max_pct_ativo",
            0.20
        )

        # Número de ativos que realmente chegaram ao Markowitz
        n_ativos = len(precos.columns)

        if n_ativos == 0:
            raise ValueError(
                "Nenhum ativo possui dados válidos para o Markowitz."
            )

        # O teto máximo precisa permitir que os pesos somem 100%.
        # Ex.: com 4 ativos, teto de 20% permitiria no máximo 80%.
        teto_minimo_viavel = 1.0 / n_ativos

        peso_maximo = max(
            max_pct,
            teto_minimo_viavel
        )

        logger.info(
            f"Markowitz | ativos={n_ativos} | "
            f"teto_configurado={max_pct:.2%} | "
            f"teto_utilizado={peso_maximo:.2%}"
        )

        # Não obrigamos cada ativo a receber peso mínimo.
        # O otimizador pode atribuir peso zero.
        logger.info("========== DIAGNÓSTICO MARKOWITZ ==========")
        logger.info(f"Colunas de preços: {list(precos.columns)}")
        logger.info(f"Formato dos preços: {precos.shape}")
        logger.info(f"Formato de mu: {mu.shape}")
        logger.info(f"Formato de cov: {cov.shape}")
        logger.info(f"NaN em mu: {mu.isna().sum()}")
        logger.info(f"NaN em cov: {cov.isna().sum().sum()}")
        logger.info(f"Teto utilizado: {peso_maximo:.2%}")
        logger.info("===========================================")


        
        ef = EfficientFrontier(
            mu,
            cov,
            weight_bounds=(0, peso_maximo)
        )

        ef.max_sharpe()

        pesos = ef.clean_weights()

        logger.info(
            f"Pesos Markowitz: {pesos}"
        )

        # ==========================================================
        # 8. TRANSFERIR PESOS PARA O DATAFRAME
        # ==========================================================

        top_df["pct_alocacao"] = (
            top_df.index.map(
                lambda ticker:
                    pesos.get(
                        f"{ticker}.SA",
                        0
                    ) * 100
            )
        ).round(2)

        top_df["metodo_alocacao"] = "Markowitz"

    except Exception as e:

        logger.warning(
            f"Markowitz falhou: {e}"
        )

        # ==========================================================
        # FALLBACK
        # ==========================================================

        total_score = (
            float(top_df["score_final"].sum())
            or 1
        )

        top_df["pct_alocacao"] = (
            top_df["score_final"]
            / total_score
            * 100
        ).round(2)

        top_df["metodo_alocacao"] = "Score fallback"

    return top_df.reset_index()
def calcular_scores_todos_ativos(
    data_ref=None,
    perfil="intermediario"
):
    return calcular_scores_v2(perfil)