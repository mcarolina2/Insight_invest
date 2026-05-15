from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


# =========================================================
# Função auxiliar para tratar NaN do pandas
# =========================================================
def limpar_nan(valor):
    """
    Converte NaN do pandas/numpy para None
    e transforma numpy.float64 em float comum.
    """
    try:
        import pandas as pd

        if pd.isna(valor):
            return None

        return float(valor)

    except Exception:
            # evita infinitos ou explosões matemáticas
        if abs(valor) > 999999999:
            return None
        return valor


# =========================================================
# KPI MICRO
# =========================================================
class KpiMicro(models.Model):
    """
    Análise fundamentalista por ativo (DRE, Balanço Patrimonial, valuation).
    Histórico: um ativo terá um registro por período analisado.
    Relação: Ativo 1:N KpiMicro
    """

    ativo = models.ForeignKey(
        'portfolio.Ativo',
        on_delete=models.CASCADE,
        related_name='kpis_micro'
    )

    data_ref = models.DateField()

    # --- Liquidez ---
    liquidez_corrente = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    liquidez_seca = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    liquidez_imediata = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    liquidez_geral = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )
    # --- Rentabilidade ---
    giro_ativo = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    indice_rentabilidade_acao = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    prazo_retorno = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    roe = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Return on Equity"
    )

    roa = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Return on Assets"
    )

    margem_liquida = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    margem_ebitda = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

        # --- Indicadores por ação ---
    valor_patrimonial_acao = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    lucro_por_acao = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    dividendo_por_acao = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    rentabilidade_dividendos = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    distribuicao_dividendos = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Payout"
    )
    # --- Endividamento ---
    divida_liquida = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True
    )

    divida_ebitda = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    # --- Crescimento ---
    crescimento_receita = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    crescimento_lucro = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    # --- Valuation ---
    pl = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Preço/Lucro"
    )

    pvpa = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Preço/Valor Patrimonial"
    )

    ev_ebitda = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    dy = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Dividend Yield"
    )

    class Meta:
        unique_together = ('ativo', 'data_ref')
        ordering = ['-data_ref']
        verbose_name = "KPI Micro"

    def __str__(self):
        return f"{self.ativo.ticker} | Micro | {self.data_ref}"


# =========================================================
# KPI MACRO
# =========================================================
class KpiMacro(models.Model):
    """
    Indicadores macroeconômicos (Banco Central, IPEA Data).
    """

    data_ref = models.DateField(unique=True)

    # --- Inflação e Juros ---
    selic = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True
    )

    ipca_mensal = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True
    )

    igpm_mensal = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True
    )

    # --- Atividade Econômica ---
    pib_trimestral = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True
    )

    ibc_br = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True
    )

    desemprego = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True
    )

    # --- Balança Comercial ---
    exportacoes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    importacoes = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    balanca_comercial = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    # --- Câmbio ---
    dolar_brl = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True
    )

    risco_brasil = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="CDS 5 anos / EMBI+"
    )

    # --- Mercado ---
    ibovespa_fechamento = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True
    )

    volume_negociado_b3 = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True
    )

    class Meta:
        ordering = ['-data_ref']
        verbose_name = "KPI Macro"

    def __str__(self):
        return f"Macro | {self.data_ref}"


# =========================================================
# KPI TIME
# =========================================================
class KpiTime(models.Model):

    ativo = models.ForeignKey(
        'portfolio.Ativo',
        on_delete=models.CASCADE,
        related_name='kpis_time'
    )

    data_ref = models.DateField()

    # --- Volume e Liquidez ---
    volume_medio_20d = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True
    )

    volume_diario = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True
    )

    # --- Volatilidade ---
    volatilidade_30d = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        null=True,
        blank=True
    )

    beta = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True
    )

    # --- Momentum ---
    retorno_1m = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        null=True,
        blank=True
    )

    retorno_3m = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        null=True,
        blank=True
    )

    retorno_12m = models.DecimalField(
        max_digits=8,
        decimal_places=6,
        null=True,
        blank=True
    )

    rsi_14 = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="RSI 14 períodos"
    )

    media_movel_50 = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True
    )

    media_movel_200 = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True
    )

    class Meta:
        unique_together = ('ativo', 'data_ref')
        ordering = ['-data_ref']
        verbose_name = "KPI Time"

    def __str__(self):
        return f"{self.ativo.ticker} | Time | {self.data_ref}"


# =========================================================
# SENTIMENTO
# =========================================================
class SentimentoMercado(models.Model):

    FONTE_CHOICES = [
        ('twitter', 'X / Twitter'),
        ('reddit', 'Reddit'),
        ('infomoney', 'InfoMoney'),
        ('valor', 'Valor Econômico'),
        ('bloomberg', 'Bloomberg Brasil'),
        ('outros', 'Outros'),
    ]

    ativo = models.ForeignKey(
        'portfolio.Ativo',
        on_delete=models.CASCADE,
        related_name='sentimentos',
        null=True,
        blank=True
    )

    data_ref = models.DateField()

    fonte = models.CharField(
        max_length=20,
        choices=FONTE_CHOICES
    )

    score_sentimento = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        validators=[
            MinValueValidator(-1),
            MaxValueValidator(1)
        ]
    )

    volume_mencoes = models.IntegerField(default=0)

    resumo_nlp = models.TextField(blank=True)

    class Meta:
        ordering = ['-data_ref']
        verbose_name = "Sentimento de Mercado"

    def __str__(self):
        ticker = self.ativo.ticker if self.ativo else "GERAL"
        return f"{ticker} | {self.fonte} | {self.data_ref} | {self.score_sentimento}"