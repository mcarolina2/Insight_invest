import json
from django.shortcuts import render , redirect
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from scoring.models import CarteiraRecomendada
from market_data.models import KpiMacro, KpiMicro
from scoring.services.motor_scoring_v2 import PERFIS, calcular_score_macro
from django.db.models import Q


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _top_recentes(model, campo_data, campo_valor,
                  filtro_extra=None, reverso=True, limite=5):
    """
    Busca o registro mais recente de cada ativo e ordena pelo campo_valor.
    Evita o erro do Postgres com distinct + order_by em campos diferentes.
    """
    qs = (model.objects
          .select_related('ativo')
          .order_by('ativo_id', f'-{campo_data}')
          .distinct('ativo_id'))
    if filtro_extra:
        qs = qs.filter(**filtro_extra)
    registros = [r for r in qs if getattr(r, campo_valor) is not None]
    registros.sort(key=lambda r: getattr(r, campo_valor), reverse=reverso)
    return registros[:limite]


def _normalizar_dy(v):
    """Converte DY para percentual, descarta outliers."""
    try:
        v = abs(float(v))
        if v > 100: return None
        return round(v * 100, 2) if v <= 1 else round(v, 2)
    except Exception:
        return None


def _enriquecer_ativo(ticker: str, perfil: str) -> dict:
    """
    Busca do banco os dados reais de um ativo para montar
    os painéis Fundamentalista, Estatístico, Mercado e Sentimento.
    Retorna dicts vazios (sem dados) se ainda não houver registros.
    """
    from portfolio.models import Ativo
    from market_data.models import KpiMicro, KpiTime, SentimentoMercado

    try:
        ativo = Ativo.objects.get(ticker=ticker.upper())
    except Ativo.DoesNotExist:
        return {}

    # ── Fundamentalista (KpiMicro mais recente) ──────────────────
    micro = (KpiMicro.objects
             .filter(ativo=ativo)
             .order_by('-data_ref')
             .first())

    fund = {}
    if micro:
        def fmt_pct(v):  return f"{float(v)*100:.1f}%" if v else "—"
        def fmt_dy(v):   return f"{float(v):.2f}%" if v is not None else "—"
        def fmt_x(v):    return f"{float(v):.2f}x"    if v else "—"
        def fmt_num(v):  return f"{float(v):.1f}"     if v else "—"
        fund = {
            'ROE':            fmt_pct(micro.roe),
            'ROA':            fmt_pct(micro.roa),
            'Marg. Liq.':     fmt_pct(micro.margem_liquida),
            'Marg. EBITDA':   fmt_pct(micro.margem_ebitda),
            'Liq. Corrente':  fmt_x(micro.liquidez_corrente),
            'Dív/EBITDA':     fmt_x(micro.divida_ebitda),
            'P/L':            fmt_num(micro.pl) + 'x' if micro.pl else '—',
            'DY':             fmt_dy(micro.dy) if micro.dy else '—',
        }

    # ── Estatístico (KpiEstatistico mais recente) ─────────────────
    estat = {}
    try:
        from market_data.models import KpiEstatistico
        ke = (KpiEstatistico.objects
              .filter(ativo=ativo, janela_dias=252)
              .order_by('-data_calculo')
              .first())
        if ke:
            def fp(v, d=4): return f"{float(v):.{d}f}" if v else '—'
            estat = {
                'Média retorno':  f"{float(ke.media_retorno)*100:.3f}%" if ke.media_retorno else '—',
                'Volatilidade':   f"{float(ke.volatilidade_anual)*100:.1f}%" if ke.volatilidade_anual else '—',
                'Beta':           fp(ke.beta, 2),
                'Skewness':       fp(ke.skewness, 3),
                'Curtose':        fp(ke.curtose, 2),
                'Jarque-Bera':    'Normal' if ke.retorno_normal else 'Não-normal',
                'CV':             fp(ke.cv, 2),
                'R²':             fp(ke.r_quadrado, 3),
            }
    except Exception:
        pass

    # ── Mercado (KpiTime mais recente) ────────────────────────────
    merc = {}
    time_kpi = (KpiTime.objects
                .filter(ativo=ativo)
                .order_by('-data_ref')
                .first())
    if time_kpi:
        def fmt_vol(v): return f"R$ {float(v)/1e6:.0f}M/dia" if v else '—'
        ret12 = float(time_kpi.retorno_12m or 0)
        merc = {
            'Volume 20d':  fmt_vol(time_kpi.volume_medio_20d),
            'Beta':        f"{float(time_kpi.beta or 0):.2f}",
            'Ret. 12m':    f"{'+' if ret12>=0 else ''}{ret12*100:.1f}%",
            'RSI 14':      f"{float(time_kpi.rsi_14 or 50):.0f}",
            'MM 50':       f"R$ {float(time_kpi.media_movel_50 or 0):.2f}",
            'MM 200':      f"R$ {float(time_kpi.media_movel_200 or 0):.2f}",
        }

    # ── Sentimento (últimas 3 notícias) ──────────────────────────
    from datetime import date, timedelta
    from django.db.models import Avg
    sent_qs = (SentimentoMercado.objects
               .filter(ativo=ativo,
                       data_ref__gte=date.today() - timedelta(days=30))
               .exclude(resumo_nlp='')
               .order_by('-data_ref')[:3])

    score_sent = (SentimentoMercado.objects
                  .filter(ativo=ativo,
                          data_ref__gte=date.today() - timedelta(days=30))
                  .aggregate(m=Avg('score_sentimento'))['m'] or 0)

    score_0_100 = round((float(score_sent) + 1) / 2 * 100)
    noticias = []
    for s in sent_qs:
        v = float(s.score_sentimento)
        noticias.append({
            'tipo': 'positivo' if v > 0.15 else ('negativo' if v < -0.15 else 'neutro'),
            'txt':  s.resumo_nlp[:120],
        })

    return {
        'fund':  fund,
        'estat': estat,
        'merc':  merc,
        'sent':  {'score': score_0_100, 'noticias': noticias},
    }


VISUAL_PERFIS = [
    ("conservador",   "Conservador",   "🛡️", "#22c55e"),
    ("intermediario", "Intermediário", "⚖️", "#f59e0b"),
    ("arrojado",      "Arrojado",      "🚀", "#ef4444"),
]
ATIVOS_VISIVEIS_HOME = 4


def _carteiras_pre_calculadas() -> list[dict]:
    """Lê a carteira ativa de cada perfil salva pelo comando atualizar_carteiras.
    A home não recalcula nada: só consulta o banco, por isso abre rápido."""
    saida = []
    for perfil, titulo, icone, cor in VISUAL_PERFIS:
        cart = (CarteiraRecomendada.objects.filter(perfil=perfil, ativo=True)
                .order_by('-data_calculo', '-id').first())
        ativos = cart.composicao if cart else []
        saida.append({
            "perfil": perfil, "titulo": titulo, "icone": icone, "cor": cor,
            "ativos": ativos[:ATIVOS_VISIVEIS_HOME],
            "qtd_total": len(ativos),
            "sem_dados": not ativos,
            "metricas": cart.metricas if cart else {},
            "data_calculo": cart.data_calculo.isoformat() if cart else None,
        })
    return saida


# ─────────────────────────────────────────────────────────────
# VIEW PRINCIPAL
# ─────────────────────────────────────────────────────────────

def home_view(request):
    from market_data.models import KpiMicro, KpiMacro, SentimentoMercado
    from scoring.models import ScoreAtivo
    from django.db.models import Avg
    from datetime import date, timedelta

    # ── Rankings ────────────────────────────────────────────────
    top_scores = _top_recentes(ScoreAtivo, 'data_calculo', 'score_final', limite=5)
    ranking_score = [
        {'posicao': i+1, 'ticker': s.ativo.ticker,
         'setor': s.ativo.setor or '—', 'valor': float(s.score_final)}
        for i, s in enumerate(top_scores)
    ]

    candidatos_dy = _top_recentes(
        KpiMicro, 'data_ref', 'dy',
        filtro_extra={'dy__isnull': False, 'dy__gt': 0}, limite=30)
    ranking_dy = []
    for k in candidatos_dy:
        v = _normalizar_dy(k.dy)
        if v and v <= 30:
            ranking_dy.append({
                'posicao': len(ranking_dy)+1,
                'ticker': k.ativo.ticker,
                'setor': k.ativo.setor or '—',
                'valor': v
            })
        if len(ranking_dy) == 5:
            break

    top_liq = _top_recentes(
        KpiMicro, 'data_ref', 'liquidez_corrente',
        filtro_extra={ "liquidez_corrente__gte": 1,
                      "liquidez_corrente__lte": 5}, limite=5)
    ranking_liq = []
    for k in top_liq:
        if float(k.liquidez_corrente) <= 20:
            ranking_liq.append({
                'posicao': len(ranking_liq)+1,
                'ticker': k.ativo.ticker,
                'setor': k.ativo.setor or '—',
                'valor': round(float(k.liquidez_corrente), 2)
            })
        if len(ranking_liq) == 5:
            break

    top_div = _top_recentes(
        KpiMicro, 'data_ref', 'divida_ebitda',
        filtro_extra={"divida_ebitda__gt": 0,
                      "divida_ebitda__lte": 3,
                      "roe__gt":0, 
                      "roa__gt":0,},
        reverso=False, limite=5)
    ranking_div = [
        {'posicao': i+1, 'ticker': k.ativo.ticker,
         'setor': k.ativo.setor or '—',
         'valor': round(float(k.divida_ebitda), 2)}
        for i, k in enumerate(top_div)
    ]

    ranking_sent = list(
        SentimentoMercado.objects
        .filter(ativo__isnull=False,
                data_ref__gte=date.today() - timedelta(days=30))
        .values('ativo__ticker', 'ativo__setor')
        .annotate(s=Avg('score_sentimento'))
        .order_by('-s')[:5]
    )
    ranking_sent = [
        {'posicao': i+1, 'ticker': r['ativo__ticker'],
         'setor': r['ativo__setor'] or '—',
         'valor': round((float(r['s']) + 1) / 2 * 100)}
        for i, r in enumerate(ranking_sent)
    ]

    # ── Macro ────────────────────────────────────────────────────
    macro = KpiMacro.objects.order_by('-data_ref').first()
    score_macro = calcular_score_macro(macro) if macro else None

    # ── Notícias ─────────────────────────────────────────────────
    noticias_qs = (SentimentoMercado.objects
                   .exclude(resumo_nlp='')
                   .order_by('-data_ref')[:8])
    noticias = [
        {
            'texto': n.resumo_nlp[:120],
            'ticker': n.ativo.ticker if n.ativo else None,
            'sentimento_label': ('positivo' if float(n.score_sentimento) > 0.15
                      else 'negativo' if float(n.score_sentimento) < -0.15
                      else 'neutro'),
        }
        for n in noticias_qs
    ]

    # ── Carteiras pré-calculadas (comando atualizar_carteiras) ───
    carteiras_coringa = _carteiras_pre_calculadas()
    carteiras_json = json.dumps(carteiras_coringa)

    return render(request, 'core/home.html', {
      'ranking_score':     ranking_score,
       'ranking_dy':        ranking_dy,
       'ranking_liquidez':  ranking_liq,     # renomeado
       'ranking_divida':    ranking_div,     # renomeado
        'ranking_sentimento': ranking_sent,   # renomeado
        'macro':             macro,
        'score_macro':       score_macro,
        'noticias':          noticias,
         'carteiras_coringa': carteiras_coringa,
        'carteiras_json':    carteiras_json,
    })


# ─────────────────────────────────────────────────────────────
# ENDPOINT AJAX: ativos similares para o botão "trocar"
# ─────────────────────────────────────────────────────────────

@require_GET
def similares_ajax(request, ticker):
    """
    GET /similares/<ticker>/
    Retorna ativos do mesmo setor ordenados por score_final.
    Usado pelo botão "trocar" da carteira interativa.
    """
    from scoring.models import ScoreAtivo
    from portfolio.models import Ativo

    try:
        ativo = Ativo.objects.get(ticker=ticker.upper())
    except Ativo.DoesNotExist:
        return JsonResponse({'similares': []})

    # Busca ativos do mesmo setor com score mais recente
    scores_setor = (
        ScoreAtivo.objects
        .filter(ativo__setor=ativo.setor)
        .exclude(ativo__ticker=ticker.upper())
        .order_by('ativo_id', '-data_calculo')
        .distinct('ativo_id')
    )

    similares = sorted(scores_setor, key=lambda s: s.score_final, reverse=True)[:5]

    resultado = [
        {
            'ticker':      s.ativo.ticker,
            'nome':        s.ativo.nome[:25],
            'score_final': float(s.score_final),
            'setor':       s.ativo.setor or '',
        }
        for s in similares
    ]

    return JsonResponse({'similares': resultado, 'setor': ativo.setor or ''})

@require_GET
def buscar_ativo(request):
    """Busca por ticker ou nome. Um único resultado abre a página do ativo."""
    from portfolio.models import Ativo

    q = request.GET.get('q', '').strip()
    if not q:
        return redirect('core:home')

    exato = Ativo.objects.filter(ticker__iexact=q).first()
    if exato:
        return redirect('core:ativo', ticker=exato.ticker)

    resultados = list(Ativo.objects.filter(Q(ticker__icontains=q) | Q(nome__icontains=q))
                      .order_by('ticker')[:30])
    if len(resultados) == 1:
        return redirect('core:ativo', ticker=resultados[0].ticker)
    return render(request, 'core/busca.html', {'q': q, 'resultados': resultados})


def ativo_view(request, ticker):
    """Página com tudo o que o Insight Invest sabe sobre um ativo."""
    from django.shortcuts import get_object_or_404
    from portfolio.models import Ativo
    from users.models import perfil_do_usuario
    from core.ativo_info import montar_contexto_ativo

    ativo = get_object_or_404(Ativo, ticker__iexact=ticker)
    if ativo.ticker != ticker:
        return redirect('core:ativo', ticker=ativo.ticker)
    perfil = perfil_do_usuario(request.user)
    return render(request, 'core/ativo.html', montar_contexto_ativo(ativo, perfil))
