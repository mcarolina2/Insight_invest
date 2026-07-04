from django.db import models
from django.utils import timezone
from datetime import timedelta
 
 
class Plano(models.Model):
    """
    Define os limites e permissões de cada plano.
    Populate via fixture/admin com 2 linhas: free e pro.
    """
    TIPO_CHOICES = [
        ('free', 'Gratuito'),
        ('pro',  'Pro'),
    ]
 
    tipo            = models.CharField(max_length=10, choices=TIPO_CHOICES, unique=True)
    nome_exibicao   = models.CharField(max_length=50)
    preco_mensal    = models.DecimalField(max_digits=8, decimal_places=2, default=0)
 
    # --- O que cada plano libera ---
    requer_quiz                    = models.BooleanField(
        default=False,
        help_text="Se True, obriga o usuário a responder o questionário de risco"
    )
    acesso_personalizacao          = models.BooleanField(
        default=False,
        help_text="Se True, gera carteira ideal personalizada por perfil de risco"
    )
    acesso_otimizacao_markowitz    = models.BooleanField(
        default=False,
        help_text="Se True, libera os filtros de risco/retorno e os 4 modelos de otimização"
    )
    acesso_simulacao_substituicao  = models.BooleanField(
        default=False,
        help_text="Se True, libera o 'trocar ativo' com cálculo de impacto no Sharpe"
    )
    limite_ativos_visiveis         = models.IntegerField(
        default=5,null=True, blank=True,
        help_text="Quantos ativos no ranking/carteira o usuário pode ver. NULL = ilimitado"
    )
    limite_recalculos_mes          = models.IntegerField(
        null=True, blank=True,
        help_text="Quantas vezes por mês pode gerar nova recomendação. NULL = ilimitado"
    )
    acesso_historico_completo      = models.BooleanField(
        default=False,
        help_text="Free só vê o último ano de KPIs; Pro vê os 8 anos completos"
    )
    acesso_sentimento_mercado      = models.BooleanField(
        default=False,
        help_text="Análise de sentimento de notícias — só Pro"
    )
 
    class Meta:
        verbose_name = "Plano"
        verbose_name_plural = "Planos"
 
    def __str__(self):
        return self.nome_exibicao
 
 
class Assinatura(models.Model):
    """
    Vincula um usuário a um plano, com controle de vigência e pagamento.
    """
    STATUS_CHOICES = [
        ('ativa',     'Ativa'),
        ('expirada',  'Expirada'),
        ('cancelada', 'Cancelada'),
        ('pendente',  'Pagamento pendente'),
    ]
 
    user            = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='assinatura'
    )
    plano           = models.ForeignKey(Plano, on_delete=models.PROTECT)
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='ativa')
 
    data_inicio     = models.DateTimeField(auto_now_add=True)
    data_fim        = models.DateTimeField(
        null=True, blank=True,
        help_text="NULL para o plano free (sem vencimento). Pro vence em 30 dias."
    )
 
    # Controle de uso (resetado mensalmente)
    recalculos_usados_mes = models.IntegerField(default=0)
    mes_referencia         = models.DateField(default=timezone.now)
 
    # Rastreabilidade do pagamento (preenchido pelo webhook do gateway)
    pagamento_id     = models.CharField(max_length=100, blank=True, null=True)
    pagamento_metodo = models.CharField(max_length=30,  blank=True, null=True)  # pix, cartao
    valor_pago        = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
 
    class Meta:
        verbose_name = "Assinatura"
 
    def __str__(self):
        return f"{self.user.username} — {self.plano.nome_exibicao} ({self.status})"
 
    @property
    def is_pro(self) -> bool:
        if self.plano.tipo != 'pro':
            return False
        if self.status != 'ativa':
            return False
        if self.data_fim and self.data_fim < timezone.now():
            return False
        return True
 
    @property
    def dias_restantes(self) -> int | None:
        if not self.data_fim:
            return None
        delta = self.data_fim - timezone.now()
        return max(0, delta.days)
 
    def ativar_pro(self, dias: int = 30, pagamento_id: str = None, valor: float = None):
        """Chamado pelo webhook de pagamento confirmado."""
        plano_pro = Plano.objects.get(tipo='pro')
        self.plano        = plano_pro
        self.status       = 'ativa'
        self.data_fim     = timezone.now() + timedelta(days=dias)
        self.pagamento_id = pagamento_id
        self.valor_pago   = valor
        self.save()
 
    def pode_recalcular(self) -> bool:
        """Verifica se ainda tem recálculos disponíveis este mês."""
        if self.plano.limite_recalculos_mes is None:
            return True  # ilimitado
 
        # Reseta contador se mudou o mês
        hoje = timezone.now().date()
        if self.mes_referencia.month != hoje.month or self.mes_referencia.year != hoje.year:
            self.recalculos_usados_mes = 0
            self.mes_referencia = hoje
            self.save(update_fields=['recalculos_usados_mes', 'mes_referencia'])
 
        return self.recalculos_usados_mes < self.plano.limite_recalculos_mes
 
    def registrar_recalculo(self):
        self.recalculos_usados_mes += 1
        self.save(update_fields=['recalculos_usados_mes'])
 
 
# ---------------------------------------------------------------------------
# Signal: cria Assinatura free automaticamente no cadastro
# ---------------------------------------------------------------------------
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
 
 
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def criar_assinatura_free(sender, instance, created, **kwargs):
    """
    Todo usuário novo recebe automaticamente o plano free,
    sem precisar passar pelo quiz ou por qualquer pagamento.
    """
    if created and not hasattr(instance, 'assinatura'):
        plano_free, _ = Plano.objects.get_or_create(
            tipo='free',
            defaults={
                'nome_exibicao': 'Gratuito',
                'preco_mensal': 0,
                'requer_quiz': False,
                'acesso_personalizacao': False,
                'acesso_otimizacao_markowitz': False,
                'acesso_simulacao_substituicao': False,
                'limite_ativos_visiveis': 5,
                'limite_recalculos_mes': 1,
                'acesso_historico_completo': False,
                'acesso_sentimento_mercado': False,
            }
        )
        Assinatura.objects.create(user=instance, plano=plano_free)