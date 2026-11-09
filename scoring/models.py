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
    score_estat       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
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

class CarteiraRecomendada(models.Model):
    perfil = models.CharField(max_length=20)  # 'conservador', 'intermediario', 'arrojado'
    data_calculo = models.DateField(auto_now_add=True)
    composicao = models.JSONField()  # lista de {ticker, setor, score_final, pct, ...}
    metricas = models.JSONField(default=dict, blank=True)   # retorno, volatilidade, sharpe, var95
    modelo_usado = models.CharField(max_length=20, blank=True, default='')
    ativo = models.BooleanField(default=True)  # só a mais recente de cada perfil fica ativa

    class Meta:
        ordering = ['-data_calculo']

    def __str__(self):
        return f"{self.perfil} — {self.data_calculo}"

# =============================================================================
# Configuração dos perfis de risco (editável pelo administrador/analista)
# =============================================================================
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

PERFIS_CHOICES = [
    ("conservador", "Conservador"),
    ("intermediario", "Intermediário"),
    ("arrojado", "Arrojado"),
]


def _dec(max_digits=4, casas=3, **kw):
    return models.DecimalField(max_digits=max_digits, decimal_places=casas, **kw)


class ConfiguracaoPerfil(models.Model):
    """
    Parâmetros do motor de scoring e da otimização para cada perfil de risco.
    Substitui o dicionário PERFIS do código: o administrador ou o economista
    ajusta pelo painel, e cada alteração é registrada em HistoricoConfiguracaoPerfil.
    """
    perfil = models.CharField(max_length=20, choices=PERFIS_CHOICES, unique=True)
    descricao = models.TextField("Para quem é este perfil", blank=True)

    peso_fundamentalista = _dec(verbose_name="Peso da camada fundamentalista")
    peso_macro = _dec(verbose_name="Peso da camada macroeconômica")
    peso_estatistico = _dec(verbose_name="Peso da camada estatística")

    peso_liquidez = _dec(verbose_name="Peso da liquidez (dentro da fundamentalista)")
    peso_rentabilidade = _dec(verbose_name="Peso da rentabilidade")
    peso_endividamento = _dec(verbose_name="Peso do endividamento")
    peso_crescimento = _dec(verbose_name="Peso do crescimento")

    score_min = _dec(5, 2, verbose_name="Escore final mínimo (0–100)")
    qtd_ativos = models.PositiveSmallIntegerField("Ativos levados à otimização")
    max_pct_ativo = _dec(verbose_name="Peso máximo por ativo (fração, ex.: 0,20 = 20%)")

    liquidez_corrente_min = _dec(6, 2, null=True, blank=True,
                                 verbose_name="Filtro: liquidez corrente mínima")
    divida_ebitda_max = _dec(6, 2, null=True, blank=True,
                             verbose_name="Filtro: dívida líquida/EBITDA máxima")
    roe_min = _dec(6, 4, null=True, blank=True, verbose_name="Filtro: ROE mínimo (fração)")

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "Configuração de perfil"
        verbose_name_plural = "Configurações de perfil"
        ordering = ["id"]
        permissions = [("recalcular_carteiras", "Pode recalcular as carteiras recomendadas")]

    def __str__(self):
        return self.get_perfil_display()

    def clean(self):
        for campo in ("peso_fundamentalista", "peso_macro", "peso_estatistico", "peso_liquidez",
                      "peso_rentabilidade", "peso_endividamento", "peso_crescimento",
                      "max_pct_ativo", "score_min"):
            v = getattr(self, campo)
            if v is not None and not isinstance(v, Decimal):
                setattr(self, campo, Decimal(str(v)))
        erros = {}
        dims = [self.peso_liquidez, self.peso_rentabilidade, self.peso_endividamento, self.peso_crescimento]
        if None not in dims and abs(sum(dims) - Decimal("1")) > Decimal("0.001"):
            erros["peso_crescimento"] = f"Os pesos das 4 dimensões devem somar 1 (somam {sum(dims)})."
        camadas = [self.peso_fundamentalista, self.peso_macro, self.peso_estatistico]
        if None not in camadas and sum(camadas) <= 0:
            erros["peso_estatistico"] = "Ao menos uma camada precisa ter peso positivo."
        if self.max_pct_ativo is not None and not (Decimal("0") < self.max_pct_ativo <= Decimal("1")):
            erros["max_pct_ativo"] = "Use uma fração entre 0 e 1 (ex.: 0,20 para 20%)."
        if self.score_min is not None and not (0 <= self.score_min <= 100):
            erros["score_min"] = "O escore mínimo deve estar entre 0 e 100."
        if erros:
            raise ValidationError(erros)

    def como_dict(self) -> dict:
        """Mesmo formato do antigo dicionário PERFIS, usado pelo motor."""
        filtros = {}
        if self.liquidez_corrente_min is not None:
            filtros["liquidez_corrente__gte"] = float(self.liquidez_corrente_min)
        if self.divida_ebitda_max is not None:
            filtros["divida_ebitda__lte"] = float(self.divida_ebitda_max)
        if self.roe_min is not None:
            filtros["roe__gte"] = float(self.roe_min)
        return {
            "score_min": float(self.score_min),
            "qtd_ativos": int(self.qtd_ativos),
            "max_pct_ativo": float(self.max_pct_ativo),
            "pesos": {"micro": float(self.peso_fundamentalista),
                      "macro": float(self.peso_macro),
                      "estat": float(self.peso_estatistico)},
            "micro_pesos": {"liquidez": float(self.peso_liquidez),
                            "rentabilidade": float(self.peso_rentabilidade),
                            "divida": float(self.peso_endividamento),
                            "crescimento": float(self.peso_crescimento)},
            "filtros_kpi": filtros,
            "descricao": self.descricao,
        }


class HistoricoConfiguracaoPerfil(models.Model):
    """Registro de cada alteração de configuração (reprodutibilidade do TCC)."""
    perfil = models.CharField(max_length=20, choices=PERFIS_CHOICES)
    configuracao = models.JSONField()
    motivo = models.CharField(max_length=255, blank=True)
    alterado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    alterado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Histórico de configuração"
        verbose_name_plural = "Histórico de configurações"
        ordering = ["-alterado_em"]

    def __str__(self):
        return f"{self.get_perfil_display()} — {self.alterado_em:%d/%m/%Y %H:%M}"
