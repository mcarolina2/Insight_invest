"""
Execute no shell do Django para diagnosticar os dois problemas:
  python manage.py shell
  exec(open('diagnostico.py').read())

Salve como: diagnostico.py na raiz do projeto
"""

import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Insight_invest.settings')

import django
django.setup()

print("=" * 60)
print("DIAGNÓSTICO 1 — Rankings de liquidez e endividamento")
print("=" * 60)

from market_data.models import KpiMicro

# Verifica quantos registros têm esses campos preenchidos
total        = KpiMicro.objects.count()
com_liq      = KpiMicro.objects.filter(liquidez_corrente__isnull=False,
                                        liquidez_corrente__gt=0).count()
com_div      = KpiMicro.objects.filter(divida_ebitda__isnull=False,
                                        divida_ebitda__gte=0).count()

print(f"Total KpiMicro          : {total:,}")
print(f"Com liquidez_corrente   : {com_liq:,}")
print(f"Com divida_ebitda       : {com_div:,}")
print()

# Mostra os 5 melhores sem o distinct (problema comum)
print("Top 5 liquidez (sem distinct — teste direto):")
top_liq = (KpiMicro.objects
           .filter(liquidez_corrente__isnull=False,
                   liquidez_corrente__gt=0,
                   liquidez_corrente__lt=20)
           .select_related('ativo')
           .order_by('-liquidez_corrente')[:5])

for k in top_liq:
    print(f"  {k.ativo.ticker:<8} liq={float(k.liquidez_corrente):.2f}x  data={k.data_ref}")

print()
print("Top 5 endividamento (sem distinct — teste direto):")
top_div = (KpiMicro.objects
           .filter(divida_ebitda__isnull=False,
                   divida_ebitda__gte=0,
                   divida_ebitda__lt=50)
           .select_related('ativo')
           .order_by('divida_ebitda')[:5])

for k in top_div:
    print(f"  {k.ativo.ticker:<8} div/ebitda={float(k.divida_ebitda):.2f}x  data={k.data_ref}")

print()
print("=" * 60)
print("DIAGNÓSTICO 2 — Ativos sem dados recentes no yfinance (ex: LITE3)")
print("=" * 60)

from portfolio.models import Ativo
from market_data.models import KpiTime
import datetime

hoje       = datetime.date.today()
um_ano     = hoje - datetime.timedelta(days=365)
dois_anos  = hoje - datetime.timedelta(days=730)

# Ativos sem nenhum KpiTime
sem_kpi_time = Ativo.objects.filter(ativo=True).exclude(
    pk__in=KpiTime.objects.values('ativo_id').distinct()
).count()

# Ativos com KpiTime mas dados antigos (última data > 1 ano atrás)
from django.db.models import Max
ativos_com_dados = (KpiTime.objects
                    .values('ativo_id')
                    .annotate(ultima=Max('data_ref')))

dados_velhos = [(a['ativo_id'], a['ultima'])
                for a in ativos_com_dados if a['ultima'] < um_ano]

print(f"Ativos sem KpiTime (nunca baixados) : {sem_kpi_time}")
print(f"Ativos com dados > 1 ano desatualizados: {len(dados_velhos)}")
print()

# Exemplo específico com LITE3
try:
    lite3 = Ativo.objects.get(ticker='LITE3')
    kpi = KpiTime.objects.filter(ativo=lite3).order_by('-data_ref').first()
    print(f"LITE3 — último KpiTime: {kpi.data_ref if kpi else 'nenhum'}")
    print(f"LITE3 — ativo no banco: {lite3.ativo}")
    print(f"LITE3 — CNPJ: {lite3.cnpj}")
except Ativo.DoesNotExist:
    print("LITE3 não encontrada no banco")