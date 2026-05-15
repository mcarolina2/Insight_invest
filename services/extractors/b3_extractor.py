import io, logging, os, time
import pandas as pd
import requests

logger = logging.getLogger(__name__)
CACHE_PATH = "data/tickers_b3.csv"

B3_URL = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/"
    "CompanyCall/GetInitialCompanies/"
)
CVM_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"

HEADERS_B3 = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.b3.com.br",
    "Referer": "https://www.b3.com.br/",
}
HEADERS_CVM = {"User-Agent": "InsightInvest/1.0"}


def _buscar_pagina_b3(pagina):
    payload = {"language": "pt-br", "pageNumber": pagina, "pageSize": 120}
    try:
        r = requests.post(B3_URL, json=payload, headers=HEADERS_B3, timeout=15)
        if r.status_code != 200 or not r.text.strip():
            return []
        dados = r.json()
        if isinstance(dados, list):
            return dados
        if isinstance(dados, dict):
            return dados.get("companies", dados.get("results", []))
    except Exception as e:
        logger.warning(f"Pagina {pagina}: {e}")
    return []


def buscar_empresas_b3():
    registros, pagina = [], 1
    print("Buscando empresas da B3", end="", flush=True)
    while True:
        empresas = _buscar_pagina_b3(pagina)
        if not empresas:
            break
        for emp in empresas:
            ticker = str(emp.get("issuingCompany", "") or "").strip().upper()
            cod_cvm = str(emp.get("codeCVM", "")).strip().zfill(6)
            if ticker and len(ticker) >= 4:
                registros.append({
                    "ticker": ticker,
                    "nome_pregao": str(emp.get("companyName", "")).strip(),
                    "cod_cvm": cod_cvm,
                    "segmento": str(emp.get("segment", "")).strip(),
                })
        print(".", end="", flush=True)
        pagina += 1
        time.sleep(0.4)
    print(f" {len(registros)} empresas")
    if not registros:
        return pd.DataFrame()
    return pd.DataFrame(registros).drop_duplicates(subset=["ticker"])


def buscar_cnpj_cvm():
    print("Baixando CNPJs da CVM...")
    r = requests.get(CVM_URL, headers=HEADERS_CVM, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep=";", encoding="latin-1", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    cols = {c.upper(): c for c in df.columns}
    col_cnpj  = cols.get("CNPJ_CIA")
    col_cvm   = cols.get("CD_CVM") or cols.get("COD_CVM")
    col_nome  = cols.get("DENOM_SOCIAL") or cols.get("DENOM_CIA") or cols.get("DENOM_COMERC")
    col_setor = cols.get("SETOR_ATIV") or cols.get("SETOR")
    col_sit   = cols.get("SIT") or cols.get("SITUACAO")
    if not col_cnpj or not col_cvm:
        print(f"AVISO: colunas disponiveis: {df.columns.tolist()}")
        return pd.DataFrame()
    resultado = pd.DataFrame({
        "cnpj":     df[col_cnpj].str.strip(),
        "cod_cvm":  df[col_cvm].str.strip().str.zfill(6),
        "nome_cvm": df[col_nome].str.strip() if col_nome else "",
        "setor":    df[col_setor].str.strip().apply(_limpar_setor) if col_setor else "",
        "sit":      df[col_sit].str.strip().str.upper() if col_sit else "ATIVO",
    })
    ativas = resultado[resultado["sit"] == "ATIVO"].copy()
    print(f"  {len(ativas):,} empresas ativas na CVM")
    return ativas



def construir_mapa_ticker_cnpj():
    """
    Usa primeiro o cache local.
    Se não existir, baixa novamente da B3/CVM.
    """

    # 1. tenta carregar cache
    df_cache = carregar_mapa_csv()

    if not df_cache.empty:
        print(f"Cache encontrado: {len(df_cache)} registros")
        return df_cache

    # 2. fallback: baixar da internet
    print("Cache não encontrado. Baixando da B3/CVM...")

    df_b3  = buscar_empresas_b3()
    df_cvm = buscar_cnpj_cvm()

    if df_b3.empty and df_cvm.empty:
        raise RuntimeError("Falha ao obter dados da B3 e CVM.")

    df = pd.merge(df_b3, df_cvm, on="cod_cvm", how="left")

    df["nome"] = df["nome_cvm"].fillna(df["nome_pregao"])

    df["tipo"] = df["ticker"].apply(_inferir_tipo)

    df = df.drop_duplicates(subset=["ticker"])

    salvar_mapa_csv(df)

    return df[[
        "ticker",
        "nome",
        "cnpj",
        "setor",
        "cod_cvm",
        "tipo"
    ]]

def salvar_mapa_csv(df, caminho=CACHE_PATH):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    df.to_csv(caminho, index=False, encoding="utf-8")
    print(f"Cache salvo: {caminho}")


def carregar_mapa_csv(caminho=CACHE_PATH):
    if os.path.exists(caminho):

        df = pd.read_csv(
            caminho,
            dtype={
                "ticker": str,
                "cnpj": str,
                "cod_cvm": str,
            }
        )

        df["cnpj"] = df["cnpj"].fillna("").str.strip()
        df["cod_cvm"] = df["cod_cvm"].fillna("").str.zfill(6)

        return df

    return pd.DataFrame()

def _inferir_tipo(ticker):
    t = str(ticker).upper()
    if t.endswith("11"): return "fii"
    if t.endswith("34"): return "bdr"
    if t.endswith("F"):  return "renda_fixa"
    return "acao"


def _limpar_setor(setor):
    if not setor or str(setor).strip() in ("nan", ""):
        return ""
    return str(setor).split(". ", 1)[-1].strip()[:100]
