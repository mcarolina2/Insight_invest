"""Nomes de setor da CVM (campo SETOR_ATIV do cadastro de companhias abertas).

A versão anterior cortava o texto no primeiro ". " para tirar um suposto
prefixo numérico ("1. Petróleo e Gás"). Mas o cadastro não tem esse prefixo, e
o corte pegava abreviações do próprio nome. Como a limpeza era aplicada duas
vezes (no extrator e no comando de carga), sobrava só o fim do texto:

    "Construção Civil, Mat. Constr. e Decoração"  ->  "e Decoração"
    "Emp. Adm. Part. - Alimentos"                 ->  "Part. - Alimentos"
    "Saneamento, Serv. Água e Gás"                ->  "Água e Gás"

`limpar_setor` agora só remove numeração no início e é idempotente;
`reparar_setor` recupera os nomes já gravados cortados.
"""
import re

_PREFIXO_NUMERICO = re.compile(r"^\s*\d+\s*[.\-)]\s+")
_HOLDING = re.compile(r"^(Emp\.\s*Adm\.\s*)?Part\.\s*-\s*", re.IGNORECASE)

# Nome cortado -> nome completo (casos em que o corte apagou o início)
_CORTADOS = {
    "e Decoração": "Construção Civil, Mat. Constr. e Decoração",
    "Constr. e Decoração": "Construção Civil, Mat. Constr. e Decoração",
    "Água e Gás": "Saneamento, Serv. Água e Gás",
    "Serv. Água e Gás": "Saneamento, Serv. Água e Gás",
}


def _holding(setor: str) -> str:
    """'Emp. Adm. Part. - Alimentos' -> 'Holding - Alimentos' (mais legível)."""
    resto = _HOLDING.sub("", setor).strip()
    resto = _CORTADOS.get(resto, resto)
    if resto.startswith("Const. Civil"):
        resto = "Construção Civil, Mat. Constr. e Decoração"
    return f"Holding - {resto}" if resto else "Holding"


def limpar_setor(setor) -> str:
    if setor is None:
        return ""
    s = str(setor).strip()
    if s.lower() in ("", "nan", "none"):
        return ""
    s = _PREFIXO_NUMERICO.sub("", s)
    if _HOLDING.match(s):
        s = _holding(s)
    return s[:100]


def reparar_setor(setor) -> str:
    """Corrige nomes já gravados com o corte antigo (e normaliza holdings)."""
    s = limpar_setor(setor)
    if s in _CORTADOS:
        return _CORTADOS[s]
    if s.startswith("Adm. Part.") or s.startswith("Part."):
        return _holding(s)
    return s
