"""Cria a configuração inicial dos três perfis com os valores descritos no TCC."""
from django.db import migrations

DESCRICOES = {
    "conservador": (
        "Para quem prioriza preservar o capital, tem horizonte curto ou pouca tolerância a perdas. "
        "A carteira privilegia empresas com boa liquidez e baixo endividamento e ações menos voláteis."),
    "intermediario": (
        "Para quem aceita oscilações moderadas em busca de retorno maior no médio prazo. "
        "A carteira equilibra solidez financeira, rentabilidade e crescimento."),
    "arrojado": (
        "Para quem tem horizonte longo, experiência com renda variável e aceita perdas temporárias maiores. "
        "A carteira privilegia rentabilidade e crescimento e é mais diversificada entre ativos."),
}


def criar(apps, schema_editor):
    from scoring.services.motor_scoring_v2 import PERFIS
    Config = apps.get_model("scoring", "ConfiguracaoPerfil")
    Hist = apps.get_model("scoring", "HistoricoConfiguracaoPerfil")
    db = schema_editor.connection.alias
    for perfil, c in PERFIS.items():
        f = c.get("filtros_kpi", {})
        obj, criado = Config.objects.using(db).get_or_create(perfil=perfil, defaults=dict(
            descricao=DESCRICOES[perfil],
            peso_fundamentalista=c["pesos"]["micro"], peso_macro=c["pesos"]["macro"],
            peso_estatistico=c["pesos"]["estat"],
            peso_liquidez=c["micro_pesos"]["liquidez"], peso_rentabilidade=c["micro_pesos"]["rentabilidade"],
            peso_endividamento=c["micro_pesos"]["divida"], peso_crescimento=c["micro_pesos"]["crescimento"],
            score_min=c["score_min"], qtd_ativos=c["qtd_ativos"], max_pct_ativo=c["max_pct_ativo"],
            liquidez_corrente_min=f.get("liquidez_corrente__gte"),
            divida_ebitda_max=f.get("divida_ebitda__lte"), roe_min=f.get("roe__gte"),
        ))
        if criado:
            Hist.objects.using(db).create(perfil=perfil, configuracao=c, motivo="Configuração inicial (TCC)")


class Migration(migrations.Migration):
    dependencies = [("scoring", "0005_configuracao_dos_perfis")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
