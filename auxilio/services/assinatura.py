"""Inspeção de assinaturas digitais em PDFs enviados.

É uma checagem de apoio (há assinatura? o nome esperado aparece?). Ela não substitui a
validação jurídica, feita em https://validar.iti.gov.br ou no assinador institucional.
"""

import io
import re
import unicodedata

from pypdf import PdfReader


def normalizar(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def inspecionar(dados: bytes, nomes_esperados=()):
    """Devolve um resumo da inspeção do PDF."""
    resultado = {
        "pdf_valido": True,
        "assinaturas": len(re.findall(rb"/ByteRange\s*\[", dados)),
        "nomes_encontrados": [],
        "nomes_esperados": list(nomes_esperados),
        "nomes_ok": None,
    }
    blobs = []
    try:
        leitor = PdfReader(io.BytesIO(dados))
        try:
            campos = leitor.get_fields() or {}
        except Exception:
            campos = {}
        for campo in campos.values():
            if campo.get("/FT") == "/Sig" and campo.get("/V"):
                sig = campo["/V"]
                if hasattr(sig, "get_object"):
                    sig = sig.get_object()
                for chave in ("/Name", "/Reason", "/Location"):
                    if sig.get(chave):
                        blobs.append(str(sig[chave]))
                conteudo = sig.get("/Contents")
                if conteudo:
                    bruto = bytes(conteudo)
                    blobs.append(bruto.decode("latin-1", errors="ignore"))
                    blobs.append(bruto.decode("utf-8", errors="ignore"))
        for pagina in leitor.pages[:12]:
            try:
                blobs.append(pagina.extract_text() or "")
            except Exception:
                pass
    except Exception:
        resultado["pdf_valido"] = False
        return resultado

    blob = normalizar(" ".join(blobs))
    for nome in nomes_esperados:
        if nome and normalizar(nome) in blob:
            resultado["nomes_encontrados"].append(nome)
    if nomes_esperados:
        resultado["nomes_ok"] = bool(resultado["nomes_encontrados"])
    return resultado


def resumo_texto(v):
    if not v.get("pdf_valido", True):
        return "Arquivo PDF ilegível."
    if v["assinaturas"] == 0:
        return "Nenhuma assinatura digital detectada no arquivo."
    base = f"{v['assinaturas']} assinatura(s) digital(is) detectada(s)"
    if v["nomes_ok"] is True:
        return f"{base}; nome esperado localizado."
    if v["nomes_ok"] is False:
        return f"{base}, mas o nome esperado não foi localizado."
    return base + "."
