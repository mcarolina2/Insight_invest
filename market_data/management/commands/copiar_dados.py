"""
Copia os dados de mercado do seu banco local para outro banco (ex.: o do Render),
usando o próprio Django: não precisa de pg_dump instalado.

Passo a passo (Windows PowerShell):
    $env:DESTINO_DATABASE_URL = "postgresql://...render.com/insightdb"   # External Database URL
    python manage.py copiar_dados --tamanhos          # só mostra o tamanho de cada tabela
    python manage.py copiar_dados                     # copia

Linux/macOS: export DESTINO_DATABASE_URL="postgresql://..."

O destino precisa estar migrado (o build do Render já roda o migrate).
Usuários, sessões e senhas NÃO são copiados.
"""
from datetime import date, timedelta

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction

# Ordem respeita as chaves estrangeiras.
# Cadastros: inseridos ou atualizados, NUNCA apagados (há dados de usuários ligados a eles).
# valor = campo que identifica o registro (o Plano é casado pelo tipo, porque o
# Render cria o plano gratuito sozinho ao criar o superusuário).
CADASTROS = {"portfolio.Ativo": "id", "billing.Plano": "tipo"}
# Dados de mercado e escores: substituídos por completo a cada cópia.
MODELOS = [
    "portfolio.Ativo",
    "billing.Plano",
    "market_data.KpiMacro",
    "market_data.KpiMicro",
    "market_data.KpiEstatistico",
    "market_data.KpiTime",
    "market_data.SentimentoMercado",
    "scoring.ScoreAtivo",
    "scoring.CarteiraRecomendada",
]
LOTE = 2000
LIMITE_RENDER_BYTES = 1024 ** 3        # Postgres gratuito do Render: 1 GB


class Command(BaseCommand):
    help = "Copia os dados de mercado do banco local para DESTINO_DATABASE_URL (ex.: Render)."

    def add_arguments(self, parser):
        parser.add_argument("--tamanhos", action="store_true",
                            help="Só mostra quantas linhas e quanto espaço cada tabela ocupa.")
        parser.add_argument("--kpitime-dias", type=int, default=400,
                            help="Copia só os últimos N dias de KpiTime (padrão 400; 0 = tudo).")

    def _gravar(self, modelo, lote, cadastro):
        chave = CADASTROS.get(modelo._meta.label)
        if cadastro:   # insere ou atualiza, sem apagar
            campos = [f.name for f in modelo._meta.concrete_fields if not f.primary_key and f.name != chave]
            if chave != "id":
                for obj in lote:
                    obj.pk = None          # o id do destino é mantido; casa pelo campo-chave
            modelo.objects.using("destino").bulk_create(
                lote, batch_size=LOTE, update_conflicts=True, unique_fields=[chave], update_fields=campos)
        else:
            modelo.objects.using("destino").bulk_create(lote, batch_size=LOTE)

    def _filtro(self, modelo, dias):
        qs = modelo.objects.using("default").all()
        if modelo._meta.label == "market_data.KpiTime" and dias:
            qs = qs.filter(data_ref__gte=date.today() - timedelta(days=dias))
        return qs.order_by("pk")

    def handle(self, *args, tamanhos, kpitime_dias, **options):
        modelos = [apps.get_model(m) for m in MODELOS]

        if tamanhos:
            total = 0
            with connections["default"].cursor() as c:
                for m in modelos:
                    c.execute("SELECT pg_total_relation_size(%s)", [m._meta.db_table])
                    bytes_ = c.fetchone()[0]
                    total += bytes_
                    n = self._filtro(m, kpitime_dias).count()
                    self.stdout.write(f"{m._meta.label:32s} {n:>10,} linhas a copiar   {bytes_ / 1024**2:8.1f} MB no local")
            self.stdout.write(f"{'TOTAL no local':32s} {'':>10}                  {total / 1024**2:8.1f} MB")
            if total > 0.8 * LIMITE_RENDER_BYTES:
                self.stdout.write(self.style.WARNING(
                    "Perto ou acima de 1 GB (limite do Postgres gratuito do Render). "
                    "Reduza KpiTime com --kpitime-dias."))
            return

        if "destino" not in connections.databases:
            raise CommandError("Defina DESTINO_DATABASE_URL com a External Database URL do Render.")

        destino = connections["destino"]
        self.stdout.write(f"Destino: {destino.settings_dict['HOST']} / {destino.settings_dict['NAME']}")
        call_command("migrate", database="destino", verbosity=0)   # garante as tabelas

        dados = [m for m in modelos if m._meta.label not in CADASTROS]
        with transaction.atomic(using="destino"):
            for m in reversed(dados):                       # apaga só dados de mercado/escores
                m.objects.using("destino").all().delete()

            for m in modelos:
                qs = self._filtro(m, kpitime_dias)
                total, copiados, lote = qs.count(), 0, []
                cadastro = m._meta.label in CADASTROS
                for obj in qs.iterator(chunk_size=LOTE):
                    lote.append(obj)
                    if len(lote) >= LOTE:
                        self._gravar(m, lote, cadastro)
                        copiados += len(lote)
                        lote = []
                        self.stdout.write(f"\r  {m._meta.label}: {copiados:,}/{total:,}", ending="")
                if lote:
                    self._gravar(m, lote, cadastro)
                    copiados += len(lote)
                self.stdout.write(f"\r  {m._meta.label}: {copiados:,}/{total:,}")

        # Ajusta os contadores de id do destino para continuarem após os ids copiados
        sql = destino.ops.sequence_reset_sql(no_style(), modelos)
        with destino.cursor() as c:
            for comando in sql:
                c.execute(comando)

        self.stdout.write(self.style.SUCCESS(
            "Cópia concluída. Agora recalcule as carteiras: no painel /admin/ > Configurações de perfil "
            "> selecione os 3 perfis > ação 'Recalcular carteiras'."))
