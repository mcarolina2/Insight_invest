"""
Tickers oficiais de cada companhia, pelo Formulário Cadastral (FCA) da CVM.

O arquivo fca_cia_aberta_valor_mobiliario_<ano>.csv (dentro de
fca_cia_aberta_<ano>.zip) lista, por CNPJ, os valores mobiliários admitidos à
negociação e o código de negociação na B3 (ex.: ITUB3, ITUB4). É a fonte
oficial do vínculo CNPJ -> ticker; substitui os tickers inventados a partir do
nome da empresa ("ITAU3", "WEG3", "AMBE3"...) que existiam na base.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

URL_FCA = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip"
HEADERS = {"User-Agent": "InsightInvest/1.0 (TCC UFPB)"}

# 4 caracteres + sufixo de ação (3 ON, 4-8 PN, 11 unit). Ex.: PETR4, B3SA3, TAEE11
PADRAO_TICKER = re.compile(r"^[A-Z0-9]{4}(3|4|5|6|7|8|11)$")


def so_digitos(cnpj) -> str:
    return re.sub(r"\D", "", str(cnpj or ""))


def _coluna(df: pd.DataFrame, *trechos: str) -> str | None:
    for c in df.columns:
        nome = c.lower()
        if all(t in nome for t in trechos):
            return c
    return None


def ler_valor_mobiliario(conteudo: bytes | str) -> pd.DataFrame:
    """Lê o CSV de valores mobiliários (bytes do arquivo ou caminho) e padroniza as colunas."""
    if isinstance(conteudo, (bytes, bytearray)):
        df = pd.read_csv(io.BytesIO(conteudo), sep=";", encoding="latin-1", dtype=str)
    else:
        df = pd.read_csv(conteudo, sep=";", encoding="latin-1", dtype=str)
    c_cnpj = _coluna(df, "cnpj")
    c_cod = _coluna(df, "codigo", "negociacao")
    if not c_cnpj or not c_cod:
        raise ValueError(f"Colunas esperadas não encontradas no FCA: {list(df.columns)}")
    c_fim = _coluna(df, "data_fim", "negociacao")
    c_merc = _coluna(df, "mercado")
    c_ref = _coluna(df, "data_referencia")
    c_nome = _coluna(df, "nome_empresarial")
    c_vm = _coluna(df, "valor_mobiliario")

    out = pd.DataFrame({
        "cnpj": df[c_cnpj].str.strip(),
        "cnpj_digitos": df[c_cnpj].map(so_digitos),
        "ticker": df[c_cod].fillna("").str.strip().str.upper(),
        "data_fim": df[c_fim] if c_fim else None,
        "mercado": df[c_merc].fillna("") if c_merc else "",
        "data_ref": df[c_ref] if c_ref else None,
        "nome": df[c_nome].fillna("").str.strip() if c_nome else "",
        "valor_mobiliario": df[c_vm].fillna("") if c_vm else "",
    })
    out = out[out["ticker"].str.match(PADRAO_TICKER)]
    # só o que ainda é negociado em bolsa
    hoje = date.today().isoformat()
    fim = out["data_fim"].fillna("").astype(str).str.strip()
    out = out[(fim == "") | (fim > hoje)]
    if c_merc:
        out = out[~out["mercado"].str.contains("balc", case=False)]
    return out


def baixar_fca(ano: int) -> pd.DataFrame:
    r = requests.get(URL_FCA.format(ano=ano), headers=HEADERS, timeout=120)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        nome = next(n for n in z.namelist() if "valor_mobiliario" in n.lower())
        return ler_valor_mobiliario(z.read(nome))


def tickers_oficiais(anos: list[int] | None = None, arquivo: str | None = None) -> pd.DataFrame:
    """
    Tabela cnpj/ticker oficial. Usa o FCA mais recente de cada companhia
    (ano corrente e anterior, pois o formulário é reapresentado a cada ano).
    `arquivo`: CSV ou ZIP já baixado (uso offline).
    """
    if arquivo:
        if arquivo.lower().endswith(".zip"):
            with zipfile.ZipFile(arquivo) as z:
                nome = next(n for n in z.namelist() if "valor_mobiliario" in n.lower())
                df = ler_valor_mobiliario(z.read(nome))
        else:
            df = ler_valor_mobiliario(arquivo)
    else:
        anos = anos or [date.today().year, date.today().year - 1]
        partes = []
        for ano in anos:
            try:
                partes.append(baixar_fca(ano))
            except Exception as e:
                logger.warning(f"FCA {ano} indisponível: {e}")
        if not partes:
            raise RuntimeError("Não foi possível baixar o FCA da CVM.")
        df = pd.concat(partes, ignore_index=True)

    # versão mais recente por companhia
    if df["data_ref"].notna().any():
        ultima = df.groupby("cnpj_digitos")["data_ref"].transform("max")
        df = df[df["data_ref"] == ultima]
    return df.drop_duplicates(subset=["ticker"]).reset_index(drop=True)
