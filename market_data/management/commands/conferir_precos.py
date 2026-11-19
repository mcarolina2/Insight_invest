"""
Confere os retornos diários do Yahoo Finance com o arquivo oficial de cotações
da B3 (COTAHIST), para descobrir se um dia extremo é mercado ou erro de dado.

1. Baixe na B3 (Market Data > Histórico > Cotações históricas) o arquivo do ano,
   COTAHIST_A2026.ZIP, ou o de um dia, COTAHIST_D26102026.ZIP.
2. Rode:
     python manage.py conferir_precos --arquivo COTAHIST_A2026.ZIP --datas 2026-10-05 2026-10-26
     python manage.py conferir_precos --arquivo COTAHIST_A2026.ZIP           # todos os dias do arquivo

Para cada data, compara o retorno do dia (fechamento / fechamento anterior - 1)
das duas fontes. Diferença acima de --limite (padrão 5 p.p.) indica erro no
Yahoo (ou evento corporativo, como desdobramento: confira a ação).
O COTAHIST traz preços SEM ajuste por proventos; em dias de "ex" a diferença
pequena é esperada.
"""
import io
import zipfile
from datetime import date, datetime

import pandas as pd
from django.core.management.base import BaseCommand, CommandError


def ler_cotahist(caminho: str) -> pd.DataFrame:
    """Linhas do mercado à vista (TPMERC 010): data, ticker, fechamento, volume financeiro."""
    if caminho.lower().endswith(".zip"):
        with zipfile.ZipFile(caminho) as z:
            nome = next(n for n in z.namelist() if n.upper().startswith("COTAHIST"))
            texto = z.read(nome).decode("latin-1")
    else:
        texto = open(caminho, encoding="latin-1").read()
    linhas = []
    for l in io.StringIO(texto):
        if not l.startswith("01") or l[24:27] != "010":
            continue
        fatcot = int(l[210:217] or 1) or 1
        linhas.append({
            "data": datetime.strptime(l[2:10], "%Y%m%d").date(),
            "ticker": l[12:24].strip(),
            "fechamento": int(l[108:121]) / 100 / fatcot,
            "volume": int(l[170:188]) / 100,
        })
    if not linhas:
        raise CommandError("Nenhuma cotação do mercado à vista no arquivo.")
    return pd.DataFrame(linhas)


class Command(BaseCommand):
    help = "Compara os retornos diários do Yahoo Finance com o COTAHIST oficial da B3."

    def add_arguments(self, parser):
        parser.add_argument("--arquivo", required=True, nargs="+", help="COTAHIST (.ZIP ou .TXT); pode passar vários")
        parser.add_argument("--datas", nargs="*", default=[], help="datas AAAA-MM-DD (padrão: todas do arquivo)")
        parser.add_argument("--limite", type=float, default=0.05, help="diferença tolerada no retorno do dia (0,05 = 5 p.p.)")
        parser.add_argument("--mostrar", type=int, default=30)

    def handle(self, *args, arquivo, datas, limite, mostrar, **o):
        from portfolio.models import Ativo
        from services.calculators.estatistico import baixar_precos

        b3 = pd.concat([ler_cotahist(a) for a in arquivo]).drop_duplicates(["data", "ticker"])
        fech = b3.pivot(index="data", columns="ticker", values="fechamento").sort_index()
        fech.index = pd.to_datetime(fech.index)
        ret_b3 = fech.pct_change(fill_method=None)

        tickers = sorted(set(Ativo.objects.filter(ativo=True, tipo="acao").values_list("ticker", flat=True))
                         & set(fech.columns))
        self.stdout.write(f"COTAHIST: {len(fech)} pregões de {fech.index[0]:%d/%m/%Y} a {fech.index[-1]:%d/%m/%Y}; "
                          f"{len(tickers)} ações da base no arquivo. Baixando o Yahoo...")
        anos = max(1, int((date.today() - fech.index[0].date()).days / 365) + 1)
        yahoo = baixar_precos(tickers, anos=anos)
        yahoo.index = pd.to_datetime(yahoo.index).tz_localize(None).normalize()
        ret_y = yahoo.pct_change(fill_method=None)

        alvo = [pd.Timestamp(d) for d in datas] if datas else list(ret_b3.index[1:])
        problemas = []
        for d in alvo:
            if d not in ret_b3.index:
                self.stdout.write(self.style.WARNING(f"{d:%d/%m/%Y}: não é pregão no COTAHIST (ou fora do arquivo)."))
                continue
            for t in tickers:
                rb = ret_b3.at[d, t] if t in ret_b3.columns else None
                ry = ret_y.at[d, t] if (t in ret_y.columns and d in ret_y.index) else None
                if rb is None or pd.isna(rb):
                    continue
                if ry is None or pd.isna(ry):
                    problemas.append((d, t, rb, None, None))
                elif abs(ry - rb) > limite:
                    problemas.append((d, t, rb, ry, ry - rb))

        if datas:
            for d in alvo:
                if d in ret_b3.index:
                    r = ret_b3.loc[d, tickers].dropna()
                    self.stdout.write(f"\n{d:%d/%m/%Y} segundo a B3: mediana {r.median():+.2%}, "
                                      f"maior queda {r.min():+.2%} ({r.idxmin()}), maior alta {r.max():+.2%} ({r.idxmax()})")
        if not problemas:
            self.stdout.write(self.style.SUCCESS(f"\nNenhuma diferença acima de {limite:.0%} entre Yahoo e B3."))
            return
        self.stdout.write(self.style.WARNING(f"\n{len(problemas)} divergências (|Yahoo − B3| > {limite:.0%} ou Yahoo sem preço):"))
        self.stdout.write(f"{'data':10s} {'ticker':8s} {'B3':>9s} {'Yahoo':>9s} {'dif.':>9s}")
        for d, t, rb, ry, dif in sorted(problemas, key=lambda x: -abs(x[4] or 9))[:mostrar]:
            self.stdout.write(f"{d:%d/%m/%Y} {t:8s} {rb:+9.2%} " + (f"{ry:+9.2%} {dif:+9.2%}" if ry is not None else "  sem preço no Yahoo"))
        por_ticker = pd.Series([p[1] for p in problemas]).value_counts()
        self.stdout.write("\nAções com mais divergências: " + ", ".join(f"{t} ({n})" for t, n in por_ticker.head(15).items()))
        self.stdout.write("Se os dias extremos forem erro do Yahoo, refaça load_estatistico e o backtest depois de corrigir "
                          "(ou rode o backtest com --fim antes da data, como teste de robustez).")
