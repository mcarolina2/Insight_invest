import logging
import numpy as np
import pandas as pd
from services.extractors.cvm_extractor import extrair_bp, baixar_arquivo_cvm, filtrar_contas, CONTAS_BP_ATIVO, CONTAS_BP_PASSIVO


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Liquidez
# ---------------------------------------------------------------------------

def liquidez_corrente(ac: float, pc: float) -> float | None:
    """Ativo Circulante / Passivo Circulante."""
    return round(ac / pc, 4) if pc and pc != 0 else None


def liquidez_seca(ac: float, estoques: float, pc: float) -> float | None:
    """(AC - Estoques) / PC — exclui estoques por serem menos líquidos."""
    if not pc or pc == 0:
        return None
    return round((ac - (estoques or 0)) / pc, 4)


def liquidez_imediata(caixa: float, pc: float) -> float | None:
    """Caixa e equivalentes / PC — o grau de liquidez mais conservador."""
    return round(caixa / pc, 4) if pc and pc != 0 else None


def liquidez_geral(ac: float, rlp: float, pc: float, pnc: float) -> float | None:
    """
    (AC + Realizável a Longo Prazo) / (PC + Passivo Não Circulante).
    Mede a saúde financeira no longo prazo.
    """
    denominador = (pc or 0) + (pnc or 0)
    if denominador == 0:
        return None
    return round(((ac or 0) + (rlp or 0)) / denominador, 4)


# ---------------------------------------------------------------------------
# Endividamento
# ---------------------------------------------------------------------------

def divida_bruta(emp_cp: float, emp_lp: float) -> float:
    """Total de empréstimos e financiamentos (CP + LP)."""
    return (emp_cp or 0) + (emp_lp or 0)


def divida_liquida(emp_cp: float, emp_lp: float, caixa: float) -> float:
    """Dívida Bruta - Caixa e Equivalentes."""
    return divida_bruta(emp_cp, emp_lp) - (caixa or 0)


def divida_sobre_ebitda(div_liq: float, ebitda: float) -> float | None:
    """Dívida Líquida / EBITDA — indicador central de alavancagem."""
    return round(div_liq / ebitda, 4) if ebitda and ebitda != 0 else None


def divida_sobre_pl(div_bruta: float, pl: float) -> float | None:
    """Dívida Bruta / Patrimônio Líquido."""
    return round(div_bruta / pl, 4) if pl and pl != 0 else None


# ---------------------------------------------------------------------------
# EBITDA (Earnings Before Interest, Taxes, Depreciation and Amortization)
# ---------------------------------------------------------------------------

def calcular_ebitda(resultado_operacional: float, depreciacao: float = 0) -> float:
    """
    EBITDA = EBIT + Depreciação + Amortização.
    Quando D&A não está disponível explicitamente, usamos o EBIT (conta 3.05)
    como proxy conservadora. Para cálculo exato, cruzar com DFC_MD (conta 6.02.01).
    """
    return (resultado_operacional or 0) + (depreciacao or 0)


# ---------------------------------------------------------------------------
# Rentabilidade
# ---------------------------------------------------------------------------

def roe(lucro_liquido: float, pl: float) -> float | None:
    """Return on Equity — retorno sobre o patrimônio dos acionistas."""
    return round(lucro_liquido / pl, 4) if pl and pl != 0 else None


def roa(lucro_liquido: float, ativo_total: float) -> float | None:
    """Return on Assets — eficiência do uso dos ativos."""
    return round(lucro_liquido / ativo_total, 4) if ativo_total and ativo_total != 0 else None


def margem_bruta(resultado_bruto: float, receita: float) -> float | None:
    return round(resultado_bruto / receita, 4) if receita and receita != 0 else None


def margem_ebitda(ebitda: float, receita: float) -> float | None:
    return round(ebitda / receita, 4) if receita and receita != 0 else None


def margem_liquida(lucro_liquido: float, receita: float) -> float | None:
    return round(lucro_liquido / receita, 4) if receita and receita != 0 else None


# ---------------------------------------------------------------------------
# Crescimento (série temporal)
# ---------------------------------------------------------------------------

def crescimento_yoy(valor_atual: float, valor_anterior: float) -> float | None:
    """Variação Year-over-Year (crescimento anual)."""
    if not valor_anterior or valor_anterior == 0:
        return None
    return round((valor_atual - valor_anterior) / abs(valor_anterior), 4)


def cagr(valor_inicial: float, valor_final: float, anos: int) -> float | None:
    """
    Compound Annual Growth Rate — taxa de crescimento anual composta.
    Usada para medir crescimento de receita ou lucro em múltiplos anos.
    """
    if not valor_inicial or valor_inicial <= 0 or anos <= 0:
        return None
    return round((valor_final / valor_inicial) ** (1 / anos) - 1, 4)


# ---------------------------------------------------------------------------
# Pipeline principal: BP + DRE → KpiMicro dict
# ---------------------------------------------------------------------------

def calcular_kpis_micro(bp_row: dict, dre_row: dict, dre_anterior: dict = None) -> dict:
    """
    Recebe uma linha do BP e uma linha da DRE (dicts) e retorna
    todos os indicadores fundamentalistas calculados.

    Args:
        bp_row:         Dict com campos do Balanço Patrimonial do período
        dre_row:        Dict com campos da DRE do período
        dre_anterior:   Dict com DRE do período anterior (para crescimento)

    Returns:
        Dict compatível com os campos do model KpiMicro
    """
    # Extrai valores do BP
    ac         = bp_row.get("ativo_circulante", 0) or 0
    caixa      = bp_row.get("caixa_equivalentes", 0) or 0
    estoques   = bp_row.get("estoques", 0) or 0
    rlp        = bp_row.get("realizavel_lp", 0) or 0
    at         = bp_row.get("ativo_total", 0) or 0
    pc         = bp_row.get("passivo_circulante", 0) or 0
    pnc        = bp_row.get("passivo_nao_circulante", 0) or 0
    emp_cp     = bp_row.get("emprestimos_cp", 0) or 0
    emp_lp     = bp_row.get("emprestimos_lp", 0) or 0
    pl         = bp_row.get("patrimonio_liquido", 0) or 0
    

    # Extrai valores da DRE
    receita     = dre_row.get("receita_liquida", 0) or 0
    res_bruto   = dre_row.get("resultado_bruto", 0) or 0
    res_oper    = dre_row.get("resultado_operacional", 0) or 0
    lucro       = dre_row.get("lucro_liquido", 0) or 0

     # Cálculos derivados
    db          = divida_bruta(emp_cp, emp_lp)
    dl          = divida_liquida(emp_cp, emp_lp, caixa)
    ebitda_val  = calcular_ebitda(res_oper)

    # Crescimento vs período anterior
    cresc_receita = None
    cresc_lucro   = None
    if dre_anterior:
        cresc_receita = crescimento_yoy(receita, dre_anterior.get("receita_liquida") or 0)
        cresc_lucro   = crescimento_yoy(lucro,   dre_anterior.get("lucro_liquido") or 0)

    giro_at = round(receita / at, 4) if at and at != 0 else None
    # (linhas de "prio"/"print" removidas — referenciavam 'bp', que não existe neste escopo)

    return {
        # Liquidez
        "liquidez_corrente":  liquidez_corrente(ac, pc),
        "liquidez_seca":      liquidez_seca(ac, estoques, pc),
        "liquidez_imediata":  liquidez_imediata(caixa, pc),
        "liquidez_geral":     liquidez_geral(ac, rlp, pc, pnc),

        # Endividamento
        "divida_bruta":       round(db, 2),
        "divida_liquida":     round(dl, 2),
        "divida_ebitda":      divida_sobre_ebitda(dl, ebitda_val),
        "divida_pl":          divida_sobre_pl(db, pl),

        # Rentabilidade
        "roe":                roe(lucro, pl),
        "roa":                roa(lucro, at),
        "giro_ativo":         giro_at,
        "margem_bruta":       margem_bruta(res_bruto, receita),
        "margem_ebitda":      margem_ebitda(ebitda_val, receita),
        "margem_liquida":     margem_liquida(lucro, receita),

        # Crescimento
        "crescimento_receita": cresc_receita,
        "crescimento_lucro":   cresc_lucro,

        # Valores absolutos (uteis para o scoring e calculos de valuation)
        "ebitda":             round(ebitda_val, 2),
        "receita_liquida":    round(receita, 2),
        "lucro_liquido":      round(lucro, 2),
        "patrimonio_liquido": round(pl, 2),
        "ativo_total":        round(at, 2),
    }


def processar_dataframe(df_bp: pd.DataFrame, df_dre: pd.DataFrame) -> pd.DataFrame:
    resultados = []

    for cnpj, grupo_bp in df_bp.groupby("CNPJ_CIA"):
        grupo_dre = df_dre[df_dre["CNPJ_CIA"] == cnpj].sort_values("ano")
        anos_disponiveis = sorted(grupo_bp["ano"].unique())

        for i, ano in enumerate(anos_disponiveis):
            bp_row = grupo_bp[grupo_bp["ano"] == ano].iloc[0].to_dict()
            dre_rows = grupo_dre[grupo_dre["ano"] == ano]
            if dre_rows.empty:
                continue
            dre_row = dre_rows.iloc[0].to_dict()

            dre_ant = None
            if i > 0:
                ano_ant = anos_disponiveis[i - 1]
                dre_ant_rows = grupo_dre[grupo_dre["ano"] == ano_ant]
                if not dre_ant_rows.empty:
                    dre_ant = dre_ant_rows.iloc[0].to_dict()

            kpis = calcular_kpis_micro(bp_row, dre_row, dre_ant)
            kpis.update({
                "cnpj": cnpj,
                "empresa": bp_row.get("DENOM_CIA", ""),
                "ano": ano,
                "data_ref": bp_row.get("DT_REFER", ""),
            })
            resultados.append(kpis)

    return pd.DataFrame(resultados) 


if __name__ == "__main__":
    print("Teste BP")

    bp = extrair_bp(2023)

    print("\nColunas:")
    print(bp.columns.tolist())

    print("\nPrimeiras linhas:")
    print(bp.head())

    print("\nPRIO:")
    prio = bp[bp["DENOM_CIA"].str.contains("PRIO", case=False, na=False)]
    print(prio.T)

