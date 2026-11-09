"""Testes da página do ativo, da busca, dos perfis, dos papéis e do quiz."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from market_data.models import KpiMicro
from portfolio.models import Ativo
from scoring.models import CarteiraRecomendada, ConfiguracaoPerfil, HistoricoConfiguracaoPerfil
from scoring.services.motor_scoring_v2 import calcular_scores_v2, selecionar_candidatos
from scoring.tests import criar_base, precos_simulados, respostas_quiz
from users.models import PerfilRisco, RespostaQuiz, User

SILENCIO = open("/dev/null", "w")


@patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
class PaginaAtivoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        criar_base()
        Ativo.objects.create(ticker="HGLG11", nome="Fundo Logístico", tipo="fii")
        Ativo.objects.create(ticker="SEMD3", nome="Sem Demonstrações SA", tipo="acao")

    def setUp(self):
        cache.clear()

    def test_ativo_do_topo_entra_na_carteira(self, _):
        df = calcular_scores_v2("intermediario")
        topo = df.iloc[0]["ticker"]
        r = self.client.get(reverse("core:ativo", args=[topo]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Entra na carteira deste perfil")
        self.assertContains(r, "Preço ajustado")               # gráfico com preços simulados
        self.assertContains(r, "Posição 1 de")

    def test_ativo_sem_dfp_explica_o_motivo(self, _):
        self.assertContains(self.client.get(reverse("core:ativo", args=["SEMD3"])),
                            "Não há demonstrações financeiras")
        self.assertContains(self.client.get(reverse("core:ativo", args=["HGLG11"])),
                            "Fundos imobiliários não publicam")

    def test_ativo_reprovado_no_filtro_mostra_qual_filtro(self, _):
        a = Ativo.objects.get(ticker="TST003")
        KpiMicro.objects.filter(ativo=a).update(liquidez_corrente=0.4)
        u = User.objects.create_user("c", password="x-senha-123")
        PerfilRisco.objects.create(user=u, tipo="conservador", pontuacao=3)
        self.client.force_login(u)
        self.assertContains(self.client.get(reverse("core:ativo", args=["TST003"])),
                            "Não passa nos filtros do perfil: liquidez corrente 0.40x")

    def test_ticker_minusculo_redireciona_e_inexistente_404(self, _):
        self.assertRedirects(self.client.get(reverse("core:ativo", args=["tst003"])),
                             reverse("core:ativo", args=["TST003"]))
        self.assertEqual(self.client.get(reverse("core:ativo", args=["XXXX9"])).status_code, 404)

    def test_busca(self, _):
        url = reverse("core:buscar_ativo")
        self.assertRedirects(self.client.get(url, {"q": "tst003"}), reverse("core:ativo", args=["TST003"]))
        self.assertRedirects(self.client.get(url, {"q": "Logístico"}), reverse("core:ativo", args=["HGLG11"]))
        r = self.client.get(url, {"q": "Empresa"})
        self.assertContains(r, "TST013")
        self.assertContains(self.client.get(url, {"q": "zzzz"}), "Nenhum ativo encontrado")

    def test_home_leva_ao_cadastro_com_perfil_sugerido(self, _):
        r = self.client.get(reverse("core:home"))
        self.assertContains(r, reverse("users:registro") + "?perfil_sugerido=arrojado")
        r = self.client.get(reverse("users:registro") + "?perfil_sugerido=arrojado")
        self.assertContains(r, "Você escolheu a carteira <b>Arrojado</b>")


class PerfisEPapeisTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        criar_base()

    def test_configuracao_inicial_igual_ao_tcc(self):
        c = ConfiguracaoPerfil.objects.get(perfil="conservador")
        self.assertEqual((c.qtd_ativos, float(c.max_pct_ativo), float(c.liquidez_corrente_min)), (8, 0.2, 1.0))
        self.assertEqual(ConfiguracaoPerfil.objects.count(), 3)

    def test_mudar_configuracao_muda_o_motor_e_a_pagina(self):
        ConfiguracaoPerfil.objects.filter(perfil="intermediario").update(qtd_ativos=4)
        cand = selecionar_candidatos(calcular_scores_v2("intermediario"), "intermediario")
        self.assertEqual(len(cand), 4)
        self.assertContains(self.client.get(reverse("users:perfis")), "até 4")

    def test_validacao_dos_pesos(self):
        from django.core.exceptions import ValidationError
        c = ConfiguracaoPerfil.objects.get(perfil="arrojado")
        c.peso_crescimento = 0.9
        with self.assertRaises(ValidationError):
            c.full_clean()

    def test_superusuario_edita_e_gera_historico_e_recalcula(self):
        admin = User.objects.create_superuser("sudo", "s@x.com", "x-senha-123")
        self.client.force_login(admin)
        c = ConfiguracaoPerfil.objects.get(perfil="arrojado")
        url = reverse("admin:scoring_configuracaoperfil_change", args=[c.pk])
        dados = {f: getattr(c, f) for f in (
            "descricao", "peso_fundamentalista", "peso_macro", "peso_estatistico", "peso_liquidez",
            "peso_rentabilidade", "peso_endividamento", "peso_crescimento", "score_min",
            "qtd_ativos", "max_pct_ativo")}
        dados.update({"qtd_ativos": 12, "liquidez_corrente_min": "", "divida_ebitda_max": "", "roe_min": "0.05"})
        r = self.client.post(url, dados)
        self.assertEqual(r.status_code, 302, r.content[:500])
        c.refresh_from_db()
        self.assertEqual(c.qtd_ativos, 12)
        self.assertEqual(c.atualizado_por, admin)
        self.assertTrue(HistoricoConfiguracaoPerfil.objects.filter(perfil="arrojado", alterado_por=admin).exists())

        with patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados):
            self.client.post(reverse("admin:scoring_configuracaoperfil_changelist"), {
                "action": "recalcular_carteiras",
                "_selected_action": list(ConfiguracaoPerfil.objects.values_list("pk", flat=True))})
        self.assertEqual(CarteiraRecomendada.objects.filter(ativo=True).count(), 3)

    def test_analista_edita_perfis_mas_nao_usuarios(self):
        u = User.objects.create_user("economista", password="x-senha-123")
        call_command("criar_analista", "economista", stdout=SILENCIO)
        u.refresh_from_db()
        self.assertTrue(u.is_staff and u.groups.filter(name="Analista").exists())
        self.client.force_login(u)
        c = ConfiguracaoPerfil.objects.first()
        self.assertEqual(self.client.get(reverse("admin:scoring_configuracaoperfil_change", args=[c.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:users_user_changelist")).status_code, 403)
        self.assertTrue(u.has_perm("scoring.recalcular_carteiras"))

        call_command("criar_analista", "economista", "--remover", stdout=SILENCIO)
        u.refresh_from_db()
        self.assertFalse(u.is_staff)

    def test_investidor_nao_entra_no_painel(self):
        u = User.objects.create_user("investidor", password="x-senha-123")
        self.client.force_login(u)
        r = self.client.get(reverse("admin:index"))
        self.assertEqual(r.status_code, 302)

    def test_garantir_superusuario_idempotente(self):
        env = {"DJANGO_SUPERUSER_USERNAME": "maria", "DJANGO_SUPERUSER_PASSWORD": "x-senha-123",
               "DJANGO_SUPERUSER_EMAIL": "m@x.com"}
        with patch.dict("os.environ", env):
            call_command("garantir_superusuario", stdout=SILENCIO)
            call_command("garantir_superusuario", stdout=SILENCIO)
        self.assertTrue(User.objects.get(username="maria").is_superuser)


class QuizTests(TestCase):

    def setUp(self):
        self.u = User.objects.create_user("ana", password="x-senha-123")
        self.client.force_login(self.u)

    def test_cada_envio_fica_no_historico(self):
        self.client.post(reverse("users:quiz"), respostas_quiz(0))
        r = self.client.post(reverse("users:quiz"), respostas_quiz(3))
        self.assertEqual(RespostaQuiz.objects.filter(user=self.u).count(), 2)
        ultima = RespostaQuiz.objects.filter(user=self.u).first()
        self.assertEqual((ultima.perfil, ultima.pontuacao), ("arrojado", 21))
        self.assertEqual(PerfilRisco.objects.get(user=self.u).tipo, "arrojado")
        self.assertContains(r, "Como você pontuou")
        self.assertContains(r, "Conhecimento e experiência")
        self.assertContains(self.client.get(reverse("users:perfil")), "Histórico do questionário")

    def test_resposta_incompleta_mostra_erro_e_nao_salva(self):
        dados = respostas_quiz(2)
        dados.pop("horizonte")
        r = self.client.post(reverse("users:quiz"), dados)
        self.assertContains(r, "Resposta ausente")
        self.assertFalse(RespostaQuiz.objects.exists())

    def test_perfil_sugerido_pela_home_fica_registrado(self):
        self.client.get(reverse("users:registro") + "?perfil_sugerido=arrojado")
        self.client.post(reverse("users:quiz"), respostas_quiz(0))
        self.assertEqual(RespostaQuiz.objects.get(user=self.u).perfil_sugerido_home, "arrojado")


class PesosAHPTests(TestCase):
    """Os pesos usados pelo sistema são os do AHP documentado no TCC."""

    def test_matrizes_consistentes_e_pesos_somam_1(self):
        from scoring.services.ahp import calcular_todos
        for perfil, r in calcular_todos().items():
            self.assertLess(r["rc_dimensoes"], 0.10, perfil)
            self.assertLess(r["rc_camadas"], 0.10, perfil)
            self.assertAlmostEqual(sum(r["dimensoes"].values()), 1.0, places=9)
            self.assertAlmostEqual(sum(r["camadas"].values()), 1.0, places=9)

    def test_sistema_usa_os_pesos_do_ahp(self):
        from scoring.services.ahp import calcular_todos
        from scoring.services.motor_scoring_v2 import obter_perfis
        ahp, em_uso = calcular_todos(), obter_perfis()
        for perfil in ahp:
            self.assertEqual(em_uso[perfil]["pesos"], ahp[perfil]["camadas"])
            self.assertEqual(em_uso[perfil]["micro_pesos"], ahp[perfil]["dimensoes"])

    def test_comando_pesos_ahp(self):
        from io import StringIO
        out = StringIO()
        call_command("pesos_ahp", stdout=out)
        self.assertIn("RC = 0.022", out.getvalue())
        self.assertNotIn("INCONSISTENTE", out.getvalue())
