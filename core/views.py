from django.shortcuts import render
from django.shortcuts import render
from django.db.models import F


def home_view(request):
    from market_data.models import KpiMicro, KpiMacro, SentimentoMercado
    from scoring.models import ScoreAtivo

    # ──────────────────────────────────────────────────────────
    # 1. RANKING: maior score_final (mais recente por ativo)
    #
    # IMPORTANTE: no Postgres, .distinct('campo') exige que o
    # ORDER BY comece pelo MESMO campo do distinct. Por isso não
    # podemos encadear .order_by('-score_final') depois do distinct.
    # Solução: busca o registro mais recente por ativo e ordena
    # por score em Python.
    # ──────────────────────────────────────────────────────────
    ultimos_scores = list(
        ScoreAtivo.objects
        .select_related('ativo')
        .order_by('ativo_id', '-data_calculo')
        .distinct('ativo_id')
    )
    top_scores = sorted(ultimos_scores, key=lambda s: s.score_final, reverse=True)[:5]

    ranking_score_lista = [
        {
            'posicao':     i + 1,
            'ticker':      s.ativo.ticker,
            'setor':       s.ativo.setor or '—',
            'valor':       float(s.score_final),
            'sufixo':      '',
        }
        for i, s in enumerate(top_scores)
    ]

    # ──────────────────────────────────────────────────────────
    # 2. RANKING: maior dividend yield (último KpiMicro com dy preenchido)
    # Mesma regra do Postgres: distinct + sort em Python.
    # ──────────────────────────────────────────────────────────
    ultimos_kpi_dy = list(
        KpiMicro.objects
        .select_related('ativo')
        .filter(dy__isnull=False, dy__gt=0)
        .order_by('ativo_id', '-data_ref')
        .distinct('ativo_id')
    )
    top_dy = sorted(ultimos_kpi_dy, key=lambda k: k.dy, reverse=True)[:5]

    ranking_dy_lista = [
        {
            'posicao': i + 1,
            'ticker':  k.ativo.ticker,
            'setor':   k.ativo.setor or '—',
            'valor':   round(float(k.dy) * 100, 1),  # dy vem em decimal (0.098 = 9.8%)
            'sufixo':  '%',
        }
        for i, k in enumerate(top_dy)
    ]

    # ──────────────────────────────────────────────────────────
    # 3. RANKING: maior score de sentimento (média últimos 30 dias)
    # ──────────────────────────────────────────────────────────
    from django.db.models import Avg
    from datetime import date, timedelta

    ranking_sentimento = (
        SentimentoMercado.objects
        .filter(ativo__isnull=False, data_ref__gte=date.today() - timedelta(days=30))
        .values('ativo__ticker', 'ativo__setor')
        .annotate(score_medio=Avg('score_sentimento'))
        .order_by('-score_medio')[:5]
    )
    ranking_sentimento_lista = [
        {
            'posicao': i + 1,
            'ticker':  r['ativo__ticker'],
            'setor':   r['ativo__setor'] or '—',
            # converte -1..+1 para 0..100 para ficar no mesmo padrão visual
            'valor':   round((float(r['score_medio']) + 1) / 2 * 100),
            'sufixo':  '',
        }
        for i, r in enumerate(ranking_sentimento)
    ]

    # ──────────────────────────────────────────────────────────
    # 4. PAINEL MACRO (último registro)
    # ──────────────────────────────────────────────────────────
    macro = KpiMacro.objects.order_by('-data_ref').first()

    from scoring.services.motor_scoring import calcular_score_macro
    score_macro = calcular_score_macro(macro) if macro else None

    # ──────────────────────────────────────────────────────────
    # 5. NOTÍCIAS recentes com sentimento
    # ──────────────────────────────────────────────────────────
    noticias = (
        SentimentoMercado.objects
        .exclude(resumo_nlp='')
        .order_by('-data_ref')[:8]
    )
    noticias_lista = [
        {
            'texto':   n.resumo_nlp,
            'fonte':   n.get_fonte_display(),
            'ticker':  n.ativo.ticker if n.ativo else None,
            'sentimento_label': (
                'positivo' if float(n.score_sentimento) > 0.15 else
                'negativo' if float(n.score_sentimento) < -0.15 else
                'neutro'
            ),
        }
        for n in noticias
    ]

    return render(request, 'scoring/home.html', {
        'ranking_score':      ranking_score_lista,
        'ranking_dy':         ranking_dy_lista,
        'ranking_sentimento': ranking_sentimento_lista,
        'macro':              macro,
        'score_macro':        score_macro,
        'noticias':           noticias_lista,
    })



# Create your views here.
"""
scoring/views.py — adicione esta view (home_view) ao arquivo existente.

Monta a tela inicial com:
  1. Rankings por indicador (score final, dividend yield, sentimento)
  2. Painel macroeconômico (último KpiMacro)
  3. Notícias recentes com sentimento (SentimentoMercado)

Esta view NÃO exige login — é a porta de entrada pública,(você só loga pra ver a carteira personalizada).
"""