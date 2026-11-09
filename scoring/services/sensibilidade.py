"""
Análise de sensibilidade dos pesos AHP e da regra de neutralidade.

Para cada perfil:
  1. Seleção-base A: ativos candidatos com os pesos do AHP.
  2. Cada julgamento a_ij das duas matrizes (dimensões fundamentalistas e
     camadas) é movido um grau na escala de Saaty (para cima e para baixo),
     os pesos são recalculados e obtém-se a seleção B.
  3. Sobreposição: índice de Jaccard J = |A ∩ B| / |A ∪ B|.
  4. Regra de neutralidade: indicador ausente recebe 50 (base); compara-se com
     25 (penalização parcial) e 0 (penalização total).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from scoring.services import ahp
from scoring.services.motor_scoring_v2 import (calcular_scores_v2, config_perfil,
                                               selecionar_candidatos, valor_ausente)

ESCALA_SAATY = [1 / 9, 1 / 8, 1 / 7, 1 / 6, 1 / 5, 1 / 4, 1 / 3, 1 / 2,
                1, 2, 3, 4, 5, 6, 7, 8, 9]
VALORES_AUSENTE = (25.0, 0.0)


def vizinho_saaty(v: float, passo: int) -> float | None:
    """Valor um grau acima (+1) ou abaixo (-1) na escala de Saaty; None fora da escala."""
    i = int(np.argmin([abs(np.log(v) - np.log(x)) for x in ESCALA_SAATY]))
    j = i + passo
    return ESCALA_SAATY[j] if 0 <= j < len(ESCALA_SAATY) else None


def fmt_saaty(v: float) -> str:
    return str(int(round(v))) if v >= 1 else f"1/{int(round(1 / v))}"


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def _cfg_ahp(perfil: str, jul_dim: dict | None = None, jul_cam: dict | None = None) -> tuple[dict, float, float]:
    cfg = dict(config_perfil(perfil))
    jd = jul_dim or ahp.JULGAMENTOS_DIMENSOES[perfil]
    jc = jul_cam or ahp.JULGAMENTOS_CAMADAS[perfil]
    wd, _, rcd = ahp.pesos_ahp(ahp.matriz_reciproca(4, jd))
    wc, _, rcc = ahp.pesos_ahp(ahp.matriz_reciproca(3, jc))
    cfg["micro_pesos"] = dict(zip(ahp.DIMENSOES, map(float, wd)))
    cfg["pesos"] = dict(zip(ahp.CAMADAS, map(float, wc)))
    return cfg, rcd, rcc


def _selecao(perfil: str, cfg: dict, data_ref: date | None) -> list[str]:
    df = calcular_scores_v2(perfil, data_ref=data_ref, cfg=cfg)
    return selecionar_candidatos(df, perfil, cfg=cfg)["ticker"].tolist()


def analisar_perfil(perfil: str, data_ref: date | None = None) -> dict:
    cfg_base, _, _ = _cfg_ahp(perfil)
    base = _selecao(perfil, cfg_base, data_ref)
    nomes = {"dimensoes": ahp.DIMENSOES, "camadas": ahp.CAMADAS}
    julgamentos = {"dimensoes": ahp.JULGAMENTOS_DIMENSOES[perfil],
                   "camadas": ahp.JULGAMENTOS_CAMADAS[perfil]}
    linhas = []
    for matriz, jul in julgamentos.items():
        for (i, j), v in jul.items():
            for passo in (-1, +1):
                novo = vizinho_saaty(v, passo)
                if novo is None:
                    continue
                alterado = {**jul, (i, j): novo}
                cfg, rcd, rcc = _cfg_ahp(perfil, **({"jul_dim": alterado} if matriz == "dimensoes"
                                                    else {"jul_cam": alterado}))
                sel = _selecao(perfil, cfg, data_ref)
                linhas.append({
                    "perfil": perfil, "matriz": matriz,
                    "julgamento": f"{nomes[matriz][i]} × {nomes[matriz][j]}",
                    "de": fmt_saaty(v), "para": fmt_saaty(novo),
                    "rc": rcd if matriz == "dimensoes" else rcc,
                    "jaccard": jaccard(base, sel),
                    "entram": sorted(set(sel) - set(base)), "saem": sorted(set(base) - set(sel)),
                })
    neutralidade = []
    for valor in VALORES_AUSENTE:
        with valor_ausente(valor):
            sel = _selecao(perfil, cfg_base, data_ref)
        neutralidade.append({"perfil": perfil, "valor_ausente": valor, "jaccard": jaccard(base, sel),
                             "entram": sorted(set(sel) - set(base)), "saem": sorted(set(base) - set(sel))})
    js = [l["jaccard"] for l in linhas]
    return {"perfil": perfil, "selecao_base": base, "julgamentos": linhas, "neutralidade": neutralidade,
            "jaccard_medio": float(np.mean(js)) if js else None,
            "jaccard_minimo": float(np.min(js)) if js else None,
            "frac_identica": float(np.mean([j == 1.0 for j in js])) if js else None}


def analisar(perfis=("conservador", "intermediario", "arrojado"), data_ref: date | None = None) -> dict:
    return {p: analisar_perfil(p, data_ref) for p in perfis}


def tabela(resultado: dict) -> pd.DataFrame:
    return pd.DataFrame([l for r in resultado.values() for l in r["julgamentos"]])
