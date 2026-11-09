"""
Mostra as matrizes de comparação par a par, os pesos e a razão de consistência
de cada perfil (método AHP), exatamente como nas tabelas do TCC.

Uso:  python manage.py pesos_ahp
"""
from fractions import Fraction

from django.core.management.base import BaseCommand

NOMES_DIM = ["Liquidez", "Rentabilidade", "Endividamento", "Crescimento"]
NOMES_CAM = ["Fundamentalista", "Macroeconômica", "Estatística"]


def _f(v):
    fr = Fraction(v).limit_denominator(9)
    return str(fr.numerator) if fr.denominator == 1 else f"1/{fr.denominator}"


class Command(BaseCommand):
    help = "Calcula os pesos dos perfis pelo método AHP (Saaty)."

    def handle(self, *args, **options):
        from scoring.services.ahp import calcular_todos

        for perfil, r in calcular_todos().items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {perfil.upper()} ==="))
            for titulo, nomes, A, w, lmax, rc in [
                ("Dimensões fundamentalistas", NOMES_DIM, r["matriz_dimensoes"],
                 r["pesos_brutos_dimensoes"], r["lambda_dimensoes"], r["rc_dimensoes"]),
                ("Camadas", NOMES_CAM, r["matriz_camadas"],
                 r["pesos_brutos_camadas"], r["lambda_camadas"], r["rc_camadas"]),
            ]:
                ok = "consistente" if rc < 0.10 else "INCONSISTENTE (revise os julgamentos)"
                self.stdout.write(f"\n{titulo}  (lambda_max = {lmax:.3f}; RC = {rc:.3f} -> {ok})")
                self.stdout.write(" " * 16 + "".join(f"{n[:6]:>8s}" for n in nomes) + "    peso")
                for nome, linha, peso in zip(nomes, A, w):
                    self.stdout.write(f"{nome:16s}" + "".join(f"{_f(x):>8s}" for x in linha) + f"   {peso:.3f}")
            self.stdout.write(f"\nPesos adotados (2 casas): dimensões {r['dimensoes']} | camadas {r['camadas']}")
