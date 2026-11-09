"""
Sensibilidade da seleção de ativos aos julgamentos do AHP e à regra de neutralidade.

    python manage.py analise_sensibilidade
    python manage.py analise_sensibilidade --data 2025-06-30 --saida data/sensibilidade

Gera julgamentos.csv, neutralidade.csv e tabela_sensibilidade.tex.
"""
from datetime import date
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from scoring.services.sensibilidade import analisar

NOMES = {"conservador": "Conservador", "intermediario": "Intermediário", "arrojado": "Arrojado"}


def _br(v, casas=2):
    return "--" if v is None else f"{v:.{casas}f}".replace(".", ",")


class Command(BaseCommand):
    help = "Análise de sensibilidade (Jaccard) dos pesos AHP e da regra de neutralidade."

    def add_arguments(self, parser):
        parser.add_argument("--perfis", nargs="+", default=["conservador", "intermediario", "arrojado"])
        parser.add_argument("--data", type=date.fromisoformat, default=None)
        parser.add_argument("--saida", default=None)

    def handle(self, *args, perfis, data, saida, **o):
        res = analisar(perfis, data)
        destino = Path(saida or Path(settings.BASE_DIR) / "data" / "sensibilidade" / date.today().isoformat())
        destino.mkdir(parents=True, exist_ok=True)

        jul = pd.DataFrame([l for r in res.values() for l in r["julgamentos"]])
        neu = pd.DataFrame([l for r in res.values() for l in r["neutralidade"]])
        for df in (jul, neu):
            for c in ("entram", "saem"):
                if c in df:
                    df[c] = df[c].apply(" ".join)
        jul.to_csv(destino / "julgamentos.csv", index=False)
        neu.to_csv(destino / "neutralidade.csv", index=False)
        (destino / "tabela_sensibilidade.tex").write_text(self._latex(res), encoding="utf-8")

        for p, r in res.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{NOMES.get(p, p)} — seleção-base: {len(r['selecao_base'])} ativos"))
            if r["jaccard_medio"] is None:
                self.stdout.write("  sem ativos selecionados")
                continue
            self.stdout.write(f"  {len(r['julgamentos'])} alterações de ±1 grau: Jaccard médio {r['jaccard_medio']:.2f}, "
                              f"mínimo {r['jaccard_minimo']:.2f}; seleção idêntica em {r['frac_identica']:.0%}")
            piores = sorted(r["julgamentos"], key=lambda l: l["jaccard"])[:3]
            for l in piores:
                self.stdout.write(f"    {l['matriz']:9s} {l['julgamento']:30s} {l['de']:>4s}→{l['para']:<4s} "
                                  f"J={l['jaccard']:.2f} RC={l['rc']:.3f} entram {l['entram']} saem {l['saem']}")
            for n in r["neutralidade"]:
                self.stdout.write(f"  ausente = {n['valor_ausente']:.0f} em vez de 50: J={n['jaccard']:.2f}")
        self.stdout.write(self.style.SUCCESS(f"\nArquivos em {destino}"))

    def _latex(self, res):
        linhas = []
        for p, r in res.items():
            neu = {n["valor_ausente"]: n["jaccard"] for n in r["neutralidade"]}
            linhas.append(" & ".join([NOMES.get(p, p), str(len(r["julgamentos"])), _br(r["jaccard_medio"]),
                                      _br(r["jaccard_minimo"]),
                                      "--" if r["frac_identica"] is None else f"{r['frac_identica']*100:.0f}\\%",
                                      _br(neu.get(25.0)), _br(neu.get(0.0))]) + " \\\\\n\\hline")
        return ("\\begin{table}[htbp]\n\\centering\n"
                "\\caption{Sensibilidade da seleção de ativos aos julgamentos do AHP e à regra de neutralidade}\n"
                "\\label{tab:sensibilidade}\n\\footnotesize\n"
                "\\begin{tabular}{|l|c|c|c|c|c|c|}\n\\hline\n"
                "\\textbf{Perfil} & \\textbf{Alterações} & \\textbf{$J$ médio} & \\textbf{$J$ mínimo} & "
                "\\textbf{Seleção idêntica} & \\textbf{$J$ (ausente=25)} & \\textbf{$J$ (ausente=0)} \\\\\n\\hline\n"
                + "\n".join(linhas) +
                "\n\\end{tabular}\n\n\\vspace{0.2cm}\n"
                "\\small Nota: cada julgamento das matrizes de dimensões e de camadas foi alterado em um grau da "
                "escala de Saaty, para cima e para baixo; $J$ é o índice de Jaccard entre a seleção original e a "
                "alterada.\\\\\nFonte: Elaboração própria.\n\\end{table}\n")
