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


# Caracteres que só aparecem quando um texto latin-1 (arquivos da CVM) foi lido
# como cp1250 (padrão de alguns Windows): "Têxtil" vira "Tęxtil".
# Todos os caracteres em que cp1250 e latin-1 divergem (ex.: "Ă" no lugar de "Ã",
# o mais comum: "MINERAÇĂO"). Calculado, para não esquecer nenhum à mão.
_SO_CP1250 = {bytes([b]).decode("cp1250", "ignore") for b in range(0xA0, 0x100)} - \
             {bytes([b]).decode("latin-1") for b in range(0xA0, 0x100)} - {""}


def reparar_mojibake(txt):
    """'Tęxtil' -> 'Têxtil'. Só altera textos com caracteres típicos do erro."""
    if not txt or not any(c in _SO_CP1250 for c in str(txt)):
        return txt
    try:
        return str(txt).encode("cp1250").decode("latin-1")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return txt


# Setores do Yahoo Finance (inglês) -> nomes em português, para ativos sem
# cadastro na CVM (BDRs, ETFs) não aparecerem com setor em inglês.
SETORES_YAHOO = {
    "financial services": "Serviços Financeiros", "financial": "Serviços Financeiros",
    "utilities": "Utilidade Pública", "energy": "Petróleo, Gás e Energia",
    "basic materials": "Materiais Básicos", "industrials": "Bens Industriais",
    "consumer cyclical": "Consumo Cíclico", "consumer defensive": "Consumo não Cíclico",
    "healthcare": "Saúde", "technology": "Tecnologia da Informação",
    "communication services": "Comunicações", "real estate": "Imobiliário",
}


def limpar_setor(setor) -> str:
    if setor is None:
        return ""
    s = reparar_mojibake(str(setor).strip())
    if s.lower() in ("", "nan", "none"):
        return ""
    s = SETORES_YAHOO.get(s.lower(), s)
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
