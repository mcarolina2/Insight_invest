"""
Mostra por que um ativo aparece ou não no escore, nos rankings e nas carteiras.

    python manage.py diagnosticar_ativo BBAS3 ITUB4 PETR4
    python manage.py diagnosticar_ativo --faltando        # grandes empresas ausentes do escore

Para cada ticker verifica, na ordem em que o pipeline usa os dados:
  1. cadastro (Ativo existe? ativo=True? CNPJ? setor?)
  2. demonstrações da CVM (KpiMicro: quantas, a mais recente)
  3. cotações e liquidez (KpiEstatistico: pregões, volume mediano, % de pregões com negócio)
  4. universo investível (mesma regra do escore, carteira, rankings e backtest)
  5. filtros de cada perfil e posição no escore
"""
from django.core.management.base import BaseCommand

# Empresas grandes que deveriam estar na base (referência para --faltando)
# (sem ELET3, JBSS3 e EMBR3: a Eletrobras virou Axia Energia e a JBS passou a ter
# só BDR na B3 em 2025, com troca de código; use a busca pelo nome para essas)
GRANDES = ["PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3", "ABEV3", "WEGE3", "B3SA3", "ITSA4",
           "SUZB3", "RENT3", "EQTL3", "PRIO3", "RADL3", "RAIL3", "GGBR4", "VIVT3",
           "SBSP3", "CMIG4", "TOTS3", "BPAC11", "SANB11", "CPLE6", "LREN3"]


class Command(BaseCommand):
    help = "Diagnostica por que um ativo entra ou não no escore e nas carteiras."

    def add_arguments(self, parser):
        parser.add_argument("tickers", nargs="*")
        parser.add_argument("--faltando", action="store_true",
                            help="lista quais das maiores empresas da bolsa estão fora do escore")

    def handle(self, *args, tickers, faltando, **o):
        from scoring.services.motor_scoring_v2 import calcular_scores_v2, obter_perfis, universo_investivel

        universo = universo_investivel()
        escores = {p: calcular_scores_v2(p) for p in obter_perfis()}
        if faltando:
            tickers = [t for t in GRANDES
                       if all(df.empty or t not in set(df["ticker"]) for df in escores.values())]
            self.stdout.write(f"{len(tickers)} de {len(GRANDES)} grandes empresas fora do escore: "
                              f"{', '.join(tickers) or 'nenhuma'}\n")
        for t in tickers:
            self._um(t.upper().strip(), universo, escores)

    def _um(self, ticker, universo, escores):
        from django.conf import settings
        from market_data.models import KpiEstatistico, KpiMicro
        from portfolio.models import Ativo
        from scoring.services.motor_scoring_v2 import CAMPOS_NAO_FINANCEIROS, config_perfil, eh_financeiro

        w = self.stdout.write
        w(self.style.MIGRATE_HEADING(f"\n=== {ticker} ==="))
        a = Ativo.objects.filter(ticker__iexact=ticker).first()
        if a is None:
            w(self.style.ERROR("  1. NÃO CADASTRADO. Rode corrigir_tickers --aplicar (cria as companhias do FCA que "
                               "faltam). Se continuar, o código pode ter mudado: procure a empresa pelo nome."))
            parecidos = list(Ativo.objects.filter(ticker__startswith=ticker[:4]).values_list("ticker", flat=True))
            if parecidos:
                w(f"     Tickers parecidos na base: {', '.join(parecidos)}")
            return
        ok = self.style.SUCCESS("ok")
        w(f"  1. cadastro: {ok if a.ativo else self.style.ERROR('INATIVO')} | CNPJ {a.cnpj or self.style.ERROR('SEM CNPJ')} "
          f"| setor '{a.setor or '—'}'{' (financeiro)' if eh_financeiro(a.setor) else ''}")

        micros = KpiMicro.objects.filter(ativo=a).order_by("-data_ref")
        m = micros.first()
        if m is None:
            w(self.style.ERROR("  2. CVM: nenhuma demonstração. Verifique o CNPJ e rode load_cvm_data --force"))
        else:
            w(f"  2. CVM: {micros.count()} DFP(s), mais recente {m.data_ref:%d/%m/%Y} | ROE {m.roe} | "
              f"liq. corrente {m.liquidez_corrente} | dív/EBITDA {m.divida_ebitda}")

        e = KpiEstatistico.objects.filter(ativo=a).order_by("-data_calculo", "-n_observacoes").first()
        if e is None:
            w(self.style.ERROR("  3. cotações: sem KpiEstatistico. Rode load_estatistico (ticker sem preço no Yahoo?)"))
        else:
            w(f"  3. cotações em {e.data_calculo:%d/%m/%Y}: {e.n_observacoes} pregões | "
              f"volume mediano {e.volume_mediano if e.volume_mediano is not None else 'não calculado'} "
              f"(mín. {settings.LIQUIDEZ_MINIMA_DIARIA:,.0f}) | pregões com negócio {e.pregoes_com_negocio} "
              f"(mín. {settings.PREGOES_COM_NEGOCIO_MIN})")

        if universo is None:
            w("  4. universo investível: sem KpiEstatistico no banco (nenhum filtro aplicado)")
        else:
            w(f"  4. universo investível: {ok if a.ticker in universo else self.style.ERROR('FORA')}")

        for perfil, df in escores.items():
            if not df.empty and a.ticker in set(df["ticker"]):
                pos = int(df.index[df["ticker"] == a.ticker][0]) + 1
                w(f"  5. {perfil}: posição {pos} de {len(df)} | escore {float(df.loc[pos - 1, 'score_final']):.1f}")
                continue
            if m is None or (universo is not None and a.ticker not in universo):
                w(f"  5. {perfil}: fora (ver itens acima)")
                continue
            falhas = []
            for chave, lim in config_perfil(perfil).get("filtros_kpi", {}).items():
                campo, op = chave.rsplit("__", 1)
                if eh_financeiro(a.setor) and campo in CAMPOS_NAO_FINANCEIROS:
                    continue
                v = getattr(m, campo, None)
                if v is None or (op == "gte" and float(v) < lim) or (op == "lte" and float(v) > lim):
                    falhas.append(f"{campo}={v} ({'≥' if op == 'gte' else '≤'} {lim})")
            w(f"  5. {perfil}: fora — " + ("; ".join(falhas) if falhas else
                                         "DFP ainda no prazo de divulgação ou outra empresa do mesmo CNPJ ficou na frente"))
