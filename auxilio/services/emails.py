"""Rascunhos de e-mail. O sistema não envia, só monta o texto para você disparar."""

from types import SimpleNamespace
from urllib.parse import quote

from .. import calculo
from ..calculo import brl


def mailto(para, assunto, corpo, cc=""):
    partes = [f"subject={quote(assunto)}", f"body={quote(corpo)}"]
    if cc:
        partes.append(f"cc={quote(cc)}")
    return f"mailto:{para}?" + "&".join(partes)


def gmail(para, assunto, corpo, cc=""):
    """Abre a escrita de e-mail do Gmail no navegador, já preenchida (o mailto abre o programa padrão do Windows)."""
    partes = ["view=cm", "fs=1", f"to={quote(para or '')}", f"su={quote(assunto)}", f"body={quote(corpo)}"]
    if cc:
        partes.append(f"cc={quote(cc)}")
    return "https://mail.google.com/mail/?" + "&".join(partes)


def _quem(sol):
    """Assinatura dos e-mails: quem preenche o pedido, com matrícula ou SIAPE."""
    p = sol.perfil
    if p.papel == "servidor":
        return f"{p.nome_completo}\nSIAPE {p.siape}"
    return f"{p.nome_completo}\nMatrícula {p.matricula}"


def nada_consta(sol, estudante=None):
    """Cada estudante pede o próprio Nada Consta (item 7.1.7)."""
    c, e = sol.campus, sol.edital
    estudante = estudante or sol.participantes.first()
    p = SimpleNamespace(
        nome_completo=estudante.nome_exibido if estudante else sol.perfil.nome_completo,
        curso=(estudante.curso_exibido if estudante else sol.perfil.curso) or "",
        matricula=estudante.matricula_exibida if estudante else sol.perfil.matricula)
    assunto = f"Solicitação de Nada Consta – Edital nº {e.numero} – {p.nome_completo}"
    corpo = (
        "Prezados(as),\n\n"
        "Solicito, por gentileza, a emissão do Nada Consta referente à inexistência de pendências em "
        "prestação de contas de projetos custeados com recursos da Ação 2994, conforme exigido no item "
        f"7.1.7 do Edital nº {e.numero} – {e.titulo}.\n\n"
        f"Nome: {p.nome_completo}\n"
        f"Curso: {p.curso.title()}\n"
        f"Campus: IFAM - {c.nome}\n"
        f"Matrícula: {p.matricula}\n\n"
        "O documento será utilizado para compor a documentação necessária à submissão da proposta ao "
        "referido edital.\n\n"
        "Desde já, agradeço a atenção e fico à disposição caso seja necessária alguma informação adicional.\n\n"
        f"Atenciosamente,\n{p.nome_completo}\nMatrícula {p.matricula}"
    )
    return {"para": c.email_servico_social, "cc": c.email_servico_social_cc, "assunto": assunto,
            "corpo": corpo, "mailto": mailto(c.email_servico_social, assunto, corpo, c.email_servico_social_cc), "gmail": gmail(c.email_servico_social, assunto, corpo, c.email_servico_social_cc)}


def diretor(sol):
    """Pedido de assinatura do Anexo I ao Diretor-Geral, que assina sobre a assinatura do estudante."""
    p, c, e = sol.perfil, sol.campus, sol.edital
    r = calculo.resumo(sol)
    prop = sol.proponente
    assunto = f"Assinatura do Anexo I – Edital nº {e.numero} – {prop['nome']}"
    ja = "já com a minha assinatura" if prop["e_quem_preenche"] else f"já com a assinatura do(a) proponente, {prop['nome']}"
    corpo = (
        f"Prezado(a) {c.diretor_nome},\n\n"
        f"Encaminho o Anexo I (Formulário de Submissão) do Edital nº {e.numero} – {e.titulo}, "
        f"{ja}, para a sua assinatura como Diretor(a)-Geral. "
        f"Evento {sol.evento_nome} ({sol.cidade}/{sol.uf}), valor total solicitado {brl(r['total'])}.\n\n"
        "Peço que assine o PDF no mesmo arquivo, mantendo a assinatura já existente, e me devolva por e-mail "
        "para eu enviar a documentação ao Protocolo.\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": c.email_diretor, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(c.email_diretor, assunto, corpo), "gmail": gmail(c.email_diretor, assunto, corpo)}


def protocolo(sol):
    p, c, e = sol.perfil, sol.campus, sol.edital
    r = calculo.resumo(sol)
    prop = sol.proponente
    ident = f"Mat. {p.matricula}" if not prop["servidor"] else f"SIAPE {prop['siape']}"
    assunto = f"Requerimento – Inscrição no Edital nº {e.numero} – {prop['nome']} – {ident}"
    corpo = (
        "Prezados(as) do Protocolo,\n\n"
        f"Encaminho o requerimento padrão e a documentação para inscrição no Edital nº {e.numero} – {e.titulo}, "
        f"para participação no evento {sol.evento_nome} ({sol.cidade}/{sol.uf}).\n\n"
        f"Proponente: {prop['nome']}. Estudantes: {r['n']}. Valor total solicitado: {brl(r['total'])}.\n\n"
        "Segue em anexo, em PDF único, a documentação completa e já assinada "
        f"({'requerimento, ' if not prop['servidor'] else ''}Anexo I com a assinatura do Diretor-Geral e Anexo II). "
        "Peço que o processo seja encaminhado ao DAES/PROEN.\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": c.email_protocolo, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(c.email_protocolo, assunto, corpo), "gmail": gmail(c.email_protocolo, assunto, corpo)}


def prestacao(sol):
    """Item 10.3: o relatório e as comprovações vão para o processo original no SIPAC, pelo Protocolo."""
    p, c, e = sol.perfil, sol.campus, sol.edital
    processo = sol.numero_processo or "(informe o número do processo)"
    assunto = f"Prestação de contas – Processo nº {processo} – {sol.proponente['nome']}"
    corpo = (
        "Prezados(as) do Protocolo,\n\n"
        f"Encaminho a prestação de contas do Auxílio Estudantil para Participação em Eventos Acadêmicos "
        f"(Edital nº {e.numero}), referente ao evento {sol.evento_nome}, e peço a juntada ao processo nº {processo}, "
        "conforme o item 10.3 do edital.\n\n"
        "Segue em anexo, em PDF único, o Relatório de Prestação de Contas (Anexo VI) assinado e as comprovações "
        "da participação.\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": c.email_protocolo, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(c.email_protocolo, assunto, corpo), "gmail": gmail(c.email_protocolo, assunto, corpo)}


def _quem_desiste(estudante, por_si):
    if estudante is None or por_si:
        return "não poderei participar", "não participei"
    ident = f"o(a) estudante {estudante.nome_exibido} (matrícula {estudante.matricula_exibida})"
    return f"{ident} não poderá participar", f"{ident} não participou"


def desistencia(sol, estudante=None, por_si=False):
    """Item 10.4.1, antes do pagamento: pedido de suspensão à Direção-Geral (do pedido ou de um estudante)."""
    c, e = sol.campus, sol.edital
    processo = f" (processo nº {sol.numero_processo})" if sol.numero_processo else ""
    vai, _ = _quem_desiste(estudante, por_si)
    motivo = estudante.desistencia_motivo if estudante else sol.desistencia_motivo
    alvo = f" referente a {estudante.nome_exibido}" if estudante else ""
    assunto = f"Desistência do auxílio para evento – {estudante.nome_exibido if estudante else sol.proponente['nome']}"
    corpo = (
        f"Prezado(a) {c.diretor_nome},\n\n"
        f"Comunico que {vai} do evento {sol.evento_nome} ({sol.cidade}/{sol.uf}) e solicito a "
        f"suspensão do pagamento do auxílio do Edital nº {e.numero}{processo}{alvo}, conforme o item 10.4.1.\n\n"
        f"Motivo: {motivo}\n\n"
        f"Atenciosamente,\n{estudante.nome_exibido if por_si and estudante else _quem(sol)}"
    )
    return {"para": c.email_diretor, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(c.email_diretor, assunto, corpo), "gmail": gmail(c.email_diretor, assunto, corpo)}


def devolucao(sol, com_comprovante=False, estudante=None, por_si=False):
    """Item 10.4.2, depois do pagamento: pedido da GRU e, depois, envio do comprovante à Direção-Geral.
    Com estudante, devolve só a parte dele (valor único por aluno, item 4.3)."""
    c, e = sol.campus, sol.edital
    r = calculo.resumo(sol)
    valor = r["por_aluno"]["total"] if estudante else r["total_ativos"]
    nome = estudante.nome_exibido if estudante else sol.proponente["nome"]
    processo = f" (processo nº {sol.numero_processo})" if sol.numero_processo else ""
    _, foi = _quem_desiste(estudante, por_si)
    motivo = estudante.desistencia_motivo if estudante else sol.desistencia_motivo
    if com_comprovante:
        assunto = f"Comprovante de devolução por GRU – {nome}"
        texto = (f"Encaminho em anexo o comprovante de pagamento da GRU referente à devolução do auxílio do "
                 f"Edital nº {e.numero}{processo}{f' de {nome}' if estudante else ''}, conforme o item 10.4.2.")
    else:
        assunto = f"Devolução do auxílio para evento – {nome}"
        texto = (f"Comunico que {foi} do evento {sol.evento_nome} ({sol.cidade}/{sol.uf}). Como o auxílio "
                 f"do Edital nº {e.numero}{processo} já foi pago, solicito a emissão da GRU para devolução do valor "
                 f"de {brl(valor)}, conforme o item 10.4.2.\n\nMotivo: {motivo}")
    assina = estudante.nome_exibido if por_si and estudante else _quem(sol)
    corpo = f"Prezado(a) {c.diretor_nome},\n\n{texto}\n\nAtenciosamente,\n{assina}"
    return {"para": c.email_diretor, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(c.email_diretor, assunto, corpo), "gmail": gmail(c.email_diretor, assunto, corpo)}


def convite(sol, estudante, link):
    """Convite para o colega entrar no pedido e enviar os próprios documentos."""
    e = sol.edital
    assunto = f"Convite para o pedido de auxílio – {sol.evento_nome or 'evento acadêmico'}"
    corpo = (
        f"Olá, {estudante.nome_exibido or 'colega'}.\n\n"
        f"Incluí você no pedido do Auxílio Estudantil para Participação em Eventos Acadêmicos (Edital nº {e.numero}) "
        f"para o evento {sol.evento_nome or '(evento a definir)'}.\n\n"
        "Entre pelo link abaixo com o seu e-mail institucional (ou crie a conta) para confirmar as declarações e "
        "enviar os seus documentos: histórico, RG e CPF e o Nada Consta.\n\n"
        f"{link}\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": estudante.email_exibido, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(estudante.email_exibido, assunto, corpo), "gmail": gmail(estudante.email_exibido, assunto, corpo)}


def proponente_servidor(sol):
    """Quando um estudante preenche em nome de um servidor, o servidor assina como proponente."""
    e, prop = sol.edital, sol.proponente
    docs = "o Anexo I e o Anexo II"
    assunto = f"Assinatura como proponente – Edital nº {e.numero} – {sol.evento_nome}"
    corpo = (
        f"Prezado(a) {prop['nome']},\n\n"
        f"Preparei o pedido do Auxílio Estudantil para Participação em Eventos Acadêmicos (Edital nº {e.numero}) "
        f"para o evento {sol.evento_nome}, com você como proponente. Encaminho {docs} para a sua assinatura "
        "(gov.br ou assinador institucional). Depois de assinados, por favor me devolva os PDFs por e-mail.\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": prop["email"], "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(prop["email"], assunto, corpo), "gmail": gmail(prop["email"], assunto, corpo)}


def servidor_responsavel(sol):
    """Pedido de assinatura do Anexo VI ao coordenador, que atesta a participação."""
    p, e = sol.perfil, sol.edital
    assunto = f"Assinatura do relatório de prestação de contas (Anexo VI) – {sol.proponente['nome']}"
    corpo = (
        f"Prezado(a) {sol.coordenador_nome or 'professor(a)'},\n\n"
        f"Encaminho em anexo o Relatório de Prestação de Contas (Anexo VI) do Edital nº {e.numero}, referente à "
        f"participação no evento {sol.evento_nome}. Peço que confira e assine como coordenador(a) e me devolva o PDF "
        "assinado por e-mail, para concluir a prestação de contas.\n\n"
        f"Atenciosamente,\n{_quem(sol)}"
    )
    return {"para": sol.coordenador_email, "cc": "", "assunto": assunto, "corpo": corpo,
            "mailto": mailto(sol.coordenador_email, assunto, corpo), "gmail": gmail(sol.coordenador_email, assunto, corpo)}
