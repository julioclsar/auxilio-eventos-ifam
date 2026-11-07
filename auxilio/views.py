from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import calculo, regras, validacao
from .forms import (ArquivoForm, CadastroForm, ParticipanteForm, PerfilServidorForm, DecisaoForm, OrcamentoForm, PerfilForm, ProtocoloForm, SolicitacaoForm)
from .models import Assinatura, Campus, Perfil, Documento, Edital, Orcamento, Participante, Solicitacao
from .services import documentos as geradores
from .services import dossie, emails, fluxo

EDITAVEIS = {"rascunho", "ag_aluno", "ag_diretor", "incompleta", "pronta"}


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------
def papel(*permitidos):
    def deco(view):
        @wraps(view)
        @login_required
        def interna(request, *a, **kw):
            perfil = getattr(request.user, "perfil", None)
            if not perfil or perfil.papel not in permitidos:
                messages.error(request, "Você não tem acesso a esta área.")
                return redirect("inicio")
            return view(request, *a, **kw)
        return interna
    return deco


def perfil_de(user):
    return getattr(user, "perfil", None)


def minha_solicitacao(request, pk):
    return get_object_or_404(Solicitacao, pk=pk, aluno=request.user)


class Entrar(auth_views.LoginView):
    template_name = "auxilio/entrar.html"
    redirect_authenticated_user = True


def cadastro(request):
    if request.user.is_authenticated:
        return redirect("inicio")
    campus = Campus.objects.first()
    form = CadastroForm(request.POST or None)
    if not campus:
        messages.error(request, "O sistema ainda não foi configurado (rode o comando configurar).")
    elif request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        u = User.objects.create_user(d["email"], d["email"], d["senha"], first_name=d["nome_completo"].split()[0])
        Perfil.objects.create(user=u, papel=d["tipo"], campus=campus, nome_completo=d["nome_completo"].strip())
        login(request, u)
        messages.success(request, "Conta criada. Complete o seu perfil para começar.")
        destino = request.GET.get("next", "")
        return redirect(destino) if destino.startswith("/convite/") else redirect("perfil")
    return render(request, "auxilio/cadastro.html", {"form": form})


@login_required
def inicio(request):
    p = getattr(request.user, "perfil", None)
    if p and p.papel == "diretor":
        return redirect("diretor_lista")
    if p and p.papel == "analista":
        return redirect("analise_lista")
    return render(request, "auxilio/inicio.html", {
        "bloqueios": validacao.bloqueios(request.user),
        "solicitacoes": request.user.solicitacoes.all(),
        "participacoes": Participante.objects.filter(usuario=request.user).exclude(
            solicitacao__aluno=request.user).select_related("solicitacao"),
        "servidor": bool(p and p.papel == "servidor"),
        "perfil_completo": p.completo if p else False,
    })


@papel("aluno", "servidor")
def perfil(request):
    classe = PerfilServidorForm if request.user.perfil.papel == "servidor" else PerfilForm
    form = classe(request.POST or None, instance=request.user.perfil)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Perfil atualizado.")
        return redirect("inicio")
    return render(request, "auxilio/perfil.html", {"form": form})


def ajuda(request):
    return render(request, "auxilio/ajuda.html", {"edital": Edital.objects.filter(vigente=True).first()})


@papel("aluno", "servidor")
@require_POST
def sol_excluir(request, pk):
    """Descarta uma solicitação que ainda não foi enviada ao Protocolo, com os arquivos dela."""
    sol = minha_solicitacao(request, pk)
    if sol.status not in EDITAVEIS:
        messages.error(request, "Esta solicitação já foi enviada e não pode ser excluída.")
        return redirect("sol_resumo", pk=sol.pk)
    for d in sol.documentos.all():
        d.arquivo.delete(save=False)
    for o in sol.orcamentos.all():
        if o.comprovante:
            o.comprovante.delete(save=False)
    sol.delete()
    messages.success(request, "Solicitação excluída.")
    return redirect("inicio")


@papel("aluno", "servidor")
@require_POST
def nova(request):
    if not request.user.perfil.completo:
        messages.warning(request, "Complete seu perfil antes de criar uma solicitação.")
        return redirect("perfil")
    bloqueios = validacao.bloqueios(request.user)
    if bloqueios:
        for msg in bloqueios:
            messages.error(request, msg)
        return redirect("inicio")
    edital = Edital.objects.filter(vigente=True).first()
    servidor = request.user.perfil.papel == "servidor"
    sol = Solicitacao.objects.create(aluno=request.user, edital=edital,
                                     proponente_tipo="servidor" if servidor else "estudante")
    if not servidor:  # o estudante que cria já entra como o primeiro estudante do pedido
        p = request.user.perfil
        Participante.objects.create(solicitacao=sol, usuario=request.user, ordem=1, nome_completo=p.nome_completo,
                                    matricula=p.matricula, email=request.user.email, curso=p.curso,
                                    nascimento=p.nascimento, convite_aceito_em=timezone.now())
    fluxo.registrar(sol, request.user, "Solicitação criada.")
    return redirect("sol_dados", pk=sol.pk)


# ---------------------------------------------------------------------------
# Contexto comum das telas do estudante
# ---------------------------------------------------------------------------
ORDEM_PASSOS = ["perfil", "dados", "estudantes", "orcamentos", "documentos", "assinaturas"]
TEXTO_PASSO = {
    "perfil": "Complete o seu perfil",
    "dados": "Complete os dados do evento",
    "estudantes": "Complete a lista de estudantes",
    "orcamentos": "Complete os orçamentos",
    "documentos": "Anexe os documentos que faltam",
    "assinaturas": "Gere e assine os documentos",
}


def proximo_passo(sol):
    if sol.status not in EDITAVEIS:
        return None
    pend = validacao.validar(sol)
    erros = [x for x in pend if x["nivel"] == "erro"]
    dados = [x for x in erros if x["fase"] == "dados"]
    if dados:
        # Segue a ordem das abas: a primeira aba com pendência é o próximo passo, e o texto diz qual é.
        for passo in ORDEM_PASSOS:
            daqui = [x for x in dados if x["passo"] == passo]
            if daqui:
                break
        else:
            daqui, passo = dados, dados[0]["passo"]
        detalhe = daqui[0]["msg"]
        if len(daqui) > 1:
            detalhe += f" E mais {len(daqui) - 1} pendência(s) nesta etapa."
        return {"texto": TEXTO_PASSO.get(passo, "Resolva as pendências"), "passo": passo, "detalhe": detalhe}
    if not sol.documentos_gerados_em or validacao.desatualizado(sol):
        return {"texto": "Gere o requerimento e os anexos", "passo": "assinaturas", "detalhe": ""}
    ass = sol.assinaturas.exclude(estado="assinado")
    if ass.filter(papel="aluno").exists():
        return {"texto": "Assine e envie os documentos", "passo": "assinaturas", "detalhe": ""}
    if ass.filter(papel="diretor").exists():
        return {"texto": "Aguardando o Diretor-Geral assinar o Anexo I", "passo": "assinaturas", "detalhe": ""}
    if erros:
        return {"texto": "Anexe os documentos que faltam", "passo": "documentos", "detalhe": erros[0]["msg"]}
    return {"texto": "Gere o PDF único e envie ao Protocolo", "passo": "envio", "detalhe": ""}


def contexto(request, sol, aba):
    r = calculo.resumo(sol)
    # Pendências na mesma ordem das abas, para a lista ler como um roteiro.
    ordem = {p: i for i, p in enumerate(ORDEM_PASSOS)}
    pend = sorted(validacao.validar(sol), key=lambda x: ordem.get(x.get("passo"), len(ordem)))
    faltas = {}
    for x in pend:
        if x["nivel"] == "erro":
            faltas[x.get("passo")] = faltas.get(x.get("passo"), 0) + 1
    return {
        "faltas": faltas,
        "sol": sol, "aba": aba, "r": r, "pendencias": pend,
        "erros": [x for x in pend if x["nivel"] == "erro"],
        "avisos": [x for x in pend if x["nivel"] in ("aviso", "info")],
        "editavel": sol.status in EDITAVEIS,
        "desatualizado": validacao.desatualizado(sol),
        # O que falta nesta aba, para mostrar no topo dela (o número no rótulo da aba sozinho não explica).
        "pend_aba": [x for x in pend if x.get("passo") == aba and x["nivel"] in ("erro", "aviso")],
        # Pendências ligadas a um campo, para aparecerem embaixo dele.
        "pend_campo": {x["campo"]: x for x in reversed(pend) if x.get("campo")},
        "navegacao": navegacao(sol, aba),
    }


ABAS = [("resumo", "sol_resumo", "Resumo"), ("dados", "sol_dados", "Evento"),
        ("estudantes", "sol_estudantes", "Estudantes"), ("orcamentos", "sol_orcamentos", "Orçamentos"),
        ("documentos", "sol_documentos", "Documentos"), ("assinaturas", "sol_assinaturas", "Assinaturas"),
        ("envio", "sol_envio", "Envio")]


def navegacao(sol, aba):
    """Links de etapa anterior e seguinte, para o rodapé de cada aba."""
    nomes = [a[0] for a in ABAS]
    if aba not in nomes:
        return {}
    i = nomes.index(aba)
    ant = ABAS[i - 1] if i > 0 else None
    seg = ABAS[i + 1] if i + 1 < len(ABAS) else None
    return {
        "anterior": {"url": reverse(ant[1], args=[sol.pk]), "nome": ant[2]} if ant else None,
        "seguinte": {"url": reverse(seg[1], args=[sol.pk]), "nome": seg[2]} if seg else None,
    }


# ---------------------------------------------------------------------------
# Passos do estudante
# ---------------------------------------------------------------------------
@papel("aluno", "servidor")
def sol_resumo(request, pk):
    sol = minha_solicitacao(request, pk)
    fluxo.atualizar_status(sol, request.user)
    c = contexto(request, sol, "resumo")
    c["proximo"] = proximo_passo(sol)
    c["checklist"] = validacao.checklist(sol)
    # Os anexos que faltam já aparecem no checklist ao lado; aqui viram uma linha só.
    faltam_docs = [x for x in c["pendencias"] if x["passo"] == "documentos" and x["nivel"] == "erro"]
    if len(faltam_docs) > 1:
        primeiro = c["pendencias"].index(faltam_docs[0])
        resto = [x for x in c["pendencias"] if x not in faltam_docs]
        resto.insert(primeiro, {"nivel": "erro", "fase": "docs", "passo": "documentos", "campo": None,
                                "msg": f"Faltam {len(faltam_docs)} documentos obrigatórios (veja o checklist)."})
        c["pendencias"] = resto
    c["historico"] = sol.historico.all()[:12]
    c["prazo_recurso"] = fluxo.prazo_recurso(sol)
    c["prazo_prestacao"] = fluxo.prazo_prestacao(sol)
    return render(request, "auxilio/sol_resumo.html", c)


@papel("aluno", "servidor")
def sol_dados(request, pk):
    sol = minha_solicitacao(request, pk)
    editavel = sol.status in EDITAVEIS
    form = SolicitacaoForm(request.POST or None, instance=sol, servidor=sol.perfil.papel == "servidor")
    if not editavel:
        for f in form.fields.values():
            f.disabled = True
    if request.method == "POST" and editavel and form.is_valid():
        sol = form.save()
        fluxo.atualizar_status(sol, request.user)
        messages.success(request, "Dados salvos.")
        return redirect("sol_estudantes", pk=sol.pk) if "avancar" in request.POST else redirect("sol_dados", pk=sol.pk)
    c = contexto(request, sol, "dados")
    c["form"] = form
    # Aqui o "seguinte" é o botão "Salvar e ir para os estudantes"; um link a mais perderia o que foi digitado.
    c["navegacao"] = {"anterior": c["navegacao"].get("anterior")} if editavel else c["navegacao"]
    inteiras, meias = calculo.composicao_regra(sol)
    diaria = Decimal(sol.edital.regras["alimentacao"]["valor_diaria"])
    c["regra"] = {"dias": calculo.dias_viagem(sol), "noites": calculo.noites(sol), "inteiras": inteiras,
                  "meias": meias, "diaria": diaria, "meia": diaria / 2, "total_alim": diaria * inteiras + diaria / 2 * meias}
    return render(request, "auxilio/sol_dados.html", c)


@papel("aluno", "servidor")
def sol_orcamentos(request, pk):
    sol = minha_solicitacao(request, pk)
    editavel = sol.status in EDITAVEIS
    enviado = request.POST.get("bloco") if request.method == "POST" else None

    def formulario(chave, categoria, trecho, aberto=False):
        """O formulário de uma vaga. Se foi ele que voltou com erro, mostra o que a pessoa digitou."""
        if chave == enviado:
            return OrcamentoForm(request.POST, request.FILES, trecho_aberto=aberto, prefix=chave)
        return OrcamentoForm(initial={"categoria": categoria, "trecho": trecho}, trecho_aberto=aberto, prefix=chave)

    if request.method == "POST" and editavel and enviado:
        form = OrcamentoForm(request.POST, request.FILES, prefix=enviado,
                             trecho_aberto=request.POST.get("aberto") == "1")
        if form.is_valid():
            o = form.save(commit=False)
            o.solicitacao = sol
            o.trecho = o.trecho.strip()
            existentes = sol.orcamentos.filter(categoria=o.categoria, trecho=o.trecho).count()
            if existentes >= 3:
                messages.error(request, "Este trecho já tem os três orçamentos. Exclua um para trocar.")
                return redirect("sol_orcamentos", pk=sol.pk)
            try:
                fluxo.validar_arquivo(o.comprovante)
            except ValidationError as e:
                form.add_error("comprovante", e.message)
            else:
                o.ordem = existentes + 1
                o.save()
                fluxo.atualizar_status(sol, request.user)
                messages.success(request, f"Orçamento {o.ordem} de 3 adicionado em {o.trecho.replace(' - ', ' → ')}.")
                return redirect("sol_orcamentos", pk=sol.pk)

    c = contexto(request, sol, "orcamentos")
    origem, destino = sol.campus.municipio, sol.cidade
    blocos = []

    def bloco(chave, categoria, rotulo, trecho, dados=None):
        orc = dados["orcamentos"] if dados else []
        blocos.append({"chave": chave, "categoria": categoria, "rotulo": rotulo, "trecho": trecho,
                       "orcamentos": orc, "menor": dados["menor"] if dados else None, "faltam": 3 - len(orc),
                       "numero": len(orc) + 1, "form": formulario(chave, categoria, trecho) if len(orc) < 3 else None})

    por_nome = {t["trecho"]: t for t in r_trechos(c["r"], "traslado")}
    if calculo.efetivo(sol, "traslado") and destino:
        ida, volta = f"{origem} - {destino}", f"{destino} - {origem}"
        bloco("ida", "traslado", "Ida", ida, por_nome.pop(ida, None))
        bloco("volta", "traslado", "Volta", volta, por_nome.pop(volta, None))
    for n, (nome, t) in enumerate(por_nome.items(), start=1):  # trechos extras ou de antes de mudar a cidade
        bloco(f"extra{n}", "traslado", "Outro trecho", nome, t)
    c["blocos_traslado"] = blocos
    blocos = []
    hosp = r_trechos(c["r"], "hospedagem")
    if calculo.efetivo(sol, "hospedagem") and destino:
        if hosp:
            for n, t in enumerate(hosp, start=1):
                bloco(f"hosp{n}", "hospedagem", "Hospedagem", t["trecho"], t)
        else:
            bloco("hosp1", "hospedagem", "Hospedagem", destino)
    c["blocos_hospedagem"] = blocos
    c["form_extra"] = formulario("novo", "traslado", "", aberto=True)
    c["n_diarias"] = calculo.diarias_hospedagem(sol)
    c["destino"] = destino
    return render(request, "auxilio/sol_orcamentos.html", c)


def r_trechos(r, categoria):
    return list(r["trechos_traslado"] if categoria == "traslado" else r["trechos_hospedagem"])


@papel("aluno", "servidor")
@require_POST
def orcamento_excluir(request, pk, oid):
    sol = minha_solicitacao(request, pk)
    if sol.status in EDITAVEIS:
        o = get_object_or_404(Orcamento, pk=oid, solicitacao=sol)
        trecho, cat = o.trecho, o.categoria
        o.delete()
        for i, x in enumerate(sol.orcamentos.filter(categoria=cat, trecho=trecho).order_by("ordem", "id"), start=1):
            if x.ordem != i:
                x.ordem = i
                x.save(update_fields=["ordem"])
        fluxo.atualizar_status(sol, request.user)
        messages.info(request, "Orçamento removido.")
    return redirect("sol_orcamentos", pk=sol.pk)


@papel("aluno", "servidor")
def sol_documentos(request, pk):
    sol = minha_solicitacao(request, pk)
    editavel = sol.status in EDITAVEIS
    if request.method == "POST" and editavel:
        tipo = request.POST.get("tipo")
        form = ArquivoForm(request.POST, request.FILES)
        participante = None
        if request.POST.get("participante"):
            participante = get_object_or_404(Participante, pk=request.POST["participante"], solicitacao=sol)
        if tipo in regras.DOC_TIPOS and form.is_valid():
            try:
                fluxo.anexar_documento(sol, request.user, tipo, request.FILES["arquivo"], participante=participante)
                messages.success(request, f"Anexado: {regras.DOC_TIPOS[tipo]}.")
            except ValidationError as e:
                messages.error(request, "; ".join(e.messages))
        else:
            messages.error(request, "Selecione um arquivo.")
        return redirect("sol_documentos", pk=sol.pk)
    c = contexto(request, sol, "documentos")
    c["itens"] = [i for i in validacao.checklist(sol) if i["kind"] == "upload"]
    c["estudantes"] = [{"e": e, "itens": validacao.checklist_estudante(sol, e), "email_nc": emails.nada_consta(sol, e)}
                       for e in sol.participantes.all()]
    return render(request, "auxilio/sol_documentos.html", c)


@papel("aluno", "servidor")
@require_POST
def nada_consta_solicitado(request, pk):
    sol = minha_solicitacao(request, pk)
    e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
    marcar_nada_consta(sol, e, request.user)
    return redirect("sol_documentos", pk=sol.pk)


def marcar_nada_consta(sol, e, user):
    if e.nc_estado == "nao_solicitado":
        e.nc_estado = "solicitado"
        e.nc_solicitado_em = timezone.now()
        e.save(update_fields=["nc_estado", "nc_solicitado_em"])
        fluxo.registrar(sol, user, f"Nada Consta de {e.nome_exibido} solicitado por e-mail ao Serviço Social.")


# ---------------------------------------------------------------------------
# Estudantes do pedido (Anexo II): convite ou preenchimento pelo proponente
# ---------------------------------------------------------------------------
@papel("aluno", "servidor")
def sol_estudantes(request, pk):
    sol = minha_solicitacao(request, pk)
    editavel = sol.status in EDITAVEIS
    if request.method == "POST" and editavel:
        acao = request.POST.get("acao")
        if acao == "incluir":
            if sol.participantes.count() >= regras.MAX_ESTUDANTES:
                messages.error(request, f"O Anexo II comporta até {regras.MAX_ESTUDANTES} estudantes.")
                return redirect("sol_estudantes", pk=sol.pk)
            form = ParticipanteForm(request.POST)
            if form.is_valid():
                e = form.save(commit=False)
                e.solicitacao = sol
                e.ordem = sol.participantes.count() + 1
                existente = User.objects.filter(username=e.email).first() if e.email else None
                ja = existente and sol.participantes.filter(usuario=existente).exists()
                if ja:
                    messages.error(request, "Esse estudante já está no pedido.")
                    return redirect("sol_estudantes", pk=sol.pk)
                e.save()
                fluxo.registrar(sol, request.user, f"Estudante incluído: {e.nome_exibido}.")
                fluxo.atualizar_status(sol, request.user)
                messages.success(request, f"{e.nome_exibido} incluído(a). Envie o convite ou preencha os dados e documentos.")
            else:
                messages.error(request, "Confira os dados do estudante: " + "; ".join(
                    f"{form.fields[k].label}: {v[0]}" for k, v in form.errors.items() if k in form.fields))
        elif acao in ("salvar", "remover"):
            e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
            if acao == "remover" and e.e_quem_preenche:
                messages.error(request, "Quem preenche o pedido não pode se remover dele. Exclua o pedido, se for o caso.")
                return redirect("sol_estudantes", pk=sol.pk)
            if acao == "salvar" and e.usuario_id and not e.e_quem_preenche:
                messages.error(request, "Este(a) estudante tem conta. As declarações são confirmadas por ele(a) (item 7.3).")
                return redirect("sol_estudantes", pk=sol.pk)
            if acao == "remover":
                for d in e.documentos.all():
                    d.arquivo.delete(save=False)
                nome = e.nome_exibido
                e.delete()
                for i, x in enumerate(sol.participantes.all(), start=1):
                    if x.ordem != i:
                        x.ordem = i
                        x.save(update_fields=["ordem"])
                fluxo.registrar(sol, request.user, f"Estudante removido: {nome}.")
                messages.info(request, f"{nome} removido(a) do pedido.")
            else:
                form = ParticipanteForm(request.POST, instance=e, so_declaracoes=bool(e.usuario_id), prefix=f"p{e.pk}")
                if form.is_valid():
                    form.save()
                    messages.success(request, f"Dados de {e.nome_exibido} salvos.")
                else:
                    messages.error(request, "Confira os campos do estudante.")
            fluxo.atualizar_status(sol, request.user)
        return redirect("sol_estudantes", pk=sol.pk)
    c = contexto(request, sol, "estudantes")
    c["linhas"] = []
    for e in sol.participantes.all():
        link = request.build_absolute_uri(reverse("convite", args=[e.convite_token]))
        c["linhas"].append({
            "e": e, "form": ParticipanteForm(instance=e, so_declaracoes=bool(e.usuario_id), prefix=f"p{e.pk}"),
            "pode_editar": not e.usuario_id or e.e_quem_preenche,
            "docs": sum(1 for i in validacao.checklist_estudante(sol, e) if i["ok"]),
            "docs_total": len(validacao.checklist_estudante(sol, e)),
            "link": link, "email_convite": emails.convite(sol, e, link) if e.email_exibido else None,
        })
    c["novo"] = ParticipanteForm()
    c["cabe_mais"] = sol.participantes.count() < regras.MAX_ESTUDANTES
    return render(request, "auxilio/sol_estudantes.html", c)


@login_required
def convite(request, token):
    """O colega abre o link, entra com a própria conta e passa a enviar os próprios documentos."""
    e = get_object_or_404(Participante, convite_token=token)
    p = perfil_de(request.user)
    if p is None or p.papel != "aluno":
        messages.error(request, "O convite é para a conta de um estudante.")
        return redirect("inicio")
    if not e.usuario_id and (not e.email or e.email.lower() != request.user.email.lower()):
        # O link sozinho não basta: só a conta com o e-mail informado pelo proponente assume a vaga.
        messages.error(request, "Este convite é para outro e-mail. Entre com o e-mail institucional que o "
                                "proponente informou, ou peça para ele corrigir o seu e-mail no pedido.")
        return redirect("inicio")
    if e.usuario_id and e.usuario_id != request.user.pk:
        messages.error(request, "Este convite já foi aceito por outra conta.")
        return redirect("inicio")
    if not e.usuario_id:
        if e.solicitacao.participantes.filter(usuario=request.user).exists():
            messages.info(request, "Você já faz parte deste pedido.")
            return redirect("inicio")
        e.usuario = request.user
        e.convite_aceito_em = timezone.now()
        e.save(update_fields=["usuario", "convite_aceito_em"])
        fluxo.registrar(e.solicitacao, request.user, f"{e.nome_exibido} aceitou o convite.")
        messages.success(request, "Pronto, você está no pedido. Confirme as declarações e envie seus documentos.")
    return redirect("participacao", pid=e.pk)


@login_required
def participacao(request, pid):
    """Tela do estudante convidado: só os próprios dados, declarações e documentos."""
    e = get_object_or_404(Participante, pk=pid, usuario=request.user)
    sol = e.solicitacao
    editavel = sol.status in EDITAVEIS
    if request.method == "POST" and (editavel or request.POST.get("acao") in ("desistir", "gru")):
        acao = request.POST.get("acao")
        try:
            if acao == "declaracoes":
                form = ParticipanteForm(request.POST, instance=e, so_declaracoes=True)
                if form.is_valid():
                    form.save()
                    messages.success(request, "Declarações salvas.")
            elif acao == "documento":
                tipo = request.POST.get("tipo")
                if tipo not in regras.DOCS_PESSOAIS:
                    raise ValidationError("Tipo inválido.")
                fluxo.anexar_documento(sol, request.user, tipo, request.FILES["arquivo"], participante=e)
                messages.success(request, f"Anexado: {regras.DOC_TIPOS[tipo]}.")
            elif acao == "nada_consta":
                marcar_nada_consta(sol, e, request.user)
            elif acao == "desistir":
                fluxo.desistir_estudante(sol, request.user, e, request.POST.get("motivo", ""))
                messages.success(request, "Desistência registrada. Envie o e-mail à Direção-Geral abaixo.")
            elif acao == "gru":
                fluxo.registrar_gru_estudante(sol, request.user, e, request.FILES["arquivo"])
                messages.success(request, "Comprovante enviado. O bloqueio sai quando a devolução for confirmada.")
        except ValidationError as err:
            messages.error(request, "; ".join(err.messages))
        except KeyError:
            messages.error(request, "Selecione um arquivo.")
        return redirect("participacao", pid=e.pk)
    return render(request, "auxilio/participacao.html", {
        "e": e, "sol": sol, "editavel": editavel, "r": calculo.resumo(sol),
        "form": ParticipanteForm(instance=e, so_declaracoes=True),
        "itens": validacao.checklist_estudante(sol, e), "email_nc": emails.nada_consta(sol, e),
        "perfil_completo": request.user.perfil.completo,
        "pode_desistir": not e.desistiu and sol.status in fluxo.ANTES_DO_PAGAMENTO + ("paga", "ag_prestacao")
                         and sum(1 for x in sol.participantes.all() if not x.desistiu) > 1,
        "desistencia": (desistencias_do_pedido(sol, so=e) or [None])[0],
    })


@papel("aluno", "servidor")
def sol_assinaturas(request, pk):
    sol = minha_solicitacao(request, pk)
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "gerar":
                fluxo.gerar_documentos(sol, request.user)
                messages.success(request, "Documentos gerados. Baixe, assine no gov.br e envie de volta.")
            elif acao == "assinado":
                tipo = request.POST.get("tipo")
                fluxo.receber_assinado(sol, request.user, tipo, "aluno", request.FILES["arquivo"])
                messages.success(request, "Documento assinado recebido.")
            elif acao == "assinado_diretor":
                fluxo.receber_assinado(sol, request.user, "anexo1", "diretor", request.FILES["arquivo"])
                messages.success(request, "Anexo I com a assinatura do Diretor-Geral recebido.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except geradores.ConversaoIndisponivel as e:
            messages.error(request, str(e))
        except KeyError:
            messages.error(request, "Selecione o PDF assinado.")
        return redirect("sol_assinaturas", pk=sol.pk)
    c = contexto(request, sol, "assinaturas")
    gerados = []
    for tipo in validacao.documentos_gerados(sol):
        d = sol.documentos.filter(tipo=tipo, origem="gerado").order_by("-versao").first()
        if d and sol.documentos_gerados_em:
            gerados.append({
                "tipo": tipo, "doc": d,
                "assinaturas": list(sol.assinaturas.filter(tipo_doc=tipo)),
                "assinado": validacao.doc_ativo(sol, tipo),
            })
    c["gerados"] = gerados
    c["email_diretor"] = emails.diretor(sol)
    c["prop"] = sol.proponente
    if not c["prop"]["e_quem_preenche"]:
        c["email_proponente"] = emails.proponente_servidor(sol)
    c["pode_gerar"] = validacao.pode_gerar(sol)
    c["bloqueios"] = [x for x in c["erros"] if x["fase"] == "dados"]
    return render(request, "auxilio/sol_assinaturas.html", c)


@papel("aluno", "servidor")
def sol_envio(request, pk):
    sol = minha_solicitacao(request, pk)
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if acao == "dossie":
                dossie.montar(sol, request.user)
                messages.success(request, "PDF único gerado.")
            elif acao == "protocolar":
                f = ProtocoloForm(request.POST)
                if f.is_valid():
                    fluxo.registrar_protocolo(sol, request.user, f.cleaned_data["numero_processo"], f.cleaned_data["data"])
                    messages.success(request, "Protocolo registrado.")
                else:
                    messages.error(request, "Informe a data do envio.")
            elif acao == "recurso":
                fluxo.interpor_recurso(sol, request.user, request.POST.get("recurso", ""))
                messages.success(request, "Recurso registrado.")
            elif acao == "evento_realizado":
                fluxo.abrir_prestacao(sol, request.user)
                return redirect("sol_prestacao", pk=sol.pk)
            elif acao == "desistir":
                fluxo.desistir(sol, request.user, request.POST.get("motivo", ""))
                messages.success(request, "Desistência registrada. Envie o e-mail à Direção-Geral abaixo.")
            elif acao == "desistir_estudante":
                e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
                fluxo.desistir_estudante(sol, request.user, e, request.POST.get("motivo", ""))
                messages.success(request, f"Desistência de {e.nome_exibido} registrada. Envie o e-mail à Direção-Geral.")
            elif acao == "desfazer_desistencia":
                e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
                fluxo.desfazer_desistencia(sol, request.user, e)
                messages.success(request, f"Desistência de {e.nome_exibido} desfeita.")
            elif acao == "gru_estudante":
                e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
                fluxo.registrar_gru_estudante(sol, request.user, e, request.FILES["arquivo"])
                messages.success(request, "Comprovante enviado. O bloqueio sai quando a devolução for confirmada.")
            elif acao == "gru":
                fluxo.registrar_gru(sol, request.user, request.FILES["arquivo"])
                messages.success(request, "Comprovante enviado. Mande o e-mail à Direção-Geral. O bloqueio sai quando a devolução for confirmada.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except KeyError:
            messages.error(request, "Selecione o arquivo.")
        return redirect("sol_envio", pk=sol.pk)
    c = contexto(request, sol, "envio")
    hoje = timezone.localdate()
    fim = sol.evento_fim or sol.viagem_volta
    c["evento_terminou"] = not fim or hoje >= fim
    c["fim_evento"] = fim
    destinos = regras.TRANSICOES.get(sol.status, [])
    c["pode_desistir"] = "cancelada" in destinos or "ag_devolucao" in destinos
    c["desistir_com_gru"] = "ag_devolucao" in destinos
    if sol.status == "cancelada":
        c["email_desistencia"] = emails.desistencia(sol)
    elif sol.status in ("ag_devolucao", "devolvida"):
        c["gru"] = validacao.gru_do_pedido(sol)
        c["email_devolucao"] = emails.devolucao(sol, com_comprovante=bool(c["gru"]))
    c["desistencias"] = desistencias_do_pedido(sol)
    ativos = [e for e in sol.participantes.all() if not e.desistiu]
    c["ativos"] = ativos
    c["pode_desistir_estudante"] = c["pode_desistir"] and len(ativos) > 1
    c["dossie"] = validacao.doc_ativo(sol, "dossie")
    c["pode_enviar"] = validacao.pode_enviar(sol)
    c["email_protocolo"] = emails.protocolo(sol)
    c["protocolo_form"] = ProtocoloForm(initial={"data": timezone.localdate()})
    c["prazo_recurso"] = fluxo.prazo_recurso(sol)
    c["recurso_vencido"] = bool(c["prazo_recurso"] and hoje > c["prazo_recurso"])
    c["sequencia"] = [r for r, _ in dossie.sequencia(sol)] if c["pode_enviar"] else []
    return render(request, "auxilio/sol_envio.html", c)


def desistencias_do_pedido(sol, so=None):
    """Estudantes que desistiram, com o e-mail certo para cada situação (suspensão ou GRU)."""
    itens = []
    for e in sol.participantes.filter(desistencia_em__isnull=False):
        if so is not None and e.pk != so.pk:
            continue
        gru = validacao.doc_ativo(sol, "gru_comprovante", e)
        por_si = so is not None
        if e.devolucao_estado in ("pendente", "confirmada"):
            email = emails.devolucao(sol, com_comprovante=bool(gru), estudante=e, por_si=por_si)
        else:
            email = emails.desistencia(sol, estudante=e, por_si=por_si)
        itens.append({"e": e, "gru": gru, "email": email})
    return itens


@papel("aluno", "servidor")
def sol_prestacao(request, pk):
    sol = minha_solicitacao(request, pk)
    if request.method == "POST":
        acao = request.POST.get("acao")
        try:
            if sol.status not in ("ag_prestacao", "prestacao_pendencia"):
                raise ValidationError("A prestação de contas não está aberta para envio agora.")
            if acao == "gerar":
                sol.resultados_texto = request.POST.get("resultados", "").strip()[:6000]
                sol.coordenador_nome = request.POST.get("coordenador_nome", "").strip()[:120]
                sol.coordenador_siape = request.POST.get("coordenador_siape", "").strip()[:20]
                sol.coordenador_email = request.POST.get("coordenador_email", "").strip()[:254]
                sol.save(update_fields=["resultados_texto", "coordenador_nome", "coordenador_siape",
                                        "coordenador_email"])
                fluxo.gerar_prestacao(sol, request.user)
                messages.success(request, "Anexo VI gerado com os seus resultados. Baixe, assine e envie de volta.")
            elif acao == "assinado_servidor":
                fluxo.receber_assinado(sol, request.user, "anexo6", "servidor", request.FILES["arquivo"])
                messages.success(request, "Anexo VI assinado pelo(a) coordenador(a) recebido.")
            elif acao == "comprovacao":
                tipo = request.POST.get("tipo")
                if tipo not in regras.COMPROVACOES_PRESTACAO:
                    raise ValidationError("Tipo inválido.")
                fluxo.anexar_documento(sol, request.user, tipo, request.FILES["arquivo"], acumular=True)
                messages.success(request, "Arquivo anexado.")
            elif acao == "remover":
                d = get_object_or_404(Documento, pk=request.POST.get("doc"), solicitacao=sol,
                                      tipo__in=regras.COMPROVACOES_PRESTACAO, ativo=True)
                d.ativo = False
                d.save(update_fields=["ativo"])
                fluxo.registrar(sol, request.user, f"Removido: {regras.DOC_TIPOS[d.tipo]} ({d.nome_original})")
                messages.success(request, "Arquivo removido.")
            elif acao == "enviar":
                faltas = fluxo.prestacao_pendencias(sol)
                if faltas:
                    raise ValidationError("Pendências: " + " ".join(faltas))
                dossie.montar_prestacao(sol, request.user)
                fluxo.mudar_status(sol, "prestacao_analise", request.user, "Prestação de contas enviada.")
                messages.success(request, "Prestação pronta. Envie o PDF único ao Protocolo com o e-mail abaixo.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except KeyError:
            messages.error(request, "Selecione um arquivo.")
        except geradores.ConversaoIndisponivel as e:
            messages.error(request, str(e))
        return redirect("sol_prestacao", pk=sol.pk)
    c = contexto(request, sol, "prestacao")
    c["prazo"] = fluxo.prazo_prestacao(sol)
    c["atrasada"] = validacao.prestacao_atrasada(sol)
    c["dias_restantes"] = (c["prazo"] - timezone.localdate()).days if c["prazo"] else None
    c["faltas_prestacao"] = fluxo.prestacao_pendencias(sol)
    c["prestacao_pdf"] = validacao.doc_ativo(sol, "prestacao_pdf")
    if c["prestacao_pdf"]:
        c["email_prestacao"] = emails.prestacao(sol)
    c["anexo6"] = sol.documentos.filter(tipo="anexo6", origem="gerado").order_by("-versao").first()
    c["ass6_servidor"] = sol.assinaturas.filter(tipo_doc="anexo6", papel="servidor").first()
    if not sol.coordenador_nome and sol.proponente["servidor"]:
        prop = sol.proponente
        sol.coordenador_nome, sol.coordenador_siape, sol.coordenador_email = prop["nome"], prop["siape"], prop["email"]
    c["email_servidor"] = emails.servidor_responsavel(sol)
    exigidos = {"prest_certificado"}
    if calculo.efetivo(sol, "traslado"):
        exigidos.add("prest_embarque")
    if calculo.efetivo(sol, "hospedagem"):
        exigidos.add("prest_hospedagem")
    c["comprovacoes"] = [(t, regras.DOC_TIPOS[t], list(sol.documentos.filter(tipo=t, ativo=True).order_by("criado_em")),
                          t in exigidos) for t in regras.COMPROVACOES_PRESTACAO]
    # Passagens: um arquivo só é aceito (pode trazer ida e volta), mas o estudante é avisado para conferir.
    n = calculo.resumo(sol)["n_ativos"]  # só quem foi ao evento envia certificado e passagens
    certificados = sol.documentos.filter(tipo="prest_certificado", ativo=True).count()
    c["certificados_poucos"] = n > 1 and 0 < certificados < n
    c["passagem_unica"] = n == 1 and calculo.efetivo(sol, "traslado") and \
        sol.documentos.filter(tipo="prest_embarque", ativo=True).count() == 1
    return render(request, "auxilio/sol_prestacao.html", c)


# ---------------------------------------------------------------------------
# Downloads com checagem de acesso
# ---------------------------------------------------------------------------
@login_required
def baixar(request, pk):
    d = get_object_or_404(Documento, pk=pk)
    p = perfil_de(request.user)
    if p is None:
        raise Http404
    permitido = (
        d.solicitacao.aluno_id == request.user.pk
        or p.papel == "analista"
        or (p.papel == "diretor" and d.tipo in ("anexo1", "gru_comprovante"))
        or (d.participante_id and d.participante.usuario_id == request.user.pk)
    )
    if not permitido:
        raise Http404
    return FileResponse(d.arquivo.open("rb"), as_attachment=False, filename=d.nome_original or d.arquivo.name.split("/")[-1])


@login_required
def baixar_comprovante(request, pk):
    o = get_object_or_404(Orcamento, pk=pk)
    p = perfil_de(request.user)
    if p is None:
        raise Http404
    if o.solicitacao.aluno_id != request.user.pk and p.papel != "analista":
        raise Http404
    return FileResponse(o.comprovante.open("rb"), filename=o.comprovante.name.split("/")[-1])


@papel("aluno", "servidor")
def baixar_pacote(request, pk):
    sol = minha_solicitacao(request, pk)
    try:
        dados = dossie.pacote_zip(sol)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect("sol_envio", pk=pk)
    r = HttpResponse(dados, content_type="application/zip")
    r["Content-Disposition"] = f'attachment; filename="Pacote_Protocolo_{sol.codigo}.zip"'
    return r


@login_required
def baixar_modelo(request, nome):
    if nome not in ("anexo4", "anexo5", "anexo6", "requerimento"):
        raise Http404
    from django.conf import settings
    ext = "pdf" if nome == "requerimento" else "docx"
    return FileResponse(open(settings.MODELOS_DIR / f"{nome}.{ext}", "rb"), as_attachment=True,
                        filename=f"{nome}.{ext}")


# ---------------------------------------------------------------------------
# Diretor-Geral
# ---------------------------------------------------------------------------
@papel("diretor")
def diretor_lista(request):
    base = Assinatura.objects.filter(tipo_doc="anexo1", papel="diretor").select_related("solicitacao")
    pendentes = [a for a in base.filter(estado="pendente")
                 if a.solicitacao.assinaturas.filter(tipo_doc="anexo1", papel="aluno", estado="assinado").exists()]
    aguardando_aluno = [a for a in base.filter(estado="pendente") if a not in pendentes]
    assinados = base.filter(estado="assinado")
    if request.method == "POST":
        sol = get_object_or_404(Solicitacao, pk=request.POST.get("solicitacao"))
        try:
            if request.POST.get("participante"):
                e = get_object_or_404(Participante, pk=request.POST["participante"], solicitacao=sol)
                fluxo.confirmar_gru_estudante(sol, request.user, e)
            else:
                fluxo.confirmar_gru(sol, request.user)
            messages.success(request, "Devolução por GRU confirmada.")
        except ValidationError as err:
            messages.error(request, "; ".join(err.messages))
        return redirect("diretor_lista")
    grus = []
    for sol in Solicitacao.objects.filter(status="ag_devolucao"):
        doc = validacao.gru_do_pedido(sol)
        if doc:
            grus.append({"sol": sol, "e": None, "doc": doc, "nome": sol.proponente["nome"]})
    for e in Participante.objects.filter(devolucao_estado="pendente").select_related("solicitacao"):
        doc = validacao.doc_ativo(e.solicitacao, "gru_comprovante", e)
        if doc:
            grus.append({"sol": e.solicitacao, "e": e, "doc": doc, "nome": e.nome_exibido})
    return render(request, "auxilio/diretor_lista.html", {
        "pendentes": pendentes, "assinados": assinados, "aguardando_aluno": aguardando_aluno, "grus": grus})


@papel("diretor")
def diretor_detalhe(request, pk):
    sol = get_object_or_404(Solicitacao, pk=pk)
    ass = get_object_or_404(Assinatura, solicitacao=sol, tipo_doc="anexo1", papel="diretor")
    if request.method == "POST":
        try:
            if request.POST.get("acao") == "assinado":
                fluxo.receber_assinado(sol, request.user, "anexo1", "diretor", request.FILES["arquivo"])
                messages.success(request, "Anexo I assinado registrado.")
            elif request.POST.get("acao") == "devolver":
                nota = request.POST.get("nota", "").strip()
                if not nota:
                    raise ValidationError("Explique o motivo da devolução.")
                fluxo.devolver(sol, request.user, "anexo1", nota)
                messages.info(request, "Devolvido ao estudante para correção.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except KeyError:
            messages.error(request, "Selecione o PDF assinado.")
        return redirect("diretor_detalhe", pk=pk)
    aluno_ass = sol.assinaturas.filter(tipo_doc="anexo1", papel="aluno").first()
    return render(request, "auxilio/diretor_detalhe.html", {
        "sol": sol, "ass": ass, "r": calculo.resumo(sol), "doc": validacao.doc_ativo(sol, "anexo1"),
        "pode_assinar": bool(aluno_ass and aluno_ass.estado == "assinado" and ass.estado != "assinado"),
    })


# ---------------------------------------------------------------------------
# Analista DAES/PROEN
# ---------------------------------------------------------------------------
@papel("analista")
def analise_lista(request):
    filtro = request.GET.get("status", "")
    qs = Solicitacao.objects.exclude(status__in=["rascunho", "ag_aluno", "ag_diretor", "incompleta"])
    if filtro:
        qs = qs.filter(status=filtro)
    busca = request.GET.get("q", "").strip()
    if busca:
        # Nome do proponente ou de um estudante, matrícula, evento ou código (#2026-0001, 2026-0001 ou 1).
        from django.db.models import Q
        import re
        cond = (Q(aluno__perfil__nome_completo__icontains=busca) | Q(prop_nome__icontains=busca)
                | Q(evento_nome__icontains=busca) | Q(participantes__nome_completo__icontains=busca)
                | Q(participantes__usuario__perfil__nome_completo__icontains=busca)
                | Q(participantes__matricula__icontains=busca))
        codigo = re.fullmatch(r"#?(?:\d{4}-)?0*(\d+)", busca)
        if codigo:
            cond |= Q(pk=int(codigo.group(1)))
        qs = qs.filter(cond).distinct()
    return render(request, "auxilio/analise_lista.html", {
        "solicitacoes": qs.select_related("aluno__perfil"),
        "status_opcoes": [s for s in regras.STATUS if s[0] not in ("rascunho", "ag_aluno", "ag_diretor", "incompleta")],
        "filtro": filtro,
        "busca": busca,
    })


ACOES_ANALISTA = {
    "iniciar": ("em_analise", "Análise iniciada."),
    "deferir": ("deferida", "Deferida."),
    "indeferir": ("indeferida", "Indeferida."),
    "pagamento": ("ag_pagamento", "Encaminhada para pagamento."),
    "pago": ("paga", "Pagamento registrado."),
    "concluir": ("concluida", "Prestação de contas aprovada. Processo encerrado."),
    "pendencia": ("prestacao_pendencia", "Prestação de contas com pendência."),
    "arquivar": ("arquivada", "Recurso indeferido. Processo arquivado."),
    "abrir_prestacao": ("ag_prestacao", "Prestação de contas aberta."),
    "confirmar_gru": ("devolvida", "Devolução por GRU confirmada."),
}


@papel("analista")
def analise_detalhe(request, pk):
    sol = get_object_or_404(Solicitacao, pk=pk)
    if request.method == "POST":
        acao = request.POST.get("acao")
        justificativa = request.POST.get("justificativa", "").strip()
        try:
            if acao in ("confirmar_gru_estudante", "desfazer_desistencia", "cobrar_gru_estudante"):
                e = get_object_or_404(Participante, pk=request.POST.get("participante"), solicitacao=sol)
                {"confirmar_gru_estudante": fluxo.confirmar_gru_estudante,
                 "desfazer_desistencia": fluxo.desfazer_desistencia,
                 "cobrar_gru_estudante": fluxo.cobrar_gru_estudante}[acao](sol, request.user, e)
                messages.success(request, "Feito.")
                return redirect("analise_detalhe", pk=pk)
            if acao not in ACOES_ANALISTA:
                raise ValidationError("Ação inválida.")
            novo, msg = ACOES_ANALISTA[acao]
            if acao in ("indeferir", "pendencia", "arquivar") and not justificativa:
                raise ValidationError("A justificativa é obrigatória.")
            if acao in ("deferir", "indeferir", "arquivar"):
                sol.decisao_justificativa = justificativa
                sol.decisao_em = timezone.now()
                sol.save(update_fields=["decisao_justificativa", "decisao_em"])
            if acao == "confirmar_gru" and not validacao.gru_do_pedido(sol):
                raise ValidationError("O estudante ainda não enviou o comprovante da GRU.")
            if acao in ("pendencia", "concluir"):
                sol.prestacao_parecer = justificativa
                sol.save(update_fields=["prestacao_parecer"])
            fluxo.mudar_status(sol, novo, request.user, f"{msg} {justificativa}".strip())
            messages.success(request, msg)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("analise_detalhe", pk=pk)
    do_analista = {novo for novo, _ in ACOES_ANALISTA.values()}
    proximas = [p for p in regras.TRANSICOES.get(sol.status, []) if p in do_analista]
    return render(request, "auxilio/analise_detalhe.html", {
        "sol": sol, "r": calculo.resumo(sol), "checklist": validacao.checklist(sol),
        "pendencias": validacao.validar(sol), "historico": sol.historico.all(),
        "dossie": validacao.doc_ativo(sol, "dossie"),
        "docs": sol.documentos.filter(ativo=True).exclude(tipo="dossie"),
        "proximas": proximas, "prazo_recurso": fluxo.prazo_recurso(sol),
        "anexo6": validacao.doc_ativo(sol, "anexo6"),
        "comprovacoes": sol.documentos.filter(ativo=True, tipo__in=regras.COMPROVACOES_PRESTACAO).order_by("tipo", "criado_em"),
        "prestacao_pdf": validacao.doc_ativo(sol, "prestacao_pdf"),
        "gru": validacao.gru_do_pedido(sol),
        "desistencias": desistencias_do_pedido(sol),
        "prestacao_atrasada": validacao.prestacao_atrasada(sol),
        "prazo_prestacao": validacao.prazo_prestacao(sol),
    })
