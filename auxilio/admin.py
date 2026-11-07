from django.contrib import admin

from . import models

for m in (models.Campus, models.Edital, models.Perfil, models.Solicitacao, models.Orcamento,
          models.Documento, models.Assinatura, models.Historico, models.NadaConsta):
    admin.site.register(m)
