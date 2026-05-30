"""
Motor de recomendação de carteira do Insight Invest.

Recebe os scores calculados e gera uma carteira ideal para um usuário
com base no seu perfil de risco.

Lógica de alocação:
  1. Filtra ativos elegíveis para o perfil
  2. Seleciona os top N por score_final
  3. Distribui o percentual proporcional ao score
  4. Aplica restrições de concentração (máx por ativo e por setor)
  5. Salva em Recomendacao + ItemRecomendacao
"""

import logging
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configurações por perfil de risco
# ---------------------------------------------------------------------------

CONFIG_PERFIL = {
    "conservador": {
        "score_minimo":      55,    # só ativos com score acima disso
        "qtd_ativos":        8,     # carteira mais concentrada em boas empresas
        "max_pct_por_ativo": 20.0,  # no máximo 20% em um único ativo
        "max_pct_por_setor": 35.0,  # no máximo 35% em um setor
        "tipos_permitidos":  ["acao", "fii"],  # sem BDRs e cripto
    },
    "intermediario": {
        "score_minimo":      45,
        "qtd_ativos":        12,
        "max_pct_por_ativo": 15.0,
        "max_pct_por_setor": 40.0,
        "tipos_permitidos":  ["acao", "fii", "etf"],
    },
    "arrojado": {
        "score_minimo":      35,
        "qtd_ativos":        20,
        "max_pct_por_ativo": 12.0,
        "max_pct_por_setor": 50.0,
        "tipos_permitidos":  ["acao", "fii", "etf", "bdr"],
    },
}


# ---------------------------------------------------------------------------
# Filtro e seleção de ativos
# ---------------------------------------------------------------------------

def selecionar_ativos(df_scores: pd.DataFrame, perfil: str) -> pd.DataFrame:
    """
    Filtra e seleciona os ativos elegíveis para o perfil de risco.

    Args:
        df_scores: DataFrame com colunas: ticker, score_final, setor, tipo, ativo_id
        perfil:    'conservador' | 'intermediario' | 'arrojado'

    Returns:
        DataFrame filtrado e ordenado por score_final decrescente
    """
    config = CONFIG_PERFIL.get(perfil, CONFIG_PERFIL["intermediario"])

    df = df_scores.copy()

    # Filtra por score mínimo
    df = df[df["score_final"] >= config["score_minimo"]]

    # Filtra por tipo de ativo permitido
    if "tipo" in df.columns:
        df = df[df["tipo"].isin(config["tipos_permitidos"])]

    # Remove ativos sem ticker válido
    if "ticker" in df.columns:
        df = df[df["ticker"].str.len() >= 4]
        df = df[~df["ticker"].str.contains(" ")]

    # Ordena por score
    df = df.sort_values("score_final", ascending=False)

    # Limita ao número de ativos do perfil
    df = df.head(config["qtd_ativos"] * 2)  # pega o dobro para ter margem após restrições

    return df


def calcular_alocacao(df_selecionados: pd.DataFrame, perfil: str) -> pd.DataFrame:
    """
    Calcula o percentual de alocação por ativo usando os scores como peso.
    Aplica restrições de concentração por ativo e por setor.

    Args:
        df_selecionados: DataFrame filtrado com ativos elegíveis
        perfil:          perfil de risco

    Returns:
        DataFrame com coluna 'percentual' adicionada (soma = 100%)
    """
    config = CONFIG_PERFIL.get(perfil, CONFIG_PERFIL["intermediario"])
    qtd_max = config["qtd_ativos"]
    max_ativo = config["max_pct_por_ativo"]
    max_setor = config["max_pct_por_setor"]

    df = df_selecionados.copy().head(qtd_max * 2)

    # Alocação proporcional ao score
    total_score = df["score_final"].sum()
    if total_score == 0:
        df["percentual_bruto"] = 100 / len(df)
    else:
        df["percentual_bruto"] = (df["score_final"] / total_score * 100)

    # Aplica cap por ativo
    df["percentual"] = df["percentual_bruto"].clip(upper=max_ativo)

    # Aplica cap por setor se a coluna existir
    if "setor" in df.columns:
        setor_totais = df.groupby("setor")["percentual"].transform("sum")
        excesso_setor = setor_totais > max_setor
        if excesso_setor.any():
            for setor in df[excesso_setor]["setor"].unique():
                mask = df["setor"] == setor
                total_setor = df.loc[mask, "percentual"].sum()
                if total_setor > max_setor:
                    fator = max_setor / total_setor
                    df.loc[mask, "percentual"] *= fator

    # Limita ao número desejado de ativos
    df = df.nlargest(qtd_max, "percentual")

    # Renormaliza para 100%
    total = df["percentual"].sum()
    df["percentual"] = (df["percentual"] / total * 100).round(2)

    # Ajuste de arredondamento para garantir soma exata de 100%
    diferenca = 100.0 - df["percentual"].sum()
    if abs(diferenca) > 0.01:
        idx_maior = df["percentual"].idxmax()
        df.loc[idx_maior, "percentual"] += round(diferenca, 2)

    return df[["ticker", "ativo_id", "score_final", "percentual",
               "score_micro", "score_macro", "score_time", "score_sentimento"]]


# ---------------------------------------------------------------------------
# Geração da justificativa
# ---------------------------------------------------------------------------

def gerar_justificativa_item(row: pd.Series) -> str:
    """
    Gera uma justificativa textual para a inclusão de um ativo na carteira.
    """
    partes = []

    score = float(row.get("score_final", 50))
    if score >= 75:
        partes.append(f"Score elevado ({score:.0f}/100)")
    elif score >= 60:
        partes.append(f"Score sólido ({score:.0f}/100)")
    else:
        partes.append(f"Score moderado ({score:.0f}/100)")

    s_micro = float(row.get("score_micro", 50))
    if s_micro >= 70:
        partes.append("fundamentos fortes")
    elif s_micro <= 35:
        partes.append("fundamentos fracos — monitorar")

    s_time = float(row.get("score_time", 50))
    if s_time >= 70:
        partes.append("bom momento técnico")
    elif s_time <= 30:
        partes.append("timing desfavorável")

    s_sent = float(row.get("score_sentimento", 50))
    if s_sent >= 70:
        partes.append("sentimento positivo no mercado")
    elif s_sent <= 30:
        partes.append("sentimento negativo — cautela")

    return ". ".join(partes) + "."


def gerar_justificativa_carteira(df_alocacao: pd.DataFrame, perfil: str, score_macro: float) -> str:
    """Gera a justificativa geral da carteira recomendada."""
    top3 = df_alocacao.nlargest(3, "score_final")["ticker"].tolist()
    media_score = df_alocacao["score_final"].mean()

    macro_desc = (
        "favorável" if score_macro >= 60 else
        "neutro"    if score_macro >= 40 else
        "desafiador"
    )

    return (
        f"Carteira recomendada para perfil {perfil.title()} "
        f"com cenário macroeconômico {macro_desc} (score macro: {score_macro:.0f}/100). "
        f"Score médio da carteira: {media_score:.1f}/100. "
        f"Principais posições: {', '.join(top3)}."
    )


# ---------------------------------------------------------------------------
# Pipeline principal de recomendação
# ---------------------------------------------------------------------------

def gerar_recomendacao(user, df_scores: pd.DataFrame, perfil: str) -> Optional[object]:
    """
    Gera e salva uma Recomendacao completa para um usuário.

    Args:
        user:      objeto User do Django
        df_scores: DataFrame retornado por calcular_scores_todos_ativos()
        perfil:    perfil de risco do usuário

    Returns:
        objeto Recomendacao salvo no banco, ou None se falhar
    """
    from scoring.models import Recomendacao, ItemRecomendacao, ScoreAtivo
    from portfolio.models import Ativo

    if df_scores.empty:
        logger.warning("DataFrame de scores vazio — sem recomendação gerada.")
        return None

    # Enriquece df_scores com tipo e setor dos ativos
    ativos_info = {
        a.ticker: {"tipo": a.tipo, "setor": a.setor or ""}
        for a in Ativo.objects.filter(ticker__in=df_scores["ticker"].tolist())
    }
    df_scores["tipo"]  = df_scores["ticker"].map(lambda t: ativos_info.get(t, {}).get("tipo", "acao"))
    df_scores["setor"] = df_scores["ticker"].map(lambda t: ativos_info.get(t, {}).get("setor", ""))

    # 1. Seleciona ativos elegíveis
    df_elegivel = selecionar_ativos(df_scores, perfil)
    if df_elegivel.empty:
        logger.warning(f"Nenhum ativo elegível para perfil {perfil}.")
        return None

    # 2. Calcula alocação
    df_alocacao = calcular_alocacao(df_elegivel, perfil)

    # 3. Métricas da carteira (simplificadas)
    score_macro_val = float(df_scores["score_macro"].iloc[0]) if "score_macro" in df_scores.columns else 50.0

    # 4. Salva Recomendacao
    justificativa = gerar_justificativa_carteira(df_alocacao, perfil, score_macro_val)

    recomendacao = Recomendacao.objects.create(
        user         = user,
        justificativa= justificativa,
        status       = "ativa",
    )

    # 5. Salva cada ItemRecomendacao
    for _, row in df_alocacao.iterrows():
        ticker   = row["ticker"]
        ativo_id = row["ativo_id"]

        # Busca o ScoreAtivo correspondente
        score_ref = (
            ScoreAtivo.objects
            .filter(ativo_id=ativo_id)
            .order_by("-data_calculo")
            .first()
        )

        # Determina a ação sugerida baseada no score
        score = float(row["score_final"])
        if score >= 70:
            tipo_acao = "comprar"
        elif score >= 50:
            tipo_acao = "manter"
        elif score >= 35:
            tipo_acao = "observar"
        else:
            tipo_acao = "vender"

        ItemRecomendacao.objects.create(
            recomendacao       = recomendacao,
            ativo_id           = ativo_id,
            tipo               = tipo_acao,
            percentual_ideal   = row["percentual"],
            score_ativo_ref    = score_ref,
            justificativa_item = gerar_justificativa_item(row),
        )

    logger.info(
        f"Recomendacao #{recomendacao.id} gerada para {user.username} "
        f"com {len(df_alocacao)} ativos."
    )
    return recomendacao