"""Workflow da solicitação: geração, assinaturas, estados e histórico."""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .. import calculo, regras, validacao
from ..models import Assinatura, Documento, Historico
from . import assinatura as insp
from . import documentos as geradores

EXTENSOES = {".pdf", ".jpg", ".jpeg", ".png"}
LIMITE_MB = 20


def registrar(sol, user, texto, de="", para=""):
    Historico.objects.create(solicitacao=sol, por=user if getattr(user, "pk", None) else None,
                             texto=texto, de_status=de, para_status=para)


def hash_dados(sol):
    p = sol.perfil
    r = calculo.resumo(sol)
    dados = {
        "evento": [sol.evento_nome, sol.natureza, sol.presencial, sol.cidade, sol.uf, sol.evento_inicio,
                   sol.evento_fim, sol.viagem_ida, sol.viagem_volta, sol.tipo_participacao],
        "pede": [sol.pede_inscricao, sol.pede_traslado, sol.pede_alimentacao, sol.pede_hospedagem],
        "valores": [r["inscricao"], r["traslado"], r["alimentacao"], r["hospedagem"], r["total"]],
        # CPF, RG e endereço só entram no requerimento, que só existe quando o proponente é estudante.
        "perfil": [p.nome_completo, p.matricula, p.cpf, p.rg, p.rg_uf, p.rg_expedicao, p.nascimento, p.endereco,
                   p.bairro, p.telefone, p.celular, p.curso, p.serie_periodo, p.turno, sol.aluno.email]
                  if "requerimento" in validacao.documentos_gerados(sol) else [],
        "proponente": sorted(sol.proponente.items()),
        "estudantes": [[e.nome_exibido, e.matricula_exibida] for e in sol.participantes.all()],
        "campus": [sol.campus.nome, sol.campus.diretor_nome],
        "orcamentos": [[o.categoria, o.trecho, o.ordem, o.fonte, o.valor]
                       for o in sol.orcamentos.order_by("categoria", "trecho", "ordem", "id")],
    }
    return hashlib.sha256(json.dumps(dados, default=str, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------
FASES_AUTOMATICAS = {"rascunho", "ag_aluno", "ag_diretor", "incompleta", "pronta"}


def atualizar_status(sol, user=None):
    """Recalcula o estado enquanto a solicitação ainda está sendo montada."""
    if sol.status not in FASES_AUTOMATICAS:
        return sol.status
    if not sol.documentos_gerados_em or validacao.desatualizado(sol):
        novo = "rascunho"
    else:
        pend = sol.assinaturas.exclude(estado="assinado")
        if pend.filter(papel="aluno").exists():
            novo = "ag_aluno"
        elif pend.filter(papel="diretor").exists():
            novo = "ag_diretor"
        elif not validacao.pode_enviar(sol):
            novo = "incompleta"
        else:
            novo = "pronta"
    if novo != sol.status:
        antigo = sol.status
        sol.status = novo
        sol.save(update_fields=["status"])
        registrar(sol, user, f"Status: {regras.STATUS_ROTULO[novo]}", antigo, novo)
    return sol.status


def mudar_status(sol, novo, user, texto=""):
    permitidos = regras.TRANSICOES.get(sol.status, [])
    if novo not in permitidos:
        raise ValidationError(f"Transição não permitida: {sol.status_rotulo} para {regras.STATUS_ROTULO.get(novo, novo)}.")
    antigo = sol.status
    sol.status = novo
    sol.save(update_fields=["status"])
    registrar(sol, user, texto or f"Status: {regras.STATUS_ROTULO[novo]}", antigo, novo)


# ---------------------------------------------------------------------------
# Geração dos documentos
# ---------------------------------------------------------------------------
def _novo_documento(sol, tipo, origem, conteudo: bytes, nome, verificacao=None, substituir=True, participante=None):
    mesmos = sol.documentos.filter(tipo=tipo, participante=participante)
    anterior = mesmos.order_by("-versao").first()
    versao = (anterior.versao + 1) if anterior else 1
    if substituir:
        mesmos.filter(ativo=True).update(ativo=False)
    doc = Documento(solicitacao=sol, tipo=tipo, origem=origem, versao=versao, nome_original=nome,
                    verificacao=verificacao or {}, participante=participante)
    doc.arquivo.save(nome, ContentFile(conteudo), save=False)
    doc.save()
    return doc


@transaction.atomic
def gerar_documentos(sol, user):
    if not validacao.pode_gerar(sol):
        raise ValidationError("Corrija as pendências de dados e orçamentos antes de gerar os documentos.")
    tipos = validacao.documentos_gerados(sol)
    hoje = timezone.localdate()
    for tipo in tipos:
        pdf = geradores.GERADORES[tipo](sol, hoje)
        _novo_documento(sol, tipo, "gerado", pdf, f"{tipo}_{sol.codigo}.pdf")
    if "anexo3" not in tipos:
        sol.documentos.filter(tipo="anexo3", ativo=True).update(ativo=False)

    # Novos documentos invalidam assinaturas anteriores (item 36 do projeto).
    sol.assinaturas.all().delete()
    for tipo in tipos:
        for ordem, papel in enumerate(regras.ASSINATURAS.get(tipo, []), start=1):
            Assinatura.objects.create(solicitacao=sol, tipo_doc=tipo, papel=papel, ordem=ordem)

    sol.dados_hash = hash_dados(sol)
    sol.documentos_gerados_em = timezone.now()
    if sol.status not in FASES_AUTOMATICAS:
        sol.status = "rascunho"
    sol.save()
    registrar(sol, user, "Documentos gerados: " + ", ".join(regras.DOC_TIPOS[t].split(",")[0] for t in tipos))
    atualizar_status(sol, user)


@transaction.atomic
def gerar_prestacao(sol, user):
    """Gera o Anexo VI (relatório de prestação de contas) e abre a assinatura do coordenador."""
    faltam = [r for r, v in [("os resultados obtidos", sol.resultados_texto),
                             ("o nome do(a) coordenador(a)", sol.coordenador_nome),
                             ("o SIAPE do(a) coordenador(a)", sol.coordenador_siape),
                             ("o e-mail do(a) coordenador(a)", sol.coordenador_email)] if not v.strip()]
    if faltam:
        raise ValidationError("Preencha " + " e ".join(faltam) + " antes de gerar o Anexo VI.")
    pdf = geradores.gerar_anexo6(sol, timezone.localdate())
    _novo_documento(sol, "anexo6", "gerado", pdf, f"anexo6_{sol.codigo}.pdf")
    sol.assinaturas.filter(tipo_doc="anexo6").delete()
    for ordem, papel in enumerate(regras.ASSINATURAS["anexo6"], start=1):
        Assinatura.objects.create(solicitacao=sol, tipo_doc="anexo6", papel=papel, ordem=ordem)
    registrar(sol, user, "Anexo VI gerado para a prestação de contas.")


def prestacao_pendencias(sol):
    """O que falta para enviar a prestação de contas (Anexo VI e comprovações)."""
    pend = []
    ass = sol.assinaturas.filter(tipo_doc="anexo6").first()
    if not sol.resultados_texto.strip():
        pend.append("Escreva os resultados obtidos com a participação.")
    if not ass:
        pend.append("Gere o Anexo VI.")
    else:
        for a in sol.assinaturas.filter(tipo_doc="anexo6").exclude(estado="assinado"):
            pend.append("Envie o Anexo VI assinado pelo(a) coordenador(a).")
    tem = lambda t: sol.documentos.filter(tipo=t, ativo=True).exists()
    if calculo.efetivo(sol, "traslado") and not tem("prest_embarque"):
        pend.append("Anexe os cartões de embarque.")
    if calculo.efetivo(sol, "hospedagem") and not tem("prest_hospedagem"):
        pend.append("Anexe o recibo da hospedagem.")
    if not tem("prest_certificado"):
        pend.append("Anexe o certificado de participação.")
    return pend


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
def validar_arquivo(arquivo, so_pdf=False):
    nome = arquivo.name.lower()
    ext = "." + nome.rsplit(".", 1)[-1] if "." in nome else ""
    permitidas = {".pdf"} if so_pdf else EXTENSOES
    if ext not in permitidas:
        raise ValidationError("Envie " + ("um PDF." if so_pdf else "PDF, JPG ou PNG."))
    if arquivo.size > LIMITE_MB * 1024 * 1024:
        raise ValidationError(f"O arquivo passa de {LIMITE_MB} MB.")
    return ext


@transaction.atomic
def anexar_documento(sol, user, tipo, arquivo, acumular=False, participante=None):
    """Guarda um arquivo enviado. Com acumular, soma ao que já existe (ex.: embarque da ida e da volta).
    Documentos pessoais (histórico, RG/CPF, Nada Consta...) pertencem a um estudante do pedido."""
    validar_arquivo(arquivo)
    if tipo in regras.DOCS_PESSOAIS and participante is None:
        participante = sol.participantes.first()
        if participante is None:
            raise ValidationError("Inclua o estudante no pedido antes de anexar documentos pessoais.")
    dados = arquivo.read()
    verificacao = {}
    if tipo == "nada_consta" and arquivo.name.lower().endswith(".pdf"):
        v = insp.inspecionar(dados)
        verificacao = {**v, "resumo": insp.resumo_texto(v)}
    if tipo not in regras.DOCS_PESSOAIS and tipo != "gru_comprovante":
        participante = None
    doc = _novo_documento(sol, tipo, "aluno", dados, arquivo.name, verificacao, substituir=not acumular,
                          participante=participante)
    if tipo == "nada_consta" and participante is not None:
        participante.nc_estado = "recebido"
        participante.save(update_fields=["nc_estado"])
    quem = f" ({participante.nome_exibido})" if participante is not None and sol.participantes.count() > 1 else ""
    registrar(sol, user, f"Enviado: {regras.DOC_TIPOS.get(tipo, tipo)}{quem}")
    atualizar_status(sol, user)
    return doc


@transaction.atomic
def receber_assinado(sol, user, tipo, papel, arquivo):
    """Registra o PDF assinado fora do sistema (gov.br, Serpro) e confere a assinatura."""
    validar_arquivo(arquivo, so_pdf=True)
    ass = sol.assinaturas.filter(tipo_doc=tipo, papel=papel).first()
    if not ass:
        raise ValidationError("Este documento não espera a assinatura informada.")
    if papel != "aluno":
        previa = sol.assinaturas.filter(tipo_doc=tipo, papel="aluno").first()
        if previa and previa.estado != "assinado":
            raise ValidationError(f"O estudante precisa assinar antes do(a) {regras.PAPEL_ROTULO[papel]}.")
    esperado = {"aluno": sol.proponente["nome"], "diretor": sol.campus.diretor_nome,
                "servidor": sol.coordenador_nome}[papel]
    dados = arquivo.read()
    v = insp.inspecionar(dados, [esperado])
    v["resumo"] = insp.resumo_texto(v)
    if not v["pdf_valido"]:
        raise ValidationError("Não foi possível ler o PDF enviado.")
    if settings.ASSINATURA_ESTRITA and (v["assinaturas"] == 0 or v["nomes_ok"] is False):
        raise ValidationError(v["resumo"])
    doc = _novo_documento(sol, tipo, papel, dados, arquivo.name, v)
    ass.estado = "assinado"
    ass.documento = doc
    ass.assinado_em = timezone.now()
    ass.nota = v["resumo"]
    ass.save()
    # Quando o diretor devolveu, o estudante reenvia e o diretor volta a ficar pendente.
    registrar(sol, user, f"{regras.PAPEL_ROTULO[papel]} assinou {regras.DOC_TIPOS[tipo].split(',')[0]} ({v['resumo']})")
    atualizar_status(sol, user)
    return doc


@transaction.atomic
def devolver(sol, user, tipo, nota):
    """O Diretor-Geral devolve o documento para correção do estudante."""
    ass = sol.assinaturas.filter(tipo_doc=tipo, papel="aluno").first()
    if ass:
        ass.estado = "devolvido"
        ass.nota = nota[:300]
        ass.save()
    sol.assinaturas.filter(tipo_doc=tipo, papel="diretor").update(estado="pendente", documento=None)
    registrar(sol, user, f"Devolvido para correção ({regras.DOC_TIPOS[tipo].split(',')[0]}): {nota}")
    atualizar_status(sol, user)


# ---------------------------------------------------------------------------
# Protocolo, decisão e prazos
# ---------------------------------------------------------------------------
def registrar_protocolo(sol, user, numero, data):
    if sol.status != "pronta":
        raise ValidationError("A solicitação ainda não está pronta para protocolo.")
    sol.numero_processo = numero
    sol.protocolada_em = data
    sol.save(update_fields=["numero_processo", "protocolada_em"])
    mudar_status(sol, "protocolada", user, f"Protocolada. Processo {numero or 'sem número informado'}.")


def dias_uteis_apos(data, n):
    d = data
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def prazo_recurso(sol):
    if sol.status == "indeferida" and sol.decisao_em:
        return dias_uteis_apos(timezone.localtime(sol.decisao_em).date(), sol.edital.regras["recurso_dias_uteis"])
    return None


def prazo_prestacao(sol):
    return validacao.prazo_prestacao(sol)


def interpor_recurso(sol, user, texto):
    """Itens 8.2 e 8.3: um único recurso, em até 2 dias úteis após a notificação do indeferimento."""
    texto = (texto or "").strip()
    if not texto:
        raise ValidationError("Escreva o texto do recurso.")
    prazo = prazo_recurso(sol)
    if prazo and timezone.localdate() > prazo:
        raise ValidationError(f"O prazo de recurso terminou em {prazo:%d/%m/%Y} (item 8.2).")
    sol.recurso_texto = texto[:2000]
    sol.save(update_fields=["recurso_texto"])
    mudar_status(sol, "em_recurso", user, "Recurso interposto pelo estudante.")


def abrir_prestacao(sol, user):
    """A prestação só abre depois do fim do evento (item 10.1)."""
    fim = sol.evento_fim or sol.viagem_volta
    if fim and timezone.localdate() < fim:
        raise ValidationError(f"O evento termina em {fim:%d/%m/%Y}. A prestação de contas abre a partir desse dia.")
    mudar_status(sol, "ag_prestacao", user, "Evento realizado. Prestação de contas aberta.")


@transaction.atomic
def desistir(sol, user, motivo):
    """Item 10.4: antes do pagamento, cancela e pede a suspensão. Depois, abre a devolução por GRU."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("Explique o motivo da desistência.")
    sol.desistencia_motivo = motivo[:1000]
    sol.desistencia_em = timezone.now()
    sol.save(update_fields=["desistencia_motivo", "desistencia_em"])
    if sol.status in ("paga", "ag_prestacao"):
        mudar_status(sol, "ag_devolucao", user, f"Desistência depois do pagamento. Devolução por GRU pendente. {motivo}")
    else:
        mudar_status(sol, "cancelada", user, f"Desistência. Pedido de suspensão do pagamento à Direção-Geral. {motivo}")


@transaction.atomic
def registrar_gru(sol, user, arquivo):
    if sol.status != "ag_devolucao":
        raise ValidationError("Não há devolução pendente nesta solicitação.")
    anexar_documento(sol, user, "gru_comprovante", arquivo)
    registrar(sol, user, "Comprovante da GRU enviado. Aguardando confirmação do DAES/Direção-Geral.")


@transaction.atomic
def confirmar_gru(sol, user):
    """A Direção-Geral (ou o DAES) confirma a devolução do pedido inteiro."""
    if not validacao.gru_do_pedido(sol):
        raise ValidationError("O comprovante da GRU ainda não foi enviado.")
    mudar_status(sol, "devolvida", user, "Devolução por GRU confirmada.")


ANTES_DO_PAGAMENTO = ("protocolada", "em_analise", "indeferida", "em_recurso", "deferida", "ag_pagamento")


@transaction.atomic
def desistir_estudante(sol, user, estudante, motivo):
    """Item 10.4 para um estudante do grupo: os outros seguem. Antes do pagamento, pede a suspensão da parte
    dele. Depois do pagamento, ele devolve a própria parte por GRU (e fica bloqueado até a confirmação)."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("Explique o motivo da desistência.")
    if estudante.desistiu:
        raise ValidationError("A desistência deste estudante já foi registrada.")
    if sol.status not in ANTES_DO_PAGAMENTO + ("paga", "ag_prestacao"):
        raise ValidationError("Neste momento do pedido não cabe desistência. Antes do envio ao Protocolo, "
                              "remova o estudante na aba Estudantes.")
    if sol.proponente_tipo == "estudante" and estudante.usuario_id == sol.aluno_id:
        raise ValidationError("O(A) estudante proponente não sai sozinho(a): sem ele(a) o pedido fica sem proponente. "
                              "Use a desistência do pedido inteiro.")
    ativos = [e for e in sol.participantes.all() if not e.desistiu]
    if len(ativos) <= 1:
        raise ValidationError("É o único estudante que ainda vai. Use a desistência do pedido inteiro.")
    estudante.desistencia_em = timezone.now()
    estudante.desistencia_motivo = motivo[:1000]
    estudante.devolucao_estado = "pendente" if sol.status in ("paga", "ag_prestacao") else "suspenso"
    estudante.save(update_fields=["desistencia_em", "desistencia_motivo", "devolucao_estado"])
    extra = " Devolução da parte dele(a) por GRU pendente." if estudante.devolucao_estado == "pendente" else \
        " Sai do valor a pagar. Pedido de suspensão da parte dele(a) à Direção-Geral."
    registrar(sol, user, f"Desistência de {estudante.nome_exibido}.{extra} {motivo}")


@transaction.atomic
def desfazer_desistencia(sol, user, estudante):
    """Para desistência registrada por engano, enquanto nenhuma GRU foi enviada ou confirmada."""
    if not estudante.desistiu:
        raise ValidationError("Este estudante não desistiu.")
    if estudante.devolucao_estado == "confirmada" or validacao.doc_ativo(sol, "gru_comprovante", estudante):
        raise ValidationError("A GRU deste estudante já foi enviada. Não dá mais para desfazer.")
    analista = getattr(getattr(user, "perfil", None), "papel", "") == "analista"
    if estudante.devolucao_estado == "pendente" and not analista:
        # Depois do pagamento existe uma dívida (item 10.5). Só o DAES pode apagá-la.
        raise ValidationError("Este estudante já recebeu e deve a própria parte por GRU. Só o DAES pode desfazer.")
    if sol.status in ("cancelada", "ag_devolucao", "devolvida", "concluida", "arquivada"):
        raise ValidationError("Neste momento do pedido não dá para desfazer a desistência.")
    if estudante.devolucao_estado == "suspenso" and sol.status not in ANTES_DO_PAGAMENTO:
        raise ValidationError("O pagamento já foi registrado sem este estudante. Fale com o DAES.")
    estudante.desistencia_em = None
    estudante.desistencia_motivo = ""
    estudante.devolucao_estado = ""
    estudante.save(update_fields=["desistencia_em", "desistencia_motivo", "devolucao_estado"])
    registrar(sol, user, f"Desistência de {estudante.nome_exibido} desfeita.")


@transaction.atomic
def cobrar_gru_estudante(sol, user, estudante):
    """Quem saiu antes do pagamento, mas recebeu mesmo assim, passa a dever a própria parte por GRU."""
    if estudante.devolucao_estado != "suspenso" or sol.status not in ("paga", "ag_prestacao", "prestacao_analise",
                                                                     "prestacao_pendencia"):
        raise ValidationError("Não se aplica a este estudante.")
    estudante.devolucao_estado = "pendente"
    estudante.save(update_fields=["devolucao_estado"])
    registrar(sol, user, f"{estudante.nome_exibido} recebeu apesar da desistência. Devolução por GRU pendente.")


@transaction.atomic
def registrar_gru_estudante(sol, user, estudante, arquivo):
    if estudante.devolucao_estado != "pendente":
        raise ValidationError("Não há devolução pendente para este estudante.")
    anexar_documento(sol, user, "gru_comprovante", arquivo, participante=estudante)
    registrar(sol, user, f"Comprovante da GRU de {estudante.nome_exibido} enviado. Aguardando confirmação.")


@transaction.atomic
def confirmar_gru_estudante(sol, user, estudante):
    if estudante.devolucao_estado != "pendente":
        raise ValidationError("Não há devolução pendente para este estudante.")
    if not validacao.doc_ativo(sol, "gru_comprovante", estudante):
        raise ValidationError("O comprovante da GRU deste estudante ainda não foi enviado.")
    estudante.devolucao_estado = "confirmada"
    estudante.save(update_fields=["devolucao_estado"])
    registrar(sol, user, f"Devolução por GRU de {estudante.nome_exibido} confirmada.")
