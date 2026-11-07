"""Motor de cálculo dos auxílios (item 22 do projeto).

Os valores são calculados uma vez aqui e reaproveitados em todos os anexos
(single source of truth). As regras vêm de Edital.regras.
"""

import unicodedata
from decimal import ROUND_HALF_UP, Decimal


def q(v):
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()


def mesma_cidade(sol):
    return bool(sol.cidade) and _norm(sol.cidade) == _norm(sol.campus.municipio)


def so_inscricao(sol):
    """Itens 4.4 e 4.5: evento virtual ou na cidade do campus só admite a inscrição."""
    return (not sol.presencial) or mesma_cidade(sol)


def efetivo(sol, item):
    """Se o auxílio será de fato solicitado, considerando as restrições do edital."""
    marcado = getattr(sol, f"pede_{item}")
    if item == "inscricao":
        return marcado
    return marcado and not so_inscricao(sol)


def noites(sol):
    if sol.viagem_ida and sol.viagem_volta:
        return max((sol.viagem_volta - sol.viagem_ida).days, 0)
    return 0


def diarias_hospedagem(sol):
    return sol.diarias_hospedagem if sol.diarias_hospedagem is not None else noites(sol)


def dias_viagem(sol):
    """Dias corridos da viagem, contando o dia de ida e o de volta."""
    if sol.viagem_ida and sol.viagem_volta and sol.viagem_volta >= sol.viagem_ida:
        return (sol.viagem_volta - sol.viagem_ida).days + 1
    return 0


def composicao_regra(sol):
    """(diárias inteiras, meias-diárias) pela regra do edital: meia no dia de ida e no de volta, inteira nos demais."""
    n = dias_viagem(sol)
    if n == 0:
        return 0, 0
    if n == 1:
        return 0, 1
    return n - 2, 2


def composicao_alimentacao(sol):
    """A regra, com o ajuste manual de cada parte quando informado."""
    inteiras, meias = composicao_regra(sol)
    if sol.alim_inteiras is not None:
        inteiras = sol.alim_inteiras
    if sol.alim_meias is not None:
        meias = sol.alim_meias
    return inteiras, meias


def diarias_alimentacao(sol):
    inteiras, meias = composicao_alimentacao(sol)
    return Decimal(inteiras) + Decimal(meias) / 2


def menores_por_trecho(sol, categoria):
    """Para cada trecho, os orçamentos e o menor preço (Anexo III)."""
    trechos = {}
    # Os trechos aparecem na ordem em que foram cadastrados (ida antes da volta).
    for o in sol.orcamentos.filter(categoria=categoria).order_by("id"):
        t = trechos.setdefault(o.trecho, {"trecho": o.trecho, "orcamentos": []})
        t["orcamentos"].append(o)
    for t in trechos.values():
        t["orcamentos"].sort(key=lambda o: (o.ordem, o.id))
        valores = [o.valor for o in t["orcamentos"]]
        t["menor"] = q(min(valores)) if valores else Decimal(0)
        t["completo"] = len(valores) >= 3 and all(o.comprovante for o in t["orcamentos"])
    return list(trechos.values())


def resumo(sol):
    regras = sol.edital.regras
    valor_diaria = Decimal(regras["alimentacao"]["valor_diaria"])

    inscricao = q(sol.valor_inscricao) if efetivo(sol, "inscricao") else Decimal(0)
    trechos_t = menores_por_trecho(sol, "traslado")
    trechos_h = menores_por_trecho(sol, "hospedagem")
    traslado = q(sum((t["menor"] for t in trechos_t), Decimal(0))) if efetivo(sol, "traslado") else Decimal(0)
    hospedagem = q(sum((t["menor"] for t in trechos_h), Decimal(0))) if efetivo(sol, "hospedagem") else Decimal(0)
    d_alim = diarias_alimentacao(sol)
    alimentacao = q(d_alim * valor_diaria) if efetivo(sol, "alimentacao") else Decimal(0)
    por_aluno_total = inscricao + traslado + alimentacao + hospedagem
    # Item 4.3: valor único por aluno participante. A proposta soma o valor de todos os estudantes.
    n = sol.n_estudantes if sol.pk else 1
    estudantes = list(sol.participantes.all()) if sol.pk else []
    # Quem desistiu antes do pagamento não recebe (item 10.4.1). Quem desistiu depois devolve a própria parte.
    n_pagos = sum(1 for e in estudantes if e.devolucao_estado != "suspenso") if estudantes else n
    n_ativos = sum(1 for e in estudantes if not e.desistiu) if estudantes else n
    return {
        "n": n,
        "n_pagos": n_pagos,
        "n_ativos": n_ativos,
        "total_pago": por_aluno_total * n_pagos,
        "total_ativos": por_aluno_total * n_ativos,
        "por_aluno": {"inscricao": inscricao, "traslado": traslado, "alimentacao": alimentacao,
                      "hospedagem": hospedagem, "total": por_aluno_total},
        "inscricao": inscricao * n,
        "traslado": traslado * n,
        "alimentacao": alimentacao * n,
        "hospedagem": hospedagem * n,
        "total": por_aluno_total * n,
        "diarias_alimentacao": d_alim,
        "alimentacao_inteiras": composicao_alimentacao(sol)[0],
        "alimentacao_meias": composicao_alimentacao(sol)[1],
        "alimentacao_manual": sol.alim_inteiras is not None or sol.alim_meias is not None,
        "valor_diaria": valor_diaria,
        "diarias_hospedagem": diarias_hospedagem(sol),
        "trechos_traslado": trechos_t,
        "trechos_hospedagem": trechos_h,
        "so_inscricao": so_inscricao(sol),
        "pedidos": {i: efetivo(sol, i) for i in ("inscricao", "traslado", "alimentacao", "hospedagem")},
        "teto": sol.edital.teto_proposta,
        "acima_do_teto": por_aluno_total * n > sol.edital.teto_proposta,
    }


def brl(v, simbolo=True):
    """Formata em reais no padrão brasileiro, como nos anexos (R$6.417,69)."""
    s = f"{Decimal(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R${s}" if simbolo else s
