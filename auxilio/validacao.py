"""Checklist de documentos e motor de validação (item 24 do projeto).

Regra central: se existe pendência obrigatória, o envio é bloqueado.
"""

import re
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from . import calculo, regras
from .models import Documento, Solicitacao


def prazo_prestacao(sol):
    """Item 10.1: 30 dias depois do fim do evento."""
    base = sol.evento_fim or sol.viagem_volta
    if not base:
        return None
    return base + timedelta(days=sol.edital.regras["prestacao_dias"])


def prestacao_atrasada(sol, hoje=None):
    hoje = hoje or timezone.localdate()
    prazo = prazo_prestacao(sol)
    return bool(prazo and sol.status in ("paga", "ag_prestacao", "prestacao_pendencia") and hoje > prazo)


def solicitacoes_do_usuario(usuario):
    """Pedidos que a pessoa preencheu ou dos quais participa como estudante."""
    return Solicitacao.objects.filter(Q(aluno=usuario) | Q(participantes__usuario=usuario)).distinct()


ATIVOS_EXCLUIDOS = ["rascunho", "indeferida", "arquivada", "cancelada", "devolvida"]


def _deve_devolucao(s, participante):
    """Item 10.5: deve a GRU quem não participou e recebeu. Na desistência do pedido inteiro, devem só os
    estudantes que ainda estavam nele; quem saiu antes do pagamento ou já devolveu a própria parte não deve."""
    if participante is not None:
        if participante.devolucao_estado == "pendente":
            return True
        if participante.desistiu:
            return False
    return s.status == "ag_devolucao"


def normalizar_matricula(m):
    """2022.000.999, 02022000999 e 2022-000999 viram 2022000999."""
    return re.sub(r"\D", "", m or "").lstrip("0")


def bloqueios(aluno, exceto=None):
    """Situações que impedem nova solicitação: GRU não paga (10.5), para quem deve a devolução, e prestação de
    contas vencida (10.6), para o requisitante, que é quem preenche e responde pelo pedido."""
    msgs = []
    for s in solicitacoes_do_usuario(aluno).exclude(pk=getattr(exceto, "pk", None)):
        e = s.participantes.filter(usuario=aluno).first()
        if _deve_devolucao(s, e):
            msgs.append(f"A devolução por GRU da solicitação #{s.codigo} ainda não foi confirmada. Enquanto isso, "
                        "novas solicitações ficam bloqueadas (item 10.5).")
        elif s.aluno_id == aluno.pk and prestacao_atrasada(s):
            msgs.append(f"A prestação de contas da solicitação #{s.codigo} venceu em {prazo_prestacao(s):%d/%m/%Y}. "
                        "Enquanto não for entregue, novas solicitações ficam bloqueadas (item 10.6).")
    return msgs


def bloqueado_por_matricula(matricula, exceto=None):
    """Mesma regra, para estudante sem conta, pela matrícula (comparada só pelos dígitos)."""
    from .models import Participante
    alvo = normalizar_matricula(matricula)
    if not alvo:
        return False
    for e in Participante.objects.exclude(solicitacao=exceto).select_related("solicitacao"):
        if normalizar_matricula(e.matricula) == alvo and _deve_devolucao(e.solicitacao, e):
            return True
    for s in Solicitacao.objects.filter(status__in=["paga", "ag_prestacao", "prestacao_pendencia"]).exclude(
            pk=getattr(exceto, "pk", None)).select_related("aluno__perfil"):
        if normalizar_matricula(getattr(s.aluno.perfil, "matricula", "")) == alvo and prestacao_atrasada(s):
            return True
    return False


def em_outro_pedido_ativo(e, sol):
    """Item 5.5: o estudante já está, sem ter desistido, em outro pedido em andamento."""
    from .models import Participante
    alvo = normalizar_matricula(e.matricula_exibida)
    for o in Participante.objects.exclude(solicitacao=sol).exclude(solicitacao__status__in=ATIVOS_EXCLUIDOS).filter(
            desistencia_em__isnull=True):
        if (e.usuario_id and o.usuario_id == e.usuario_id) or (alvo and normalizar_matricula(o.matricula_exibida) == alvo):
            return True
    return False


def doc_ativo(sol, tipo, participante=None):
    qs = sol.documentos.filter(tipo=tipo, ativo=True)
    if participante is not None:
        qs = qs.filter(participante=participante)
    return qs.order_by("-criado_em").first()


def gru_do_pedido(sol):
    """Comprovante da GRU do pedido inteiro (as devoluções individuais ficam ligadas ao estudante)."""
    return sol.documentos.filter(tipo="gru_comprovante", ativo=True, participante__isnull=True) \
        .order_by("-criado_em").first()


def documentos_gerados(sol):
    """Documentos que o sistema gera para este pedido. O requerimento padrão é do estudante que protocola;
    quando o proponente é servidor, o pedido segue por processo no SIPAC aberto por ele (item 7.1)."""
    tipos = ["requerimento"] if sol.proponente_tipo == "estudante" else []
    tipos += ["anexo1", "anexo2"]
    if calculo.efetivo(sol, "traslado") or calculo.efetivo(sol, "hospedagem"):
        tipos.append("anexo3")
    return tipos


def checklist_estudante(sol, e):
    """Documentos pessoais de um estudante do pedido."""
    idade = e.idade_em(sol.viagem_ida or timezone.localdate())
    itens = []

    def add(codigo, ref, detalhe=""):
        d = doc_ativo(sol, codigo, e)
        itens.append({"codigo": codigo, "rotulo": regras.DOC_TIPOS[codigo], "ref": ref, "obrigatorio": True,
                      "kind": "pessoal", "ok": bool(d), "detalhe": detalhe, "doc": d, "participante": e,
                      "modelo": None})

    add("historico", "Item 7.1.4", "Emitido no SIGAA")
    add("identidade", "Item 7.1.5", "RG ou CNH, com CPF")
    add("nada_consta", "Item 7.1.7", "Pedido por e-mail ao Serviço Social (" + e.get_nc_estado_display().lower() + ")")
    if idade is not None and idade < 18:
        add("autorizacao_responsavel", "Item 7.1.6", "Estudante menor de idade")
    if e.reprovacoes_acima_limite:
        add("relatorio_pedagogico", "Item 7.1.8", "Descumprimento do item 5.7.2.1")
    return itens


def desatualizado(sol):
    from .services.fluxo import hash_dados
    return bool(sol.dados_hash) and sol.dados_hash != hash_dados(sol)


def checklist(sol):
    """Lista de itens documentais, com estado e se são obrigatórios para este caso."""
    itens = []
    r = calculo.resumo(sol)
    gerados = bool(sol.documentos_gerados_em) and not desatualizado(sol)

    def add(codigo, ref, obrigatorio, kind, ok, detalhe="", modelo=None):
        itens.append({
            "codigo": codigo, "rotulo": regras.DOC_TIPOS.get(codigo, codigo), "ref": ref,
            "obrigatorio": obrigatorio, "kind": kind, "ok": ok, "detalhe": detalhe,
            "modelo": modelo, "doc": doc_ativo(sol, codigo), "participante": None,
        })

    # Documentos gerados pelo sistema e assinados fora dele
    refs = {"requerimento": "Requerimento do campus", "anexo1": "Item 7.1.1", "anexo2": "Anexo II"}
    for cod in [t for t in documentos_gerados(sol) if t in refs]:
        ref = refs[cod]
        ass = list(sol.assinaturas.filter(tipo_doc=cod))
        if not gerados:
            add(cod, ref, True, "gerado", False, "Ainda não gerado")
        else:
            falta = [a.papel_rotulo for a in ass if a.estado != "assinado"]
            add(cod, ref, True, "gerado", not falta,
                "Assinado" if not falta else "Falta assinatura de " + " e ".join(falta))
    if calculo.efetivo(sol, "traslado") or calculo.efetivo(sol, "hospedagem"):
        add("anexo3", "Itens 7.1.9 e 7.1.10", True, "gerado", gerados,
            "Gerado" if gerados else "Ainda não gerado")

    # Uploads do estudante
    ref_aceite = "Item 7.1.3"
    add("folder_evento", "Item 7.1.2", True, "upload", bool(doc_ativo(sol, "folder_evento")),
        "Datas, local, programação e taxa de inscrição")
    add("aceite", ref_aceite, True, "upload", bool(doc_ativo(sol, "aceite")),
        "Com nome do estudante e descrição do trabalho apresentado")
    if calculo.efetivo(sol, "inscricao") and sol.valor_inscricao > 0:
        add("recibo_inscricao", "Comprova o valor pago", False, "upload", bool(doc_ativo(sol, "recibo_inscricao")))

    # Documentos de quem viaja junto
    if sol.servidor_acompanha:
        add("anexo4", "Item 7.1.12", True, "upload", bool(doc_ativo(sol, "anexo4")),
            "Termo do servidor que acompanha", modelo="anexo4")
        add("anexo5", "Item 7.1.13", True, "upload", bool(doc_ativo(sol, "anexo5")),
            "Termo do Diretor-Geral", modelo="anexo5")

    # Documentos pessoais, um conjunto por estudante
    for e in sol.participantes.all():
        itens.extend(checklist_estudante(sol, e))
    return itens


def comprovantes_orcamento(sol):
    """Comprovantes das pesquisas de preço, exigidos nos itens 7.1.9 e 7.1.10."""
    faltas = []
    for cat, rot in [("traslado", "traslado"), ("hospedagem", "hospedagem")]:
        if not calculo.efetivo(sol, cat):
            continue
        trechos = calculo.menores_por_trecho(sol, cat)
        if not trechos:
            faltas.append((f"Cadastre a pesquisa de preços de {rot}.", "orcamentos", "erro"))
        for t in trechos:
            t = {**t, "trecho": t["trecho"].replace(" - ", " → ")}
            n = len(t["orcamentos"])
            if n < 3:
                faltas.append((f"{t['trecho']}: faltam {3 - n} orçamento(s) (são exigidos três).", "orcamentos", "erro"))
            sem = [o for o in t["orcamentos"] if not o.comprovante]
            if sem:
                faltas.append((f"{t['trecho']}: {len(sem)} orçamento(s) sem comprovante anexado.", "orcamentos", "erro"))
    return faltas


def validar(sol):
    """Retorna pendências com nível (erro, aviso, info), fase e o passo onde corrigir.

    fase 'dados' bloqueia a geração dos anexos; fase 'docs' bloqueia o envio.
    """
    p = []
    hoje = timezone.localdate()

    def add(nivel, fase, msg, passo, campo=None):
        p.append({"nivel": nivel, "fase": fase, "msg": msg, "passo": passo, "campo": campo})

    perfil = sol.perfil
    if perfil.papel == "servidor":
        if not perfil.completo:
            add("erro", "dados", "Complete seu perfil (SIAPE e cargo).", "perfil")
    elif "requerimento" in documentos_gerados(sol):
        if not perfil.completo:
            add("erro", "dados", "Complete seu perfil (matrícula, CPF, RG, endereço e curso) para o requerimento.",
                "perfil")
    elif not perfil.matricula:
        add("erro", "dados", "Informe a sua matrícula no perfil.", "perfil")
    # Item 7.1: o estudante só é o proponente se for maior de 18 anos.
    if sol.proponente_tipo == "estudante" and perfil.papel == "aluno":
        idade = perfil.idade_em(sol.viagem_ida or sol.evento_inicio or timezone.localdate())
        if idade is not None and idade < 18:
            add("erro", "dados", "Estudante menor de 18 anos não pode ser o proponente (item 7.1). Indique um(a) "
                                 "servidor(a) como proponente.", "dados", "proponente_tipo")
    prop = sol.proponente
    if prop["servidor"] and not prop["e_quem_preenche"]:
        faltam = [r for r, v in [("nome", prop["nome"]), ("SIAPE", prop["siape"]), ("cargo", prop["cargo"]),
                                 ("e-mail", prop["email"])] if not (v or "").strip()]
        if faltam:
            add("erro", "dados", "Informe " + ", ".join(faltam) + " do(a) servidor(a) proponente.", "dados",
                "prop_nome")

    # Estudantes beneficiários (Anexo II)
    estudantes = list(sol.participantes.all())
    if not estudantes:
        add("erro", "dados", "Inclua ao menos um estudante no pedido.", "estudantes")
    if len(estudantes) > regras.MAX_ESTUDANTES:
        add("erro", "dados", f"O Anexo II comporta até {regras.MAX_ESTUDANTES} estudantes.", "estudantes")
    vistas = {}
    for e in estudantes:
        nome = e.nome_exibido or "Estudante sem nome"
        mat = e.matricula_exibida
        chave = normalizar_matricula(mat)
        if chave and chave in vistas:
            add("erro", "dados", f"A matrícula {mat} aparece duas vezes ({vistas[chave]} e {nome}).", "estudantes")
        if chave:
            vistas.setdefault(chave, nome)
        if not e.nome_exibido or not e.matricula_exibida:
            add("erro", "dados", f"{nome}: informe nome completo e matrícula.", "estudantes")
        if not e.declaracoes_ok:
            add("erro", "dados", f"{nome}: faltam as declarações de elegibilidade (PAES, frequência e "
                                 "ausência de pendências).", "estudantes")
        eu = e.usuario_id and e.usuario_id == sol.aluno_id
        bloqueado = (bool(bloqueios(e.usuario, exceto=sol)) if e.usuario_id else False) or \
            bloqueado_por_matricula(mat, exceto=sol)
        if bloqueado and not eu:
            # Sem expor o outro pedido nem a situação do colega: só o próprio estudante vê os detalhes.
            add("erro", "dados", f"{nome} tem devolução por GRU ou prestação de contas pendente em outro pedido e "
                                 "precisa regularizar antes (itens 10.5 e 10.6).", "estudantes")
        if em_outro_pedido_ativo(e, sol):
            add("aviso", "dados", f"{nome} já está em outra solicitação ativa. O edital permite apenas um "
                                  "evento por estudante no ano (item 5.5).", "estudantes")

    for campo, rotulo in [("evento_nome", "o nome do evento"), ("cidade", "a cidade"), ("uf", "a UF"),
                          ("evento_inicio", "a data de início do evento"), ("evento_fim", "a data de fim do evento"),
                          ("viagem_ida", "a data de ida"), ("viagem_volta", "a data de retorno")]:
        if not getattr(sol, campo):
            if campo in ("viagem_ida", "viagem_volta") and not sol.presencial:
                continue
            # Ida e retorno saem das datas do evento; sem elas, o aviso já está no início e no fim.
            if (campo == "viagem_ida" and not sol.evento_inicio) or (campo == "viagem_volta" and not sol.evento_fim):
                continue
            add("erro", "dados", f"Informe {rotulo}.", "dados", campo)

    if sol.evento_inicio and sol.evento_fim and sol.evento_fim < sol.evento_inicio:
        add("erro", "dados", "O fim do evento é anterior ao início.", "dados", "evento_fim")
    fora_do_evento = False
    if sol.viagem_ida and sol.viagem_volta and sol.evento_inicio and sol.evento_fim:
        de = f"{sol.viagem_ida:%d/%m} a {sol.viagem_volta:%d/%m}"
        fora_do_evento = sol.viagem_ida > sol.evento_fim or sol.viagem_volta < sol.evento_inicio
        if fora_do_evento:
            add("erro", "dados", f"A viagem ({de}) não acontece nas datas do evento "
                                 f"({sol.evento_inicio:%d/%m} a {sol.evento_fim:%d/%m}). Confira ida e retorno.",
                "dados", "viagem_ida")
        else:
            antes = (sol.evento_inicio - sol.viagem_ida).days
            depois = (sol.viagem_volta - sol.evento_fim).days
            if antes > 2:
                add("aviso", "dados", f"A ida é {antes} dias antes do início do evento. Confira a data.", "dados",
                    "viagem_ida")
            if depois > 2:
                add("aviso", "dados", f"O retorno é {depois} dias depois do fim do evento. Confira a data.", "dados",
                    "viagem_volta")
    if sol.viagem_ida and sol.viagem_volta:
        if sol.viagem_volta < sol.viagem_ida:
            add("erro", "dados", "A data de retorno é anterior à ida.", "dados", "viagem_volta")
        if sol.evento_inicio and sol.viagem_ida > sol.evento_inicio and not fora_do_evento:
            add("aviso", "dados", "A viagem começa depois do início do evento.", "dados", "viagem_ida")
        if sol.evento_fim and sol.viagem_volta < sol.evento_fim and not fora_do_evento:
            add("aviso", "dados", "O retorno é anterior ao fim do evento.", "dados", "viagem_volta")

    if not (sol.pede_inscricao or sol.pede_traslado or sol.pede_alimentacao or sol.pede_hospedagem):
        add("erro", "dados", "Selecione ao menos um auxílio.", "dados", "pede_inscricao")
    if calculo.so_inscricao(sol):
        motivo = "o evento é virtual" if not sol.presencial else "o evento é na cidade do campus"
        add("info", "dados", f"Somente o Auxílio-Inscrição pode ser solicitado, pois {motivo} (itens 4.4 e 4.5).", "dados", "pede_inscricao")
    if sol.pede_inscricao and sol.valor_inscricao <= 0 and not sol.pede_traslado:
        add("erro", "dados", "Informe o valor da inscrição.", "dados", "valor_inscricao")

    # Prazo mínimo (item 6.2) e janelas (item 6.1). Só importam antes do envio ao Protocolo.
    antes_do_envio = sol.status in ("rascunho", "ag_aluno", "ag_diretor", "incompleta", "pronta")
    if sol.evento_inicio and antes_do_envio:
        dias = (sol.evento_inicio - hoje).days
        minimo = sol.edital.regras["prazo_minimo_dias"]
        if dias < 0:
            add("erro", "dados", "O evento já começou. Confira a data de início.", "dados", "evento_inicio")
        elif dias < minimo and not sol.justificativa_prazo.strip():
            add("erro", "dados", f"O evento começa em {dias} dia(s) e o edital pede {minimo} dias entre o resultado "
                                 f"final e o início. Justifique o caso excepcional (item 6.2.1).", "dados",
                "justificativa_prazo")
        elif dias < minimo:
            add("aviso", "dados", f"Prazo menor que {minimo} dias, com justificativa registrada.", "dados",
                "justificativa_prazo")
    janelas = sol.edital.regras.get("janelas", [])
    if antes_do_envio and janelas and not any(date.fromisoformat(j["inicio"]) <= hoje <= date.fromisoformat(j["fim"]) for j in janelas):
        add("aviso", "dados", "Hoje não está em nenhuma janela de submissão listada no edital. Confirme com o DAES.", None)

    for msg in bloqueios(sol.aluno, exceto=sol):
        add("erro", "dados", msg, None)

    # Valores
    r = calculo.resumo(sol)
    if r["acima_do_teto"]:
        add("erro", "dados", f"O total solicitado ({calculo.brl(r['total'])}) passa do teto de 5% do valor global "
                             f"({calculo.brl(r['teto'])}), item 5.3.", "orcamentos")
    for msg, passo, nivel in comprovantes_orcamento(sol):
        add(nivel, "dados", msg, passo)

    # Documentos
    for item in checklist(sol):
        if item["kind"] == "gerado":
            continue
        quem = f" de {item['participante'].nome_exibido}" if item.get("participante") else ""
        if item["obrigatorio"] and not item["ok"]:
            add("erro", "docs", f"Falta anexar: {item['rotulo']}{quem}.", "documentos")
        elif not item["obrigatorio"] and not item["ok"]:
            add("aviso", "docs", f"Recomendado anexar: {item['rotulo']}.", "documentos")
    if desatualizado(sol):
        add("erro", "docs", "Os dados mudaram depois da geração dos documentos. Gere-os novamente "
                            "(as assinaturas anteriores serão invalidadas).", "assinaturas")
    elif sol.documentos_gerados_em:
        for a in sol.assinaturas.exclude(estado="assinado"):
            add("erro", "docs", f"Falta a assinatura de {a.papel_rotulo} em {a.doc_rotulo}.", "assinaturas")
    else:
        add("erro", "docs", "Gere o requerimento e os anexos para iniciar as assinaturas.", "assinaturas")
    return p


def pode_gerar(sol):
    return not any(x["nivel"] == "erro" and x["fase"] == "dados" for x in validar(sol))


def pode_enviar(sol):
    return not any(x["nivel"] == "erro" for x in validar(sol))
