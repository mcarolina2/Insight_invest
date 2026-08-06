"""
Extrator de dados da CVM (Comissão de Valores Mobiliários)
Fonte oficial: https://dados.cvm.gov.br

Baixa os arquivos DFP (anuais) e ITR (trimestrais) diretamente do portal
de dados abertos da CVM, extraindo Balanço Patrimonial e DRE.

Arquivos disponíveis por ano:
  dfp_cia_aberta_BPA_con_{ano}.zip  → Balanço Ativo Consolidado
  dfp_cia_aberta_BPP_con_{ano}.zip  → Balanço Passivo Consolidado
  dfp_cia_aberta_DRE_con_{ano}.zip  → Demonstração de Resultado

Estrutura dos arquivos CSV (campos principais):
  CNPJ_CIA   → CNPJ da empresa
  DENOM_CIA  → Nome da empresa
  DT_REFER   → Data de referência
  CONTA      → Código da conta (ex: "1.01" = Ativo Circulante)
  DS_CONTA   → Descrição da conta
  VL_CONTA   → Valor da conta (em R$ mil)
"""

import io
import zipfile
import logging
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração das URLs e contas de interesse
# ---------------------------------------------------------------------------

CVM_BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"

# Tipos de demonstração disponíveis
DEMONSTRACOES = {
    "BPA": "Balanço Patrimonial Ativo",
    "BPP": "Balanço Patrimonial Passivo",
    "DRE": "Demonstração de Resultado",
    "DFC_MD": "Fluxo de Caixa (Método Direto)",
}

# Mapeamento de contas contábeis relevantes → nome amigável
# Baseado no padrão IFRS adotado pela CVM para empresas abertas
CONTAS_BP_ATIVO = {
    "1":        "ativo_total",
    "1.01":     "ativo_circulante",
    "1.01.01":  "caixa_equivalentes",
    "1.01.02":  "aplicacoes_financeiras_cp",
    "1.01.03":  "contas_receber",
    "1.01.04":  "estoques",
    "1.02":     "ativo_nao_circulante",
    "1.02.01":  "realizavel_lp",
    "1.02.03":  "imobilizado",
    "1.02.04":  "intangivel",
}

CONTAS_BP_PASSIVO = {
   # "2":        "passivo_total",
    "2.01":     "passivo_circulante",
    "2.01.04":  "emprestimos_cp",
    "2.02":     "passivo_nao_circulante",
    "2.02.01":  "emprestimos_lp",
    "2.03":     "patrimonio_liquido",
}

CONTAS_DRE = {
    "3.01":     "receita_liquida",
    "3.02":     "custo_bens_servicos",
    "3.03":     "resultado_bruto",
    "3.04":     "despesas_operacionais",
    "3.05":     "resultado_operacional",   # EBIT aproximado
    "3.06":     "resultado_financeiro",
    "3.07":     "resultado_equivalencia",
    "3.08":     "ebt",                     # Lucro antes de IR
    "3.09":     "imposto_renda",
    "3.11":     "lucro_liquido",
}


# ---------------------------------------------------------------------------
# Download e parsing
# ---------------------------------------------------------------------------

def baixar_arquivo_cvm(tipo: str, ano: int) -> pd.DataFrame:
    """
    Baixa e descompacta um arquivo DFP da CVM para um DataFrame.

    Args:
        tipo: 'BPA', 'BPP' ou 'DRE'
        ano:  Ano de referência (ex: 2023)

    Returns:
        DataFrame com todos os registros do arquivo
    """
    url = f"{CVM_BASE_URL}/dfp_cia_aberta_{ano}.zip"
    logger.info(f"Baixando: {url}")

    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        raise ValueError(f"Arquivo não encontrado: {url} (HTTP {resp.status_code})")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        # O arquivo CSV dentro do zip tem o mesmo nome sem o .zip
        csv_name = f"dfp_cia_aberta_{tipo}_con_{ano}.csv"
        with zf.open(csv_name) as f:
            df = pd.read_csv(
                f,
                sep=";",
                encoding="latin-1",
                dtype={"CNPJ_CIA": str, "CD_CONTA": str}, # <-- Alterado aqui
            )

    logger.info(f"Linhas carregadas: {len(df):,}")
    print(csv_name)
    return df


def filtrar_contas(df, contas_interesse):

    codigos = list(contas_interesse.keys())

    filtrado = df.copy()

    # somente exercício atual
    filtrado = filtrado[
        filtrado["ORDEM_EXERC"] == "ÚLTIMO"
    ]

    # somente última versão
    filtrado = (
        filtrado
        .sort_values(["DT_REFER","VERSAO","ORDEM_EXERC"])
        .drop_duplicates(
            subset=["CNPJ_CIA", "DT_REFER", "CD_CONTA"],
            keep="last"
        )
    )

    # somente contas desejadas
    filtrado = filtrado[
        filtrado["CD_CONTA"].isin(codigos)
    ]

    filtrado["INDICADOR"] = filtrado["CD_CONTA"].map(contas_interesse)

    pivot = filtrado.pivot_table(
        index=["CNPJ_CIA", "DENOM_CIA", "DT_REFER"],
        columns="INDICADOR",
        values="VL_CONTA",
        aggfunc="last"
    ).reset_index()

    return pivot


def extrair_bp(ano: int) -> pd.DataFrame:
    df_ativo  = baixar_arquivo_cvm("BPA", ano)
    df_passivo = baixar_arquivo_cvm("BPP", ano)

    pivot_ativo  = filtrar_contas(df_ativo,  CONTAS_BP_ATIVO)
    pivot_passivo = filtrar_contas(df_passivo, CONTAS_BP_PASSIVO)

    bp = pd.merge(
        pivot_ativo, pivot_passivo,
        on=["CNPJ_CIA", "DENOM_CIA", "DT_REFER"],
        how="outer"
    )
    bp["ano"] = ano
    return bp

def extrair_dre(ano: int) -> pd.DataFrame:
    df_dre = baixar_arquivo_cvm("DRE", ano)
    dre = filtrar_contas(df_dre, CONTAS_DRE)
    dre["ano"] = ano
    return dre


def extrair_historico(anos: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Baixa e consolida BP e DRE para múltiplos anos.

    Args:
        anos: lista de anos, ex: list(range(2016, 2025))

    Returns:
        (df_bp_historico, df_dre_historico)
    """
    bps, dres = [], []

    for ano in anos:
        try:
            bps.append(extrair_bp(ano))
            dres.append(extrair_dre(ano))
            logger.info(f"Ano {ano} extraído com sucesso.")
        except Exception as e:
            logger.warning(f"Falha ao extrair ano {ano}: {e}")

    df_bp  = pd.concat(bps,  ignore_index=True) if bps  else pd.DataFrame()
    df_dre = pd.concat(dres, ignore_index=True) if dres else pd.DataFrame()

     
    return df_bp, df_dre
     

# ---------------------------------------------------------------------------
# Cruzamento ticker ↔ CNPJ
# ---------------------------------------------------------------------------

def carregar_mapa_cnpj_ticker() -> pd.DataFrame:
    """
    Baixa o cadastro de empresas da CVM para cruzar CNPJ com ticker B3.
    O campo COD_CVM pode ser usado junto com dados da B3 para o mapeamento.

    A CVM não fornece o ticker diretamente — o mapeamento completo
    exige um arquivo auxiliar (incluso em /data/cnpj_ticker.csv).
    """
    url = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
    resp = requests.get(url, timeout=30)
    df = pd.read_csv(
        io.StringIO(resp.text),
        sep=";",
        encoding="latin-1",
        dtype={"CNPJ_CIA": str},
    )
    # Mantém apenas empresas com situação ativa
    df = df[df["SIT"] == "ATIVO"].copy()
    return df[["CNPJ_CIA", "DENOM_CIA", "COD_CVM", "SETOR_ATIV"]]


if __name__ == "__main__":
    print("Iniciando teste...")

    bp = extrair_bp(2023)

    print(bp.head())