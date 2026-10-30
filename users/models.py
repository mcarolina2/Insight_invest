from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

# Create your models here.

 
class PerfilRisco(models.Model):
    TIPOS = [("conservador", "Conservador"),
             ("intermediario", "Intermediário"),
             ("arrojado", "Arrojado")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE,
                                related_name="perfil_risco_obj")
    tipo = models.CharField(max_length=20, choices=TIPOS)
    pontuacao = models.PositiveSmallIntegerField()
    respostas = models.JSONField(default=dict)
    atualizado_em = models.DateTimeField(auto_now=True)


def perfil_do_usuario(user):
    """Retorna 'conservador' | 'intermediario' | 'arrojado' ou None se ainda não fez o quiz."""
    obj = PerfilRisco.objects.filter(user=user).first()
    return obj.tipo if obj else None
 
 
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
 