from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
# Create your models here.
class KpiMicro(models.Model):
    """
    Análise fundamentalista por ativo (DRE, Balanço Patrimonial, valuation).
    Histórico: um ativo terá um registro por período analisado.
    Relação: Ativo 1:N KpiMicro
    """
    ativo              = models.ForeignKey('portfolio.Ativo', on_delete=models.CASCADE, related_name='kpis_micro')
    data_ref           = models.DateField()
 
    # --- Rentabilidade ---
    roe                = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                             help_text="Return on Equity")
    roa                = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                             help_text="Return on Assets")
    margem_liquida     = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    margem_ebitda      = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
 
    # --- Endividamento ---
    divida_liquida     = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    divida_ebitda      = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
 
    # --- Crescimento ---
    crescimento_receita = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    crescimento_lucro   = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
 
    # --- Valuation ---
    pl                 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                             help_text="Preço/Lucro")
    pvpa               = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                             help_text="Preço/Valor Patrimonial")
    ev_ebitda          = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    dy                 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True,
                                             help_text="Dividend Yield")
 
    class Meta:
        unique_together = ('ativo', 'data_ref')
        ordering = ['-data_ref']
        verbose_name = "KPI Micro"
 
    def __str__(self):
        return f"{self.ativo.ticker} | Micro | {self.data_ref}"
 
 
class KpiMacro(models.Model):
    """
    Indicadores macroeconômicos (Banco Central, IPEA Data).
    Não está vinculado a um ativo específico — é contexto de mercado geral.
    Relação: independente (1:N com ScoreAtivo via data_ref)
    """
    data_ref          = models.DateField(unique=True)
 
    # --- Inflação e Juros ---
    selic             = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    ipca_mensal       = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    igpm_mensal       = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
 
    # --- Atividade Econômica ---
    pib_trimestral    = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    desemprego        = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
 
    # --- Câmbio e Exterior ---
    dolar_brl         = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    risco_brasil      = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                            help_text="CDS 5 anos / EMBI+")
 
    # --- Mercado ---
    ibovespa_fechamento = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    volume_negociado_b3  = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
 
    class Meta:
        ordering = ['-data_ref']
        verbose_name = "KPI Macro"
 
    def __str__(self):
        return f"Macro | {self.data_ref}"
 
 
class KpiTime(models.Model):
    """
    Análise de timing/técnica por ativo (volume, volatilidade, momentum).
    Histórico diário — tamanho da posição e força do movimento no mercado.
    Relação: Ativo 1:N KpiTime
    """
    ativo         = models.ForeignKey('portfolio.Ativo', on_delete=models.CASCADE, related_name='kpis_time')
    data_ref      = models.DateField()
 
    # --- Volume e Liquidez ---
    volume_medio_20d = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    volume_diario    = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
 
    # --- Volatilidade ---
    volatilidade_30d = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True,
                                           help_text="Desvio padrão anualizado 30d")
    beta             = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
 
    # --- Momentum / Tendência ---
    retorno_1m       = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    retorno_3m       = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    retorno_12m      = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    rsi_14           = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                           help_text="RSI 14 períodos (0-100)")
    media_movel_50   = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    media_movel_200  = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
 
    class Meta:
        unique_together = ('ativo', 'data_ref')
        ordering = ['-data_ref']
        verbose_name = "KPI Time"
 
    def __str__(self):
        return f"{self.ativo.ticker} | Time | {self.data_ref}"
 
 
class SentimentoMercado(models.Model):
    """
    Score de sentimento extraído de redes sociais e notícias (NLP).
    Pode ser geral (sem ativo) ou específico de um ativo.
    Relação: Ativo 0/1:N SentimentoMercado (null = sentimento geral)
    """
    FONTE_CHOICES = [
        ('twitter',    'X / Twitter'),
        ('reddit',     'Reddit'),
        ('infomoney',  'InfoMoney'),
        ('valor',      'Valor Econômico'),
        ('bloomberg',  'Bloomberg Brasil'),
        ('outros',     'Outros'),
    ]
 
    ativo           = models.ForeignKey(
        'portfolio.Ativo', on_delete=models.CASCADE,
        related_name='sentimentos', null=True, blank=True,
        help_text="Nulo = sentimento macroeconômico geral"
    )
    data_ref        = models.DateField()
    fonte           = models.CharField(max_length=20, choices=FONTE_CHOICES)
 
    # Score entre -1 (muito negativo) e +1 (muito positivo)
    score_sentimento = models.DecimalField(
        max_digits=4, decimal_places=3,
        validators=[MinValueValidator(-1), MaxValueValidator(1)]
    )
    volume_mencoes  = models.IntegerField(default=0)
    resumo_nlp      = models.TextField(blank=True)
 
    class Meta:
        ordering = ['-data_ref']
        verbose_name = "Sentimento de Mercado"
 
    def __str__(self):
        ticker = self.ativo.ticker if self.ativo else "GERAL"
        return f"{ticker} | {self.fonte} | {self.data_ref} | {self.score_sentimento}"