"""
Testes ponta a ponta da plataforma com dados simulados (sem acesso à internet).

Rodar:  python manage.py test
Os preços do Yahoo Finance são substituídos por séries simuladas.
"""
import json
import zlib
from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from market_data.models import KpiEstatistico, KpiMacro, KpiMicro
from portfolio.models import Ativo
from scoring.models import CarteiraRecomendada, ScoreAtivo
from scoring.services.carteira import MODELO_MAP, RISCO_MAP
from scoring.services.motor_scoring_v2 import PERFIS, calcular_scores_v2, selecionar_candidatos
from users.models import PerfilRisco, User

SETORES = ["Financeiro", "Energia", "Utilidade Pública", "Consumo", "Materiais Básicos"]
N_ATIVOS = 30


def precos_simulados(tickers, anos=3):
    """Preços diários com retornos positivos e correlação moderada (determinísticos)."""
    tickers = list(tickers)
    # semente determinística (hash() do Python muda a cada execução)
    rng = np.random.default_rng(zlib.crc32(",".join(sorted(tickers)).encode()))
    n, T = len(tickers), 252 * anos
    mu = np.linspace(0.0005, 0.0012, n)
    vol = np.linspace(0.008, 0.020, n)
    corr = 0.3 * np.ones((n, n)) + 0.7 * np.eye(n)
    r = (rng.standard_normal((T, n)) @ np.linalg.cholesky(corr).T) * vol + mu
    datas = pd.bdate_range(end=date.today(), periods=T)
    return pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=datas, columns=tickers)


def criar_base():
    """30 ações com DFP do exercício anterior, estatísticas e dados macro."""
    rng = np.random.default_rng(0)
    dfp = date(date.today().year - 1, 12, 31)
    if date.today() - dfp < timedelta(days=90):       # garante DFP já "publicada"
        dfp = date(dfp.year - 1, 12, 31)
    for i in range(N_ATIVOS):
        a = Ativo.objects.create(ticker=f"TST{i:02d}3"[:6], nome=f"Empresa {i}", tipo="acao",
                                 setor=SETORES[i % len(SETORES)])
        KpiMicro.objects.create(
            ativo=a, data_ref=dfp,
            liquidez_corrente=round(0.8 + rng.random() * 2, 2), liquidez_seca=round(0.6 + rng.random(), 2),
            liquidez_imediata=round(rng.random(), 2), liquidez_geral=round(0.5 + rng.random(), 2),
            roe=round(rng.uniform(-0.1, 0.35), 4), roa=round(rng.uniform(-0.05, 0.15), 4),
            giro_ativo=round(rng.uniform(0.2, 1.5), 2),
            margem_liquida=round(rng.uniform(-0.1, 0.3), 4), margem_ebitda=round(rng.uniform(0, 0.5), 4),
            divida_ebitda=round(rng.uniform(-1, 5), 2),
            crescimento_receita=round(rng.uniform(-0.2, 0.4), 4),
            crescimento_lucro=round(rng.uniform(-0.5, 0.8), 4),
        )
        KpiEstatistico.objects.create(
            ativo=a, data_calculo=date.today() - timedelta(days=1), janela_dias=252,
            volatilidade_anual=round(rng.uniform(0.15, 0.5), 4), beta=round(rng.uniform(0.4, 1.6), 3),
            cv=round(rng.uniform(5, 40), 3), skewness=round(rng.uniform(-1, 1), 3),
            curtose=round(rng.uniform(2, 8), 3), retorno_normal=bool(rng.random() > 0.5),
        )
    KpiMacro.objects.create(data_ref=date.today() - timedelta(days=5), selic=15, ipca_mensal=0.4,
                            pib_trimestral=0.5, desemprego=6.5)


def respostas_quiz(valor):
    """Mesma alternativa (0 a 3) em todas as 7 perguntas."""
    from users.suitability import PERGUNTAS
    return {p["id"]: str(valor) for p in PERGUNTAS}


@patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
class FluxoCompletoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        criar_base()

    def setUp(self):
        cache.clear()

    def _usuario_com_perfil(self, nivel=2):
        u = User.objects.create_user("maria", password="senha-forte-123")
        self.client.force_login(u)
        self.client.post(reverse("users:quiz"), respostas_quiz(nivel))
        return u

    # ── cadastro, quiz e redirecionamentos ───────────────────────────
    def test_cadastro_quiz_e_login_levam_a_carteira(self, _):
        r = self.client.post(reverse("users:registro"), {
            "username": "joao", "password1": "Senha-Forte-987", "password2": "Senha-Forte-987"})
        self.assertRedirects(r, reverse("users:quiz"))
        r = self.client.post(reverse("users:quiz"), respostas_quiz(3))
        self.assertContains(r, "Arrojado")
        self.assertEqual(PerfilRisco.objects.get(user__username="joao").tipo, "arrojado")
        self.client.logout()
        r = self.client.post(reverse("users:login"), {"username": "joao", "password": "Senha-Forte-987"})
        self.assertRedirects(r, reverse("scoring:carteira"))

    def test_usuario_sem_perfil_vai_para_quiz(self, _):
        u = User.objects.create_user("sem", password="x-senha-123")
        self.client.force_login(u)
        self.assertRedirects(self.client.get(reverse("scoring:carteira")), reverse("users:quiz"))

    def test_visitante_vai_para_login(self, _):
        r = self.client.get(reverse("scoring:carteira"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("users:login"), r.url)

    def test_regra_de_trava_do_quiz(self, _):
        u = User.objects.create_user("trava", password="x-senha-123")
        self.client.force_login(u)
        resp = respostas_quiz(3)
        resp["horizonte"] = "0"                      # até 1 ano -> conservador
        self.client.post(reverse("users:quiz"), resp)
        self.assertEqual(PerfilRisco.objects.get(user=u).tipo, "conservador")

    def test_refazer_quiz_mostra_respostas_anteriores(self, _):
        self._usuario_com_perfil(1)
        import re
        html = self.client.get(reverse("users:quiz")).content.decode()
        self.assertEqual(len(re.findall(r"required\s+checked", html)), 7)

    def test_paginas_do_usuario(self, _):
        self._usuario_com_perfil()
        for nome in ("users:perfil", "users:quiz", "billing:upgrade", "scoring:carteira"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)

    # ── otimização interativa ────────────────────────────────────────
    def _otimizar(self, **filtros):
        return self.client.post(reverse("scoring:otimizar"), json.dumps(filtros),
                                content_type="application/json")

    def test_nove_combinacoes_de_risco_e_retorno(self, _):
        self._usuario_com_perfil(2)                  # 14 pontos -> intermediário
        teto = PERFIS["intermediario"]["max_pct_ativo"] * 100
        usados = set()
        for (risco, retorno), modelo in MODELO_MAP.items():
            with self.subTest(risco=risco, retorno=retorno):
                r = self._otimizar(risco=risco, retorno=retorno, setor="todos")
                self.assertEqual(r.status_code, 200, r.content)
                d = r.json()
                total = sum(a["pct"] for a in d["alocacao"])
                self.assertAlmostEqual(total, 100, delta=0.2)
                acoes = [a for a in d["alocacao"] if a["tipo"] == "acao"]
                self.assertAlmostEqual(sum(a["pct_nas_acoes"] for a in acoes), 100, delta=0.2)
                self.assertTrue(all(a["pct_nas_acoes"] <= teto + 0.01 for a in acoes))
                # o modelo pedido é usado, salvo se inviável (aí cai para escore com aviso)
                self.assertIn(d["modelo"], (modelo, "score"))
                if d["modelo"] != modelo:
                    self.assertTrue(d["aviso"])
                vol_max = RISCO_MAP[risco]
                if d["modelo"] == "sharpe" and vol_max:
                    self.assertLessEqual(d["metricas_acoes"]["volatilidade"], vol_max + 1e-3)
                if d["modelo"] != "score":
                    self.assertGreater(len(d["fronteira"]), 2)
                usados.add(d["modelo"])
        # com os dados simulados, os quatro modelos precisam ter sido de fato aplicados
        self.assertEqual(usados, {"risk_parity", "markowitz", "sharpe", "score"})

    def test_filtro_setorial(self, _):
        self._usuario_com_perfil(2)
        d = self._otimizar(risco="medio", retorno="medio", setor="Energia").json()
        self.assertTrue(d["alocacao"])
        self.assertTrue(all(a["setor"] == "Energia" for a in d["alocacao"] if a["tipo"] == "acao"))

    def test_setor_sem_ativos_retorna_mensagem(self, _):
        self._usuario_com_perfil(2)
        r = self._otimizar(risco="medio", retorno="medio", setor="Inexistente")
        self.assertEqual(r.status_code, 422)
        self.assertIn("Inexistente", r.json()["erro"])

    def test_filtro_invalido(self, _):
        self._usuario_com_perfil(2)
        self.assertEqual(self._otimizar(risco="x", retorno="medio").status_code, 400)

    def test_sem_precos_cai_para_escore(self, mock_precos):
        mock_precos.side_effect = Exception("sem rede")
        self._usuario_com_perfil(2)
        d = self._otimizar(risco="medio", retorno="medio").json()
        self.assertEqual(d["modelo"], "score")
        self.assertTrue(d["aviso"])

    # ── fluxo em lote e home ─────────────────────────────────────────
    def test_atualizar_carteiras_e_home(self, _):
        call_command("atualizar_carteiras", stdout=open("/dev/null", "w"))
        self.assertEqual(CarteiraRecomendada.objects.filter(ativo=True).count(), 3)
        for c in CarteiraRecomendada.objects.filter(ativo=True):
            self.assertAlmostEqual(sum(a["pct"] for a in c.composicao), 100, delta=0.2)
            self.assertIn("sharpe", c.metricas)
        r = self.client.get(reverse("core:home"))
        self.assertEqual(r.status_code, 200)
        cart = CarteiraRecomendada.objects.get(perfil="conservador", ativo=True)
        self.assertContains(r, cart.composicao[0]["ticker"])

    def test_calcular_scores_salva_scoreativo(self, _):
        call_command("calcular_scores", stdout=open("/dev/null", "w"))
        self.assertEqual(ScoreAtivo.objects.count(), N_ATIVOS)
        self.assertTrue(ScoreAtivo.objects.exclude(score_estat=None).exists())


class MotorScoringTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        criar_base()

    def test_escores_entre_0_e_100_e_ordenados(self):
        df = calcular_scores_v2("intermediario")
        self.assertEqual(len(df), N_ATIVOS)
        self.assertTrue(df["score_final"].between(0, 100).all())
        self.assertTrue(df["score_final"].is_monotonic_decreasing)

    def test_perfis_geram_rankings_diferentes(self):
        top = {p: tuple(calcular_scores_v2(p).head(8)["ticker"]) for p in PERFIS}
        self.assertNotEqual(top["conservador"], top["arrojado"])

    def test_filtro_do_perfil_usa_o_balanco_mais_recente(self):
        """Antes, um ativo cujo balanço mais recente falhava no filtro entrava
        usando um balanço antigo. Agora ele fica de fora."""
        a = Ativo.objects.get(ticker="TST003")
        recente = KpiMicro.objects.get(ativo=a)
        recente.liquidez_corrente = 0.5            # falha no filtro do conservador (>= 1)
        recente.save()
        KpiMicro.objects.create(ativo=a, data_ref=recente.data_ref.replace(year=recente.data_ref.year - 1),
                                liquidez_corrente=3.0, divida_ebitda=1.0)
        self.assertNotIn("TST003", calcular_scores_v2("conservador")["ticker"].tolist())

    def test_data_ref_nao_olha_o_futuro(self):
        """No backtest, uma DFP só pode ser usada ~3 meses após o fim do exercício."""
        dfp = KpiMicro.objects.first().data_ref
        self.assertTrue(calcular_scores_v2("intermediario", data_ref=dfp + timedelta(days=30)).empty)
        self.assertFalse(calcular_scores_v2("intermediario", data_ref=dfp + timedelta(days=120)).empty)

    def test_selecao_respeita_quantidade_do_perfil(self):
        for p, cfg in PERFIS.items():
            cand = selecionar_candidatos(calcular_scores_v2(p), p)
            self.assertLessEqual(len(cand), cfg["qtd_ativos"])
            self.assertTrue((cand["score_final"] >= cfg["score_min"]).all())


class OtimizadorTests(TestCase):
    """Testes do módulo puro de otimização."""

    def setUp(self):
        from scoring.services.otimizador import estimar_parametros, preparar_precos
        self.precos = precos_simulados([f"A{i}" for i in range(8)])
        self.scores = pd.Series(np.linspace(50, 80, 8), index=self.precos.columns)
        self.mu, self.cov = estimar_parametros(preparar_precos(self.precos))

    def test_sharpe_com_selic_inatingivel_usa_escore(self):
        from scoring.services.otimizador import otimizar
        res = otimizar(self.precos, self.scores, "sharpe", w_max=0.2, taxa_livre_risco=5.0)
        self.assertEqual(res.modelo_usado, "score")
        self.assertIn("taxa livre de risco", res.aviso)

    def test_ponto_de_sharpe_maximo_nao_e_dominado(self):
        from scoring.services.otimizador import otimizar
        res = otimizar(self.precos, self.scores, "sharpe", w_max=0.2,
                       taxa_livre_risco=0.0, calcular_fronteira=True)
        m = res.metricas
        dominado = any(p["vol"] <= m["volatilidade"] - 1e-4 and p["ret"] >= m["retorno"] + 1e-4
                       for p in res.fronteira)
        self.assertFalse(dominado)

    def test_paridade_de_risco_iguala_contribuicoes(self):
        from scoring.services.otimizador import otimizar
        w = otimizar(self.precos, self.scores, "risk_parity", w_max=0.3).pesos.values
        C = self.cov.values
        contrib = w * (C @ w) / (w @ C @ w)
        self.assertLess(contrib.std(), 0.01)

    def test_teto_por_ativo_e_soma(self):
        from scoring.services.otimizador import otimizar
        for modelo in ("risk_parity", "markowitz", "sharpe", "score"):
            w = otimizar(self.precos, self.scores, modelo, w_max=0.15).pesos
            self.assertAlmostEqual(w.sum(), 1.0, places=6)
            self.assertLessEqual(w.max(), 0.15 + 1e-6)


class RendaFixaTests(TestCase):
    """Divisão renda fixa x ações pela perda máxima do quiz (Tobin, 1958)."""

    def test_regra_da_parcela_em_acoes(self):
        from scoring.services.renda_fixa import parcela_em_acoes
        self.assertEqual(parcela_em_acoes(0.20, 0.0), 0.0)            # nenhuma perda -> só renda fixa
        self.assertEqual(parcela_em_acoes(0.20, None), 1.0)           # sem limite -> só ações
        self.assertAlmostEqual(parcela_em_acoes(0.20, 0.05), 0.152, places=3)
        self.assertAlmostEqual(parcela_em_acoes(0.20, 0.15), 0.4559, places=3)
        self.assertEqual(parcela_em_acoes(0.05, 0.15), 1.0)           # ações pouco voláteis

    def test_perda_provavel_respeita_o_limite(self):
        from scoring.services.renda_fixa import montar_carteira_completa
        res = {"alocacao": {"AAA3": 60.0, "BBB3": 40.0},
               "metricas": {"retorno": 0.18, "volatilidade": 0.22}}
        for limite in (0.05, 0.15):
            c = montar_carteira_completa(res, limite, rf=0.12)
            self.assertLessEqual(c["metricas_totais"]["perda_provavel_12m"], limite + 1e-4)
            self.assertAlmostEqual(sum(i["pct"] for i in c["itens"]), 100, delta=0.05)

    @patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
    def test_usuario_que_nao_aceita_perdas_fica_so_em_renda_fixa(self, _):
        criar_base()
        u = User.objects.create_user("zero", password="x-senha-123")
        self.client.force_login(u)
        self.client.post(reverse("users:quiz"), respostas_quiz(0))
        d = self.client.post(reverse("scoring:otimizar"), json.dumps({"risco": "medio", "retorno": "medio"}),
                             content_type="application/json").json()
        self.assertEqual(d["pct_renda_fixa"], 100)
        self.assertEqual([a["tipo"] for a in d["alocacao"]], ["renda_fixa"])

    @patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
    def test_home_conservador_tem_mais_renda_fixa(self, _):
        criar_base()
        call_command("atualizar_carteiras", stdout=open("/dev/null", "w"))
        rf = {c.perfil: c.metricas["pct_renda_fixa"] for c in CarteiraRecomendada.objects.filter(ativo=True)}
        self.assertGreater(rf["conservador"], rf["intermediario"])
        self.assertGreater(rf["intermediario"], rf["arrojado"])
        self.assertEqual(rf["arrojado"], 0)
        cons = CarteiraRecomendada.objects.get(perfil="conservador", ativo=True)
        self.assertEqual(cons.composicao[0]["tipo"], "renda_fixa")
        self.assertContains(self.client.get(reverse("core:home")), "Renda fixa")


class AnaliseCarteiraTests(TestCase):
    """Distribuição dos retornos e sensibilidade macro (só exibição)."""

    def _precos_e_macro(self, beta_ipca=-2.0, meses_macro=36):
        precos = precos_simulados(["AAA3", "BBB3", "CCC3"], anos=3)
        pesos = {"AAA3": 0.5, "BBB3": 0.3, "CCC3": 0.2}
        from scoring.services.analise_carteira import _mensal, retornos_diarios
        r, _ = retornos_diarios(precos, pesos, 0.0, 0.12)
        mensal = _mensal(r)                                   # % no mês
        rng = np.random.default_rng(1)
        meses = list(mensal.index)[-meses_macro:]
        for i, per in enumerate(meses):
            ini = per.to_timestamp().date()
            ipca = (mensal[per] / beta_ipca) + rng.normal(0, 0.05)
            # linha mensal (IPCA, balança, IBC-Br) no dia 1 e linhas diárias de Selic
            KpiMacro.objects.create(data_ref=ini, ipca_mensal=round(ipca, 4),
                                    exportacoes=30000 + 500 * i, importacoes=25000 + 300 * i,
                                    ibc_br=140 + i * 0.3 + rng.normal(0, 0.5))
            for d in (10, 20):
                KpiMacro.objects.create(data_ref=ini.replace(day=d), selic=10 + 0.25 * (i // 3))
        return precos, pesos

    def test_estatistica_da_distribuicao(self):
        from scoring.services.analise_carteira import analisar_carteira
        precos, pesos = self._precos_e_macro()
        a = analisar_carteira(precos, pesos, 0.0, 0.12)
        e = a["estatistica"]
        self.assertTrue(e["disponivel"])
        self.assertEqual(sum(e["histograma"]["contagens"]), e["n_dias"])
        self.assertLess(e["var95_hist"], 0)
        self.assertLessEqual(e["cvar95_hist"], e["var95_hist"])
        self.assertLessEqual(e["drawdown_max"], 0)
        self.assertEqual(len(e["correlacao"]["matriz"]), 3)
        self.assertEqual(e["correlacao"]["matriz"][0][0], 1.0)
        json.dumps(a)                                         # sem NaN/objetos não serializáveis

    def test_sensibilidade_recupera_relacao_com_ipca(self):
        from scoring.services.analise_carteira import analisar_carteira
        precos, pesos = self._precos_e_macro(beta_ipca=-2.0)
        m = analisar_carteira(precos, pesos, 0.0, 0.12, {"AAA3": {"score_micro": 70}})["macro"]
        self.assertTrue(m["disponivel"])
        ipca = next(v for v in m["variaveis"] if v["campo"] == "ipca")
        self.assertTrue(ipca["significativo"])
        self.assertLess(ipca["correlacao"], -0.8)
        self.assertAlmostEqual(ipca["beta"], -2.0, delta=0.6)
        self.assertEqual(m["destaque"], "ipca")
        balanca = next(v for v in m["variaveis"] if v["campo"] == "balanca")
        self.assertTrue(balanca["disponivel"])                # exportações − importações
        self.assertEqual(len(m["por_ativo"]), 3)
        self.assertEqual(m["por_ativo"][0]["score_micro"], 70)

    def test_poucos_meses_de_macro_nao_inventa_resultado(self):
        from scoring.services.analise_carteira import analisar_carteira
        precos, pesos = self._precos_e_macro(meses_macro=6)
        m = analisar_carteira(precos, pesos, 0.0, 0.12)["macro"]
        self.assertFalse(m["disponivel"])
        self.assertIn("mínimo", m["motivo"])

    def test_renda_fixa_reduz_oscilacao(self):
        from scoring.services.analise_carteira import analisar_carteira
        precos, _ = self._precos_e_macro()
        so_acoes = analisar_carteira(precos, {"AAA3": 0.5, "BBB3": 0.5}, 0.0, 0.12)["estatistica"]
        metade = analisar_carteira(precos, {"AAA3": 0.25, "BBB3": 0.25}, 50.0, 0.12)["estatistica"]
        self.assertAlmostEqual(metade["desvio_diario"], so_acoes["desvio_diario"] / 2, delta=1e-4)
        tudo_rf = analisar_carteira(None, {}, 100.0, 0.12)
        self.assertFalse(tudo_rf["estatistica"]["disponivel"])
        self.assertIn("100% em renda fixa", tudo_rf["estatistica"]["motivo"])

    @patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
    def test_endpoint_devolve_analise(self, _):
        criar_base()
        u = User.objects.create_user("ana", password="x-senha-123")
        self.client.force_login(u)
        self.client.post(reverse("users:quiz"), respostas_quiz(3))
        d = self.client.post(reverse("scoring:otimizar"), json.dumps({"risco": "alto", "retorno": "alto"}),
                             content_type="application/json").json()
        self.assertIn("analise", d)
        self.assertTrue(d["analise"]["estatistica"]["disponivel"])
        self.assertIn("disponivel", d["analise"]["macro"])


class BacktestTests(TestCase):
    """Walk-forward, métricas, teste de Ledoit-Wolf e análise de sensibilidade."""

    def test_simulacao_deriva_e_custos(self):
        from scoring.services.backtest import simular
        idx = pd.bdate_range("2024-01-01", periods=6)
        r = pd.DataFrame({"A": [0.0, 0.10, -0.05, 0.02, 0.0, 0.01], "B": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}, index=idx)
        tj = simular(r, {1: pd.Series({"A": 0.5, "B": 0.5})}, inicio=1)
        self.assertEqual(tj.bruto.iloc[0], 0.0)                 # executa no fechamento, rende a partir do dia seguinte
        self.assertAlmostEqual(tj.bruto.iloc[1], -0.025)        # 0,5 × (−5%)
        self.assertAlmostEqual(tj.turnovers[0], 0.5)            # sai de caixa: ½·Σ|Δw| = 0,5
        self.assertAlmostEqual(tj.liquido(0.003).iloc[0], -0.003)

    def test_teste_ledoit_wolf(self):
        from scoring.services.backtest import teste_sharpe_ledoit_wolf
        rng = np.random.default_rng(7)
        idx = pd.bdate_range("2020-01-01", periods=1500)
        base = pd.Series(rng.normal(0.0002, 0.01, 1500), index=idx)
        melhor = base + 0.0012 + pd.Series(rng.normal(0, 0.002, 1500), index=idx)
        t = teste_sharpe_ledoit_wolf(melhor, base, reamostragens=400)
        self.assertGreater(t["delta_sharpe"], 0)
        self.assertLess(t["p_valor"], 0.05)
        ruido = base + pd.Series(rng.normal(0, 0.0005, 1500), index=idx)
        self.assertGreater(teste_sharpe_ledoit_wolf(ruido, base, reamostragens=400)["p_valor"], 0.10)

    def test_estatisticas_da_janela(self):
        from scoring.services.backtest import estatisticas_janela
        p = precos_simulados(["AAA3", "BBB3"], anos=2)
        e = estatisticas_janela(p)
        r = np.log(p / p.shift(1)).dropna()
        self.assertAlmostEqual(e.loc["AAA3", "volatilidade_anual"], r["AAA3"].std() * np.sqrt(252), places=6)
        self.assertTrue(set(e.columns) >= {"beta", "skewness", "curtose", "retorno_normal", "cv"})

    def test_sem_vies_de_antecipacao(self):
        """Mudar os preços DEPOIS de uma decisão não altera essa decisão."""
        from scoring.services.backtest import executar_backtest
        criar_base()
        tickers = list(Ativo.objects.values_list("ticker", flat=True))
        precos = precos_simulados(tickers, anos=3)
        a = executar_backtest(precos, perfis=["intermediario"], reamostragens=50)
        primeira = sorted(a["perfis"]["intermediario"]["carteiras"]["combinada"])[0]
        corte = precos.index[precos.index.get_indexer([pd.Timestamp(primeira)])[0] - 1]
        alterado = precos.copy()
        alterado.loc[alterado.index > corte] *= np.linspace(1, 3, len(alterado.index[alterado.index > corte]))[:, None]
        b = executar_backtest(alterado, perfis=["intermediario"], reamostragens=50)
        pa = a["perfis"]["intermediario"]["carteiras"]["combinada"][primeira]
        pb = b["perfis"]["intermediario"]["carteiras"]["combinada"][primeira]
        self.assertEqual(pa, pb)
        m = a["perfis"]["intermediario"]["metricas"]["0.0010"]
        self.assertEqual(set(m), {"combinada", "fundamentalista", "estatistica", "igual"})

    @patch("scoring.services.precos.baixar_ibovespa",
           side_effect=lambda anos=3: precos_simulados(["IBOVX", "Y1"], anos)["IBOVX"])
    @patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados)
    def test_comando_backtest_gera_arquivos(self, *_):
        import tempfile
        from io import StringIO
        from pathlib import Path
        criar_base()
        with tempfile.TemporaryDirectory() as d:
            call_command("backtest", "--anos", "3", "--perfis", "conservador", "--reamostragens", "50",
                         "--saida", d, stdout=StringIO())
            nomes = {p.name for p in Path(d).iterdir()}
            self.assertTrue({"metricas.csv", "testes_sharpe.csv", "tabela_backtest.tex", "resumo.json"} <= nomes)
            self.assertIn("Ibovespa", (Path(d) / "tabela_backtest.tex").read_text())

    def test_sensibilidade_ahp(self):
        from scoring.services.motor_scoring_v2 import _VALOR_AUSENTE, valor_ausente
        from scoring.services.sensibilidade import analisar_perfil, vizinho_saaty
        self.assertEqual(vizinho_saaty(2, +1), 3)
        self.assertEqual(vizinho_saaty(1, -1), 0.5)
        self.assertAlmostEqual(vizinho_saaty(1 / 4, -1), 1 / 5)
        self.assertIsNone(vizinho_saaty(9, +1))
        with valor_ausente(0):
            self.assertEqual(_VALOR_AUSENTE.get(), 0)
        self.assertEqual(_VALOR_AUSENTE.get(), 50)
        criar_base()
        r = analisar_perfil("intermediario")
        self.assertEqual(len(r["julgamentos"]), 18)            # 9 julgamentos × 2 direções
        self.assertTrue(0 <= r["jaccard_minimo"] <= r["jaccard_medio"] <= 1)
        self.assertEqual(len(r["neutralidade"]), 2)


class CandidatosSemPrecoTests(TestCase):
    """Ativo sem cotação no Yahoo é pulado e o próximo do ranking entra."""

    def test_pula_ativo_sem_historico(self):
        criar_base()
        from scoring.services.carteira import montar_carteira_otimizada
        from scoring.services.motor_scoring_v2 import calcular_scores_v2, config_perfil
        df = calcular_scores_v2("intermediario")
        qtd = config_perfil("intermediario")["qtd_ativos"]
        topo = df.sort_values("score_final", ascending=False)["ticker"].iloc[0]

        def sem_o_primeiro(tickers, anos=3):
            return precos_simulados([t for t in tickers if t != topo], anos)

        with patch("scoring.services.precos.baixar_precos", side_effect=sem_o_primeiro):
            cache.clear()
            res = montar_carteira_otimizada(df, "intermediario", modelo="score", calcular_fronteira=False)
        self.assertNotIn(topo, res["alocacao"])
        self.assertEqual(len(res["alocacao"]), qtd)          # carteira continua cheia
        self.assertIn(topo, res["aviso"])


class UmTickerPorEmpresaTests(TestCase):
    def test_on_e_pn_nao_entram_juntas(self):
        criar_base()
        from scoring.services.carteira import montar_carteira_otimizada
        from scoring.services.motor_scoring_v2 import calcular_scores_v2
        df0 = calcular_scores_v2("intermediario")
        topo = df0["ticker"].iloc[0]
        a = Ativo.objects.get(ticker=topo)
        a.cnpj = "60.872.504/0001-23"
        a.save()
        irmao = Ativo.objects.create(ticker=topo[:4] + "4", nome=a.nome, tipo="acao", setor=a.setor, cnpj=a.cnpj)
        for k in KpiMicro.objects.filter(ativo=a):
            k.pk = None
            k.ativo = irmao
            k.save()
        df = calcular_scores_v2("intermediario")
        par = df[df["ticker"].isin([topo, irmao.ticker])]
        self.assertEqual(par["score_micro"].nunique(), 1)          # mesma empresa, mesmo fundamento
        with patch("scoring.services.precos.baixar_precos", side_effect=precos_simulados):
            cache.clear()
            res = montar_carteira_otimizada(df, "intermediario", modelo="score", calcular_fronteira=False)
        self.assertEqual(len({topo, irmao.ticker} & set(res["alocacao"])), 1)
