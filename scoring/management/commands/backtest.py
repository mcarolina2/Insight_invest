"""
Backtest walk-forward (seção "Avaliação e Validação das Carteiras" do TCC).

    python manage.py backtest                       # 3 perfis, 7 anos de preços
    python manage.py backtest --perfis intermediario --anos 6
    python manage.py backtest --inicio 2021-01-01 --fim 2025-12-31

Gera em data/backtest/<data>/:
    metricas.csv           métricas por perfil, custo e carteira
    testes_sharpe.csv      diferenças de Sharpe (Ledoit-Wolf, 2008)
    retornos_diarios.csv   séries líquidas (para gráficos no TCC)
    tabela_backtest.tex    tabela pronta para o Overleaf (custo 0,10%)
    resumo.json            período, hipóteses e diagnóstico da camada macro
    patrimonio.csv         quanto viraram R$ 10.000 (e R$ 10.000 + R$ 10.000/ano)
    tabela_patrimonio.tex  a mesma comparação em reais, pronta para o Overleaf

    python manage.py backtest --inicio 2023-01-02   # teste sem a pandemia na calibração
    python manage.py backtest --publicar            # atualiza a página "Desempenho do modelo"
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from scoring.services.backtest import CUSTOS_PADRAO, ESTRATEGIAS, executar_backtest, patrimonio

ARQUIVO_PUBLICADO = Path(__file__).resolve().parents[2] / "dados_publicados" / "backtest.json"
VALOR_INICIAL = 10_000.0
APORTE_ANUAL = 10_000.0

PERFIS = ("conservador", "intermediario", "arrojado")
NOMES_PERFIL = {"conservador": "Conservador", "intermediario": "Intermediário", "arrojado": "Arrojado"}


def _br(v, casas=2, pct=False):
    if v is None or pd.isna(v):
        return "--"
    v = v * 100 if pct else v
    return f"{v:.{casas}f}".replace(".", ",") + ("\\%" if pct else "")


class Command(BaseCommand):
    help = "Executa o backtest walk-forward das carteiras (point-in-time, com custos)."

    def add_arguments(self, parser):
        parser.add_argument("--perfis", nargs="+", choices=PERFIS, default=list(PERFIS))
        parser.add_argument("--anos", type=int, default=7, help="anos de preços a baixar (padrão 7)")
        parser.add_argument("--inicio", type=date.fromisoformat, default=None)
        parser.add_argument("--fim", type=date.fromisoformat, default=None)
        parser.add_argument("--janela-calibracao", type=int, default=504)
        parser.add_argument("--janela-teste", type=int, default=63)
        parser.add_argument("--modelo", choices=["sharpe", "markowitz", "risk_parity", "score"], default=None,
                            help="força um modelo de alocação para todos os perfis")
        parser.add_argument("--reamostragens", type=int, default=2000)
        parser.add_argument("--saida", default=None)
        parser.add_argument("--publicar", action="store_true",
                            help="grava o resultado para a página Desempenho do modelo (scoring/dados_publicados)")

    def handle(self, *args, **o):
        from portfolio.models import Ativo
        from scoring.services.precos import obter_ibovespa, obter_precos, obter_volumes

        tickers = sorted(set(Ativo.objects.filter(kpis_micro__isnull=False, ativo=True)
                             .values_list("ticker", flat=True)))
        tickers = [t for t in tickers if t and 4 <= len(t) <= 6 and " " not in t]
        if not tickers:
            raise CommandError("Nenhum ativo com dados fundamentalistas (rode load_cvm_data).")
        self.stdout.write(f"Baixando {o['anos']} anos de preços de {len(tickers)} ativos...")
        precos = obter_precos(tickers, anos=o["anos"])
        volumes = obter_volumes(tickers, anos=o["anos"])
        if volumes is None:
            self.stdout.write(self.style.WARNING(
                "Sem dados de volume: o filtro de liquidez não será aplicado no backtest."))
        if precos.empty:
            raise CommandError("Não foi possível obter preços (sem internet ou Yahoo indisponível).")
        ibov = obter_ibovespa(anos=o["anos"])
        if ibov.empty:
            self.stdout.write(self.style.WARNING(
                "Ibovespa indisponível: beta calculado contra a média do universo e carteira Ibovespa omitida."))
        self.stdout.write(f"Preços: {precos.shape[1]} ativos, {precos.index.min():%d/%m/%Y} a {precos.index.max():%d/%m/%Y}")

        def progresso(perfil, i, n, d):
            if i == 1 or i == n or i % 4 == 0:
                self.stdout.write(f"  {perfil}: rebalanceamento {i}/{n} ({d:%d/%m/%Y})")

        try:
            res = executar_backtest(
                precos, perfis=o["perfis"], ibovespa=ibov, inicio=o["inicio"], fim=o["fim"],
                janela_cal=o["janela_calibracao"], janela_teste=o["janela_teste"],
                modelo=o["modelo"], reamostragens=o["reamostragens"], progresso=progresso,
                volumes=volumes)
        except ValueError as e:
            raise CommandError(str(e))

        destino = Path(o["saida"] or Path(settings.BASE_DIR) / "data" / "backtest" / date.today().isoformat())
        destino.mkdir(parents=True, exist_ok=True)
        self._salvar(res, destino)
        self._imprimir(res)
        if o["publicar"]:
            self._publicar(res)
            self.stdout.write(self.style.SUCCESS(f"Publicado em {ARQUIVO_PUBLICADO} (faça commit para subir ao Render)"))
        self.stdout.write(self.style.SUCCESS(f"\nArquivos em {destino}"))

    # ── saída ─────────────────────────────────────────────────────────
    def _tabelas(self, res):
        linhas, testes, series = [], [], {}
        for perfil, rp in res["perfis"].items():
            for custo, por_est in rp["metricas"].items():
                for est, m in por_est.items():
                    linhas.append({"perfil": perfil, "custo": float(custo), "carteira": est, **m})
            for custo, por_est in rp["testes"].items():
                for est, t in por_est.items():
                    testes.append({"perfil": perfil, "custo": float(custo), "contra": est, **t})
            for est, s in rp["series"][f"{0.001:.4f}"].items():
                series[f"{perfil}|{est}"] = s
        return pd.DataFrame(linhas), pd.DataFrame(testes), pd.DataFrame(series)

    def _hipoteses(self, res, df, dt, custo=0.001):
        out = {}
        sel = dt[dt["custo"] == custo] if not dt.empty else dt
        for perfil in res["perfis"]:
            s = sel[sel["perfil"] == perfil] if not sel.empty else sel

            def sustentada(contra):
                linhas = s[s["contra"].isin(contra)] if not s.empty else s
                if linhas.empty or len(linhas) < len(contra):
                    return None
                return bool(((linhas["delta_sharpe"] > 0) & (linhas["p_valor"] < 0.05)).all())
            out[perfil] = {"H1": sustentada(["fundamentalista", "estatistica"]),
                           "H2": sustentada(["igual", "ibovespa"] if "ibovespa" in set(s.get("contra", [])) else ["igual"])}
        comb = df[(df["custo"] == custo) & (df["carteira"] == "combinada")].set_index("perfil")
        ordem = [p for p in PERFIS if p in comb.index]
        if len(ordem) == 3:
            out["H3"] = {
                "volatilidade_crescente": bool(comb.loc[ordem, "volatilidade"].is_monotonic_increasing),
                # drawdown é negativo: "maior queda" crescente = valores decrescentes
                "drawdown_crescente": bool(comb.loc[ordem, "max_drawdown"].is_monotonic_decreasing),
            }
        return out

    def _salvar(self, res, destino):
        df, dt, ds = self._tabelas(res)
        df.to_csv(destino / "metricas.csv", index=False)
        dt.to_csv(destino / "testes_sharpe.csv", index=False)
        ds.to_csv(destino / "retornos_diarios.csv")
        resumo = {"periodo": res["periodo"],
                  "hipoteses_custo_0,10%": self._hipoteses(res, df, dt),
                  "camada_macro": {p: {"jaccard_medio_com_vs_sem_macro": r["camada_macro"]["jaccard_medio"]}
                                   for p, r in res["perfis"].items()},
                  "n_ativos_medio": {p: r["n_ativos_medio"] for p, r in res["perfis"].items()}}
        (destino / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
        (destino / "tabela_backtest.tex").write_text(self._latex(res, df, dt), encoding="utf-8")
        dp = self._patrimonio(res)
        dp.to_csv(destino / "patrimonio.csv", index=False)
        (destino / "tabela_patrimonio.tex").write_text(self._latex_patrimonio(res, dp), encoding="utf-8")
        resumo["patrimonio_custo_0,10%"] = dp.to_dict(orient="records")
        self._resumo = resumo

    # ── comparação em reais (sugestão da banca) ──────────────────────
    def _patrimonio(self, res, custo=0.001):
        linhas = []
        for perfil, rp in res["perfis"].items():
            for est, serie in rp["series"][f"{custo:.4f}"].items():
                so_inicial = patrimonio(serie, VALOR_INICIAL)
                com_aportes = patrimonio(serie, VALOR_INICIAL, APORTE_ANUAL)
                linhas.append({"perfil": perfil, "carteira": est,
                               "final_10mil": so_inicial["valor_final"],
                               "aplicado_com_aportes": com_aportes["aplicado"],
                               "final_com_aportes": com_aportes["valor_final"]})
        return pd.DataFrame(linhas)

    def _latex_patrimonio(self, res, dp):
        def reais(v):
            return "R\\$ " + f"{v:,.0f}".replace(",", ".")
        corpo = []
        for perfil in res["perfis"]:
            corpo.append(f"\\multicolumn{{3}}{{l}}{{\\textit{{Perfil {NOMES_PERFIL[perfil]}}}}} \\\\")
            for _, m in dp[dp["perfil"] == perfil].iterrows():
                corpo.append(f"{ESTRATEGIAS[m['carteira']]} & {reais(m['final_10mil'])} & "
                             f"{reais(m['final_com_aportes'])} \\\\")
            corpo.append("\\midrule")
        aplicado = dp["aplicado_com_aportes"].iloc[0] if not dp.empty else 0
        return ("\\begin{table}[htbp]\n\\centering\n"
                "\\caption{Valor final de um investimento em cada carteira (custo de 0,10\\%)}\n"
                "\\label{tab:patrimonio}\n\\footnotesize\n\\begin{tabular}{lrr}\n\\toprule\n"
                "\\textbf{Carteira} & \\textbf{R\\$ 10 mil iniciais} & "
                "\\textbf{R\\$ 10 mil + R\\$ 10 mil/ano} \\\\\n\\midrule\n"
                + "\n".join(corpo[:-1]) + "\n\\bottomrule\n\\end{tabular}\n\n\\vspace{0.2cm}\n"
                f"\\small Nota: com aportes, o total aplicado é {reais(aplicado)}; aporte somado a cada 252 pregões.\\\\\n"
                "Fonte: Elaboração própria.\n\\end{table}\n")

    def _publicar(self, res):
        """Resumo enxuto para a página pública "Desempenho do modelo"."""
        df, dt, _ = self._tabelas(res)
        perfis = {}
        for perfil, rp in res["perfis"].items():
            series = {}
            for est, s in rp["series"][f"{0.001:.4f}"].items():
                acum = (1 + s.fillna(0)).cumprod().resample("W").last() - 1
                series[est] = [round(float(v), 4) for v in acum.values]
                datas = [d.date().isoformat() for d in acum.index]
            perfis[perfil] = {
                "metricas": df[(df["perfil"] == perfil)].replace({float("nan"): None}).to_dict(orient="records"),
                "testes": dt[(dt["perfil"] == perfil)].to_dict(orient="records") if not dt.empty else [],
                "serie": {"datas": datas, **series},
                "jaccard_macro": rp["camada_macro"]["jaccard_medio"],
            }
        dados = {"gerado_em": date.today().isoformat(), "periodo": res["periodo"],
                 "hipoteses": self._resumo["hipoteses_custo_0,10%"],
                 "patrimonio": self._resumo["patrimonio_custo_0,10%"], "perfis": perfis,
                 "nomes": ESTRATEGIAS}
        ARQUIVO_PUBLICADO.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_PUBLICADO.write_text(json.dumps(dados, ensure_ascii=False, default=str), encoding="utf-8")

    def _latex(self, res, df, dt, custo=0.001):
        p = res["periodo"]
        ini = "/".join(reversed(p["inicio"].split("-")))
        fim = "/".join(reversed(p["fim"].split("-")))
        corpo = []
        for perfil in res["perfis"]:
            corpo.append(f"\\midrule\n\\multicolumn{{9}}{{l}}{{\\textit{{Perfil {NOMES_PERFIL[perfil]}}}}} \\\\")
            sub = df[(df["perfil"] == perfil) & (df["custo"] == custo)]
            tt = dt[(dt["perfil"] == perfil) & (dt["custo"] == custo)] if not dt.empty else dt
            for _, m in sub.iterrows():
                pv = "--"
                if m["carteira"] != "combinada" and not tt.empty:
                    linha = tt[tt["contra"] == m["carteira"]]
                    if not linha.empty:
                        pv = _br(linha["p_valor"].iloc[0], 3)
                corpo.append(" & ".join([
                    ESTRATEGIAS[m["carteira"]], _br(m["retorno_anual"], 1, True), _br(m["volatilidade"], 1, True),
                    _br(m["sharpe"]), _br(m["sortino"]), _br(m["max_drawdown"], 1, True),
                    _br(m["cvar95"], 2, True), _br(m["turnover_medio"], 1, True), pv]) + " \\\\")
        return (
            "\\begin{table}[htbp]\n\\centering\n"
            f"\\caption{{Desempenho fora da amostra das carteiras ({ini} a {fim}; custo de 0,10\\%)}}\n"
            "\\label{tab:resultado-backtest}\n\\footnotesize\n"
            "\\begin{tabular}{lrrrrrrrr}\n\\toprule\n"
            "\\textbf{Carteira} & \\textbf{Ret.} & \\textbf{Vol.} & \\textbf{Sharpe} & \\textbf{Sortino} & "
            "\\textbf{MDD} & \\textbf{CVaR} & \\textbf{Turn.} & \\textbf{p (LW)} \\\\\n"
            + "\n".join(corpo) +
            "\n\\bottomrule\n\\end{tabular}\n\n\\vspace{0.2cm}\n"
            "\\small Nota: retorno e volatilidade anualizados; CVaR diário a 95\\%; \\textit{turnover} médio por "
            "rebalanceamento; p (LW) é o p-valor do teste de Ledoit e Wolf (2008) para a diferença de Sharpe "
            f"em relação à carteira combinada. {p['rebalanceamentos']} rebalanceamentos trimestrais.\\\\\n"
            "Fonte: Elaboração própria.\n\\end{table}\n")

    def _imprimir(self, res):
        p = res["periodo"]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nPeríodo de teste: {p['inicio']} a {p['fim']} — {p['rebalanceamentos']} rebalanceamentos"))
        for perfil, rp in res["perfis"].items():
            for custo in (f"{c:.4f}" for c in CUSTOS_PADRAO):
                self.stdout.write(self.style.MIGRATE_HEADING(f"\n{NOMES_PERFIL[perfil]} — custo {float(custo):.2%}"))
                self.stdout.write(f"{'carteira':34s} {'ret.a.a':>8s} {'vol':>7s} {'sharpe':>7s} {'sortino':>8s} "
                                  f"{'MDD':>7s} {'CVaR':>7s} {'turn':>6s}  {'p(LW)':>6s}")
                testes = rp["testes"].get(custo, {})
                for est, m in rp["metricas"][custo].items():
                    if not m:
                        continue
                    pv = testes.get(est, {}).get("p_valor")
                    self.stdout.write(
                        f"{ESTRATEGIAS[est]:34s} {m['retorno_anual']:8.1%} {m['volatilidade']:7.1%} "
                        f"{m['sharpe']:7.2f} {m['sortino']:8.2f} {m['max_drawdown']:7.1%} {m['cvar95']:7.2%} "
                        f"{(m['turnover_medio'] if pd.notna(m['turnover_medio']) else 0):6.1%}  "
                        f"{('' if pv is None else f'{pv:.3f}'):>6s}")
            cm = rp["camada_macro"]["jaccard_medio"]
            if cm is not None:
                self.stdout.write(f"Seleção com x sem camada macro (Jaccard médio): {cm:.2f}")
        self.stdout.write(self.style.MIGRATE_HEADING("\nHipóteses (custo de 0,10%, nível de 5%)"))
        self.stdout.write(json.dumps(self._resumo["hipoteses_custo_0,10%"], ensure_ascii=False, indent=2))
