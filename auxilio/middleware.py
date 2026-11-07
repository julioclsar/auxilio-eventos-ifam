"""Depois de salvar, volta para o item que a pessoa estava mexendo, com o aviso ao lado dele.

O navegador manda no POST o campo "alvo" (o id do item onde fica o formulário, ver base.html). Se a resposta
redireciona para uma página do sistema, o redirecionamento ganha #alvo e os avisos desse pedido ficam marcados com
o alvo. A tela então mostra o aviso dentro do item e não no topo, e não volta para o início da página.
"""

import re
from urllib.parse import urlsplit

ALVO = re.compile(r"[A-Za-z][\w-]{0,80}")


class VoltarAoItem:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        if request.method != "POST" or resposta.status_code not in (301, 302):
            return resposta
        alvo = request.POST.get("alvo", "")
        destino = resposta.get("Location", "")
        partes = urlsplit(destino)
        # Só dentro do próprio sistema. Se o item não existir na página de destino, o aviso fica no topo.
        if not ALVO.fullmatch(alvo) or "#" in destino or partes.netloc or not partes.path.startswith("/"):
            return resposta
        resposta["Location"] = f"{destino}#{alvo}"
        guardados = getattr(getattr(request, "_messages", None), "_queued_messages", [])
        for m in guardados:
            if not m.extra_tags:
                m.extra_tags = f"alvo:{alvo}"
        return resposta
