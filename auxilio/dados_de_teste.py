"""Dados fictícios usados SOMENTE pelos testes automáticos (python manage.py test).

Não é um comando do sistema e não deve ser usado em produção.
"""

import io
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from auxilio import regras
from auxilio.models import Campus, Edital, Orcamento, Participante, Perfil, Solicitacao
from auxilio.services import fluxo


def pdf_demo(titulo, linhas=()):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 760, titulo)
    c.setFont("Helvetica", 11)
    y = 730
    for linha in linhas:
        c.drawString(60, y, linha)
        y -= 18
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, 60, "DOCUMENTO FICTÍCIO DE DEMONSTRAÇÃO. Substitua pelo arquivo real.")
    c.save()
    return buf.getvalue()


def criar(docs=True):
    if True:
        campus, _ = Campus.objects.get_or_create(
            sigla="CMZL",
            defaults=dict(nome="Campus Manaus Zona Leste", municipio="Manaus", uf="AM",
                          diretor_nome="David Washington Freitas Lima",
                          email_diretor="david.lima@ifam.edu.br",
                          email_protocolo="protocolo.cmzl@ifam.edu.br",   # CONFIRMAR com o campus
                          email_servico_social="servicosocial.cmzl@ifam.edu.br"))
        edital, _ = Edital.objects.get_or_create(
            numero=regras.EDITAL_03_2026["numero"],
            defaults=dict(titulo=regras.EDITAL_03_2026["titulo"], regras=regras.EDITAL_03_2026))

        def usuario(login, nome, papel, email, **perfil):
            u, novo = User.objects.get_or_create(username=login, defaults=dict(email=email, first_name=nome.split()[0]))
            u.email = email
            u.set_password("demo123")
            u.save()
            Perfil.objects.update_or_create(user=u, defaults=dict(papel=papel, campus=campus, nome_completo=nome, **perfil))
            return u

        aluno = usuario(
            "aluno", "Maria Exemplo da Silva", "aluno", "2024000000@ifam.edu.br",
            matricula="2024000000", cpf="123.456.789-09", rg="1234567-8", rg_uf="AM",
            rg_expedicao=date(2018, 2, 27), nascimento=date(2005, 6, 5),
            endereco="Rua das Flores", numero="100", bairro="Gilberto Mestrinho",
            telefone="(92) 3333-4444", celular="(92) 99123-4567",
            curso="Bacharelado em Engenharia de Software", serie_periodo="6P", turno="Vesp.")
        usuario("diretor", "David Washington Freitas Lima", "diretor", "david.lima@ifam.edu.br")
        usuario("analista", "Analista Exemplo do DAES", "analista", "daes@ifam.edu.br")

        if Solicitacao.objects.filter(aluno=aluno).exists():
                sol = Solicitacao.objects.filter(aluno=aluno).first()
        else:
            sol = Solicitacao.objects.create(
                aluno=aluno, edital=edital,
                evento_nome="Brazilian Conference on Intelligent Systems (BRACIS 2026)",
                natureza="cientifico", cidade="Cuiabá", uf="MT",
                evento_inicio=date(2026, 10, 19), evento_fim=date(2026, 10, 22),
                viagem_ida=date(2026, 10, 18), viagem_volta=date(2026, 10, 23),
                tipo_participacao="Apresentador de trabalho científico.",
                valor_inscricao=Decimal("272.00"),
                justificativa_prazo="Demonstração do caso real, enviado antes do prazo na prática.",
                decl_paes=True, decl_frequencia=True, decl_sem_pendencias=True)
            dados = [
                ("traslado", "Manaus - Cuiabá", [("Azul", "4848.72"), ("LATAM Standard", "4007.31"), ("LATAM Light", "3917.31")]),
                ("traslado", "Cuiabá - Manaus", [("Azul", "2500.38"), ("LATAM Standard", "3938.75"), ("LATAM Light", "3848.75")]),
                ("hospedagem", "Cuiabá (5 diárias)", [("Hotel Gran Odara", "3614.00"), ("Hotel Deville Prime", "2874.00"), ("Amazon Aeroporto Hotel", "2625.00")]),
            ]
            for cat, trecho, itens in dados:
                for i, (fonte, valor) in enumerate(itens, start=1):
                    o = Orcamento(solicitacao=sol, categoria=cat, trecho=trecho, ordem=i, fonte=fonte, valor=Decimal(valor))
                    o.comprovante.save(f"comprovante_{cat}_{i}.pdf", ContentFile(pdf_demo(
                        f"Comprovante de pesquisa ({cat})", [f"{trecho}", f"{fonte}", f"R$ {valor}"])), save=False)
                    o.save()
            p = aluno.perfil
            Participante.objects.create(solicitacao=sol, usuario=aluno, ordem=1, nome_completo=p.nome_completo,
                                        matricula=p.matricula, email=aluno.email, curso=p.curso, nascimento=p.nascimento,
                                        decl_paes=True, decl_frequencia=True, decl_sem_pendencias=True)
            fluxo.registrar(sol, aluno, "Solicitação de demonstração criada.")

        if docs:
            for tipo, titulo in [("folder_evento", "Folder do evento"), ("aceite", "Carta de aceite do trabalho"),
                                 ("historico", "Histórico escolar"), ("identidade", "RG e CPF"),
                                 ("nada_consta", "Certidão de Nada Consta"), ("recibo_inscricao", "Recibo da inscrição")]:
                if not sol.documentos.filter(tipo=tipo, ativo=True).exists():
                    e = sol.participantes.first() if tipo in ("historico", "identidade", "nada_consta") else None
                    fluxo._novo_documento(sol, tipo, "aluno", pdf_demo(titulo), f"{tipo}.pdf", participante=e)
