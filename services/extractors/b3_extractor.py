import io
import json
import base64
import logging
import os
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)
CACHE_PATH = "data/tickers_b3.csv"

# ─────────────────────────────────────────────────────────────
# URLs e headers
# ─────────────────────────────────────────────────────────────

B3_URL = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/"
GETDETAIL_URL = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetDetail/"
CVM_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"

HEADERS_B3 = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}
HEADERS_CVM = {"User-Agent": "InsightInvest/1.0"}


# ─────────────────────────────────────────────────────────────
# GetInitialCompanies — lista empresas ativas (paginado)
# ─────────────────────────────────────────────────────────────

def _buscar_pagina_b3(pagina):
    payload = {"language": "pt-br", "pageNumber": pagina, "pageSize": 120}
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    url = B3_URL + payload_b64
    try:
        r = requests.get(url, headers=HEADERS_B3, timeout=20)
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


# ─────────────────────────────────────────────────────────────
# GetDetail — tickers reais por empresa
# ─────────────────────────────────────────────────────────────

def buscar_detalhe_empresa(cod_cvm, tentativas=4, timeout=25):
    """Busca os tickers reais de uma empresa via GetDetail, com retry automático."""
    payload = {"codeCVM": str(cod_cvm), "language": "pt-br"}
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()

    for tentativa in range(1, tentativas + 1):
        try:
            r = requests.get(GETDETAIL_URL + payload_b64, headers=HEADERS_B3, timeout=timeout)
            if r.status_code != 200 or not r.text.strip() or r.text.strip() == "{}":
                return None
            return r.json()
        except Exception as e:
            if tentativa == tentativas:
                logger.warning(f"GetDetail cod_cvm={cod_cvm} falhou após {tentativas} tentativas: {e}")
                return None
            time.sleep(2 * tentativa)  # espera progressiva: 2s, 4s, 6s, 8s...
    return None


def _tickers_validos(other_codes):
    """Mantém só tickers negociáveis de ações/FIIs/BDRs — descarta debêntures (têm hífen)."""
    validos = []
    for item in other_codes or []:
        code = str(item.get("code", "")).strip().upper()
        if code and "-" not in code and code[-1].isdigit():
            validos.append(code)
    return validos


def buscar_empresas_b3(checkpoint_path="data/_checkpoint_b3.csv"):
    empresas_brutas, pagina = [], 1
    print("Listando empresas da B3", end="", flush=True)
    while True:
        empresas = _buscar_pagina_b3(pagina)
        if not empresas:
            break
        empresas_brutas.extend([e for e in empresas if str(e.get("status")) == "A"])
        print(".", end="", flush=True)
        pagina += 1
        time.sleep(0.4)
    print(f" {len(empresas_brutas)} empresas ativas listadas")

    # carrega checkpoint anterior, se existir (retomada)
    registros = []
    cod_cvms_ja_processados = set()
    if os.path.exists(checkpoint_path):
        df_ckpt = pd.read_csv(checkpoint_path, dtype={"cod_cvm": str})
        if not df_ckpt.empty:
            registros = df_ckpt.to_dict("records")
            cod_cvms_ja_processados = set(df_ckpt["cod_cvm"].astype(str))
            print(f"Checkpoint encontrado: retomando de {len(cod_cvms_ja_processados)} empresas já processadas")

    print("Buscando tickers reais por empresa (GetDetail)...")
    total_falhas = 0
    for i, emp in enumerate(empresas_brutas):
        cod_cvm_bruto = str(emp.get("codeCVM", "")).strip()

        if cod_cvm_bruto in cod_cvms_ja_processados:
            continue  # já processado num run anterior, pula

        detalhe = buscar_detalhe_empresa(cod_cvm_bruto)
        time.sleep(0.4)  # espaça as chamadas para reduzir pressão na API da B3

        if not detalhe:
            total_falhas += 1
            continue

        if detalhe.get("hasQuotation") != "S":
            continue

        cod_cvm_padronizado = cod_cvm_bruto.zfill(6)

        for ticker in _tickers_validos(detalhe.get("otherCodes")):
            registros.append({
                "ticker": ticker,
                "nome_pregao": detalhe.get("tradingName", "").strip(),
                "cod_cvm": cod_cvm_padronizado,
                "segmento": emp.get("segment", "").strip(),
            })

        # salva checkpoint a cada 100 empresas processadas
        if (i + 1) % 100 == 0:
            pd.DataFrame(registros).to_csv(checkpoint_path, index=False)
            print(f"  {i + 1}/{len(empresas_brutas)} empresas processadas (checkpoint salvo, {total_falhas} falhas até agora)")

    # salva checkpoint final também
    pd.DataFrame(registros).to_csv(checkpoint_path, index=False)

    print(f"Total de tickers negociáveis: {len(registros)} | Empresas com falha definitiva: {total_falhas}")
    if not registros:
        return pd.DataFrame()
    return pd.DataFrame(registros).drop_duplicates(subset=["ticker"])

# ─────────────────────────────────────────────────────────────
# CVM — CNPJ, setor, situação cadastral
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Mapa consolidado ticker ↔ CNPJ (com cache)
# ─────────────────────────────────────────────────────────────

def construir_mapa_ticker_cnpj():
    """
    Usa primeiro o cache local.
    Se não existir, baixa novamente da B3/CVM.
    """
    df_cache = carregar_mapa_csv()
    if not df_cache.empty:
        print(f"Cache encontrado: {len(df_cache)} registros")
        return df_cache

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

    return df[["ticker", "nome", "cnpj", "setor", "cod_cvm", "tipo"]]


def salvar_mapa_csv(df, caminho=CACHE_PATH):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    df.to_csv(caminho, index=False, encoding="utf-8")
    print(f"Cache salvo: {caminho}")


def carregar_mapa_csv(caminho=CACHE_PATH):
    if os.path.exists(caminho):
        df = pd.read_csv(
            caminho,
            dtype={"ticker": str, "cnpj": str, "cod_cvm": str},
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