import logging
from django.core.management.base import BaseCommand
from django.db import transaction

# Ajuste para o nome do seu app
from market_data.models import KpiMacro
from services.extractors.bcb_extractor import consolidar_series, calcular_balanca_comercial

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Baixa indicadores macroeconômicos do Banco Central e popula KpiMacro"

    def add_arguments(self, parser):
        parser.add_argument(
            "--meses",
            type=int,
            default=120,
            help="Quantos meses de histórico buscar (padrão: 120 = 10 anos)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Atualiza registros mesmo que já existam",
        )

    def handle(self, *args, **options):
        meses = options["meses"]
        force = options["force"]

        self.stdout.write(f"Buscando últimos {meses} meses de dados macroeconômicos...")

        # 1. Consolida todas as séries do BCB
        registros = consolidar_series(meses=meses)
        self.stdout.write(f"Datas encontradas: {len(registros)}")

        # 2. Salva no banco
        novos = 0
        atualizados = 0

        with transaction.atomic():
            for reg in registros:
                data = reg["data_ref"]

                balanca = calcular_balanca_comercial(
                    reg.get("exportacoes"),
                    reg.get("importacoes"),
                )

                obj, criado = KpiMacro.objects.update_or_create(
                    data_ref=data,
                    defaults={
                        "selic":              reg.get("selic"),
                        "ipca_mensal":        reg.get("ipca_mensal"),
                        "igpm_mensal":        reg.get("igpm_mensal"),
                        "pib_trimestral":     reg.get("pib_trimestral"),
                        "ibc_br":             reg.get("ibc_br"),
                        "exportacoes":        reg.get("exportacoes"),
                        "importacoes":        reg.get("importacoes"),
                        "balanca_comercial":  balanca,
                        "desemprego":         reg.get("desemprego"),
                    },
                )

                if criado:
                    novos += 1
                else:
                    atualizados += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Concluído — Novos: {novos} | Atualizados: {atualizados}"
            )
        )
