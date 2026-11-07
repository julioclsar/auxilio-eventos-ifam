from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("entrar/", views.Entrar.as_view(), name="entrar"),
    path("cadastro/", views.cadastro, name="cadastro"),
    path("sair/", auth_views.LogoutView.as_view(), name="sair"),
    path("perfil/", views.perfil, name="perfil"),
    path("ajuda/", views.ajuda, name="ajuda"),
    path("s/<int:pk>/excluir/", views.sol_excluir, name="sol_excluir"),
    path("solicitacoes/nova/", views.nova, name="nova"),

    path("s/<int:pk>/", views.sol_resumo, name="sol_resumo"),
    path("s/<int:pk>/dados/", views.sol_dados, name="sol_dados"),
    path("s/<int:pk>/estudantes/", views.sol_estudantes, name="sol_estudantes"),
    path("convite/<str:token>/", views.convite, name="convite"),
    path("participacao/<int:pid>/", views.participacao, name="participacao"),
    path("s/<int:pk>/orcamentos/", views.sol_orcamentos, name="sol_orcamentos"),
    path("s/<int:pk>/orcamentos/<int:oid>/excluir/", views.orcamento_excluir, name="orcamento_excluir"),
    path("s/<int:pk>/documentos/", views.sol_documentos, name="sol_documentos"),
    path("s/<int:pk>/documentos/nada-consta/", views.nada_consta_solicitado, name="nada_consta_solicitado"),
    path("s/<int:pk>/assinaturas/", views.sol_assinaturas, name="sol_assinaturas"),
    path("s/<int:pk>/envio/", views.sol_envio, name="sol_envio"),
    path("s/<int:pk>/prestacao/", views.sol_prestacao, name="sol_prestacao"),
    path("s/<int:pk>/pacote.zip", views.baixar_pacote, name="baixar_pacote"),

    path("arquivo/<int:pk>/", views.baixar, name="baixar"),
    path("comprovante/<int:pk>/", views.baixar_comprovante, name="baixar_comprovante"),
    path("modelo/<str:nome>/", views.baixar_modelo, name="baixar_modelo"),

    path("diretor/", views.diretor_lista, name="diretor_lista"),
    path("diretor/<int:pk>/", views.diretor_detalhe, name="diretor_detalhe"),

    path("analise/", views.analise_lista, name="analise_lista"),
    path("analise/<int:pk>/", views.analise_detalhe, name="analise_detalhe"),
]
