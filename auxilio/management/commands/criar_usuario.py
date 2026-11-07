"""Cria um usuário de apoio (Diretor-Geral ou analista). Estudantes se cadastram sozinhos em /cadastro/.

    python manage.py criar_usuario diretor "Nome Completo" email@ifam.edu.br
    python manage.py criar_usuario analista "Nome Completo" email@ifam.edu.br
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from auxilio.models import Campus, Perfil


class Command(BaseCommand):
    help = "Cria um Diretor-Geral ou analista, pedindo a senha no terminal."

    def add_arguments(self, parser):
        parser.add_argument("papel", choices=["diretor", "analista"])
        parser.add_argument("nome")
        parser.add_argument("email")

    def handle(self, *args, **o):
        campus = Campus.objects.first()
        if not campus:
            raise CommandError("Rode antes: python manage.py configurar")
        if User.objects.filter(username=o["email"]).exists():
            raise CommandError("Já existe um usuário com esse e-mail.")
        from getpass import getpass
        senha = getpass("Senha: ")
        if senha != getpass("Repita a senha: "):
            raise CommandError("As senhas não conferem.")
        u = User.objects.create_user(o["email"], o["email"], senha, first_name=o["nome"].split()[0])
        Perfil.objects.create(user=u, papel=o["papel"], campus=campus, nome_completo=o["nome"])
        self.stdout.write(self.style.SUCCESS(f"Usuário criado. Entre com {o['email']}."))
