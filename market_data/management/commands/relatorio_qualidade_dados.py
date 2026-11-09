"""
Gera os números da Seção "Caracterização e Qualidade dos Dados" do TCC.

Uso:
    python manage.py relatorio_qualidade_dados
    python manage.py relatorio_qualidade_dados --sem-download   # pula yfinance

Saída: impressa no terminal e salva em data/relatorio_qualidade/
  - funil.csv            -> Tabela "Funil de elegibilidade dos ativos"
  - completude.csv       -> Tabela "Completude dos indicadores por fonte"
  - descritivas.csv      -> Tabela "Estatísticas descritivas dos indicadores"
  - descritivas.tex      -> mesma tabela já em LaTeX (linhas \\hline)
"""
import os
from datetime import date

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand

from portfolio.models import Ativo
from market_data.models import KpiMicro, KpiEstatistico, KpiMacro

PASTA_SAIDA = os.path.join("data", "relatorio_qualidade")

# Indicadores da Tabela de descritivas: (campo, rótulo, fonte)
INDICADORES_FUND = [
    ("liquidez_corrente",   "Liquidez corrente"),
    ("roe",                 "ROE"),
    ("roa",                 "ROA"),
    ("margem_liquida",      "Margem líquida"),
    ("margem_ebitda",       "Margem EBITDA"),
    ("divida_ebitda",       "Dívida líq./EBITDA"),
    ("crescimento_receita", "Cresc. receita"),
]
INDICADORES_ESTAT = [
    ("volatilidade_anual", "Volatilidade anual"),
    ("beta",               "Beta"),
]
CAMPOS_MACRO = ["selic", "ipca_mensal", "pib_trimestral", "desemprego"]

MIN_PREGOES = 252                 # mesmo critério do motor_scoring_v2
LIMIAR_VOLUME = 1_000_000         # R$ 1 milhão/dia
PREGOES_6M = 126                  # ~6 meses


def _para_float(df):
    return df.apply(pd.to_numeric, errors="coerce")


def descritivas(serie: pd.Series) -> dict:
    s = serie.dropna().astype(float)
    if s.empty:
        return {"N": 0}
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    outliers = ((s < q1 - 3 * iqr) | (s > q3 + 3 * iqr)).sum()
    return {
        "N": int(s.size), "Media": s.mean(), "DP": s.std(),
        "Min": s.min(), "Mediana": s.median(), "Max": s.max(),
        "Outliers_3IQR": int(outliers),
    }


class Command(BaseCommand):
    help = "Relatório de cobertura, completude, outliers, histórico e liquidez da base."

    def add_arguments(self, parser):
        parser.add_argument("--sem-download", action="store_true",
                            help="Não baixa preços do yfinance (pula histórico e liquidez).")

    def handle(self, *args, **opts):
        os.makedirs(PASTA_SAIDA, exist_ok=True)
        out = self.stdout.write

        # ── 1. Cadastro ────────────────────────────────────────────────
        total = Ativo.objects.count()
        por_tipo = pd.Series(list(Ativo.objects.values_list("tipo", flat=True))).value_counts().to_dict()
        com_cnpj = Ativo.objects.exclude(cnpj__isnull=True).exclude(cnpj="").count()
        n_setores = Ativo.objects.exclude(setor="").values("setor").distinct().count()

        # ── 2. Fundamentalistas: último KpiMicro por ativo ────────────
        campos_fund = [c for c, _ in INDICADORES_FUND]
        micro = pd.DataFrame(list(
            KpiMicro.objects.order_by("ativo_id", "-data_ref").distinct("ativo_id")
            .values("ativo_id", "ativo__ticker", "data_ref", *campos_fund)
        ))
        universo = micro["ativo__ticker"].tolist() if not micro.empty else []
        n_universo = len(universo)
        micro_num = _para_float(micro[campos_fund]) if not micro.empty else pd.DataFrame()

        # ── 3. Estatísticos: último KpiEstatistico por ativo do universo ──
        estat = pd.DataFrame(list(
            KpiEstatistico.objects.filter(ativo__ticker__in=universo)
            .order_by("ativo_id", "-data_calculo").distinct("ativo_id")
            .values("ativo__ticker", "volatilidade_anual", "beta", "n_observacoes")
        ))
        estat_num = _para_float(estat[[c for c, _ in INDICADORES_ESTAT]]) if not estat.empty else pd.DataFrame()

        # ── 4. Macro: completude nos últimos 10 anos ──────────────────
        macro = pd.DataFrame(list(
            KpiMacro.objects.filter(data_ref__gte=date(date.today().year - 10, 1, 1))
            .values("data_ref", *CAMPOS_MACRO)
        ))

        # ── 5. Histórico e liquidez (yfinance) ────────────────────────
        n_hist, n_liq = None, None
        if not opts["sem_download"] and universo:
            import yfinance as yf
            out("Baixando 3 anos de preços para o universo (pode levar alguns minutos)...")
            dados = yf.download([f"{t}.SA" for t in universo], period="3y",
                                auto_adjust=False, progress=False, threads=True)
            close, volume = dados["Close"], dados["Volume"]
            n_obs = close.notna().sum()
            ok_hist = n_obs[n_obs >= MIN_PREGOES].index
            n_hist = len(ok_hist)
            vol_fin = (close * volume).tail(PREGOES_6M).mean()
            n_liq = int((vol_fin.reindex(ok_hist) > LIMIAR_VOLUME).sum())
            pd.DataFrame({"n_pregoes_3a": n_obs, "volume_financeiro_medio_6m": vol_fin}) \
              .to_csv(os.path.join(PASTA_SAIDA, "historico_liquidez_por_ativo.csv"))

        # ── Tabela: funil ─────────────────────────────────────────────
        funil = pd.DataFrame([
            ("Ativos mapeados na B3", total),
            ("Com CNPJ mapeado na CVM", com_cnpj),
            ("Com indicadores fundamentalistas calculados", n_universo),
            (f"Com ao menos {MIN_PREGOES} pregões na janela de 3 anos", n_hist),
            ("Com volume financeiro médio diário > R$ 1 mi (6 meses)", n_liq),
        ], columns=["Etapa", "Ativos"])
        funil.to_csv(os.path.join(PASTA_SAIDA, "funil.csv"), index=False)

        # ── Tabela: completude ────────────────────────────────────────
        linhas = []
        for c, rot in INDICADORES_FUND:
            v = micro_num[c].notna().sum() if not micro_num.empty else 0
            linhas.append(("CVM / fundamentalista", rot, 100 * v / max(n_universo, 1)))
        for c, rot in INDICADORES_ESTAT:
            v = estat_num[c].notna().sum() if not estat_num.empty else 0
            linhas.append(("Yahoo Finance / estatística", rot, 100 * v / max(n_universo, 1)))
        for c in CAMPOS_MACRO:
            v = macro[c].notna().mean() * 100 if not macro.empty else 0
            linhas.append(("BCB / macroeconômica (meses, 10 anos)", c, v))
        completude = pd.DataFrame(linhas, columns=["Fonte", "Indicador", "Completude_%"])
        completude.to_csv(os.path.join(PASTA_SAIDA, "completude.csv"), index=False)

        # ── Tabela: descritivas ───────────────────────────────────────
        desc = []
        for c, rot in INDICADORES_FUND:
            desc.append({"Indicador": rot, **(descritivas(micro_num[c]) if not micro_num.empty else {"N": 0})})
        for c, rot in INDICADORES_ESTAT:
            desc.append({"Indicador": rot, **(descritivas(estat_num[c]) if not estat_num.empty else {"N": 0})})
        desc = pd.DataFrame(desc)
        desc.to_csv(os.path.join(PASTA_SAIDA, "descritivas.csv"), index=False)

        def fmt(x):
            if pd.isna(x):
                return "--"
            return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        with open(os.path.join(PASTA_SAIDA, "descritivas.tex"), "w", encoding="utf-8") as f:
            for _, r in desc.iterrows():
                f.write(f"{r['Indicador']} & {int(r.get('N', 0))} & " +
                        " & ".join(fmt(r.get(k)) for k in ["Media", "DP", "Min", "Mediana", "Max"]) +
                        f" & {int(r['Outliers_3IQR']) if pd.notna(r.get('Outliers_3IQR')) else '--'} \\\\\n\\hline\n")

        # ── Impressão ─────────────────────────────────────────────────
        pd.set_option("display.width", 160)
        out(self.style.SUCCESS("\n== CADASTRO =="))
        out(f"Total: {total} | por tipo: {por_tipo} | com CNPJ: {com_cnpj} "
            f"({100 * com_cnpj / max(total, 1):.1f}%) | setores: {n_setores}")
        out(self.style.SUCCESS("\n== FUNIL ==")); out(funil.to_string(index=False))
        out(self.style.SUCCESS("\n== COMPLETUDE ==")); out(completude.round(1).to_string(index=False))
        out(self.style.SUCCESS("\n== DESCRITIVAS ==")); out(desc.round(3).to_string(index=False))
        out("\nObs.: confira a unidade do ROE/ROA/margens no banco (fração ou %) antes de copiar.")
        out(self.style.SUCCESS(f"\nArquivos salvos em {PASTA_SAIDA}/"))
