"""
Pesos dos perfis pelo Analytic Hierarchy Process (SAATY, 1977; 1980).

Os julgamentos par a par abaixo são os documentados no TCC (seção
"Definição dos Pesos pelo Método AHP"). Para reproduzir as tabelas:
    python manage.py pesos_ahp
"""
import numpy as np

# Índice aleatório (IR) de Saaty, por ordem da matriz
INDICE_ALEATORIO = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32}

DIMENSOES = ["liquidez", "rentabilidade", "divida", "crescimento"]
CAMADAS = ["micro", "macro", "estat"]

# {(i, j): a_ij} para i < j — quantas vezes o critério i é mais importante que j
JULGAMENTOS_DIMENSOES = {
    "conservador":   {(0, 1): 1, (0, 2): 2, (0, 3): 3, (1, 2): 1, (1, 3): 3, (2, 3): 3},
    "intermediario": {(0, 1): 1/2, (0, 2): 1, (0, 3): 1, (1, 2): 2, (1, 3): 2, (2, 3): 1},
    "arrojado":      {(0, 1): 1/4, (0, 2): 1, (0, 3): 1/4, (1, 2): 4, (1, 3): 1, (2, 3): 1/4},
}
JULGAMENTOS_CAMADAS = {
    "conservador":   {(0, 1): 2, (0, 2): 3, (1, 2): 1},
    "intermediario": {(0, 1): 2, (0, 2): 2, (1, 2): 2},
    "arrojado":      {(0, 1): 2, (0, 2): 2, (1, 2): 1},
}


def matriz_reciproca(n: int, julgamentos: dict) -> np.ndarray:
    A = np.ones((n, n))
    for (i, j), v in julgamentos.items():
        A[i, j] = v
        A[j, i] = 1.0 / v
    return A


def pesos_ahp(A: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Retorna (pesos pelo autovetor principal, lambda_max, razão de consistência)."""
    valores, vetores = np.linalg.eig(A)
    k = int(np.argmax(valores.real))
    w = np.abs(vetores[:, k].real)
    w = w / w.sum()
    n = A.shape[0]
    lmax = float(valores[k].real)
    ic = (lmax - n) / (n - 1) if n > 2 else 0.0
    ir = INDICE_ALEATORIO.get(n, 1.32)
    rc = max(ic / ir, 0.0) if ir else 0.0
    return w, lmax, rc


def pesos_arredondados(w, casas: int = 2) -> list[float]:
    """Arredonda mantendo a soma exatamente igual a 1 (ajuste no maior peso)."""
    r = [round(float(x), casas) for x in w]
    r[int(np.argmax(w))] += round(1 - sum(r), casas)
    return [round(x, casas) for x in r]


def calcular_todos() -> dict:
    saida = {}
    for perfil in JULGAMENTOS_DIMENSOES:
        Ad = matriz_reciproca(4, JULGAMENTOS_DIMENSOES[perfil])
        Ac = matriz_reciproca(3, JULGAMENTOS_CAMADAS[perfil])
        wd, ld, rcd = pesos_ahp(Ad)
        wc, lc, rcc = pesos_ahp(Ac)
        saida[perfil] = {
            "dimensoes": dict(zip(DIMENSOES, pesos_arredondados(wd))),
            "camadas": dict(zip(CAMADAS, pesos_arredondados(wc))),
            "matriz_dimensoes": Ad, "matriz_camadas": Ac,
            "pesos_brutos_dimensoes": wd, "pesos_brutos_camadas": wc,
            "lambda_dimensoes": ld, "lambda_camadas": lc,
            "rc_dimensoes": rcd, "rc_camadas": rcc,
        }
    return saida
