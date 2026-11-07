"""Cria o estudante beneficiário das solicitações já existentes e liga a ele os documentos pessoais."""

import secrets

from django.db import migrations

PESSOAIS = ["historico", "identidade", "nada_consta", "autorizacao_responsavel", "relatorio_pedagogico"]


def criar(apps, schema_editor):
    Solicitacao = apps.get_model("auxilio", "Solicitacao")
    Participante = apps.get_model("auxilio", "Participante")
    Documento = apps.get_model("auxilio", "Documento")
    NadaConsta = apps.get_model("auxilio", "NadaConsta")
    Perfil = apps.get_model("auxilio", "Perfil")
    for sol in Solicitacao.objects.all():
        if Participante.objects.filter(solicitacao=sol).exists():
            continue
        perfil = Perfil.objects.filter(user_id=sol.aluno_id).first()
        nc = NadaConsta.objects.filter(solicitacao=sol).first()
        p = Participante.objects.create(
            solicitacao=sol, usuario_id=sol.aluno_id, ordem=1,
            nome_completo=perfil.nome_completo if perfil else "", matricula=perfil.matricula if perfil else "",
            curso=perfil.curso if perfil else "", nascimento=perfil.nascimento if perfil else None,
            convite_token=secrets.token_urlsafe(16),
            decl_paes=sol.decl_paes, decl_frequencia=sol.decl_frequencia, decl_sem_pendencias=sol.decl_sem_pendencias,
            reprovacoes_acima_limite=sol.reprovacoes_acima_limite,
            nc_estado=nc.estado if nc else "nao_solicitado", nc_solicitado_em=nc.solicitado_em if nc else None)
        Documento.objects.filter(solicitacao=sol, tipo__in=PESSOAIS).update(participante=p)


class Migration(migrations.Migration):
    dependencies = [("auxilio", "0005_grupos_e_proponente")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
