"""Geração dos documentos do processo a partir dos modelos oficiais do edital.

- Anexos I a III e VI: preenchimento dos modelos .docx e conversão para PDF (LibreOffice).
- Requerimento do campus: texto sobreposto ao PDF padrão, caixa por caixa.
"""

import copy
import io
import os
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import black
from reportlab.pdfgen import canvas

from .. import calculo, regras
from ..calculo import brl

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]


class ConversaoIndisponivel(RuntimeError):
    pass


def data_extenso(d):
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def data_curta(d):
    return d.strftime("%d/%m/%Y") if d else ""


def _modelo(nome):
    return Path(settings.MODELOS_DIR) / nome


# ---------------------------------------------------------------------------
# docx -> pdf
# ---------------------------------------------------------------------------
def docx_para_pdf(caminho_docx: Path) -> bytes:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:  # instalação padrão no Windows e no macOS, que não entra no PATH
        for candidato in [r"C:\Program Files\LibreOffice\program\soffice.exe",
                          r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                          "/Applications/LibreOffice.app/Contents/MacOS/soffice"]:
            if os.path.exists(candidato):
                exe = candidato
                break
    if not exe:
        raise ConversaoIndisponivel(
            "LibreOffice não encontrado. Instale o LibreOffice para converter os anexos em PDF.")
    with tempfile.TemporaryDirectory() as perfil, tempfile.TemporaryDirectory() as saida:
        cmd = [exe, f"-env:UserInstallation={Path(perfil).as_uri()}", "--headless", "--convert-to", "pdf",
               "--outdir", saida, str(caminho_docx)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        pdf = Path(saida) / (caminho_docx.stem + ".pdf")
        return pdf.read_bytes()


def _salvar_e_converter(doc: Document) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "documento.docx"
        doc.save(caminho)
        return docx_para_pdf(caminho)


# ---------------------------------------------------------------------------
# Helpers de preenchimento de docx
# ---------------------------------------------------------------------------
def _anexar(par, texto, base=None):
    """Acrescenta um trecho ao parágrafo, com a fonte do rótulo mas sem negrito."""
    base = base or (par.runs[0] if par.runs else None)
    run = par.add_run(texto)
    if base is not None and base._r.rPr is not None:
        run._r.insert(0, copy.deepcopy(base._r.rPr))
    run.bold = False
    return run


def _substituir(par, texto):
    """Troca todo o texto do parágrafo mantendo a formatação do primeiro trecho."""
    if not par.runs:
        par.add_run(texto)
        return
    par.runs[0].text = texto
    for r in par.runs[1:]:
        r.text = ""


def _celulas_unicas(linha):
    vistas, saida = set(), []
    for c in linha.cells:
        if id(c._tc) not in vistas:
            vistas.add(id(c._tc))
            saida.append(c)
    return saida


def _celula_texto(celula, texto, tamanho=10, centro=True):
    par = celula.paragraphs[0]
    for r in par.runs:
        r.text = ""
    run = par.add_run(texto)
    run.font.size = Pt(tamanho)
    run.font.name = "Arial"
    run.bold = False
    if centro:
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _linha_data(doc, indice, campus, hoje):
    _substituir(doc.paragraphs[indice], f"{campus.municipio}-{campus.uf}, {data_extenso(hoje)}.")


# ---------------------------------------------------------------------------
# Anexo I
# ---------------------------------------------------------------------------
def gerar_anexo1(sol, hoje=None) -> bytes:
    hoje = hoje or date.today()
    campus, r = sol.campus, calculo.resumo(sol)
    doc = Document(_modelo("anexo1.docx"))
    t0, t1, t2 = doc.tables

    prop = sol.proponente  # o mesmo proponente em todo o processo
    _anexar(t0.rows[1].cells[0].paragraphs[0], prop["nome"])
    _anexar(t0.rows[2].cells[0].paragraphs[0], campus.nome)
    _anexar(t0.rows[3].cells[0].paragraphs[0], prop["cargo"])
    _substituir(t0.rows[3].cells[1].paragraphs[0], f"E-mail: {prop['email']}")
    _anexar(t0.rows[4].cells[0].paragraphs[0], prop["siape"])

    celula = t1.rows[1].cells[0]
    alvo = 2 + regras.NATUREZA_INDEX[sol.natureza]
    par = celula.paragraphs[alvo]
    par.runs[0].text = par.runs[0].text.replace("(   )", "( X )", 1)
    _anexar(t1.rows[2].cells[0].paragraphs[0], " " + sol.evento_nome)
    _anexar(t1.rows[3].cells[0].paragraphs[0], f" {sol.cidade}, {sol.uf} - Brasil")
    ini, fim = sol.viagem_ida or sol.evento_inicio, sol.viagem_volta or sol.evento_fim
    _substituir(t1.rows[3].cells[1].paragraphs[0], f"Período:  {data_curta(ini)} à {data_curta(fim)}")
    _anexar(t1.rows[4].cells[0].paragraphs[0], " " + sol.tipo_participacao)

    def bloco(linha, texto):
        _substituir(t2.rows[linha].cells[0].paragraphs[2], texto)

    n = r["n"]
    if calculo.efetivo(sol, "inscricao"):
        bloco(1, f"Valor unitário: {brl(r['por_aluno']['inscricao'])}.   Quantidade de participantes: {n}.")
    else:
        bloco(1, "Não solicitado.")
    for linha, chave in [(2, "traslado"), (3, "alimentacao"), (4, "hospedagem")]:
        if calculo.efetivo(sol, chave):
            bloco(linha, f"Quantidade de participantes: {n}. Valor total: {brl(r[chave])}.")
        else:
            bloco(linha, "Não solicitado.")

    _linha_data(doc, 9, campus, hoje)
    return _salvar_e_converter(doc)


# ---------------------------------------------------------------------------
# Anexo II
# ---------------------------------------------------------------------------
def gerar_anexo2(sol, hoje=None) -> bytes:
    hoje = hoje or date.today()
    campus, r = sol.campus, calculo.resumo(sol)
    doc = Document(_modelo("anexo2.docx"))
    tab = doc.tables[0]
    pa = r["por_aluno"]
    for i, e in enumerate(sol.participantes.all()[:regras.MAX_ESTUDANTES], start=1):
        linha = _celulas_unicas(tab.rows[i])
        valores = [str(i), e.nome_exibido, e.matricula_exibida,
                   brl(pa["inscricao"]) if pa["inscricao"] else "-",
                   brl(pa["traslado"]) if pa["traslado"] else "-",
                   brl(pa["alimentacao"]) if pa["alimentacao"] else "-",
                   brl(pa["hospedagem"]) if pa["hospedagem"] else "-",
                   brl(pa["total"])]
        for celula, texto in zip(linha, valores):
            _celula_texto(celula, texto, tamanho=9 if len(texto) > 18 else 10)
    total_linha = _celulas_unicas(tab.rows[13])
    _celula_texto(total_linha[-1], brl(r["total"]))
    _linha_data(doc, 8, campus, hoje)
    return _salvar_e_converter(doc)


# ---------------------------------------------------------------------------
# Anexo III
# ---------------------------------------------------------------------------
def preencher_anexo3(sol) -> Document:
    """Preenche o modelo do Anexo III. Separado da conversão para poder ser testado."""
    r = calculo.resumo(sol)
    doc = Document(_modelo("anexo3.docx"))
    blocos = [(doc.tables[0], r["trechos_traslado"] if calculo.efetivo(sol, "traslado") else [], False),
              (doc.tables[1], r["trechos_hospedagem"] if calculo.efetivo(sol, "hospedagem") else [], True)]
    for tabela, trechos, hospedagem in blocos:
        for i, t in enumerate(trechos[:5], start=1):
            celulas = _celulas_unicas(tabela.rows[i])
            nome = t["trecho"]
            if hospedagem and "diária" not in nome:
                nome = f"{nome} ({calculo.diarias_hospedagem(sol)} diárias)"
            _celula_texto(celulas[1], nome, centro=False)
            for j, o in enumerate(t["orcamentos"][:3]):
                _celula_texto(celulas[2 + j], brl(o.valor), centro=False)
            _celula_texto(celulas[5], brl(t["menor"]), centro=False)
    return doc


def gerar_anexo3(sol, hoje=None) -> bytes:
    return _salvar_e_converter(preencher_anexo3(sol))


# ---------------------------------------------------------------------------
# Anexo VI, relatório de prestação de contas
# ---------------------------------------------------------------------------
def preencher_anexo6(sol, hoje=None) -> Document:
    """Preenche o modelo do Anexo VI. Separado da conversão para poder ser testado."""
    hoje = hoje or date.today()
    campus = sol.campus
    doc = Document(_modelo("anexo6.docx"))
    ident = doc.tables[0]
    # Proponente = quem pediu, o mesmo do Anexo I (itens 7.1, 10.1 e 10.6). O coordenador só atesta, assinando.
    prop = sol.proponente
    linha_nome = _celulas_unicas(ident.rows[2])
    linha_contato = _celulas_unicas(ident.rows[3])
    _anexar(linha_nome[0].paragraphs[0], " " + prop["nome"])
    _anexar(linha_nome[-1].paragraphs[0], " " + prop["siape"])
    _anexar(linha_contato[0].paragraphs[0], " " + (prop["telefone"] or ""))
    _anexar(linha_contato[-1].paragraphs[0], " " + prop["email"])
    corpo = doc.tables[1]

    # Natureza: as opções estão na mesma ordem do Anexo I (a sétima, Visitas Técnicas, só existe aqui).
    opcoes = [p for p in corpo.rows[1].cells[0].paragraphs if p.text.strip().startswith("(")]
    indice = regras.NATUREZA_INDEX.get(sol.natureza)
    if indice is not None and indice < len(opcoes) and opcoes[indice].runs:
        primeiro = opcoes[indice].runs[0]
        primeiro.text = primeiro.text.replace("(", "( X", 1).replace("( X  ", "( X", 1)

    for linha in corpo.rows:
        for c in _celulas_unicas(linha):
            texto = c.text.strip()
            if texto.startswith("Local do Evento"):
                _anexar(c.paragraphs[0], f" {sol.cidade}, {sol.uf}")
            elif texto.startswith("Data de Início"):
                _anexar(c.paragraphs[0], f" {data_curta(sol.evento_inicio)}")
            elif texto.startswith("Data de Término"):
                _anexar(c.paragraphs[0], f" {data_curta(sol.evento_fim)}")

    # Resultados obtidos, escritos pelo estudante no sistema, logo abaixo das orientações do modelo.
    if sol.resultados_texto.strip():
        celula = corpo.rows[5].cells[0]
        com_texto = [p for p in celula.paragraphs if p.runs and p.text.strip()]
        base = com_texto[-1].runs[-1] if com_texto else None
        celula.add_paragraph("")
        participaram = [e for e in sol.participantes.all() if not e.desistiu]
        rotulo = "Estudante beneficiado(a)" if len(participaram) == 1 else "Estudantes beneficiados(as)"
        _anexar(celula.add_paragraph(), f"{rotulo}: " + "; ".join(
            f"{e.nome_exibido} (matrícula {e.matricula_exibida})" for e in participaram) + ".", base)
        celula.add_paragraph("")
        for bloco in sol.resultados_texto.strip().split("\n"):
            if bloco.strip():
                par = celula.add_paragraph()
                _anexar(par, bloco.strip(), base)

    for par in doc.paragraphs:
        if "-AM" in par.text and "de" in par.text:
            _substituir(par, f"{campus.municipio}-{campus.uf}, {data_extenso(hoje)}.")
        elif "Assinatura do (a) Coordenador" in par.text:
            _substituir(par, f"Assinatura do(a) Coordenador(a), {sol.coordenador_nome}, SIAPE {sol.coordenador_siape}")
            break
    return doc


def gerar_anexo6(sol, hoje=None) -> bytes:
    return _salvar_e_converter(preencher_anexo6(sol, hoje))


# ---------------------------------------------------------------------------
# Requerimento padrão do campus (PDF sem campos, texto sobreposto)
# ---------------------------------------------------------------------------
PT = 72 / 200  # pixels a 200 dpi -> pontos
ALTURA_PAGINA = 841.8

# Fronteiras verticais (px a 200 dpi) das caixinhas, detectadas no PDF do CMZL.
_GRADE = [285, 331, 377, 423, 469, 515, 561, 607, 653, 708, 757, 803, 849, 892, 938,
          984, 1030, 1076, 1122, 1168, 1214, 1260, 1306, 1352, 1398, 1444, 1490, 1536, 1582]
_DATA = [1122, 1168, 1214, 1260, 1306, 1352, 1398, 1444, 1490, 1536, 1582]
_CEL = [1168, 1214, 1260, 1306, 1352, 1398, 1444, 1490, 1536, 1582]
# Faixas horizontais (px a 200 dpi) de cada linha de caixinhas.
_FAIXA = {
    "nome1": (309, 350), "nome2": (350, 391), "matricula": (391, 446), "cpf": (446, 500),
    "rg": (500, 554), "end1": (554, 595), "end2": (595, 636), "bairro": (636, 677),
    "tel": (677, 718), "curso1": (718, 759), "curso2": (759, 800), "ano": (800, 841),
    "email": (841, 882),
}


def _quebrar(texto, largura):
    """Divide em linhas de até `largura` caracteres, sem partir palavras."""
    linhas, atual = [], ""
    for palavra in texto.split():
        if len(atual) + len(palavra) + (1 if atual else 0) <= largura:
            atual = f"{atual} {palavra}".strip()
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def _escrever_caixas(c, faixa, fronteiras, indices, texto, tamanho=11):
    """Escreve um caractere por caixa, centralizado."""
    y0, y1 = _FAIXA[faixa]
    base = ALTURA_PAGINA - ((y0 + y1) / 2) * PT - tamanho * 0.35
    for idx, ch in zip(indices, texto):
        if ch == " ":
            continue
        x = (fronteiras[idx] + fronteiras[idx + 1]) / 2 * PT
        c.drawCentredString(x, base, ch)


def _so_digitos(s):
    return "".join(ch for ch in s if ch.isdigit())


def _data_em_caixas(c, faixa, d):
    if not d:
        return
    _escrever_caixas(c, faixa, _DATA, [0, 1], f"{d.day:02d}")
    _escrever_caixas(c, faixa, _DATA, [3, 4], f"{d.month:02d}")
    _escrever_caixas(c, faixa, _DATA, [6, 7, 8, 9], f"{d.year:04d}")


def gerar_requerimento(sol, hoje=None) -> bytes:
    hoje = hoje or date.today()
    p = sol.perfil
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595.2, 841.8))
    c.setFillColor(black)
    c.setFont("Helvetica-Bold", 11)

    # Nome em até duas linhas de 28 caixas
    linhas = _quebrar(p.nome_completo.upper(), 28)
    for faixa, texto in zip(("nome1", "nome2"), linhas[:2]):
        _escrever_caixas(c, faixa, _GRADE, range(28), texto)
    _escrever_caixas(c, "matricula", _GRADE, range(15), _so_digitos(p.matricula)[:15])

    # CPF: 3 dígitos, ponto na caixa 3, 3 dígitos, ponto na caixa 7, 3 dígitos, hífen na 11, 2 dígitos
    d = _so_digitos(p.cpf).ljust(11)[:11]
    mapa = [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13]
    _escrever_caixas(c, "cpf", _GRADE, mapa, d)
    _data_em_caixas(c, "cpf", p.nascimento)

    rg = p.rg.upper().replace(" ", "")
    _escrever_caixas(c, "rg", _GRADE, range(10), rg[:10])
    _escrever_caixas(c, "rg", _GRADE, [12, 13], (p.rg_uf or "").upper()[:2])
    _data_em_caixas(c, "rg", p.rg_expedicao)

    for faixa, texto in zip(("end1", "end2"), _quebrar(p.endereco_completo.upper(), 28)[:2]):
        _escrever_caixas(c, faixa, _GRADE, range(28), texto)
    _escrever_caixas(c, "bairro", _GRADE, range(28), p.bairro.upper()[:28])

    def fone(faixa, fronteiras, numero):
        dig = _so_digitos(numero)[-8:]
        if len(dig) == 8:
            _escrever_caixas(c, faixa, fronteiras, [0, 1, 2, 3], dig[:4])
            _escrever_caixas(c, faixa, fronteiras, [5, 6, 7, 8], dig[4:])

    fone("tel", _GRADE, p.telefone)
    fone("tel", _CEL, p.celular)

    for faixa, texto in zip(("curso1", "curso2"), _quebrar(p.curso.upper(), 28)[:2]):
        _escrever_caixas(c, faixa, _GRADE, range(28), texto)

    ano = str(sol.viagem_ida.year if sol.viagem_ida else hoje.year)
    _escrever_caixas(c, "ano", _GRADE, [0, 1, 2, 3], ano)
    _escrever_caixas(c, "ano", [849, 892, 938, 984, 1030], [0, 1, 2, 3], p.serie_periodo.upper()[:4])
    # O formulário do campus tem 9 caixas para o turno. "VESPERTINO" não cabe, então sai abreviado.
    turno = p.turno.upper()
    turno = turno if len(turno) <= 9 else {"VESPERTINO": "VESP."}.get(turno, turno[:9])
    _escrever_caixas(c, "ano", [1214, 1260, 1306, 1352, 1398, 1444, 1490, 1536, 1582], range(9), turno)
    _escrever_caixas(c, "email", _GRADE, range(28), sol.aluno.email.upper()[:28], tamanho=10)

    # Marca "OUTROS (ESPECIFICAR)" na lista de solicitações
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(780 * PT, ALTURA_PAGINA - 1590 * PT, "X")

    # Documento anexo, outros, especificado (texto padrão usado nas solicitações aprovadas)
    c.setFont("Helvetica", 8.5)
    texto = f"Inscrição no EDITAL Nº {sol.edital.numero} {sol.edital.titulo.upper()}"
    for i, linha in enumerate(_quebrar(texto, 80)[:2]):
        c.drawString(191, ALTURA_PAGINA - (649 + 10 * i), linha)

    # Data, sobre as três lacunas de "DATA ___/___/___" (coordenadas medidas no PDF)
    c.setFont("Helvetica", 10)
    for x, txt in [(55.0, f"{hoje.day:02d}"), (75.9, f"{hoje.month:02d}"), (98.4, f"{hoje.year}")]:
        c.drawCentredString(x, ALTURA_PAGINA - 746.5, txt)
    c.save()

    base = PdfReader(str(_modelo("requerimento.pdf")))
    camada = PdfReader(io.BytesIO(buf.getvalue()))
    # A página entra no PDF de saída antes da sobreposição (exigência do pypdf 7; funciona também no 5 e no 6).
    saida = PdfWriter()
    pagina = saida.add_page(base.pages[0])
    pagina.merge_page(camada.pages[0])
    out = io.BytesIO()
    saida.write(out)
    return out.getvalue()


GERADORES = {
    "requerimento": gerar_requerimento,
    "anexo1": gerar_anexo1,
    "anexo2": gerar_anexo2,
    "anexo3": gerar_anexo3,
    "anexo6": gerar_anexo6,
}
