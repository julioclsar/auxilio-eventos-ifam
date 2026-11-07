"""Cadastra o campus e o edital. Rode uma vez na instalação (pode repetir sem problema).

    python manage.py configurar

Os valores abaixo podem ser mudados depois no painel /admin/ (Campus e Edital).
"""

from django.core.management.base import BaseCommand

from auxilio import regras
from auxilio.models import Campus, Edital


class Command(BaseCommand):
    help = "Cadastra o campus CMZL e o Edital 03/2026 (sem criar dados de exemplo)."

    def add_arguments(self, parser):
        parser.add_argument("--diretor", default="David Washington Freitas Lima")
        parser.add_argument("--email-diretor", default="david.lima@ifam.edu.br")
        parser.add_argument("--email-protocolo", default="protocolo.cmzl@ifam.edu.br")
        parser.add_argument("--email-servico-social", default="servicosocial.cmzl@ifam.edu.br")
        parser.add_argument("--email-servico-social-cc", default="jose.nascimento@ifam.edu.br")

    def handle(self, *args, **o):
        campus, novo = Campus.objects.update_or_create(
            sigla="CMZL",
            defaults=dict(nome="Campus Manaus Zona Leste", municipio="Manaus", uf="AM",
                          diretor_nome=o["diretor"], email_diretor=o["email_diretor"],
                          email_protocolo=o["email_protocolo"],
                          email_servico_social=o["email_servico_social"],
                          email_servico_social_cc=o["email_servico_social_cc"]))
        Edital.objects.get_or_create(
            numero=regras.EDITAL_03_2026["numero"],
            defaults=dict(titulo=regras.EDITAL_03_2026["titulo"], regras=regras.EDITAL_03_2026))
        self.stdout.write(self.style.SUCCESS(
            f"{'Campus criado' if novo else 'Campus atualizado'}: {campus}. Edital {regras.EDITAL_03_2026['numero']} pronto."))
