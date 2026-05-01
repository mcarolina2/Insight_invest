from django.db import models
from django.contrib.auth.models import AbstractUser
# Create your models here.

 
class PerfilRisco(models.Model):
    """
    Precisa ser criado antes de User (FK em User aponta para cá).
    Populado via fixtures ou painel admin com os 3 perfis fixos.
    """
    TIPO_CHOICES = [
        ('conservador',   'Conservador'),
        ('intermediario', 'Intermediário'),
        ('arrojado',      'Arrojado'),
    ]
 
    tipo      = models.CharField(max_length=20, choices=TIPO_CHOICES, unique=True)
    score_min = models.IntegerField()
    score_max = models.IntegerField()
    descricao = models.TextField(blank=True)
 
    # Parâmetros de alocação máxima por classe de ativo para esse perfil
    # Usados pelo motor de recomendação ao montar a carteira ideal
    max_renda_variavel_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=100,
        help_text="% máximo permitido em renda variável"
    )
 
    class Meta:
        verbose_name = "Perfil de Risco"
        verbose_name_plural = "Perfis de Risco"
 
    def __str__(self):
        return self.tipo
 
 
class User(AbstractUser):
    """
    Extensão do AbstractUser.
    ATENÇÃO: 'nome_completo' NÃO pode ser CharField e @property ao mesmo tempo.
    Usando first_name/last_name do AbstractUser e expondo via @property.
    """
    renda_mensal = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True
    )
    perfil_risco = models.ForeignKey(
        PerfilRisco,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='usuarios'
    )
    criado_em = models.DateTimeField(auto_now_add=True)
 
    # Nível de educação financeira (atualizado pelo consumo de conteúdo)
    pontos_educacao = models.IntegerField(default=0)
 
    @property
    def nome_completo(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username
 
    def __str__(self):
        return self.username
 
    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
 
 
class Pergunta(models.Model):
    """
    Perguntas do questionário de suitability (perfil de risco).
    Peso determina quanto a resposta contribui para o score total.
    """
    CATEGORIA_CHOICES = [
        ('risco',   'Tolerância a Risco'),
        ('renda',   'Situação de Renda'),
        ('prazo',   'Horizonte de Investimento'),
        ('objetivo','Objetivo Financeiro'),
    ]
 
    texto     = models.CharField(max_length=500)
    peso      = models.IntegerField(default=1)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES)
    ativa     = models.BooleanField(default=True)
    ordem     = models.IntegerField(default=0)
 
    class Meta:
        ordering = ['ordem']
 
    def __str__(self):
        return f"[{self.categoria}] {self.texto[:60]}"
 
 
class OpcaoResposta(models.Model):
    """
    Opções de cada pergunta (múltipla escolha).
    Cada opção carrega um valor de score que será somado no final.
    Relação: Pergunta 1:N OpcaoResposta
    """
    pergunta   = models.ForeignKey(Pergunta, on_delete=models.CASCADE, related_name='opcoes')
    texto      = models.CharField(max_length=255)
    valor_score = models.IntegerField(help_text="Contribuição desta opção para o score total")
 
    def __str__(self):
        return f"{self.pergunta.texto[:30]} → {self.texto}"
 
 
class RespostaUsuario(models.Model):
    """
    Salva qual opção cada usuário escolheu por pergunta.
    N:M entre User e Pergunta, resolvido aqui com campos extras (opcao, score).
    unique_together garante 1 resposta por pergunta por usuário.
    """
    user         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='respostas')
    pergunta     = models.ForeignKey(Pergunta, on_delete=models.CASCADE)
    opcao        = models.ForeignKey(OpcaoResposta, on_delete=models.SET_NULL, null=True)
    score        = models.IntegerField()
    respondida_em = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        unique_together = ('user', 'pergunta')
        verbose_name = "Resposta do Usuário"
 
    def __str__(self):
        return f"{self.user.username} — {self.pergunta.texto[:40]}"
 