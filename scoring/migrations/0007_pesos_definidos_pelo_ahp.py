"""
Aplica os pesos definidos pelo método AHP (seção "Definição dos Pesos pelo
Método AHP" do TCC). Só altera um perfil se os pesos dele ainda forem os
valores iniciais; ajustes feitos manualmente no painel são preservados.
Cada alteração fica registrada no histórico de configurações.
"""
from decimal import Decimal

from django.db import migrations

ANTIGOS = {
    "conservador":   (0.50, 0.30, 0.20, 0.35, 0.30, 0.25, 0.10),
    "intermediario": (0.40, 0.30, 0.15, 0.25, 0.35, 0.20, 0.20),
    "arrojado":      (0.30, 0.20, 0.20, 0.10, 0.40, 0.10, 0.40),
}
AHP = {
    "conservador":   (0.55, 0.24, 0.21, 0.36, 0.29, 0.25, 0.10),
    "intermediario": (0.49, 0.31, 0.20, 0.20, 0.40, 0.20, 0.20),
    "arrojado":      (0.50, 0.25, 0.25, 0.10, 0.40, 0.10, 0.40),
}
CAMPOS = ("peso_fundamentalista", "peso_macro", "peso_estatistico",
          "peso_liquidez", "peso_rentabilidade", "peso_endividamento", "peso_crescimento")


def _valores(c):
    return tuple(float(getattr(c, f)) for f in CAMPOS)


def aplicar(apps, schema_editor, origem=ANTIGOS, destino=AHP, motivo="Pesos definidos pelo método AHP"):
    db = schema_editor.connection.alias
    Config = apps.get_model("scoring", "ConfiguracaoPerfil")
    Hist = apps.get_model("scoring", "HistoricoConfiguracaoPerfil")
    for c in Config.objects.using(db).all():
        if c.perfil not in destino:
            continue
        if any(abs(a - b) > 1e-6 for a, b in zip(_valores(c), origem[c.perfil])):
            continue                     # foi ajustado manualmente: não mexe
        for campo, valor in zip(CAMPOS, destino[c.perfil]):
            setattr(c, campo, Decimal(str(valor)))
        c.save()
        Hist.objects.using(db).create(perfil=c.perfil, motivo=motivo, configuracao={
            "pesos": dict(zip(("micro", "macro", "estat"), destino[c.perfil][:3])),
            "micro_pesos": dict(zip(("liquidez", "rentabilidade", "divida", "crescimento"),
                                    destino[c.perfil][3:])),
        })


def reverter(apps, schema_editor):
    aplicar(apps, schema_editor, origem=AHP, destino=ANTIGOS, motivo="Reversão dos pesos AHP")


class Migration(migrations.Migration):
    dependencies = [("scoring", "0006_cria_configuracao_inicial_dos_perfis")]
    operations = [migrations.RunPython(aplicar, reverter)]
