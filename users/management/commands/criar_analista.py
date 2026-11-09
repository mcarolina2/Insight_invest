"""
Dá a um usuário existente o papel de Analista (ex.: o economista).

Uso:  python manage.py criar_analista <usuario>
      python manage.py criar_analista <usuario> --remover
"""
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Adiciona (ou remove) um usuário do grupo Analista."

    def add_arguments(self, parser):
        parser.add_argument("usuario")
        parser.add_argument("--remover", action="store_true")

    def handle(self, *args, usuario, remover, **options):
        from users.models import User
        try:
            u = User.objects.get(username=usuario)
        except User.DoesNotExist:
            raise CommandError(f"Usuário '{usuario}' não existe. Peça para ele criar a conta no site.")
        call_command("configurar_papeis", stdout=self.stdout)
        grupo = Group.objects.get(name="Analista")
        if remover:
            u.groups.remove(grupo)
            if not u.is_superuser:
                u.is_staff = False
        else:
            u.groups.add(grupo)
            u.is_staff = True              # necessário para entrar em /admin/
        u.save()
        acao = "removido do" if remover else "adicionado ao"
        self.stdout.write(self.style.SUCCESS(f"{u.username} {acao} grupo Analista."))
