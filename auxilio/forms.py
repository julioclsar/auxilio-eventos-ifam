import re
from datetime import timedelta
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from . import calculo, regras
from .models import Orcamento, Participante, Perfil, Solicitacao


class ReaisField(forms.CharField):
    """Aceita 4.848,72 / 4848,72 / 4848.72 / 1.234 / R$ 4.848,72. A tela formata ao sair do campo."""

    widget = forms.TextInput(attrs={"inputmode": "decimal", "data-mascara": "reais", "placeholder": "0,00",
                                    "autocomplete": "off"})

    def to_python(self, value):
        valor = super().to_python(value)
        if valor in (None, ""):
            return None
        s = str(valor).replace("R$", "").replace(" ", "").strip()
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", s):
            s = s.replace(".", "")  # ponto como separador de milhar (1.234 ou 1.234.567)
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise forms.ValidationError("Informe um valor em reais, por exemplo 4.848,72.")
        if d < 0:
            raise forms.ValidationError("O valor não pode ser negativo.")
        return d.quantize(Decimal("0.01"))

    def prepare_value(self, value):
        if isinstance(value, Decimal):
            return calculo.brl(value, simbolo=False)
        return value


def _inteiro():
    return forms.TextInput(attrs={"inputmode": "numeric", "pattern": "[0-9]*", "maxlength": 3, "autocomplete": "off",
                                  "data-mascara": "inteiro"})


def _data():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
       "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]
TURNOS = ["Matutino", "Vespertino", "Noturno", "Integral"]


def _digitos(v):
    return re.sub(r"\D", "", v or "")


def cpf_valido(cpf):
    d = _digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def formatar_telefone(v):
    d = _digitos(v)
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return v


class PerfilForm(forms.ModelForm):
    rg_uf = forms.ChoiceField(label="UF", choices=[("", "Selecione")] + [(u, u) for u in UFS])
    turno = forms.ChoiceField(label="Turno", choices=[("", "Selecione")] + [(t, t) for t in TURNOS])
    OBRIGATORIOS = ["nome_completo", "matricula", "cpf", "rg", "rg_uf", "nascimento", "endereco", "numero",
                    "bairro", "celular", "curso", "serie_periodo", "turno"]

    class Meta:
        model = Perfil
        fields = ["nome_completo", "matricula", "cpf", "rg", "rg_uf", "rg_expedicao", "nascimento",
                  "endereco", "numero", "complemento", "bairro", "telefone", "celular", "curso",
                  "serie_periodo", "turno"]
        widgets = {
            "rg_expedicao": _data(), "nascimento": _data(),
            "matricula": forms.TextInput(attrs={"placeholder": "ex.: 2024000046", "inputmode": "numeric"}),
            "cpf": forms.TextInput(attrs={"placeholder": "000.000.000-00", "inputmode": "numeric", "data-mascara": "cpf"}),
            "rg": forms.TextInput(attrs={"placeholder": "só os números"}),
            "endereco": forms.TextInput(attrs={"placeholder": "ex.: Rua Leandro Ferreira"}),
            "numero": forms.TextInput(attrs={"placeholder": "ex.: 12 ou S/N"}),
            "complemento": forms.TextInput(attrs={"placeholder": "ex.: Bloco B, apto 3"}),
            "telefone": forms.TextInput(attrs={"placeholder": "(92) 3333-4444", "inputmode": "tel", "data-mascara": "telefone"}),
            "celular": forms.TextInput(attrs={"placeholder": "(92) 99999-9999", "inputmode": "tel", "data-mascara": "telefone"}),
            "curso": forms.TextInput(attrs={"placeholder": "ex.: Bacharelado em Engenharia de Software"}),
            "serie_periodo": forms.TextInput(attrs={"placeholder": "ex.: 6P"}),
        }
        labels = {"matricula": "Matrícula", "cpf": "CPF", "rg": "RG", "nascimento": "Data de nascimento",
                  "rg_expedicao": "Data de expedição do RG", "endereco": "Rua", "numero": "Número",
                  "telefone": "Telefone fixo", "serie_periodo": "Série/turma/período"}
        help_texts = {"celular": "Pode digitar só os números, o sistema formata.",
                      "numero": "Use S/N se não houver número."}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for campo in self.OBRIGATORIOS:
            self.fields[campo].required = True

    def clean_cpf(self):
        cpf = self.cleaned_data.get("cpf", "")
        if not cpf_valido(cpf):
            raise forms.ValidationError("CPF inválido. Confira os 11 números.")
        d = _digitos(cpf)
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"

    def _telefone(self, campo, obrigatorio):
        v = self.cleaned_data.get(campo, "")
        if not v and not obrigatorio:
            return ""
        if len(_digitos(v)) not in (10, 11):
            raise forms.ValidationError("Informe DDD e número, por exemplo (92) 99999-9999.")
        return formatar_telefone(v)

    def clean_celular(self):
        return self._telefone("celular", True)

    def clean_telefone(self):
        return self._telefone("telefone", False)

    def clean_matricula(self):
        return self.cleaned_data.get("matricula", "").strip()


class PerfilServidorForm(forms.ModelForm):
    class Meta:
        model = Perfil
        fields = ["nome_completo", "siape", "cargo", "telefone", "celular"]
        labels = {"cargo": "Cargo (ex.: Professor EBTT)"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for campo in ("siape", "cargo"):
            self.fields[campo].required = True


class ParticipanteForm(forms.ModelForm):
    """Dados e declarações de um estudante do pedido."""
    class Meta:
        model = Participante
        fields = ["nome_completo", "matricula", "email", "curso", "nascimento",
                  "decl_paes", "decl_frequencia", "decl_sem_pendencias", "reprovacoes_acima_limite"]
        widgets = {"nascimento": _data()}
        labels = {"nome_completo": "Nome completo", "email": "E-mail institucional", "matricula": "Matrícula",
                  "nascimento": "Data de nascimento"}

    def __init__(self, *a, so_declaracoes=False, **kw):
        super().__init__(*a, **kw)
        if so_declaracoes:  # estudante com conta: os dados vêm do perfil dele
            for campo in ("nome_completo", "matricula", "email", "curso", "nascimento"):
                del self.fields[campo]

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()


class SolicitacaoForm(forms.ModelForm):
    valor_inscricao = ReaisField(label="Valor da inscrição (R$)", required=False)

    class Meta:
        model = Solicitacao
        fields = ["proponente_tipo", "prop_nome", "prop_siape", "prop_cargo", "prop_email",
                  "evento_nome", "natureza", "presencial", "cidade", "uf", "evento_inicio", "evento_fim",
                  "viagem_ida", "viagem_volta", "tipo_participacao",
                  "pede_inscricao", "pede_traslado", "pede_alimentacao", "pede_hospedagem",
                  "valor_inscricao", "diarias_hospedagem", "alim_inteiras", "alim_meias",
                  "servidor_acompanha", "justificativa_prazo"]
        labels = {"uf": "UF", "cidade": "Cidade",
                  "servidor_acompanha": "Um servidor vai junto na viagem (entram os Anexos IV e V)"}
        widgets = {
            "evento_inicio": _data(), "evento_fim": _data(), "viagem_ida": _data(), "viagem_volta": _data(),
            "justificativa_prazo": forms.Textarea(attrs={"rows": 3}),
            "natureza": forms.Select(choices=[(c, r.split(" (")[0]) for c, r in regras.NATUREZAS]),
            "uf": forms.Select(choices=[("", "Selecione")] + [(u, u) for u in UFS]),
            "proponente_tipo": forms.RadioSelect,
            # Diárias são números inteiros: texto com teclado numérico, sem as setinhas do campo numérico.
            "diarias_hospedagem": _inteiro(), "alim_inteiras": _inteiro(), "alim_meias": _inteiro(),
        }

    # Necessários para gerar os documentos. O rascunho pode ser salvo sem eles, então não bloqueiam o
    # "Salvar", mas levam o asterisco vermelho para a pessoa reconhecer o que falta.
    OBRIGATORIOS_ENVIO = ["evento_nome", "natureza", "cidade", "uf", "evento_inicio", "evento_fim"]

    def __init__(self, *a, servidor=False, **kw):
        super().__init__(*a, **kw)
        if servidor:  # quem preenche é servidor: ele mesmo é o proponente
            for campo in ("proponente_tipo", "prop_nome", "prop_siape", "prop_cargo", "prop_email"):
                del self.fields[campo]
        for campo in self.OBRIGATORIOS_ENVIO:
            if campo in self.fields:
                self.fields[campo].obrig_envio = True
        for campo in ("prop_nome", "prop_email"):
            if campo in self.fields:
                self.fields[campo].obrig_envio = True
        # Em branco vale a regra: o número calculado aparece no campo, em cinza, e na ajuda.
        sol = self.instance
        inteiras, meias = calculo.composicao_regra(sol)
        valor = Decimal(sol.edital.regras["alimentacao"]["valor_diaria"]) if sol.edital_id else None
        if valor:
            self.fields["alim_inteiras"].label = f"Diárias inteiras ({calculo.brl(valor).replace('R$', 'R$ ')})"
            self.fields["alim_meias"].label = f"Meias-diárias ({calculo.brl(valor / 2).replace('R$', 'R$ ')})"
        for campo, padrao in [("diarias_hospedagem", calculo.noites(sol)), ("alim_inteiras", inteiras), ("alim_meias", meias)]:
            f = self.fields[campo]
            f.error_messages["invalid"] = "Use só números inteiros, sem vírgula."
            f.widget.attrs["placeholder"] = str(padrao)
            f.help_text = ""
        self.fields["justificativa_prazo"].nota_rotulo = (
            "(o edital pede 30 dias entre o resultado final e o início do evento, item 6.2. "
            "Só preencha se o prazo for menor)")

    def clean_valor_inscricao(self):
        return self.cleaned_data.get("valor_inscricao") or Decimal("0.00")

    def clean_uf(self):
        return (self.cleaned_data.get("uf") or "").upper()

    def clean(self):
        dados = super().clean()
        # Ida e retorno que estavam no padrão acompanham a mudança das datas do evento.
        antigo = self.instance
        um_dia = timedelta(days=1)
        for ev, vi, delta in [("evento_inicio", "viagem_ida", -um_dia), ("evento_fim", "viagem_volta", um_dia)]:
            velho_ev, velho_vi = getattr(antigo, ev), getattr(antigo, vi)
            if (velho_ev and velho_vi == velho_ev + delta and dados.get(ev) and dados.get(ev) != velho_ev
                    and dados.get(vi) == velho_vi):
                dados[vi] = dados[ev] + delta
        # Padrão do campus: viaja um dia antes do evento e volta um dia depois.
        if dados.get("evento_inicio") and not dados.get("viagem_ida"):
            dados["viagem_ida"] = dados["evento_inicio"] - timedelta(days=1)
        if dados.get("evento_fim") and not dados.get("viagem_volta"):
            dados["viagem_volta"] = dados["evento_fim"] + timedelta(days=1)
        # Limites que a própria regra impõe às diárias.
        ida, volta = dados.get("viagem_ida"), dados.get("viagem_volta")
        if dados.get("alim_meias") is not None and dados["alim_meias"] > 2:
            self.add_error("alim_meias", "No máximo 2: uma no dia de ida e outra no dia de volta.")
        if ida and volta and volta >= ida:
            dias, noites = (volta - ida).days + 1, (volta - ida).days
            padrao = calculo.composicao_regra(SimpleNamespace(viagem_ida=ida, viagem_volta=volta))
            inteiras = dados.get("alim_inteiras") if dados.get("alim_inteiras") is not None else padrao[0]
            meias = dados.get("alim_meias") if dados.get("alim_meias") is not None else padrao[1]
            if inteiras + meias > dias and not self.has_error("alim_meias"):
                self.add_error("alim_inteiras", f"Diárias inteiras e meias somam {inteiras + meias}, mas a viagem tem "
                                                f"{dias} dias.")
            if dados.get("diarias_hospedagem") is not None and dados["diarias_hospedagem"] > noites:
                self.add_error("diarias_hospedagem", f"A viagem tem {noites} noite(s).")
        if dados.get("proponente_tipo") == "servidor":
            for campo, rotulo in [("prop_nome", "o nome"), ("prop_email", "o e-mail")]:
                if not (dados.get(campo) or "").strip():
                    self.add_error(campo, f"Informe {rotulo} do(a) servidor(a) proponente.")
        return dados


class OrcamentoForm(forms.ModelForm):
    """Um orçamento de um trecho já escolhido na tela. Empresa, valor e print são obrigatórios (itens 7.1.9 e 7.1.10)."""
    valor = ReaisField(label="Valor (R$)")
    categoria = forms.ChoiceField(choices=Orcamento.CATEGORIAS, widget=forms.HiddenInput)

    class Meta:
        model = Orcamento
        fields = ["categoria", "trecho", "fonte", "valor", "comprovante"]
        labels = {"trecho": "Trecho", "comprovante": "Print da pesquisa (imagem ou PDF)"}

    def __init__(self, *a, trecho_aberto=False, **kw):
        super().__init__(*a, **kw)
        self.fields["fonte"].required = True
        self.fields["comprovante"].required = True
        hosp = (self.initial.get("categoria") or self.data.get(self.add_prefix("categoria"))) == "hospedagem"
        self.fields["fonte"].label = "Hotel ou plataforma" if hosp else "Empresa ou site"
        self.fields["fonte"].widget.attrs["placeholder"] = "ex.: Booking, Hotel Deville" if hosp else "ex.: LATAM, Azul, Decolar"
        self.fields["trecho"].help_text = ""
        if trecho_aberto:
            self.fields["trecho"].widget.attrs["placeholder"] = "ex.: Manaus - Parintins"
        else:
            self.fields["trecho"].widget = forms.HiddenInput()


class ArquivoForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo")


class DecisaoForm(forms.Form):
    justificativa = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Justificativa")


class ProtocoloForm(forms.Form):
    numero_processo = forms.CharField(label="Número do processo (se houver)", required=False, max_length=40)
    data = forms.DateField(label="Data do envio", widget=_data())


class CadastroForm(forms.Form):
    tipo = forms.ChoiceField(label="Eu sou", choices=[("aluno", "Estudante"), ("servidor", "Servidor(a)")],
                             widget=forms.RadioSelect, initial="aluno", required=False)

    def clean_tipo(self):
        return self.cleaned_data.get("tipo") or "aluno"
    nome_completo = forms.CharField(label="Nome completo", max_length=120)
    email = forms.EmailField(label="E-mail institucional (@ifam.edu.br)")
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput, help_text="Pelo menos 8 caracteres.")
    senha2 = forms.CharField(label="Repita a senha", widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if not email.endswith("@ifam.edu.br"):
            raise forms.ValidationError("Use o seu e-mail institucional, que termina em @ifam.edu.br.")
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("Já existe uma conta com esse e-mail. Use Entrar.")
        return email

    def clean(self):
        d = super().clean()
        if d.get("senha"):
            validate_password(d["senha"])
            if d["senha"] != d.get("senha2"):
                self.add_error("senha2", "As senhas não conferem.")
        return d
