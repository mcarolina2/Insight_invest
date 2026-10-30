from django.test import TestCase
from django.test import SimpleTestCase
from .suitability import classificar, PERGUNTAS

def resp(valor):  # todas as respostas com o mesmo índice
    return {p["id"]: valor for p in PERGUNTAS}

class QuizTests(SimpleTestCase):
    def test_faixas(self):
        self.assertEqual(classificar(resp(1))["perfil"], "conservador")   # 7 pontos
        self.assertEqual(classificar(resp(2))["perfil"], "intermediario") # 14 pontos
        self.assertEqual(classificar(resp(3))["perfil"], "arrojado")      # 21 pontos

    def test_trava_de_horizonte_curto(self):
        r = resp(3); r["horizonte"] = 0
        self.assertEqual(classificar(r)["perfil"], "conservador")

    def test_resposta_invalida(self):
        r = resp(1); r["queda"] = 9
        with self.assertRaises(ValueError):
            classificar(r)