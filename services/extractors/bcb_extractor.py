"""
Extrator de indicadores macroeconômicos do Banco Central do Brasil.

Fontes:
  BCB SGS (Sistema Gerenciador de Séries Temporais)
  → api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados

  BCB OLINDA (câmbio PTAX oficial)
  → olinda.bcb.gov.br/olinda/servico/PTAX

Séries utilizadas:
  432   → Selic Meta (% a.a.)
  433   → IPCA mensal (%)
  189   → IGP-M mensal (%)
  4380  → IBC-Br (proxy mensal do PIB, índice)
  7326  → PIB trimestral (variação % vs trimestre anterior)
  2383  → Exportações FOB (US$ milhões)
  2384  → Importações FOB (US$ milhões)
  24369 → Taxa de desemprego PNAD (%)
  1     → Dólar (taxa de câmbio - venda)
  4189  → CDS Brasil 5 anos (risco-país) — via IPEADATA

Como usar:
  python manage.py load_macro_data
  python manage.py load_macro_data --meses 24
  python manage.py load_macro_data --serie selic
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração das séries
# ---------------------------------------------------------------------------

BCB_SGS_URL  = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json"
BCB_PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@di,dataFinalCotacao=@df)"
    "?@di='{inicio}'&@df='{fim}'&$format=json&$select=cotacaoVenda,dataHoraCotacao"
)
BCB_ULTIMOS_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json"
)

BCB_SGS_PERIODO_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json&dataInicial={inicio}&dataFinal={fim}"
)

SERIES_BCB = {
    # nome_campo       : (codigo_sgs, descricao)
    "selic"            : (432,   "Taxa Selic Meta (% a.a.)"),
    "ipca_mensal"      : (433,   "IPCA mensal (%)"),
    "igpm_mensal"      : (189,   "IGP-M mensal (%)"),
    "ibc_br"           : (4380,  "IBC-Br — proxy do PIB mensal (índice)"),
    "pib_trimestral"   : (7326,  "PIB trimestral — variação % vs tri anterior"),
    "exportacoes"      : (2383,  "Exportações FOB (US$ milhões)"),
    "importacoes"      : (2384,  "Importações FOB (US$ milhões)"),
    "desemprego"       : (24369, "Taxa de desemprego PNAD (%)"),
}

HEADERS = {
    "User-Agent": "InsightInvest/1.0 (contato@seuprojeto.com.br)",
    "Accept":     "application/json",
}

# ---------------------------------------------------------------------------
# Funções de busca
# ---------------------------------------------------------------------------

def buscar_serie_completa(codigo: int) -> list[dict]:
    """
    Baixa toda a série histórica disponível para um código SGS.
    Retorna lista de dicts com 'data' (str DD/MM/AAAA) e 'valor' (str).
    """
    url = BCB_SGS_URL.format(codigo=codigo)
    logger.info(f"GET {url}")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def buscar_ultimos(codigo: int, n: int = 60) -> list[dict]:
    """
    Busca os últimos N registros de uma série.
    Mais eficiente para atualizações incrementais.
    """
    url = BCB_ULTIMOS_URL.format(codigo=codigo, n=n)
    logger.info(f"GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def buscar_dolar_ptax(data_inicio: str, data_fim: str) -> list[dict]:
    """
    Busca cotações PTAX do dólar via endpoint OLINDA.

    Args:
        data_inicio: formato MM-DD-YYYY (padrão BCB Olinda)
        data_fim:    formato MM-DD-YYYY

    Returns:
        Lista de dicts com 'dataHoraCotacao' e 'cotacaoVenda'
    """
    url = BCB_PTAX_URL.format(inicio=data_inicio, fim=data_fim)
    logger.info(f"GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])

def buscar_por_periodo(codigo: int, meses: int = 120) -> list[dict]:
    """
    Busca os registros de uma série filtrando por data inicial e final.
    Substitui a busca por '/ultimos/N' devido ao novo limite do BCB.
    """
    hoje = date.today()
    
    # Converte meses para dias (1 mês ~= 30.44 dias)
    dias = int(meses * 30.44)
    
    # A API do BCB limita o intervalo a no máximo 10 anos (3652 dias)
    if dias > 3652:
        dias = 3652
        logger.warning(f"Aviso: A busca foi limitada a 10 anos devido a restrições da API do BCB.")
        
    data_inicio = hoje - timedelta(days=dias)
    
    str_fim = hoje.strftime("%d/%m/%Y")
    str_inicio = data_inicio.strftime("%d/%m/%Y")
    
    url = BCB_SGS_PERIODO_URL.format(codigo=codigo, inicio=str_inicio, fim=str_fim)
    logger.info(f"GET {url}")
    
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    
    return resp.json()


# ---------------------------------------------------------------------------
# Conversão e normalização de dados
# ---------------------------------------------------------------------------

def parse_data_bcb(data_str: str) -> date:
    """Converte '31/12/2023' para date(2023, 12, 31)."""
    return datetime.strptime(data_str, "%d/%m/%Y").date()


def parse_valor(valor_str: str) -> Optional[float]:
    """Converte string do BCB para float, tratando valores inválidos."""
    if not valor_str or valor_str.strip() == "":
        return None
    try:
        return float(valor_str.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def consolidar_series(meses: int = 120) -> list[dict]:
    """
    Busca todas as séries configuradas e consolida em uma lista
    de dicts indexados por data — um dict por mês.

    Args:
        meses: quantos meses buscar (padrão: 120 = 10 anos)

    Returns:
        Lista de dicts com todos os campos de KpiMacro por data
    """
    # 1. Baixa cada série
    dados_por_serie = {}
    for campo, (codigo, descricao) in SERIES_BCB.items():
        try:
            registros = buscar_por_periodo(codigo, meses=meses)
            #registros = buscar_ultimos(codigo, n=meses)
            # Indexa por data para facilitar merge
            dados_por_serie[campo] = {
                parse_data_bcb(r["data"]): parse_valor(r["valor"])
                for r in registros
            }
            logger.info(f"  {campo}: {len(registros)} registros carregados")
        except Exception as e:
            logger.warning(f"  FALHA ao buscar {campo} (cod={codigo}): {e}")
            dados_por_serie[campo] = {}

    # 2. Determina o universo de datas (union de todas as séries)
    todas_datas = set()
    for serie in dados_por_serie.values():
        todas_datas.update(serie.keys())

    # 3. Consolida em registros por data
    consolidado = []
    for data in sorted(todas_datas):
        registro = {"data_ref": data}
        for campo in SERIES_BCB:
            registro[campo] = dados_por_serie.get(campo, {}).get(data)
        consolidado.append(registro)

    return consolidado


# ---------------------------------------------------------------------------
# Exportações e importações: saldo da balança comercial
# ---------------------------------------------------------------------------

def calcular_balanca_comercial(exportacoes: Optional[float], importacoes: Optional[float]) -> Optional[float]:
    """
    Saldo da balança comercial = Exportações - Importações (US$ milhões).
    Positivo = superávit | Negativo = déficit.
    """
    if exportacoes is None or importacoes is None:
        return None
    return round(exportacoes - importacoes, 2)

