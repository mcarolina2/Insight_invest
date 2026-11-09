"""
Cria o superusuário a partir de variáveis de ambiente, se ainda não existir.
Roda no build do Render (que não tem terminal no plano gratuito).

Variáveis: DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD
"""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Cria o superusuário definido nas variáveis de ambiente (idempotente)."

    def handle(self, *args, **options):
        from users.models import User
        usuario = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        senha = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        if not usuario or not senha:
            self.stdout.write("Variáveis do superusuário não definidas; nada a fazer.")
            return
        if User.objects.filter(username=usuario).exists():
            self.stdout.write(f"Superusuário '{usuario}' já existe.")
            return
        User.objects.create_superuser(usuario, os.environ.get("DJANGO_SUPERUSER_EMAIL", ""), senha)
        self.stdout.write(self.style.SUCCESS(f"Superusuário '{usuario}' criado."))
