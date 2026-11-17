"""Corrige nomes de setor gravados cortados ("e Decoração", "Part. - Alimentos")."""
from django.db import migrations


def reparar(apps, schema_editor):
    from services.setores import reparar_setor
    Ativo = apps.get_model("portfolio", "Ativo")
    for a in Ativo.objects.exclude(setor="").exclude(setor__isnull=True).only("id", "setor"):
        novo = reparar_setor(a.setor)
        if novo != a.setor:
            Ativo.objects.filter(pk=a.pk).update(setor=novo)


class Migration(migrations.Migration):
    dependencies = [("portfolio", "0004_cnpj_nao_unico")]
    operations = [migrations.RunPython(reparar, migrations.RunPython.noop)]
