"""Testes da extração e do cálculo dos indicadores fundamentalistas."""
from io import StringIO

import pandas as pd
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from services.calculators import indicadores as ind
from services.extractors.cvm_extractor import CONTAS_DRE, filtrar_contas


def _linhas_dre(cnpj, data, escala, receita, lucro):
    base = {"CNPJ_CIA": cnpj, "DENOM_CIA": "EMPRESA X", "DT_REFER": data, "VERSAO": 1,
            "ORDEM_EXERC": "ÚLTIMO", "ESCALA_MOEDA": escala}
    return [dict(base, CD_CONTA="3.01", VL_CONTA=receita),
            dict(base, CD_CONTA="3.11", VL_CONTA=lucro)]


class EscalaCVMTests(SimpleTestCase):

    def test_empresa_que_muda_de_milhares_para_reais(self):
        # 2023 em MIL (1.000 mil = R$ 1 milhão); 2024 em reais (R$ 1,1 milhão): +10%
        df = pd.DataFrame(_linhas_dre("1", "2023-12-31", "MIL", 1000, 100) +
                          _linhas_dre("1", "2024-12-31", "UNIDADE", 1_100_000, 120_000))
        dre = filtrar_contas(df, CONTAS_DRE).sort_values("DT_REFER")
        self.assertEqual(list(dre["receita_liquida"]), [1_000_000, 1_100_000])
        cresc = ind.crescimento_yoy(*dre["receita_liquida"].iloc[::-1])
        self.assertAlmostEqual(cresc, 0.10)          # antes da correção: 1099,0 (109.900%)

    def test_arquivo_sem_coluna_de_escala_continua_funcionando(self):
        df = pd.DataFrame(_linhas_dre("1", "2024-12-31", "UNIDADE", 500, 50)).drop(columns="ESCALA_MOEDA")
        self.assertEqual(float(filtrar_contas(df, CONTAS_DRE)["receita_liquida"].iloc[0]), 500)


class CasosSemSentidoEconomicoTests(SimpleTestCase):

    def test_roe_com_patrimonio_negativo_ou_nulo(self):
        self.assertIsNone(ind.roe(-100, -50))        # prejuízo / PL negativo daria +200%
        self.assertIsNone(ind.roe(10, 0))
        self.assertEqual(ind.roe(10, 100), 0.1)

    def test_crescimento_a_partir_de_prejuizo_ou_zero(self):
        self.assertIsNone(ind.crescimento_yoy(100, -10))
        self.assertIsNone(ind.crescimento_yoy(100, 0))
        self.assertEqual(ind.crescimento_yoy(90, 100), -0.1)

    def test_divida_ebitda(self):
        self.assertEqual(ind.divida_sobre_ebitda(300, 100), 3.0)
        self.assertEqual(ind.divida_sobre_ebitda(300, -50), ind.DIVIDA_EBITDA_SEM_GERACAO_DE_CAIXA)
        self.assertIsNone(ind.divida_sobre_ebitda(-300, -50))    # caixa líquido e EBITDA negativo


class AuditoriaTests(TestCase):

    def test_aponta_valores_implausiveis(self):
        from datetime import date
        from market_data.models import KpiMicro
        from portfolio.models import Ativo
        a = Ativo.objects.create(ticker="ERRO3", nome="Empresa com erro", tipo="acao")
        KpiMicro.objects.create(ativo=a, data_ref=date(2024, 12, 31), crescimento_receita=1200, roe=0.12)
        out = StringIO()
        call_command("auditar_indicadores", stdout=out)
        self.assertIn("ERRO3", out.getvalue())
        self.assertIn("120,000.0%", out.getvalue())


class CorrigirTickersTests(TestCase):
    """Tickers inventados a partir do nome são trocados pelos oficiais do FCA da CVM."""

    FCA = (
        "CNPJ_Companhia;Data_Referencia;Versao;ID_Documento;Nome_Empresarial;Valor_Mobiliario;"
        "Sigla_Classe_Acao_Preferencial;Classe_Acao_Preferencial;Codigo_Negociacao;Composicao_BDR_Unit;"
        "Mercado;Sigla_Entidade_Administradora;Entidade_Administradora;Data_Inicio_Negociacao;"
        "Data_Fim_Negociacao;Segmento;Data_Inicio_Listagem;Data_Fim_Listagem\n"
        "60.872.504/0001-23;2026-01-01;1;1;ITAU UNIBANCO HOLDING S.A.;Ações Ordinárias;;;ITUB3;;Bolsa;B3;B3;;;N1;;\n"
        "60.872.504/0001-23;2026-01-01;1;1;ITAU UNIBANCO HOLDING S.A.;Ações Preferenciais;PN;;ITUB4;;Bolsa;B3;B3;;;N1;;\n"
        "60.872.504/0001-23;2026-01-01;1;1;ITAU UNIBANCO HOLDING S.A.;Debêntures;;;ITUB-D11;;Balcão;;;;;;;\n"
        "84.429.695/0001-11;2026-01-01;1;1;WEG S.A.;Ações Ordinárias;;;WEGE3;;Bolsa;B3;B3;;;NM;;\n"
        "11.111.111/0001-11;2026-01-01;1;1;EMPRESA SAIU;Ações Ordinárias;;;SAIU3;;Bolsa;B3;B3;;2024-01-01;;;\n"
    )

    def _fca(self):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="latin-1")
        f.write(self.FCA)
        f.close()
        return f.name

    def test_le_fca_e_filtra(self):
        from services.extractors.fca_extractor import tickers_oficiais
        df = tickers_oficiais(arquivo=self._fca())
        self.assertEqual(set(df["ticker"]), {"ITUB3", "ITUB4", "WEGE3"})   # sem debênture nem deslistado

    def test_corrige_inventados(self):
        from io import StringIO
        from portfolio.models import Ativo
        from market_data.models import KpiMicro
        from datetime import date
        itau = Ativo.objects.create(ticker="ITAU3", nome="ITAU UNIBANCO", tipo="acao", setor="Bancos",
                                    cnpj="60.872.504/0001-23")
        KpiMicro.objects.create(ativo=itau, data_ref=date(2024, 12, 31), roe=0.2)
        Ativo.objects.create(ticker="WEGE3", nome="WEG", tipo="acao")                 # sem CNPJ (o "bug dos 317")
        Ativo.objects.create(ticker="FECH3", nome="FECHADA", tipo="acao", cnpj="22.222.222/0001-22")
        out = StringIO()
        call_command("corrigir_tickers", "--arquivo", self._fca(), stdout=out)
        self.assertIn("Tickers inventados (não existem na B3)   : 1", out.getvalue())
        self.assertTrue(Ativo.objects.filter(ticker="ITAU3").exists())          # diagnóstico não altera
        call_command("corrigir_tickers", "--arquivo", self._fca(), "--aplicar", stdout=StringIO())
        self.assertFalse(Ativo.objects.filter(ticker="ITAU3").exists())
        self.assertEqual(set(Ativo.objects.filter(cnpj="60.872.504/0001-23").values_list("ticker", flat=True)),
                         {"ITUB3", "ITUB4"})
        self.assertEqual(Ativo.objects.get(ticker="WEGE3").cnpj, "84.429.695/0001-11")
        self.assertFalse(Ativo.objects.get(ticker="FECH3").ativo)
