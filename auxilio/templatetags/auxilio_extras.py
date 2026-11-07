from django import template

from .. import calculo, regras

register = template.Library()


@register.filter
def brl(valor):
    if valor in (None, ""):
        return ""
    return calculo.brl(valor).replace("R$", "R$ ")


@register.filter
def data_br(valor):
    return valor.strftime("%d/%m/%Y") if valor else ""


@register.filter
def hora_br(valor):
    from django.utils import timezone
    return timezone.localtime(valor).strftime("%d/%m/%Y %H:%M") if valor else ""


@register.filter
def rotulo_doc(tipo):
    return regras.DOC_TIPOS.get(tipo, tipo)


@register.simple_tag
def etapas(sol):
    atual = regras.ORDEM_STATUS.get(sol.status, 0)
    saida = []
    for i, nome in enumerate(regras.ETAPAS_LINHA_DO_TEMPO):
        estado = "feito" if i < atual else ("atual" if i == atual else "futuro")
        if regras.ETAPA_BLOQUEADA.get(sol.status) == i:
            estado = "bloqueado"
        saida.append({"nome": nome, "estado": estado})
    return saida


@register.filter
def inteiro_ou_meio(valor):
    return ("%s" % valor).rstrip("0").rstrip(".") if "." in ("%s" % valor) else valor


@register.simple_tag
def prazo_prestacao_info(sol):
    """Prazo da prestação para mostrar na lista, só quando ela está em aberto."""
    from .. import validacao
    if sol.status not in ("paga", "ag_prestacao", "prestacao_pendencia"):
        return None
    prazo = validacao.prazo_prestacao(sol)
    if not prazo:
        return None
    return {"prazo": prazo, "atrasada": validacao.prestacao_atrasada(sol)}


@register.simple_tag
def versao_estatico(caminho):
    """Muda a URL do CSS quando o arquivo muda, para o navegador não usar a versão antiga guardada."""
    import os
    from django.contrib.staticfiles import finders
    arquivo = finders.find(caminho)
    return int(os.path.getmtime(arquivo)) if arquivo else 0


@register.filter
def pega(dicionario, chave):
    """Valor de um dicionário pela chave, no template: {{ d|pega:nome }}."""
    return dicionario.get(chave) if hasattr(dicionario, "get") else None


@register.filter
def setas(trecho):
    """"Manaus - Cuiabá" vira "Manaus → Cuiabá" na tela (o documento continua com o hífen)."""
    return str(trecho).replace(" - ", " → ")
