# users/suitability.py
PERGUNTAS = [
    {"id": "objetivo", "dimensao": "objetivos",
     "texto": "Qual é o principal objetivo deste investimento?",
     "opcoes": ["Preservar o capital", "Gerar renda regular",
                "Crescimento moderado", "Maximizar o crescimento"]},
    {"id": "horizonte", "dimensao": "objetivos",
     "texto": "Por quanto tempo pretende manter o dinheiro investido?",
     "opcoes": ["Até 1 ano", "De 1 a 3 anos", "De 3 a 5 anos", "Mais de 5 anos"]},
    {"id": "queda", "dimensao": "perdas",
     "texto": "Se sua carteira caísse 20% em um mês, o que você faria?",
     "opcoes": ["Venderia tudo", "Venderia parte", "Manteria", "Compraria mais"]},
    {"id": "perda_max", "dimensao": "perdas",
     "texto": "Qual perda máxima você aceitaria em 12 meses?",
     "opcoes": ["Nenhuma", "Até 5%", "Até 15%", "Mais de 15%"]},
    {"id": "necessidade", "dimensao": "perdas",
     "texto": "Esse dinheiro pode fazer falta nos próximos 12 meses?",
     "opcoes": ["Sim, com certeza", "Talvez", "Provavelmente não",
                "Não, tenho reserva separada"]},
    {"id": "conhecimento", "dimensao": "conhecimento",
     "texto": "Como você avalia seu conhecimento sobre investimentos?",
     "opcoes": ["Nenhum", "Básico (poupança, CDB)",
                "Intermediário (ações, fundos)", "Avançado"]},
    {"id": "experiencia", "dimensao": "conhecimento",
     "texto": "Qual sua experiência com investimentos?",
     "opcoes": ["Nunca investi", "Apenas renda fixa",
                "Renda variável há menos de 2 anos",
                "Renda variável há mais de 2 anos"]},
]

PONTUACAO_MAXIMA = 3 * len(PERGUNTAS)          # 21
DIMENSOES = {
    "objetivos":    "Objetivos e horizonte",
    "perdas":       "Tolerância a perdas e necessidade de recursos",
    "conhecimento": "Conhecimento e experiência",
}
FAIXAS = [(8, "conservador"), (15, "intermediario"), (21, "arrojado")]


def validar(respostas: dict) -> dict:
    """Converte para int e garante que toda pergunta tem resposta entre 0 e 3."""
    limpas = {}
    for p in PERGUNTAS:
        bruto = respostas.get(p["id"])
        try:
            valor = int(bruto)
        except (TypeError, ValueError):
            raise ValueError(f"Resposta ausente ou inválida: {p['id']}")
        if not 0 <= valor < len(p["opcoes"]):
            raise ValueError(f"Alternativa fora do intervalo: {p['id']}")
        limpas[p["id"]] = valor
    return limpas


def classificar(respostas: dict) -> dict:
    r = validar(respostas)
    pontos = sum(r.values())
    perfil = next(nome for limite, nome in FAIXAS if pontos <= limite)

    # Regra de trava: a resposta mais restritiva prevalece sobre a pontuação
    vetos = []
    if r["horizonte"] == 0:
        vetos.append("horizonte de investimento de até 1 ano")
    if r["perda_max"] == 0:
        vetos.append("nenhuma tolerância a perdas")
    if vetos:
        perfil = "conservador"

    por_dimensao = []
    for chave, nome in DIMENSOES.items():
        ids = [p["id"] for p in PERGUNTAS if p["dimensao"] == chave]
        por_dimensao.append({"dimensao": chave, "nome": nome,
                             "pontos": sum(r[i] for i in ids), "maximo": 3 * len(ids)})

    return {"pontos": pontos, "perfil": perfil, "vetos": vetos, "respostas": r,
            "por_dimensao": por_dimensao, "maximo": PONTUACAO_MAXIMA}