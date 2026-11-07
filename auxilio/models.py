import secrets
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from . import regras


def _caminho(instance, filename):
    sol = getattr(instance, "solicitacao_id", None) or "x"
    return f"solicitacoes/{sol}/{timezone.now():%Y%m%d%H%M%S%f}_{filename}"


class Campus(models.Model):
    nome = models.CharField(max_length=120)
    sigla = models.CharField(max_length=10)
    municipio = models.CharField(max_length=80)
    uf = models.CharField(max_length=2, default="AM")
    diretor_nome = models.CharField("Diretor(a)-Geral", max_length=120)
    email_diretor = models.EmailField("E-mail do Diretor(a)-Geral", blank=True)
    email_protocolo = models.EmailField()
    email_servico_social = models.EmailField()
    email_servico_social_cc = models.EmailField(blank=True)

    def __str__(self):
        return f"{self.nome} ({self.sigla})"


class Edital(models.Model):
    numero = models.CharField(max_length=40, unique=True)
    titulo = models.CharField(max_length=200)
    vigente = models.BooleanField(default=True)
    regras = models.JSONField(default=dict)

    def __str__(self):
        return f"Edital {self.numero}"

    @property
    def valor_global(self):
        return Decimal(self.regras["valor_global"])

    @property
    def teto_proposta(self):
        return (self.valor_global * Decimal(self.regras["teto_proposta_pct"]) / 100).quantize(Decimal("0.01"))


class Perfil(models.Model):
    PAPEIS = [("aluno", "Estudante"), ("servidor", "Servidor(a)"), ("diretor", "Diretor-Geral"),
              ("analista", "Analista DAES/PROEN")]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="perfil")
    papel = models.CharField(max_length=10, choices=PAPEIS, default="aluno")
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT)
    nome_completo = models.CharField(max_length=120)
    # Dados do estudante usados no requerimento e nos anexos.
    matricula = models.CharField(max_length=20, blank=True)
    cpf = models.CharField(max_length=14, blank=True)
    rg = models.CharField(max_length=20, blank=True)
    rg_uf = models.CharField("UF do RG", max_length=2, blank=True)
    rg_expedicao = models.DateField("Data de expedição do RG", null=True, blank=True)
    nascimento = models.DateField(null=True, blank=True)
    endereco = models.CharField("Rua", max_length=120, blank=True)
    numero = models.CharField("Número", max_length=10, blank=True)
    complemento = models.CharField(max_length=60, blank=True)
    bairro = models.CharField(max_length=60, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    celular = models.CharField(max_length=20, blank=True)
    curso = models.CharField(max_length=120, blank=True)
    serie_periodo = models.CharField("Série/turma/período", max_length=10, blank=True)
    turno = models.CharField(max_length=10, blank=True)
    # Dados do servidor, usados quando ele é o proponente.
    siape = models.CharField("SIAPE", max_length=20, blank=True)
    cargo = models.CharField(max_length=80, blank=True)

    def __str__(self):
        return f"{self.nome_completo} ({self.get_papel_display()})"

    @property
    def endereco_completo(self):
        partes = [self.endereco.strip(), self.numero.strip()]
        texto = ", ".join(p for p in partes if p)
        return f"{texto} - {self.complemento.strip()}" if self.complemento.strip() else texto

    @property
    def completo(self):
        if self.papel == "servidor":
            return all([self.nome_completo, self.siape, self.cargo])
        obrigatorios = [self.matricula, self.cpf, self.rg, self.nascimento, self.endereco,
                        self.numero, self.bairro, self.curso]
        return all(obrigatorios)

    def idade_em(self, data):
        if not self.nascimento:
            return None
        return data.year - self.nascimento.year - ((data.month, data.day) < (self.nascimento.month, self.nascimento.day))


class Solicitacao(models.Model):
    # Conta que criou e preenche o pedido (estudante ou servidor). O nome do campo ficou da primeira versão.
    aluno = models.ForeignKey(User, on_delete=models.PROTECT, related_name="solicitacoes")
    # Proponente (item 7.1): o próprio estudante ou o servidor responsável. É o mesmo em todo o processo.
    PROPONENTES = [("estudante", "O próprio estudante"), ("servidor", "Um servidor responsável")]
    proponente_tipo = models.CharField("Quem é o proponente", max_length=10, choices=PROPONENTES, default="estudante")
    prop_nome = models.CharField("Nome do(a) servidor(a) proponente", max_length=120, blank=True)
    prop_siape = models.CharField("SIAPE", max_length=20, blank=True)
    prop_cargo = models.CharField("Cargo", max_length=80, blank=True)
    prop_email = models.EmailField("E-mail institucional", blank=True)
    edital = models.ForeignKey(Edital, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=regras.STATUS, default="rascunho")
    criada_em = models.DateTimeField(auto_now_add=True)

    # Evento
    evento_nome = models.CharField("Nome do evento", max_length=200, blank=True)
    natureza = models.CharField(max_length=20, choices=regras.NATUREZAS, default="cientifico")
    presencial = models.BooleanField("Participação presencial", default=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField(max_length=2, blank=True)
    evento_inicio = models.DateField("Início do evento", null=True, blank=True)
    evento_fim = models.DateField("Fim do evento", null=True, blank=True)
    viagem_ida = models.DateField("Data de ida", null=True, blank=True)
    viagem_volta = models.DateField("Data de retorno", null=True, blank=True)
    tipo_participacao = models.CharField("Tipo de participação", max_length=200, blank=True,
                                         default="Apresentador de trabalho científico.")

    # O que será solicitado
    pede_inscricao = models.BooleanField("Auxílio-Inscrição", default=True)
    pede_traslado = models.BooleanField("Auxílio-Traslado", default=True)
    pede_alimentacao = models.BooleanField("Auxílio-Alimentação", default=True)
    pede_hospedagem = models.BooleanField("Auxílio-Hospedagem", default=True)
    valor_inscricao = models.DecimalField("Valor da inscrição", max_digits=10, decimal_places=2, default=0)
    diarias_hospedagem = models.PositiveSmallIntegerField("Diárias de hospedagem", null=True, blank=True,
                                                          help_text="Em branco para calcular pelas datas da viagem.")
    # Ajuste manual da alimentação em dias inteiros, como a regra paga: diária inteira e meia-diária de embarque.
    alim_inteiras = models.PositiveSmallIntegerField("Dias com diária inteira", null=True, blank=True)
    alim_meias = models.PositiveSmallIntegerField("Dias de embarque com meia-diária", null=True, blank=True)

    # Condições que mudam a documentação
    servidor_acompanha = models.BooleanField("Um servidor acompanhará a viagem", default=False)
    reprovacoes_acima_limite = models.BooleanField(
        "Reprovação em mais de dois componentes no período (exige relatório pedagógico)", default=False)
    justificativa_prazo = models.TextField("Justificativa para prazo menor que 30 dias", blank=True)

    # Declarações de elegibilidade (sem integração com o SIGAA, ficam por conta do estudante)
    decl_paes = models.BooleanField("Estou inscrito e selecionado no Edital PAES, Grupo 01", default=False)
    decl_frequencia = models.BooleanField(
        "Tenho frequência igual ou superior a 75% e não tenho rendimento insuficiente em mais de dois componentes",
        default=False)
    decl_sem_pendencias = models.BooleanField(
        "Não tenho pendência de prestação de contas de eventos anteriores da Ação 2994", default=False)

    # Controle de versão dos documentos gerados
    dados_hash = models.CharField(max_length=64, blank=True)
    documentos_gerados_em = models.DateTimeField(null=True, blank=True)

    # Protocolo e decisão
    numero_processo = models.CharField("Número do processo", max_length=40, blank=True)
    protocolada_em = models.DateField(null=True, blank=True)
    decisao_justificativa = models.TextField(blank=True)
    decisao_em = models.DateTimeField(null=True, blank=True)
    recurso_texto = models.TextField(blank=True)

    # Prestação de contas e desistência
    # Coordenador(a) da participação: aparece no Anexo VI como proponente e assina o relatório.
    coordenador_nome = models.CharField("Nome do(a) servidor(a) responsável", max_length=120, blank=True)
    coordenador_siape = models.CharField("SIAPE", max_length=20, blank=True)
    coordenador_email = models.EmailField("E-mail do(a) servidor(a)", blank=True)
    coordenador_telefone = models.CharField("Telefone do(a) servidor(a)", max_length=20, blank=True)
    resultados_texto = models.TextField("Resultados obtidos com a participação", blank=True)
    prestacao_parecer = models.TextField(blank=True)
    desistencia_motivo = models.TextField(blank=True)
    desistencia_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criada_em"]

    def __str__(self):
        return f"#{self.codigo} {self.evento_nome or 'Sem título'}"

    @property
    def codigo(self):
        return f"{self.criada_em.year if self.criada_em else timezone.now().year}-{self.pk:04d}" if self.pk else "novo"

    @property
    def status_classe(self):
        """Cor do badge, conforme o design system: vermelho erro, amarelo pendente, verde andamento positivo."""
        if self.status in ("cancelada", "devolvida"):
            return "cinza"
        if self.status in ("indeferida", "arquivada", "incompleta", "prestacao_pendencia", "ag_devolucao"):
            return "erro"
        if self.status in ("rascunho", "ag_aluno", "ag_diretor", "em_analise", "em_recurso",
                           "ag_pagamento", "ag_prestacao", "prestacao_analise"):
            return "aviso"
        return ""

    @property
    def status_rotulo(self):
        return regras.STATUS_ROTULO.get(self.status, self.status)

    @property
    def valor_total(self):
        from . import calculo
        return calculo.resumo(self)["total"]

    @property
    def perfil(self):
        """Perfil de quem preenche o pedido."""
        return self.aluno.perfil

    @property
    def proponente(self):
        """Dados do proponente, iguais em todos os documentos (Anexos I, II, VI, requerimento e e-mails)."""
        if self.proponente_tipo == "servidor":
            if self.aluno.perfil.papel == "servidor":
                p = self.aluno.perfil
                return {"servidor": True, "nome": p.nome_completo, "cargo": p.cargo, "siape": p.siape,
                        "email": self.aluno.email, "telefone": p.celular or p.telefone, "e_quem_preenche": True}
            return {"servidor": True, "nome": self.prop_nome, "cargo": self.prop_cargo, "siape": self.prop_siape,
                    "email": self.prop_email, "telefone": "", "e_quem_preenche": False}
        p = self.aluno.perfil
        return {"servidor": False, "nome": p.nome_completo, "cargo": "Estudante", "siape": "Não se aplica",
                "email": self.aluno.email, "telefone": p.celular or p.telefone, "e_quem_preenche": True}

    @property
    def estudantes(self):
        return self.participantes.all()

    @property
    def n_estudantes(self):
        return max(self.participantes.count(), 1)

    @property
    def campus(self):
        return self.aluno.perfil.campus


class Participante(models.Model):
    """Estudante beneficiário (até 12, como no Anexo II). Pode ter conta própria ou ser preenchido pelo proponente."""
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="participantes")
    usuario = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="participacoes")
    ordem = models.PositiveSmallIntegerField(default=1)
    nome_completo = models.CharField(max_length=120)
    matricula = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    curso = models.CharField(max_length=120, blank=True)
    nascimento = models.DateField(null=True, blank=True)
    # Convite: o colega entra pelo link, vincula a conta e envia os próprios documentos.
    convite_token = models.CharField(max_length=40, blank=True, db_index=True)
    convite_aceito_em = models.DateTimeField(null=True, blank=True)
    # Declarações de elegibilidade de cada estudante (itens 3 e 5 do edital).
    decl_paes = models.BooleanField("Está inscrito e selecionado no Edital PAES, Grupo 01", default=False)
    decl_frequencia = models.BooleanField(
        "Tem frequência igual ou superior a 75% e não tem rendimento insuficiente em mais de dois componentes",
        default=False)
    decl_sem_pendencias = models.BooleanField(
        "Não tem pendência de prestação de contas de eventos anteriores da Ação 2994", default=False)
    reprovacoes_acima_limite = models.BooleanField(
        "Reprovação em mais de dois componentes no período (exige relatório pedagógico)", default=False)
    # Nada Consta (item 7.1.7), pedido por e-mail ao Serviço Social por cada estudante.
    nc_estado = models.CharField(max_length=15, default="nao_solicitado",
                                 choices=[("nao_solicitado", "Não solicitado"), ("solicitado", "Solicitado por e-mail"),
                                          ("recebido", "Recebido")])
    nc_solicitado_em = models.DateTimeField(null=True, blank=True)
    # Desistência de um estudante do grupo (item 10.4), sem cancelar o pedido dos outros.
    desistencia_em = models.DateTimeField(null=True, blank=True)
    desistencia_motivo = models.TextField(blank=True)
    DEVOLUCAO = [("", "Não se aplica"), ("suspenso", "Saiu antes do pagamento"),
                 ("pendente", "Devolução por GRU pendente"), ("confirmada", "Devolução confirmada")]
    devolucao_estado = models.CharField(max_length=12, choices=DEVOLUCAO, blank=True, default="")

    class Meta:
        ordering = ["ordem", "id"]

    def __str__(self):
        return self.nome_exibido

    def save(self, *args, **kwargs):
        if not self.convite_token:
            self.convite_token = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    @property
    def perfil(self):
        return getattr(self.usuario, "perfil", None) if self.usuario_id else None

    def _de_perfil(self, campo):
        p = self.perfil
        return getattr(p, campo, None) or getattr(self, campo)

    @property
    def nome_exibido(self):
        return self._de_perfil("nome_completo")

    @property
    def matricula_exibida(self):
        return self._de_perfil("matricula")

    @property
    def curso_exibido(self):
        return self._de_perfil("curso")

    @property
    def nascimento_exibido(self):
        return self._de_perfil("nascimento")

    @property
    def email_exibido(self):
        return (self.usuario.email if self.usuario_id else "") or self.email

    def idade_em(self, data):
        n = self.nascimento_exibido
        if not n:
            return None
        return data.year - n.year - ((data.month, data.day) < (n.month, n.day))

    @property
    def declaracoes_ok(self):
        return self.decl_paes and self.decl_frequencia and self.decl_sem_pendencias

    @property
    def desistiu(self):
        return bool(self.desistencia_em)

    @property
    def e_quem_preenche(self):
        return self.usuario_id == self.solicitacao.aluno_id


class Orcamento(models.Model):
    CATEGORIAS = [("traslado", "Traslado"), ("hospedagem", "Hospedagem")]
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="orcamentos")
    categoria = models.CharField(max_length=12, choices=CATEGORIAS)
    trecho = models.CharField(max_length=120, help_text="Ex.: Manaus - Cuiabá, ou Cuiabá (5 diárias)")
    ordem = models.PositiveSmallIntegerField(default=1)
    fonte = models.CharField("Empresa ou site", max_length=80, blank=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    comprovante = models.FileField(upload_to=_caminho, blank=True)

    class Meta:
        ordering = ["categoria", "trecho", "ordem"]

    def __str__(self):
        return f"{self.trecho} #{self.ordem} R$ {self.valor}"


class Documento(models.Model):
    ORIGENS = [("gerado", "Gerado pelo sistema"), ("aluno", "Enviado pelo estudante"),
               ("diretor", "Enviado pelo Diretor-Geral"), ("sistema", "Montado pelo sistema")]
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="documentos")
    tipo = models.CharField(max_length=30)
    origem = models.CharField(max_length=10, choices=ORIGENS)
    arquivo = models.FileField(upload_to=_caminho)
    nome_original = models.CharField(max_length=200, blank=True)
    versao = models.PositiveSmallIntegerField(default=1)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    verificacao = models.JSONField(default=dict, blank=True)
    # Documentos pessoais (histórico, RG/CPF, Nada Consta...) pertencem a um estudante do pedido.
    participante = models.ForeignKey(Participante, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="documentos")

    class Meta:
        ordering = ["-criado_em"]

    @property
    def rotulo(self):
        return regras.DOC_TIPOS.get(self.tipo, self.tipo)


class Assinatura(models.Model):
    ESTADOS = [("pendente", "Pendente"), ("assinado", "Assinado"), ("devolvido", "Devolvido para correção")]
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="assinaturas")
    tipo_doc = models.CharField(max_length=30)
    papel = models.CharField(max_length=10)
    ordem = models.PositiveSmallIntegerField(default=1)
    estado = models.CharField(max_length=10, choices=ESTADOS, default="pendente")
    documento = models.ForeignKey(Documento, null=True, blank=True, on_delete=models.SET_NULL)
    assinado_em = models.DateTimeField(null=True, blank=True)
    nota = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["tipo_doc", "ordem"]

    @property
    def doc_rotulo(self):
        return regras.DOC_TIPOS.get(self.tipo_doc, self.tipo_doc)

    @property
    def papel_rotulo(self):
        return regras.PAPEL_ROTULO.get(self.papel, self.papel)


class Historico(models.Model):
    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="historico")
    em = models.DateTimeField(auto_now_add=True)
    por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    de_status = models.CharField(max_length=20, blank=True)
    para_status = models.CharField(max_length=20, blank=True)
    texto = models.CharField(max_length=400)

    class Meta:
        ordering = ["-em"]


class NadaConsta(models.Model):
    """Etapa externa, pedida por e-mail ao Serviço Social (item 7.1.7)."""
    ESTADOS = [("nao_solicitado", "Não solicitado"), ("solicitado", "Solicitado por e-mail"),
               ("recebido", "Recebido")]
    solicitacao = models.OneToOneField(Solicitacao, on_delete=models.CASCADE, related_name="nada_consta")
    estado = models.CharField(max_length=15, choices=ESTADOS, default="nao_solicitado")
    solicitado_em = models.DateTimeField(null=True, blank=True)
    recebido_em = models.DateTimeField(null=True, blank=True)
