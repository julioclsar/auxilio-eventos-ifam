"""Montagem do dossiê em PDF único (funcionalidade proposta pelo sistema, não exigência do edital).

Atenção: ao juntar PDFs, a validação criptográfica das assinaturas digitais de cada arquivo é
perdida no consolidado (o carimbo visual permanece). Por isso o pacote para envio também inclui
os PDFs assinados originais.
"""

import io
import zipfile

from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.utils import timezone
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .. import calculo, regras, validacao
from ..models import Documento
from . import fluxo


def _paginas_de(arquivo_field):
    """Lê PDF ou imagem e devolve um PdfReader."""
    arquivo_field.open("rb")
    try:
        dados = arquivo_field.read()
    finally:
        arquivo_field.close()
    nome = arquivo_field.name.lower()
    if nome.endswith((".jpg", ".jpeg", ".png")):
        imagem = Image.open(io.BytesIO(dados)).convert("RGB")
        saida = io.BytesIO()
        imagem.save(saida, format="PDF", resolution=150.0)
        dados = saida.getvalue()
    return PdfReader(io.BytesIO(dados))


def sequencia(sol):
    """Lista ordenada de (rótulo, FieldFile) que entram no dossiê."""
    itens = []
    estudantes = list(sol.participantes.all())
    varios = len(estudantes) > 1

    def doc(tipo, e=None):
        d = validacao.doc_ativo(sol, tipo, e)
        if d:
            rotulo = regras.DOC_TIPOS[tipo] + (f", {e.nome_exibido}" if e is not None and varios else "")
            itens.append((rotulo, d.arquivo))

    for tipo in ["requerimento", "anexo1", "anexo2", "anexo3", "folder_evento", "aceite"]:
        doc(tipo)
    for e in estudantes:
        for tipo in ["historico", "identidade", "nada_consta"]:
            doc(tipo, e)
    doc("recibo_inscricao")
    for cat, rotulo in [("traslado", "Comprovantes de pesquisa de preços, traslado"),
                        ("hospedagem", "Comprovantes de pesquisa de preços, hospedagem")]:
        if calculo.efetivo(sol, cat):
            for t in calculo.menores_por_trecho(sol, cat):
                for o in t["orcamentos"]:
                    if o.comprovante:
                        itens.append((f"{rotulo}: {t['trecho']} (orçamento {o.ordem})", o.comprovante))
    for e in estudantes:
        for tipo in ["autorizacao_responsavel", "relatorio_pedagogico"]:
            doc(tipo, e)
    for tipo in ["anexo4", "anexo5", "outros"]:
        doc(tipo)
    return itens


def _juntar(itens):
    saida = PdfWriter()
    for numero, (rotulo, campo) in enumerate(itens, start=1):
        leitor = _paginas_de(campo)
        inicio = len(saida.pages)
        for pagina in leitor.pages:
            saida.add_page(pagina)
        saida.add_outline_item(f"{numero:02d} - {rotulo}", inicio)
    buf = io.BytesIO()
    saida.write(buf)
    return buf.getvalue(), len(saida.pages)


def sequencia_prestacao(sol):
    """Anexo VI assinado seguido de todas as comprovações, na ordem do edital."""
    itens = []
    anexo6 = validacao.doc_ativo(sol, "anexo6")
    if anexo6:
        itens.append((regras.DOC_TIPOS["anexo6"], anexo6.arquivo))
    for tipo in regras.COMPROVACOES_PRESTACAO:
        docs = sol.documentos.filter(tipo=tipo, ativo=True).order_by("criado_em")
        for i, d in enumerate(docs, start=1):
            rotulo = regras.DOC_TIPOS[tipo] + (f" ({i})" if docs.count() > 1 else "")
            itens.append((rotulo, d.arquivo))
    return itens


def montar_prestacao(sol, user):
    """PDF único da prestação, para juntar ao processo original no SIPAC (item 10.3)."""
    dados, paginas = _juntar(sequencia_prestacao(sol))
    nome = f"Prestacao_de_contas_{sol.codigo}.pdf"
    sol.documentos.filter(tipo="prestacao_pdf", ativo=True).update(ativo=False)
    versao = sol.documentos.filter(tipo="prestacao_pdf").count() + 1
    d = Documento(solicitacao=sol, tipo="prestacao_pdf", origem="sistema", versao=versao, nome_original=nome,
                  verificacao={"paginas": paginas})
    d.arquivo.save(nome, ContentFile(dados), save=False)
    d.save()
    fluxo.registrar(sol, user, f"PDF único da prestação de contas gerado ({paginas} páginas).")
    return d


def montar(sol, user):
    if not validacao.pode_enviar(sol):
        raise ValidationError("Ainda há pendências obrigatórias. Resolva-as antes de gerar o dossiê.")
    saida = PdfWriter()
    for numero, (rotulo, campo) in enumerate(sequencia(sol), start=1):
        leitor = _paginas_de(campo)
        inicio = len(saida.pages)
        for pagina in leitor.pages:
            saida.add_page(pagina)
        saida.add_outline_item(f"{numero:02d} - {rotulo}", inicio)
    buf = io.BytesIO()
    saida.write(buf)
    nome = f"Solicitacao_Auxilio_{sol.codigo}.pdf"
    sol.documentos.filter(tipo="dossie", ativo=True).update(ativo=False)
    versao = sol.documentos.filter(tipo="dossie").count() + 1
    d = Documento(solicitacao=sol, tipo="dossie", origem="sistema", versao=versao, nome_original=nome,
                  verificacao={"paginas": len(saida.pages)})
    d.arquivo.save(nome, ContentFile(buf.getvalue()), save=False)
    d.save()
    fluxo.registrar(sol, user, f"PDF único gerado ({len(saida.pages)} páginas).")
    return d


def pacote_zip(sol):
    """Dossiê consolidado mais os PDFs assinados originais, para envio ao Protocolo."""
    dossie = validacao.doc_ativo(sol, "dossie")
    if not dossie:
        raise ValidationError("Gere o dossiê primeiro.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        dossie.arquivo.open("rb")
        z.writestr(dossie.nome_original or "dossie.pdf", dossie.arquivo.read())
        dossie.arquivo.close()
        for tipo in ("requerimento", "anexo1", "anexo2"):
            d = validacao.doc_ativo(sol, tipo)
            if d:
                d.arquivo.open("rb")
                z.writestr(f"originais_assinados/{tipo}.pdf", d.arquivo.read())
                d.arquivo.close()
    return buf.getvalue()
