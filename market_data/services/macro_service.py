# market_data/services/macro_service.py

import requests
from datetime import datetime
from market_data.models import KpiMacro

BCB_API = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json"

CODIGOS = {
    "selic": 432,
    "ipca": 433,
}

def fetch_bcb(codigo):
    url = BCB_API.format(codigo=codigo)
    response = requests.get(url)
    return response.json()


def update_macro():
    selic_data = fetch_bcb(CODIGOS["selic"])
    ipca_data = fetch_bcb(CODIGOS["ipca"])

    for i in range(len(selic_data)):
        data = datetime.strptime(selic_data[i]['data'], "%d/%m/%Y").date()

        KpiMacro.objects.update_or_create(
            data_ref=data,
            defaults={
                "selic": float(selic_data[i]['valor']),
                "ipca_mensal": float(ipca_data[i]['valor']),
            }
        )