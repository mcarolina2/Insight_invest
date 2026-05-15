"""
Salve em: market_data/management/commands/load_cvm_data.py

Execução:
  python manage.py load_cvm_data                  # últimos 8 anos
  python manage.py load_cvm_data --ano 2023       # só um ano
  python manage.py load_cvm_data --force          # reprocessa mesmo se já existir
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Baixa BP e DRE da CVM, calcula KPIs fundamentalistas e salva em KpiMicro"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ano",
            type=int,
            default=None,
            help="Ano específico (ex: 2023). Sem este flag, baixa os últimos 8 anos.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reprocessa e sobrescreve registros já existentes no banco.",
        )

    def handle(self, *args, **options):
        # -------------------------------------------------------------------
        # Imports locais — evita erro se Django ainda não estiver configurado
        # -------------------------------------------------------------------
        try:
            from services.extractors.cvm_extractor import extrair_historico
            from services.calculators.indicadores import processar_dataframe
        except ModuleNotFoundError as e:
            raise CommandError(
                f"Módulo não encontrado: {e}\n"
                "Verifique se services/extractors/cvm_extractor.py e "
                "services/calculators/indicadores.py existem e se há __init__.py nas pastas."
            )

        from portfolio.models import Ativo
        from market_data.models import KpiMicro

        ano_flag  = options.get("ano")
        force     = options.get("force", False)
        ano_atual = date.today().year
        anos      = [ano_flag] if ano_flag else list(range(ano_atual - 8, ano_atual))

        self.stdout.write(f"Anos a processar: {anos}\n")

        # -------------------------------------------------------------------
        # Passo 1 — Download da CVM
        # -------------------------------------------------------------------
        self.stdout.write("[1/3] Baixando dados da CVM (pode demorar vários minutos)...")

        try:
            df_bp, df_dre = extrair_historico(anos)
        except Exception as e:
            raise CommandError(f"Falha no download da CVM: {e}")

        if df_bp.empty or df_dre.empty:
            raise CommandError(
                "Nenhum dado retornado pela CVM. "
                "Verifique sua conexão ou tente com --ano 2023."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"    BP: {len(df_bp):,} linhas | DRE: {len(df_dre):,} linhas\n"
            )
        )

        # -------------------------------------------------------------------
        # Passo 2 — Calcula indicadores fundamentalistas
        # -------------------------------------------------------------------
        self.stdout.write("[2/3] Calculando indicadores (ROE, liquidez, margens...)...")

        try:
            df_kpis = processar_dataframe(df_bp, df_dre)
        except Exception as e:
            raise CommandError(f"Falha no cálculo dos indicadores: {e}")

        self.stdout.write(
            self.style.SUCCESS(f"    {len(df_kpis):,} registros calculados\n")
        )

        # -------------------------------------------------------------------
        # Passo 3 — Salva no banco
        # -------------------------------------------------------------------
        self.stdout.write("[3/3] Salvando no banco de dados...")

        # Mapa CNPJ → objeto Ativo
        # IMPORTANTE: o model Ativo precisa ter um campo 'cnpj' (CharField)
        # Se ainda não tiver, adicione e rode makemigrations + migrate
        ativos_por_cnpj = {
            a.cnpj: a
            for a in Ativo.objects.filter(cnpj__isnull=False)
            if a.cnpj
        }

        import math

        def limpar(valor):
            """Converte nan/NaN para None — necessario para DecimalField do Django."""
            if valor is None:
                return None
            try:
                f = float(valor)
                return None if math.isnan(f) or math.isinf(f) else valor
            except (TypeError, ValueError):
                return None

        sem_ativo  = 0
        ignorados  = 0
        kpis_batch = []

        for _, row in df_kpis.iterrows():
            ativo    = ativos_por_cnpj.get(str(row.get("cnpj", "")))
            data_ref = row.get("data_ref")

            if not ativo:
                sem_ativo += 1
                continue

            if not data_ref:
                continue

            # Pula se ja existe e --force nao foi informado
            if not force and KpiMicro.objects.filter(ativo=ativo, data_ref=data_ref).exists():
                ignorados += 1
                continue

            kpis_batch.append(KpiMicro(
                ativo               = ativo,
                data_ref            = data_ref,
                # --- Liquidez ---
                liquidez_corrente   = limpar(row.get("liquidez_corrente")),
                liquidez_seca       = limpar(row.get("liquidez_seca")),
                liquidez_imediata   = limpar(row.get("liquidez_imediata")),
                liquidez_geral      = limpar(row.get("liquidez_geral")),
                # --- Rentabilidade ---
                roe                 = limpar(row.get("roe")),
                roa                 = limpar(row.get("roa")),
                giro_ativo          = limpar(row.get("giro_ativo")),
                margem_liquida      = limpar(row.get("margem_liquida")),
                margem_ebitda       = limpar(row.get("margem_ebitda")),
                # --- Endividamento ---
                divida_liquida      = limpar(row.get("divida_liquida")),
                divida_ebitda       = limpar(row.get("divida_ebitda")),
                # --- Crescimento (requer 2+ anos consecutivos) ---
                crescimento_receita = limpar(row.get("crescimento_receita")),
                crescimento_lucro   = limpar(row.get("crescimento_lucro")),
                # pl, pvpa, dy, lucro_por_acao etc. vem do load_price_data
            ))

        salvos = 0
        if kpis_batch:
            with transaction.atomic():
                KpiMicro.objects.bulk_create(
                    kpis_batch,
                    update_conflicts = True,
                    unique_fields    = ["ativo", "data_ref"],
                    update_fields    = [
                        "liquidez_corrente", "liquidez_seca",
                        "liquidez_imediata", "liquidez_geral",
                        "roe", "roa", "giro_ativo",
                        "margem_liquida", "margem_ebitda",
                        "divida_liquida", "divida_ebitda",
                        "crescimento_receita", "crescimento_lucro",
                    ],
                )
            salvos = len(kpis_batch)

        self.stdout.write(self.style.SUCCESS(
            f"\nConcluído!\n"
            f"  Salvos/atualizados   : {salvos}\n"
            f"  Ignorados (já existem): {ignorados}\n"
            f"  Sem ativo cadastrado : {sem_ativo}\n\n"
            "Dica: se 'sem ativo' for alto, rode primeiro:\n"
            "  python manage.py load_price_data --todos --apenas-info\n"
            "para cadastrar os ativos com nome, setor e CNPJ."
        ))