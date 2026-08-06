"""
Salve como: core/carteira_builder.py

Módulo isolado para construir as carteiras coringa da home.
Separado do views.py para facilitar debug e teste.

Teste rápido no shell:
  python manage.py shell
  from core.carteira_builder import montar_carteiras_home
  import json
  resultado = montar_carteiras_home()
  print(json.dumps(resultado))   # se funcionar, JSON está ok
  for perfil, ativos in resultado.items():
      print(f"{perfil}: {len(ativos)} ativos")
"""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# HELPER: converte qualquer tipo para float ou None
# ─────────────────────────────────────────────────────────────

def _f(valor, casas=2):
    """Converte Decimal/float/None para float arredondado. Seguro para JSON."""
    if valor is None:
        return None
    try:
        return round(float(valor), casas)
    except (TypeError, ValueError):
        return None


def _pct(valor):
    """Converte valor decimal (0.18) para percentual string '18.0%'."""
    v = _f(valor, 4)
    if v is None:
        return '—'
    return f"{v*100:.1f}%"


def _x(valor):
    """Formata como múltiplo '2.1x'."""
    v = _f(valor, 2)
    return f"{v:.2f}x" if v is not None else '—'


# ─────────────────────────────────────────────────────────────
# BUSCA OS KPIs DE UM ATIVO (4 camadas)
# ─────────────────────────────────────────────────────────────

def _buscar_fund(ativo_id: int) -> dict:
    """Busca o KpiMicro mais recente e formata para o painel Fundamentalista."""
    from market_data.models import KpiMicro
    try:
        micro = (KpiMicro.objects
                 .filter(ativo_id=ativo_id)
                 .order_by('-data_ref')
                 .values('roe', 'roa', 'margem_liquida', 'margem_ebitda',
                         'liquidez_corrente', 'divida_ebitda', 'pl', 'dy')
                 .first())
        if not micro:
            return {}
        return {
            'ROE':           _pct(micro['roe']),
            'ROA':           _pct(micro['roa']),
            'Marg. Liq.':    _pct(micro['margem_liquida']),
            'Marg. EBITDA':  _pct(micro['margem_ebitda']),
            'Liq. Corrente': _x(micro['liquidez_corrente']),
            'Dív/EBITDA':    _x(micro['divida_ebitda']),
            'P/L':           f"{_f(micro['pl'],1)}x" if micro['pl'] else '—',
            'DY':            _pct(micro['dy']) if micro['dy'] else '—',
        }
    except Exception as e:
        logger.warning(f"_buscar_fund ativo_id={ativo_id}: {e}")
        return {}


def _buscar_estat(ativo_id: int) -> dict:
    """Busca o KpiEstatistico mais recente."""
    try:
        from market_data.models import KpiEstatistico
        ke = (KpiEstatistico.objects
              .filter(ativo_id=ativo_id, janela_dias=252)
              .order_by('-data_calculo')
              .values('media_retorno', 'volatilidade_anual', 'beta',
                      'skewness', 'curtose', 'retorno_normal', 'cv', 'r_quadrado')
              .first())
        if not ke:
            return {}
        mr = _f(ke['media_retorno'], 6)
        return {
            'Média retorno':  f"{mr*100:.3f}%" if mr is not None else '—',
            'Volatilidade':   f"{_f(ke['volatilidade_anual'],4)*100:.1f}%" if ke['volatilidade_anual'] else '—',
            'Beta':           f"{_f(ke['beta'],2)}" if ke['beta'] else '—',
            'Skewness':       f"{_f(ke['skewness'],3)}" if ke['skewness'] else '—',
            'Curtose':        f"{_f(ke['curtose'],2)}" if ke['curtose'] else '—',
            'Jarque-Bera':    'Normal' if ke['retorno_normal'] else 'Não-normal',
            'CV':             f"{_f(ke['cv'],2)}" if ke['cv'] else '—',
            'R²':             f"{_f(ke['r_quadrado'],3)}" if ke['r_quadrado'] else '—',
        }
    except Exception as e:
        logger.warning(f"_buscar_estat ativo_id={ativo_id}: {e}")
        return {}


def _buscar_merc(ativo_id: int) -> dict:
    """Busca o KpiTime mais recente."""
    from market_data.models import KpiTime
    try:
        kt = (KpiTime.objects
              .filter(ativo_id=ativo_id)
              .order_by('-data_ref')
              .values('volume_medio_20d', 'beta', 'retorno_12m',
                      'rsi_14', 'media_movel_50', 'media_movel_200',
                      'retorno_1m', 'retorno_3m')
              .first())
        if not kt:
            return {}
        r12 = _f(kt['retorno_12m'], 4)
        r1  = _f(kt['retorno_1m'],  4)
        r3  = _f(kt['retorno_3m'],  4)
        vol = _f(kt['volume_medio_20d'], 0)
        return {
            'Volume 20d':   f"R$ {vol/1e6:.0f}M/dia" if vol else '—',
            'Beta':         f"{_f(kt['beta'],2)}" if kt['beta'] else '—',
            'Ret. 1m':      f"{'+' if r1 and r1>=0 else ''}{r1*100:.1f}%" if r1 is not None else '—',
            'Ret. 3m':      f"{'+' if r3 and r3>=0 else ''}{r3*100:.1f}%" if r3 is not None else '—',
            'Ret. 12m':     f"{'+' if r12 and r12>=0 else ''}{r12*100:.1f}%" if r12 is not None else '—',
            'RSI 14':       f"{_f(kt['rsi_14'],0)}" if kt['rsi_14'] else '—',
            'MM 50':        f"R$ {_f(kt['media_movel_50'],2)}" if kt['media_movel_50'] else '—',
            'MM 200':       f"R$ {_f(kt['media_movel_200'],2)}" if kt['media_movel_200'] else '—',
        }
    except Exception as e:
        logger.warning(f"_buscar_merc ativo_id={ativo_id}: {e}")
        return {}


def _buscar_sent(ativo_id: int) -> dict:
    """Busca as últimas notícias e score médio de sentimento."""
    from market_data.models import SentimentoMercado
    from django.db.models import Avg
    from datetime import date, timedelta
    try:
        desde = date.today() - timedelta(days=30)
        qs = (SentimentoMercado.objects
              .filter(ativo_id=ativo_id, data_ref__gte=desde))

        media = qs.aggregate(m=Avg('score_sentimento'))['m']
        score = round((_f(media, 4) + 1) / 2 * 100) if media is not None else 50

        noticias_qs = (qs.exclude(resumo_nlp='')
                       .order_by('-data_ref')
                       .values('score_sentimento', 'resumo_nlp')[:3])

        noticias = []
        for n in noticias_qs:
            v = _f(n['score_sentimento'], 4) or 0
            noticias.append({
                'tipo': 'positivo' if v > 0.15 else ('negativo' if v < -0.15 else 'neutro'),
                'txt':  str(n['resumo_nlp'])[:120],
            })

        return {'score': score, 'noticias': noticias}
    except Exception as e:
        logger.warning(f"_buscar_sent ativo_id={ativo_id}: {e}")
        return {'score': 50, 'noticias': []}


# ─────────────────────────────────────────────────────────────
# MONTA AS CARTEIRAS — função principal
# ─────────────────────────────────────────────────────────────

CONFIG_PERFIS = {
    'conservador':   {'score_min': 50, 'qtd': 5},
    'intermediario': {'score_min': 42, 'qtd': 6},
    'arrojado':      {'score_min': 35, 'qtd': 7},
}


def montar_carteiras_home() -> dict:
    """
    Monta as 3 carteiras coringa (uma por perfil) para a home pública.

    Retorna dict serializável para JSON:
    {
      'conservador':   [{ticker, nome, pct, score, fund, estat, merc, sent}, ...],
      'intermediario': [...],
      'arrojado':      [...],
    }
    """
    from portfolio.models import Ativo
    from scoring.services.motor_scoring_v2 import calcular_scores_v2
    carteiras = {}

    for perfil, cfg in CONFIG_PERFIS.items():
        try:
            # 1. Calcula scores
            df = calcular_scores_v2(perfil=perfil)
            if df is None or df.empty:
                carteiras[perfil] = []
                continue

            # 2. Filtra: score mínimo + ticker válido (sem espaço, 4-6 chars)
            df = df[df['score_final'] >= cfg['score_min']].copy()
            df = df[~df['ticker'].str.contains(' ', na=True)]
            df = df[df['ticker'].str.len().between(4, 6)]

            if df.empty:
                carteiras[perfil] = []
                continue

            # 3. Top N por score
            top = df.nlargest(cfg['qtd'], 'score_final')

            # 4. Busca nomes dos ativos em lote (1 query só)
            ids = top['ativo_id'].tolist()
            ativos_map = {
                a.id: {'nome': a.nome[:22], 'setor': a.setor or ''}
                for a in Ativo.objects.filter(id__in=ids).only('id', 'nome', 'setor')
            }

            # 5. Calcula percentual proporcional ao score
            # 5. Utiliza a alocação calculada pelo motor
            ativos = []

            for _, row in top.iterrows():

                ativo_id = int(row['ativo_id'])
                ticker = str(row['ticker'])

                score = _f(row['score_final'], 1) or 50

                pct = round(
                    float(row.get('pct_markowitz', 0)),
                    1
                )

                info = ativos_map.get(
                    ativo_id,
                    {'nome': ticker, 'setor': ''}
                )

                ativos.append({
                    'ticker': ticker,
                    'nome': info['nome'],
                    'setor': info['setor'],
                    'pct': pct,
                    'score': int(round(score)),
                    'fund': _buscar_fund(ativo_id),
                    'estat': _buscar_estat(ativo_id),
                    'merc': _buscar_merc(ativo_id),
                    'sent': _buscar_sent(ativo_id),
                })
            carteiras[perfil] = ativos
            logger.info(f"Carteira {perfil}: {len(ativos)} ativos")

        except Exception as e:
            logger.error(f"Erro ao montar carteira {perfil}: {e}", exc_info=True)
            carteiras[perfil] = []

    return carteiras