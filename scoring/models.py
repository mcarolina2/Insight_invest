from django.db import models

# Create your models here.
class ScoreAtivo(models.Model):
    """
    Score composto calculado para cada ativo em cada data.
    Agrega micro + macro + time + sentimento em um score_final ponderado.
    Relação: Ativo 1:N ScoreAtivo
    """
    ativo             = models.ForeignKey('portfolio.Ativo', on_delete=models.CASCADE, related_name='scores')
    data_calculo      = models.DateField()
 
    # Scores por camada (0 a 100)
    score_micro       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    score_macro       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    score_time        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    score_sentimento  = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
 
    # Score final ponderado
    score_final       = models.DecimalField(max_digits=5, decimal_places=2)
 
    # Snapshot dos KPIs usados no cálculo (rastreabilidade)
    kpi_macro_ref     = models.ForeignKey(
        'market_data.KpiMacro', on_delete=models.SET_NULL, null=True, blank=True
    )
 
    class Meta:
        unique_together = ('ativo', 'data_calculo')
        ordering = ['-data_calculo', '-score_final']
        verbose_name = "Score do Ativo"
 
    def __str__(self):
        return f"{self.ativo.ticker} | Score {self.score_final} | {self.data_calculo}"
 
 
class Recomendacao(models.Model):
    """
    Recomendação de carteira gerada para um usuário em uma data.
    Um usuário pode ter várias recomendações (histórico).
    Relação: User 1:N Recomendacao
    """
    STATUS_CHOICES = [
        ('ativa',    'Ativa'),
        ('aceita',   'Aceita pelo usuário'),
        ('ignorada', 'Ignorada'),
    ]
 
    user           = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='recomendacoes')
    criado_em      = models.DateTimeField(auto_now_add=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ativa')
    justificativa  = models.TextField(blank=True, help_text="Explicação gerada pela IA")
 
    # Métricas da carteira recomendada
    retorno_esperado = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    volatilidade_esperada = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)
    sharpe_esperado  = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
 
    class Meta:
        ordering = ['-criado_em']
        verbose_name = "Recomendação"
 
    def __str__(self):
        return f"Rec #{self.pk} — {self.user.username} | {self.criado_em.date()}"
 
 
class ItemRecomendacao(models.Model):
    """
    Tabela intermediária N:M entre Recomendacao e Ativo.
    Necessária pois carrega dados extras: % de alocação e tipo de ação sugerida.
    Uma recomendação tem vários ativos; um ativo pode aparecer em várias recomendações.
    """
    TIPO_CHOICES = [
        ('comprar',  'Comprar'),
        ('manter',   'Manter'),
        ('vender',   'Vender'),
        ('observar', 'Observar'),
    ]
 
    recomendacao      = models.ForeignKey(Recomendacao, on_delete=models.CASCADE, related_name='itens')
    ativo             = models.ForeignKey('portfolio.Ativo', on_delete=models.CASCADE, related_name='recomendacoes')
    tipo              = models.CharField(max_length=10, choices=TIPO_CHOICES)
    percentual_ideal  = models.DecimalField(max_digits=5, decimal_places=2,
                                            help_text="% sugerido na carteira (soma = 100%)")
    score_ativo_ref   = models.ForeignKey(ScoreAtivo, on_delete=models.SET_NULL, null=True, blank=True,
                                          help_text="Score usado como base da recomendação")
    justificativa_item = models.TextField(blank=True)
 
    class Meta:
        unique_together = ('recomendacao', 'ativo')
        verbose_name = "Item da Recomendação"
 
    def __str__(self):
        return f"[{self.tipo.upper()}] {self.ativo.ticker} — {self.percentual_ideal}%"