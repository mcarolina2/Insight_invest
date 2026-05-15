import os
import time
import requests
import pandas as pd

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Insight_invest.settings")

import django
django.setup()

from portfolio.models import Ativo

BRAPI_LIST_URL = "https://brapi.dev/api/quote/list"

HEADERS = {
    "User-Agent": "InsightInvest/1.0",
    "Accept": "application/json",
}


def buscar_tickers_brapi():
    print("Buscando tickers da brapi...")

    r = requests.get(BRAPI_LIST_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()

    dados = r.json()

    return dados.get("stocks", [])


def carregar_empresas_cvm():
    """
    Carrega cadastro oficial da CVM.
    """

    url = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"

    print("Baixando cadastro CVM...")

    df = pd.read_csv(
        url,
        sep=";",
        encoding="latin1"
    )

    return df


def normalizar(txt):
    if not txt:
        return ""

    return (
        str(txt)
        .upper()
        .replace("S.A.", "")
        .replace("SA", "")
        .replace("  ", " ")
        .strip()
    )


def corrigir_tickers():

    stocks = buscar_tickers_brapi()
    df_cvm = carregar_empresas_cvm()

    # mapa nome -> cnpj
    mapa_cvm = {}

    for _, row in df_cvm.iterrows():

        nome = normalizar(row.get("DENOM_SOCIAL"))
        cnpj = str(row.get("CNPJ_CIA", "")).strip()

        if nome and cnpj:
            mapa_cvm[nome] = cnpj

    ativos = list(Ativo.objects.all())

    atualizados = 0

    print(f"\nProcessando {len(stocks)} tickers...\n")

    for i, stock in enumerate(stocks, 1):

        ticker = stock.get("stock")
        nome   = normalizar(stock.get("name"))

        cnpj = mapa_cvm.get(nome)

        print(f"[{i}] {ticker:<8} {nome[:40]}", end=" ")

        if not cnpj:
            print("→ sem match CVM")
            continue

        ativo = Ativo.objects.filter(cnpj=cnpj).first()

        if not ativo:
            print("→ ativo não encontrado")
            continue

        antigo = ativo.ticker

        if antigo != ticker:
            ativo.ticker = ticker
            ativo.save(update_fields=["ticker"])

            atualizados += 1

            print(f"→ atualizado ({antigo} → {ticker})")
        else:
            print("→ ok")

        time.sleep(0.05)

    print("\n==============================")
    print(f"Atualizados: {atualizados}")


if __name__ == "__main__":
    corrigir_tickers()