"""
Lista os indicadores fundamentalistas com valores implausíveis, para verificação.

Uso:
    python manage.py auditar_indicadores                 # resumo + 30 piores casos
    python manage.py auditar_indicadores --todos         # todos os casos
    python manage.py auditar_indicadores --ticker PETR4  # histórico de um ativo

Gera também data/relatorio_qualidade/auditoria_indicadores.csv.

Os limites abaixo não são "erros" automáticos: indicam valores que merecem
conferência na DFP original (site da CVM ou RI da empresa). Depois de corrigir o
extrator, recarregue com:  python manage.py load_cvm_data --force
"""
import os

import pandas as pd
from django.core.management.base import BaseCommand

# campo: (mínimo plausível, máximo plausível, como exibir)
LIMITES = {
    "roe":                 (-2.0, 2.0, "pct"),     # -200% a +200%
    "roa":                 (-1.0, 1.0, "pct"),
    "margem_liquida":      (-5.0, 1.0, "pct"),     # margem > 100% é impossível sem receita extraordinária
    "margem_ebitda":       (-5.0, 1.0, "pct"),
    "crescimento_receita": (-1.0, 5.0, "pct"),     # receita não cai mais que 100%
    "crescimento_lucro":   (-20.0, 20.0, "pct"),
    "liquidez_corrente":   (0.0, 50.0, "x"),
    "divida_ebitda":       (-50.0, 50.0, "x"),     # 99 = EBITDA negativo com dívida (proposital)
}
PASTA = os.path.join("data", "relatorio_qualidade")


def _fmt(v, tipo):
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:,.1f}%" if tipo == "pct" else f"{v:,.2f}x"


class Command(BaseCommand):
    help = "Aponta valores implausíveis nos indicadores fundamentalistas."

    def add_arguments(self, parser):
        parser.add_argument("--todos", action="store_true")
        parser.add_argument("--ticker")

    def handle(self, *args, todos, ticker, **options):
        from market_data.models import KpiMicro

        campos = list(LIMITES)
        qs = KpiMicro.objects.all()
        if ticker:
            qs = qs.filter(ativo__ticker__iexact=ticker)
        df = pd.DataFrame(list(qs.values("ativo__ticker", "ativo__nome", "data_ref", *campos)))
        if df.empty:
            self.stdout.write("Nenhum indicador no banco.")
            return
        for c in campos:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)

        if ticker:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Histórico de {ticker.upper()}"))
            for _, r in df.sort_values("data_ref").iterrows():
                vals = "  ".join(f"{c}={_fmt(r[c], LIMITES[c][2])}" for c in campos)
                self.stdout.write(f"{r['data_ref']}: {vals}")
            return

        casos = []
        for c, (mn, mx, tipo) in LIMITES.items():
            fora = df[(df[c] < mn) | (df[c] > mx)]
            if c == "divida_ebitda":
                sem_geracao = int((df[c] == 99.0).sum())
                fora = fora[fora[c] != 99.0]
                self.stdout.write(f"divida_ebitda = 99 (EBITDA negativo com dívida, proposital): {sem_geracao}")
            for _, r in fora.iterrows():
                casos.append({"ticker": r["ativo__ticker"], "empresa": r["ativo__nome"],
                              "data_ref": r["data_ref"], "indicador": c, "valor": r[c],
                              "exibicao": _fmt(r[c], tipo), "faixa_plausivel": f"{_fmt(mn, tipo)} a {_fmt(mx, tipo)}"})

        resumo = pd.DataFrame(casos)
        os.makedirs(PASTA, exist_ok=True)
        resumo.to_csv(os.path.join(PASTA, "auditoria_indicadores.csv"), index=False)

        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{len(df)} registros analisados"))
        for c in campos:
            n = int((resumo["indicador"] == c).sum()) if not resumo.empty else 0
            self.stdout.write(f"  {c:22s} {n:5d} fora da faixa ({n / len(df):.1%})")
        if resumo.empty:
            self.stdout.write(self.style.SUCCESS("Nenhum valor fora das faixas plausíveis."))
            return

        resumo["gravidade"] = resumo["valor"].abs()
        mostrar = resumo.sort_values("gravidade", ascending=False)
        if not todos:
            mostrar = mostrar.head(30)
        self.stdout.write(self.style.MIGRATE_HEADING("\nCasos para conferir na DFP (maiores primeiro):"))
        for _, r in mostrar.iterrows():
            self.stdout.write(f"  {r['ticker']:7s} {str(r['data_ref']):10s} {r['indicador']:20s} "
                              f"{r['exibicao']:>14s}   (plausível: {r['faixa_plausivel']})  {r['empresa'][:40]}")
        self.stdout.write(f"\nLista completa em {PASTA}/auditoria_indicadores.csv")
