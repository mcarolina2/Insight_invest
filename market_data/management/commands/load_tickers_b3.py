"""
Salve em: market_data/management/commands/load_tickers_b3.py

Execução:
  python manage.py load_tickers_b3              # baixa tudo da B3 + CVM
  python manage.py load_tickers_b3 --cache      # usa CSV salvo (sem rebaixar)
  python manage.py load_tickers_b3 --force      # recria todos os registros

Este comando resolve o problema de "Sem ativo cadastrado: 3471".
Deve ser o PRIMEIRO comando a rodar antes de qualquer load_*_data.
"""

import logging
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)

CACHE_PATH = "data/tickers_b3.csv"


class Command(BaseCommand):
    help = (
        "Baixa todos os tickers da B3, cruza com CNPJs da CVM "
        "e cadastra na tabela Ativo"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cache",
            action="store_true",
            help=f"Usa o CSV em cache ({CACHE_PATH}) sem rebaixar da internet.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Atualiza todos os ativos mesmo que já existam.",
        )
        parser.add_argument(
            "--apenas-cnpj",
            action="store_true",
            help="Só atualiza o CNPJ dos ativos já cadastrados, sem criar novos.",
        )

    def handle(self, *args, **options):
        # -------------------------------------------------------------------
        # Imports locais
        # -------------------------------------------------------------------
        try:
            from services.extractors.b3_extractor import (
                construir_mapa_ticker_cnpj,
                salvar_mapa_csv,
                carregar_mapa_csv,
            )
        except ModuleNotFoundError as e:
            raise CommandError(
                f"Módulo não encontrado: {e}\n"
                "Verifique se services/extractors/b3_extractor.py existe."
            )

        from portfolio.models import Ativo

        usar_cache  = options.get("cache", False)
        force       = options.get("force", False)
        apenas_cnpj = options.get("apenas_cnpj", False)

        # -------------------------------------------------------------------
        # Passo 1 — Obtém o mapa ticker → CNPJ
        # -------------------------------------------------------------------
        if usar_cache and os.path.exists(CACHE_PATH):
            self.stdout.write(f"Carregando cache de {CACHE_PATH}...")
            df = carregar_mapa_csv(CACHE_PATH)
            self.stdout.write(self.style.SUCCESS(f"    {len(df)} tickers no cache\n"))
        else:
            self.stdout.write(
                "Baixando lista de empresas da B3 e CNPJs da CVM\n"
                "(isso pode levar 1-2 minutos)...\n"
            )
            try:
                df = construir_mapa_ticker_cnpj()
            except Exception as e:
                raise CommandError(f"Falha ao construir mapa: {e}")

            if df.empty:
                raise CommandError(
                    "Nenhum ticker obtido da B3.\n"
                    "Verifique sua conexão com a internet."
                )

            # Salva CSV para uso futuro com --cache
            salvar_mapa_csv(df, CACHE_PATH)
            self.stdout.write(
                self.style.SUCCESS(
                    f"    {len(df)} tickers obtidos e salvos em {CACHE_PATH}\n"
                )
            )

        # -------------------------------------------------------------------
        # Passo 2 — Relatório antes de salvar
        # -------------------------------------------------------------------
        com_cnpj = df["cnpj"].notna().sum()
        sem_cnpj = df["cnpj"].isna().sum()

        self.stdout.write(
            f"Resumo do mapa:\n"
            f"  Total de tickers  : {len(df)}\n"
            f"  Com CNPJ          : {com_cnpj}\n"
            f"  Sem CNPJ          : {sem_cnpj}\n"
        )

        # -------------------------------------------------------------------
        # Passo 3 — Salva no banco
        # -------------------------------------------------------------------
        criados    = 0
        atualizados = 0
        ignorados   = 0

        with transaction.atomic():
            for _, row in df.iterrows():
                ticker = str(row.get("ticker", "")).strip().upper()
                if not ticker or len(ticker) < 4:
                    continue

                cnpj  = row.get("cnpj")
                nome  = row.get("nome") or ticker
                setor = row.get("setor") or ""

                # Limpa o CNPJ (remove pontos, traços, barras)
                if cnpj and str(cnpj) != "nan":
                    cnpj = str(cnpj).strip()
                else:
                    cnpj = None

                if apenas_cnpj:
                    # Só atualiza CNPJ de quem já está no banco
                    atualizado = Ativo.objects.filter(ticker=ticker).update(cnpj=cnpj)
                    if atualizado:
                        atualizados += 1
                    continue

                # Decide o tipo do ativo pelo sufixo do ticker
                tipo = _inferir_tipo(ticker)

                # Classifica o setor de forma mais limpa
                setor_limpo = _limpar_setor(setor)

                if force:
                    obj, criado = Ativo.objects.update_or_create(
                        ticker=ticker,
                        defaults={
                            "nome":   nome[:255],
                            "cnpj":   cnpj,
                            "setor":  setor_limpo,
                            "tipo":   tipo,
                            "ativo":  True,
                        },
                    )
                    if criado:
                        criados += 1
                    else:
                        atualizados += 1
                else:
                    # Só cria se não existir — preserva dados já cadastrados
                    obj, criado = Ativo.objects.get_or_create(
                        ticker=ticker,
                        defaults={
                            "nome":   nome[:255],
                            "cnpj":   cnpj,
                            "setor":  setor_limpo,
                            "tipo":   tipo,
                            "ativo":  True,
                        },
                    )
                    if criado:
                        criados += 1
                    else:
                        # Atualiza só o CNPJ se estava faltando
                        if not obj.cnpj and cnpj:
                            obj.cnpj = cnpj
                            obj.save(update_fields=["cnpj"])
                            atualizados += 1
                        else:
                            ignorados += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nConcluído!\n"
            f"  Criados     : {criados}\n"
            f"  Atualizados : {atualizados}\n"
            f"  Ignorados   : {ignorados}\n"
            f"\nAgora você pode rodar os outros comandos na ordem:\n"
            f"  python manage.py load_price_data --todos --anos 8\n"
            f"  python manage.py load_macro_data --meses 120\n"
            f"  python manage.py load_cvm_data\n"
            f"  python manage.py load_sentiment_data --sem-modelo\n"
        ))


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def _inferir_tipo(ticker: str) -> str:
    """
    Infere o tipo do ativo pelo sufixo do ticker.
      3, 4       → ação (ON e PN)
      11         → FII ou ETF
      34         → BDR
      F          → renda fixa
    """
    t = ticker.upper()
    if t.endswith("11"):
        return "fii"       # pode ser ETF também — refinado depois
    if t.endswith("34"):
        return "bdr"
    if t.endswith("F"):
        return "renda_fixa"
    return "acao"


def _limpar_setor(setor: str) -> str:
    """Remove prefixos numéricos que a CVM inclui nos nomes de setor."""
    if not setor or str(setor) == "nan":
        return ""
    # Ex: "1. Petróleo e Gás" → "Petróleo e Gás"
    partes = str(setor).split(". ", 1)
    return partes[-1].strip()[:100]