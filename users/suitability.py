# users/suitability.py — lógica pura, sem Django, fácil de testar
PERGUNTAS = [
    # (id, dimensão, enunciado, [alternativas em ordem crescente de risco])
    ("objetivo", "objetivos", "Qual é o principal objetivo deste investimento?",
     ["Preservar o capital", "Gerar renda regular", "Crescimento moderado", "Maximizar o crescimento"]),
    ("horizonte", "objetivos", "Por quanto tempo pretende manter o dinheiro investido?",
     ["Até 1 ano", "De 1 a 3 anos", "De 3 a 5 anos", "Mais de 5 anos"]),
    ("queda", "perdas", "Se sua carteira caísse 20% em um mês, o que você faria?",
     ["Venderia tudo", "Venderia parte", "Manteria", "Compraria mais"]),
    ("perda_max", "perdas", "Qual perda máxima você aceitaria em 12 meses?",
     ["Nenhuma", "Até 5%", "Até 15%", "Mais de 15%"]),
    ("necessidade", "perdas", "Esse dinheiro poderá fazer falta nos próximos 12 meses?",
     ["Sim, com certeza", "Talvez", "Provavelmente não", "Não, tenho reserva separada"]),
    ("conhecimento", "conhecimento", "Como você avalia seu conhecimento sobre investimentos?",
     ["Nenhum", "Básico (poupança, CDB)", "Intermediário (ações, fundos)", "Avançado"]),
    ("experiencia", "conhecimento", "Qual sua experiência com investimentos?",
     ["Nunca investi", "Apenas renda fixa", "Renda variável há menos de 2 anos", "Renda variável há mais de 2 anos"]),
]

FAIXAS = [(8, "conservador"), (15, "intermediario"), (21, "arrojado")]  # pontuação máxima = 21


def classificar(respostas: dict) -> dict:
    """respostas: {id_pergunta: índice da alternativa (0 a 3)}."""
    pontos = sum(int(respostas[p[0]]) for p in PERGUNTAS)
    perfil = next(nome for limite, nome in FAIXAS if pontos <= limite)

    # Regras de trava: prazo curto ou intolerância a perda limitam o perfil
    vetos = []
    if int(respostas["horizonte"]) == 0:
        vetos.append("horizonte de até 1 ano")
    if int(respostas["perda_max"]) == 0:
        vetos.append("nenhuma tolerância a perdas")
    if vetos:
        perfil = "conservador"

    return {"pontos": pontos, "perfil": perfil, "vetos": vetos}