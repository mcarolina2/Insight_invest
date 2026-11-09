"""
Retrato macroeconômico "mais recente" a partir de KpiMacro.

KpiMacro mistura linhas diárias (Selic) e mensais/trimestrais (IPCA, IBC-Br,
balança, PIB, desemprego). A linha mais recente quase sempre é um dia útil com
SÓ a Selic preenchida; usar `KpiMacro.objects.order_by('-data_ref').first()`
fazia o escore macro tratar IPCA, PIB e desemprego como ausentes. Aqui cada
indicador recebe o seu último valor disponível até a data de referência.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

CAMPOS = ["selic", "ipca_mensal", "igpm_mensal", "pib_trimestral", "ibc_br",
          "exportacoes", "importacoes", "balanca_comercial", "desemprego",
          "dolar_brl", "risco_brasil"]


def macro_consolidado(data_ref: date | None = None):
    """Objeto com o último valor de cada indicador (atributos com os nomes de KpiMacro),
    a data de cada um em `.datas` e `id`/`data_ref` da linha mais recente.
    Também traz `ipca_12m` (inflação acumulada nos 12 últimos meses). None se não houver dados."""
    from market_data.models import KpiMacro

    qs = KpiMacro.objects.all()
    if data_ref:
        qs = qs.filter(data_ref__lte=data_ref)
    ultima = qs.order_by("-data_ref").first()
    if not ultima:
        return None
    valores, datas = {}, {}
    for campo in CAMPOS:
        linha = (qs.exclude(**{f"{campo}__isnull": True}).order_by("-data_ref")
                 .values("data_ref", campo).first())
        valores[campo] = float(linha[campo]) if linha else None
        datas[campo] = linha["data_ref"] if linha else None
    if valores["balanca_comercial"] is None and None not in (valores["exportacoes"], valores["importacoes"]):
        valores["balanca_comercial"] = valores["exportacoes"] - valores["importacoes"]
        datas["balanca_comercial"] = datas["exportacoes"]

    ipca = list(qs.exclude(ipca_mensal__isnull=True).order_by("-data_ref")
                .values_list("ipca_mensal", flat=True)[:12])
    ipca_12m = None
    if len(ipca) == 12:
        acum = 1.0
        for v in ipca:
            acum *= 1 + float(v) / 100
        ipca_12m = round((acum - 1) * 100, 2)

    return SimpleNamespace(id=ultima.id, data_ref=ultima.data_ref, datas=datas,
                           ipca_12m=ipca_12m, **valores)
