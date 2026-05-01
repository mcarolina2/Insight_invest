from django.db import models
from django.contrib.auth.models import AbstractUser



# Create your models here.
class Ativo(models.Model):
    """
    Cadastro de ativos negociáveis (ações, FIIs, ETFs, renda fixa).
    Tabela central: referenciada por KPIs, Scores e Posições.
    """
    TIPO_CHOICES = [
        ('acao',        'Ação'),
        ('fii',         'Fundo Imobiliário'),
        ('etf',         'ETF'),
        ('bdr',         'BDR'),
        ('renda_fixa',  'Renda Fixa'),
        ('crypto',      'Criptomoeda'),
    ]
 
    ticker       = models.CharField(max_length=20, unique=True)
    nome         = models.CharField(max_length=255)
    setor        = models.CharField(max_length=100, blank=True)
    subsetor     = models.CharField(max_length=100, blank=True)
    tipo         = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descricao    = models.TextField(blank=True)
    ativo        = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)
 
    def __str__(self):
        return f"{self.ticker} — {self.nome}"
 
 
class Carteira(models.Model):
    """
    Carteira de um usuário. Um usuário pode ter várias carteiras
    (ex: carteira pessoal, carteira de simulação).
    Relação: User 1:N Carteira
    """
    TIPO_CHOICES = [
        ('real',     'Carteira Real'),
        ('simulada', 'Simulação'),
    ]
 
    user              = models.ForeignKey('users.User', on_delete=models.CASCADE, related_name='carteiras')
    nome              = models.CharField(max_length=100, default='Minha Carteira')
    tipo              = models.CharField(max_length=10, choices=TIPO_CHOICES, default='real')
    patrimonio_total  = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    criada_em         = models.DateTimeField(auto_now_add=True)
    data_atualizacao  = models.DateTimeField(auto_now=True)
 
    # Origem dos dados (open finance)
    open_finance_id   = models.CharField(max_length=100, blank=True, null=True)
 
    class Meta:
        verbose_name = "Carteira"
 
    def __str__(self):
        return f"{self.user.username} — {self.nome}"
 
 
class Posicao(models.Model):
    """
    Tabela intermediária N:M entre Carteira e Ativo.
    Necessária pois a relação carrega dados extras: quantidade e preço médio.
    Uma carteira tem muitos ativos; um ativo pode estar em muitas carteiras.
    """
    carteira        = models.ForeignKey(Carteira, on_delete=models.CASCADE, related_name='posicoes')
    ativo           = models.ForeignKey(Ativo, on_delete=models.CASCADE, related_name='posicoes')
    quantidade      = models.DecimalField(max_digits=16, decimal_places=4)
    preco_medio     = models.DecimalField(max_digits=12, decimal_places=4)
    percentual_ideal = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="% ideal na carteira recomendada"
    )
    data_atualizacao = models.DateTimeField(auto_now=True)
 
    class Meta:
        unique_together = ('carteira', 'ativo')
        verbose_name = "Posição"
 
    @property
    def valor_total(self):
        return self.quantidade * self.preco_medio
 
    def __str__(self):
        return f"{self.carteira} — {self.ativo.ticker} x{self.quantidade}"