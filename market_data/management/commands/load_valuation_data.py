"""
Salve em: market_data/management/commands/load_valuation_data.py

Preenche os campos de valuation do KpiMicro que dependem do preço da ação:
  pl, pvpa, dy, ev_ebitda
  lucro_por_acao, valor_patrimonial_acao
  dividendo_por_acao, rentabilidade_dividendos, distribuicao_dividendos

Execução:
  python manage.py load_valuation_data --todos
  python manage.py load_valuation_data --tickers PETR4 VALE3 ITUB4
"""

import logging
import math
import warnings

# Suprime os warnings e prints do yfinance (404, rate limit, etc.)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

import yfinance as yf
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _limpar(valor):
    """Converte nan/inf para None e arredonda."""
    if valor is None:
        return None
    try:
        f = float(valor)
        return None if math.isnan(f) or math.isinf(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _ticker_valido(ticker: str) -> bool:
    """
    Filtra tickers inválidos para o Yahoo Finance:
    - Não pode ter espaço (ex: "ASA 3")
    - Deve ter entre 4 e 6 caracteres
    - Só letras e números
    """
    t = ticker.strip()
    if " " in t:
        return False
    if not (4 <= len(t) <= 6):
        return False
    if not t.replace(".", "").isalnum():
        return False
    return True


def obter_valuation_yfinance(ticker: str) -> dict:
    """
    Busca indicadores de valuation via yfinance.
    Retorna dict vazio silenciosamente se o ticker não existir.
    """
    try:
        # Suprime saída de erro do yfinance redirecionando stderr
        import io, sys, contextlib

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            info = yf.Ticker(ticker.upper() + ".SA").info

        # Se o ticker não existe, yfinance retorna um dict com poucos campos
        if not info or info.get("regularMarketPrice") is None and info.get("trailingPE") is None:
            # Tenta verificar pelo quoteType
            quote_type = info.get("quoteType", "")
            if quote_type not in ("EQUITY", "ETF", "MUTUALFUND") and not info.get("trailingPE"):
                return {}

        return {
            "pl":                       _limpar(info.get("trailingPE") or info.get("forwardPE")),
            "pvpa":                     _limpar(info.get("priceToBook")),
            "dy":                       _limpar(info.get("dividendYield")),
            "ev_ebitda":                _limpar(info.get("enterpriseToEbitda")),
            "lucro_por_acao":           _limpar(info.get("trailingEps") or info.get("forwardEps")),
            "valor_patrimonial_acao":   _limpar(info.get("bookValue")),
            "dividendo_por_acao":       _limpar(info.get("dividendRate")),
            "rentabilidade_dividendos": _limpar(info.get("dividendYield")),
            "distribuicao_dividendos":  _limpar(info.get("payoutRatio")),
        }

    except Exception:
        return {}


class Command(BaseCommand):
    help = "Preenche PL, PVPA, DY, EV/EBITDA e indicadores por ação em KpiMicro"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tickers",
            nargs="+",
            type=str,
            default=None,
            help="Tickers específicos (ex: --tickers PETR4 VALE3)",
        )
        parser.add_argument(
            "--todos",
            action="store_true",
            help="Processa todos os ativos com KpiMicro cadastrado",
        )

    def handle(self, *args, **options):
        from market_data.models import KpiMicro

        tickers_raw = options.get("tickers") or []

        if options.get("todos"):
            tickers_raw = list(
                KpiMicro.objects.values_list("ativo__ticker", flat=True).distinct()
            )

        if not tickers_raw:
            raise CommandError("Informe --tickers PETR4 VALE3 ou use --todos")

        # Filtra tickers válidos para o Yahoo Finance
        tickers_validos   = [t for t in tickers_raw if _ticker_valido(t)]
        tickers_invalidos = [t for t in tickers_raw if not _ticker_valido(t)]

        self.stdout.write(
            f"Total de ativos  : {len(tickers_raw)}\n"
            f"Validos (Yahoo)  : {len(tickers_validos)}\n"
            f"Invalidos (ignor): {len(tickers_invalidos)}"
            + (f" ex: {tickers_invalidos[:5]}" if tickers_invalidos else "")
            + "\n"
        )

        atualizados  = 0
        sem_dados    = 0
        sem_kpi      = 0
        total        = len(tickers_validos)

        for i, ticker in enumerate(tickers_validos, 1):
            # Mostra progresso a cada 10 ou no primeiro/último
            if i == 1 or i % 10 == 0 or i == total:
                self.stdout.write(f"  [{i}/{total}] processando...", ending="\r")

            valuation = obter_valuation_yfinance(ticker)

            # Sem nenhum dado útil — pula silenciosamente
            if not any(v is not None for v in valuation.values()):
                sem_dados += 1
                continue

            try:
                kpi = (
                    KpiMicro.objects
                    .filter(ativo__ticker=ticker)
                    .order_by("-data_ref")
                    .first()
                )

                if not kpi:
                    sem_kpi += 1
                    continue

                campos_salvos = []
                for campo, valor in valuation.items():
                    if valor is not None and hasattr(kpi, campo):
                        setattr(kpi, campo, valor)
                        campos_salvos.append(campo)

                if campos_salvos:
                    kpi.save(update_fields=campos_salvos)
                    atualizados += 1

            except Exception as e:
                logger.debug(f"{ticker}: {e}")
                sem_dados += 1

        # Limpa a linha do progresso
        self.stdout.write(" " * 50, ending="\r")

        self.stdout.write(self.style.SUCCESS(
            f"Concluido!\n"
            f"  Atualizados com valuation : {atualizados}\n"
            f"  Sem dados no Yahoo Finance: {sem_dados}\n"
            f"  Sem KpiMicro no banco     : {sem_kpi}\n"
            f"  Tickers invalidos saltados: {len(tickers_invalidos)}\n"
            f"\n"
            f"Campos preenchidos: pl, pvpa, dy, ev_ebitda,\n"
            f"  lucro_por_acao, valor_patrimonial_acao,\n"
            f"  dividendo_por_acao, rentabilidade_dividendos, distribuicao_dividendos\n"
            f"\n"
            f"Para ver os resultados:\n"
            f"  python manage.py runserver\n"
            f"  http://127.0.0.1:8000/admin/market_data/kpimicro/"
        ))