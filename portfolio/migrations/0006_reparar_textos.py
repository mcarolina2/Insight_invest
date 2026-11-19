"""Corrige acentuação trocada ("Tęxtil" -> "Têxtil") e setores do Yahoo em inglês."""
from django.db import migrations


def reparar(apps, schema_editor):
    from services.setores import reparar_mojibake, reparar_setor
    Ativo = apps.get_model("portfolio", "Ativo")
    for a in Ativo.objects.only("id", "nome", "setor", "subsetor"):
        novo = {
            "nome": reparar_mojibake(a.nome),
            "setor": reparar_setor(a.setor) if a.setor else a.setor,
            "subsetor": reparar_mojibake(a.subsetor),
        }
        mudou = {k: v for k, v in novo.items() if v != getattr(a, k)}
        if mudou:
            Ativo.objects.filter(pk=a.pk).update(**mudou)


class Migration(migrations.Migration):
    dependencies = [("portfolio", "0005_reparar_setores")]
    operations = [migrations.RunPython(reparar, migrations.RunPython.noop)]
