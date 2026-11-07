"""Regras do edital como dados (versionáveis), sem lógica de tela.

Tudo que muda de edital para edital fica aqui ou no campo Edital.regras do banco.
"""

from decimal import Decimal

# ---------------------------------------------------------------------------
# Edital 03/2026/PROEN/IFAM
# ---------------------------------------------------------------------------
EDITAL_03_2026 = {
    "numero": "03/2026/PROEN/IFAM",
    "titulo": "Auxílio Estudantil para Participação em Eventos Acadêmicos",
    "valor_global": "700352.85",          # item 1.2
    "teto_proposta_pct": 5,               # item 5.3
    "prazo_minimo_dias": 30,              # item 6.2
    "prestacao_dias": 30,                 # item 10.1
    "recurso_dias_uteis": 2,              # item 8.2
    "participantes_max": 12,              # linhas do Anexo II
    "alimentacao": {
        "valor_diaria": "150.00",         # item 7.1.11
        # Item 7.1.11: meia-diária no dia do embarque. A viagem tem dois embarques
        # (ida e volta), então os dois dias das pontas valem meia-diária e os dias
        # do meio valem diária inteira. Ex.: 18/10 a 23/10 = 75 + 4 x 150 + 75 = 750.
    },
    # Janelas de submissão (item 6.1). Dado configurável, não regra fixa.
    "janelas": [
        {"mes": "Abril", "inicio": "2026-04-01", "fim": "2026-04-10"},
        {"mes": "Maio", "inicio": "2026-05-04", "fim": "2026-05-13"},
        {"mes": "Junho", "inicio": "2026-06-01", "fim": "2026-06-10"},
        {"mes": "Julho", "inicio": "2026-07-01", "fim": "2026-07-10"},
        {"mes": "Agosto", "inicio": "2026-08-03", "fim": "2026-08-25"},
    ],
}

# ---------------------------------------------------------------------------
# Natureza do evento (itens 3.1.x do edital, na ordem do Anexo I)
# ---------------------------------------------------------------------------
NATUREZAS = [
    ("cientifico", "Eventos científicos e tecnológicos (congressos, seminários, simpósios, fóruns, conferências, jornadas e colóquios) com trabalho aprovado"),
    ("cultural", "Eventos de natureza cultural, artística e de extensão, além de feiras (como apresentador, expositor ou outra participação ativa)"),
    ("esportivo", "Eventos esportivos (competições regionais, estaduais e nacionais representando o campus ou o IFAM)"),
    ("olimpiada", "Olimpíadas do conhecimento, competições acadêmicas, científicas ou tecnológicas"),
    ("movimento", "Eventos do movimento estudantil (grêmios, diretórios, centros acadêmicos e associações atléticas)"),
    ("representacao", "Eventos de representação em espaços institucionais para representantes da categoria discente"),
]
NATUREZA_INDEX = {codigo: i for i, (codigo, _) in enumerate(NATUREZAS)}
NATUREZA_ROTULO = dict(NATUREZAS)

# ---------------------------------------------------------------------------
# Tipos de documento
# ---------------------------------------------------------------------------
# gerado = o sistema produz o PDF; upload = o aluno envia; assinatura = quem assina e em que ordem
DOC_TIPOS = {
    "requerimento": "Requerimento padrão do campus",
    "anexo1": "Anexo I, Formulário de Submissão",
    "anexo2": "Anexo II, Relação dos estudantes beneficiários",
    "anexo3": "Anexo III, Registro de pesquisa de preços",
    "folder_evento": "Folder, cartaz ou carta convite do evento",
    "aceite": "Carta de aceite ou aprovação do trabalho",
    "historico": "Histórico escolar atualizado",
    "identidade": "Documento de identidade e CPF",
    "nada_consta": "Certidão de Nada Consta",
    "recibo_inscricao": "Comprovante de pagamento da inscrição",
    "autorizacao_responsavel": "Autorização dos pais ou responsáveis",
    "relatorio_pedagogico": "Relatório pedagógico",
    "anexo4": "Anexo IV, Termo de responsabilidade do proponente",
    "anexo5": "Anexo V, Termo de responsabilidade do Diretor-Geral",
    "outros": "Outros documentos",
    "dossie": "PDF único para o Protocolo",
    "anexo6": "Anexo VI, Relatório de prestação de contas",
    "prest_embarque": "Passagens ou cartões de embarque (ida e volta)",
    "prest_hospedagem": "Recibo da hospedagem",
    "prest_certificado": "Certificado de participação",
    "prest_outros": "Outras comprovações da participação",
    "prestacao_pdf": "PDF único da prestação de contas",
    "gru_comprovante": "Comprovante de pagamento da GRU",
}

# Documentos pessoais: um de cada estudante do pedido (itens 7.1.4 a 7.1.8).
DOCS_PESSOAIS = ["historico", "identidade", "nada_consta", "autorizacao_responsavel", "relatorio_pedagogico"]

# Limite de estudantes por proposta (linhas do Anexo II).
MAX_ESTUDANTES = 12

# Quem assina cada documento gerado, em ordem. "aluno" é o proponente (estudante ou servidor).
ASSINATURAS = {
    "requerimento": ["aluno"],
    "anexo1": ["aluno", "diretor"],
    "anexo2": ["aluno"],
    "anexo6": ["servidor"],  # o coordenador atesta o relatório (linha única do modelo)
}

PAPEL_ROTULO = {"aluno": "Proponente (gov.br)", "diretor": "Diretor-Geral",
                "servidor": "Coordenador(a)"}

# Ordem de montagem do PDF único (item 26 do projeto).
ORDEM_DOSSIE = [
    "requerimento", "anexo1", "anexo2", "anexo3", "folder_evento", "aceite",
    "historico", "identidade", "nada_consta", "recibo_inscricao",
    "autorizacao_responsavel", "relatorio_pedagogico", "anexo4", "anexo5", "outros",
]

# ---------------------------------------------------------------------------
# Máquina de estados
# ---------------------------------------------------------------------------
STATUS = [
    ("rascunho", "Rascunho"),
    ("ag_aluno", "Aguardando assinaturas do proponente"),
    ("ag_diretor", "Aguardando assinatura do Diretor-Geral"),
    ("incompleta", "Documentação incompleta"),
    ("pronta", "Pronta para protocolo"),
    ("protocolada", "Protocolada"),
    ("em_analise", "Em análise (DAES/PROEN)"),
    ("deferida", "Deferida"),
    ("indeferida", "Indeferida"),
    ("em_recurso", "Em recurso"),
    ("ag_pagamento", "Aguardando pagamento"),
    ("paga", "Paga"),
    ("ag_prestacao", "Aguardando prestação de contas"),
    ("prestacao_analise", "Prestação de contas em análise"),
    ("prestacao_pendencia", "Prestação de contas com pendência"),
    ("concluida", "Concluída"),
    ("arquivada", "Arquivada"),
    ("cancelada", "Cancelada por desistência"),
    ("ag_devolucao", "Aguardando devolução por GRU"),
    ("devolvida", "Valor devolvido por GRU"),
]
STATUS_ROTULO = dict(STATUS)

# Transições manuais permitidas (as de rascunho até "pronta" são calculadas).
TRANSICOES = {
    "pronta": ["protocolada"],
    "protocolada": ["em_analise", "cancelada"],
    "em_analise": ["deferida", "indeferida", "cancelada"],
    "indeferida": ["em_recurso", "cancelada"],
    "em_recurso": ["deferida", "arquivada", "cancelada"],
    "deferida": ["ag_pagamento", "cancelada"],
    "ag_pagamento": ["paga", "cancelada"],
    # Item 10.4: quem não participou do evento depois de pago devolve o valor por GRU.
    "paga": ["ag_prestacao", "ag_devolucao"],
    "ag_prestacao": ["prestacao_analise", "ag_devolucao"],
    "ag_devolucao": ["devolvida"],
    "prestacao_analise": ["concluida", "prestacao_pendencia"],
    "prestacao_pendencia": ["prestacao_analise"],
}

ETAPAS_LINHA_DO_TEMPO = [
    "Solicitação criada",
    "Preenchimento",
    "Assinatura do proponente",
    "Assinatura do Diretor-Geral",
    "Protocolada",
    "Análise DAES/PROEN",
    "Resultado",
    "Pagamento",
    "Evento e prestação de contas",
    "Concluída",
]

# Índice da etapa em andamento para cada status (as anteriores aparecem como concluídas).
ORDEM_STATUS = {
    "rascunho": 1, "ag_aluno": 2, "ag_diretor": 3, "incompleta": 3, "pronta": 4,
    "protocolada": 5, "em_analise": 5, "indeferida": 6, "em_recurso": 6, "arquivada": 6,
    "deferida": 7, "ag_pagamento": 7, "paga": 8, "ag_prestacao": 8,
    "prestacao_analise": 8, "prestacao_pendencia": 8, "concluida": 10,
    "cancelada": 6, "ag_devolucao": 8, "devolvida": 8,
}

# Etapa da linha do tempo que fica marcada como interrompida em cada status final ou de bloqueio.
ETAPA_BLOQUEADA = {"indeferida": 6, "arquivada": 6, "cancelada": 6, "ag_devolucao": 8, "devolvida": 8}

# Comprovações da prestação de contas. Cada tipo aceita vários arquivos.
# Art. 29, I, da Resolução 001-CONSUP/IFAM/2024 (citado no item 10.3 do edital): certificado de
# participação, relatório (Anexo VI, assinado pelo coordenador) e passagens de ida e volta quando
# não houver veículo oficial. O Anexo VI acrescenta o recibo da hospedagem.
COMPROVACOES_PRESTACAO = ["prest_embarque", "prest_hospedagem", "prest_certificado", "prest_outros"]

D = Decimal
