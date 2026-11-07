"""Testes do fluxo completo, do rascunho até a prestação de contas."""

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from auxilio import calculo, validacao
from auxilio.dados_de_teste import criar, pdf_demo
from auxilio.models import Orcamento, Solicitacao
from auxilio.services import assinatura as insp
from auxilio.services import fluxo

MEDIA = tempfile.mkdtemp()


def pdf(nome="x.pdf", titulo="Arquivo de teste"):
    return SimpleUploadedFile(nome, pdf_demo(titulo), content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class FluxoCompleto(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        criar(docs=True)
        self.sol = Solicitacao.objects.first()
        hoje = timezone.localdate()
        self.sol.evento_inicio = hoje + timedelta(days=45)
        self.sol.evento_fim = hoje + timedelta(days=48)
        self.sol.viagem_ida = hoje + timedelta(days=44)
        self.sol.viagem_volta = hoje + timedelta(days=49)
        self.sol.save()
        self.aluno, self.diretor, self.analista = Client(), Client(), Client()
        for c, u in [(self.aluno, "aluno"), (self.diretor, "diretor"), (self.analista, "analista")]:
            self.assertTrue(c.login(username=u, password="demo123"))

    def passar_evento(self):
        """Move as datas para o passado sem mexer no hash dos documentos (simula o tempo passando)."""
        hoje = timezone.localdate()
        Solicitacao.objects.filter(pk=self.sol.pk).update(
            evento_inicio=hoje - timedelta(days=5), evento_fim=hoje - timedelta(days=2),
            viagem_ida=hoje - timedelta(days=6), viagem_volta=hoje - timedelta(days=1))
        self.sol.refresh_from_db()

    def ir_para(self, status, **campos):
        Solicitacao.objects.filter(pk=self.sol.pk).update(status=status, **campos)
        self.sol.refresh_from_db()

    def baixar(self, doc):
        return SimpleUploadedFile("assinado.pdf", doc.arquivo.read(), content_type="application/pdf")

    # ------------------------------------------------------------------ cálculo
    def test_valores_do_caso_real(self):
        r = calculo.resumo(self.sol)
        self.assertEqual(r["inscricao"], Decimal("272.00"))
        self.assertEqual(r["traslado"], Decimal("6417.69"))   # 3.917,31 + 2.500,38
        self.assertEqual(r["alimentacao"], Decimal("750.00"))
        self.assertEqual(r["hospedagem"], Decimal("2625.00"))
        self.assertEqual(r["total"], Decimal("10064.69"))

    def test_alimentacao_meia_nos_embarques(self):
        # 18 a 23: 75 + 4 x 150 + 75 = 750
        self.assertEqual(calculo.composicao_alimentacao(self.sol), (4, 2))
        self.assertEqual(calculo.diarias_alimentacao(self.sol), Decimal("5"))
        self.assertEqual(calculo.resumo(self.sol)["alimentacao"], Decimal("750.00"))
        # viagem de 3 dias: 75 + 150 + 75 = 300
        self.sol.viagem_volta = self.sol.viagem_ida + timedelta(days=2)
        self.assertEqual(calculo.resumo(self.sol)["alimentacao"], Decimal("300.00"))
        # ida e volta no mesmo dia: uma meia-diária
        self.sol.viagem_volta = self.sol.viagem_ida
        self.assertEqual(calculo.resumo(self.sol)["alimentacao"], Decimal("75.00"))

    def test_evento_virtual_ou_na_cidade_so_inscricao(self):
        self.sol.presencial = False
        self.assertEqual(calculo.resumo(self.sol)["total"], Decimal("272.00"))
        self.sol.presencial = True
        self.sol.cidade = "Manaus"
        self.assertEqual(calculo.resumo(self.sol)["total"], Decimal("272.00"))

    def test_teto_por_proposta(self):
        self.sol.valor_inscricao = Decimal("40000.00")
        erros = [p["msg"] for p in validacao.validar(self.sol) if p["nivel"] == "erro"]
        self.assertTrue(any("teto" in e for e in erros))

    # --------------------------------------------------------------- validação
    def test_orcamento_incompleto_bloqueia_geracao(self):
        Orcamento.objects.filter(solicitacao=self.sol, trecho="Manaus - Cuiabá", ordem=3).delete()
        self.assertFalse(validacao.pode_gerar(self.sol))
        r = self.aluno.post(f"/s/{self.sol.pk}/assinaturas/", {"acao": "gerar"}, follow=True)
        self.assertContains(r, "faltam 1 orçamento")
        self.assertIsNone(Solicitacao.objects.get(pk=self.sol.pk).documentos_gerados_em)

    def test_orcamento_pela_vaga_do_trecho(self):
        """A tela monta o trecho; empresa, valor e print são obrigatórios; o quarto orçamento é recusado."""
        Orcamento.objects.filter(solicitacao=self.sol, trecho="Manaus - Cuiabá", ordem=3).delete()
        url = f"/s/{self.sol.pk}/orcamentos/"
        dados = {"bloco": "ida", "ida-categoria": "traslado", "ida-trecho": "Manaus - Cuiabá", "ida-fonte": "Gol",
                 "ida-valor": "1.234,50"}
        r = self.aluno.post(url, dados)
        self.assertContains(r, "Não foi possível salvar")
        self.assertEqual(Orcamento.objects.filter(solicitacao=self.sol, trecho="Manaus - Cuiabá").count(), 2)
        r = self.aluno.post(url, {**dados, "ida-comprovante": pdf()})
        self.assertEqual(r.status_code, 302)
        o = Orcamento.objects.get(solicitacao=self.sol, trecho="Manaus - Cuiabá", ordem=3)
        self.assertEqual((o.fonte, o.valor), ("Gol", Decimal("1234.50")))
        r = self.aluno.post(url, {**dados, "ida-comprovante": pdf()}, follow=True)
        self.assertContains(r, "já tem os três orçamentos")
        self.assertContains(self.aluno.get(url), "Ida · Manaus → Cuiabá")

    def test_prazo_menor_que_30_dias_exige_justificativa(self):
        self.sol.evento_inicio = timezone.localdate() + timedelta(days=5)
        self.sol.justificativa_prazo = ""
        erros = [p["msg"] for p in validacao.validar(self.sol) if p["nivel"] == "erro"]
        self.assertTrue(any("Justifique" in e for e in erros))

    def test_documentos_condicionais(self):
        self.sol.servidor_acompanha = True
        e = self.sol.participantes.first()
        e.reprovacoes_acima_limite = True
        e.save()
        codigos = {i["codigo"] for i in validacao.checklist(self.sol)}
        self.assertTrue({"anexo4", "anexo5", "relatorio_pedagogico"} <= codigos)

    # ------------------------------------------------------------------ acesso
    def test_aluno_nao_ve_solicitacao_de_outro(self):
        outro = User.objects.create_user("outro", password="x")
        from auxilio.models import Perfil
        Perfil.objects.create(user=outro, papel="aluno", campus=self.sol.campus, nome_completo="Outro")
        c = Client()
        c.login(username="outro", password="x")
        self.assertEqual(c.get(f"/s/{self.sol.pk}/").status_code, 404)

    def test_diretor_so_baixa_anexo1(self):
        fluxo.gerar_documentos(self.sol, self.aluno_user)
        req = self.sol.documentos.get(tipo="requerimento", origem="gerado")
        self.assertEqual(self.diretor.get(f"/arquivo/{req.pk}/").status_code, 404)

    @property
    def aluno_user(self):
        return User.objects.get(username="aluno")

    # ------------------------------------------------------------ fluxo inteiro
    def test_fluxo_do_rascunho_a_conclusao(self):
        sol, pk = self.sol, self.sol.pk

        # 1. gerar documentos
        r = self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "gerar"}, follow=True)
        self.assertEqual(r.status_code, 200)
        sol.refresh_from_db()
        self.assertEqual(sol.status, "ag_aluno")
        self.assertEqual(sol.assinaturas.count(), 4)  # requerimento, anexo1 (2), anexo2

        # 2. o diretor não pode assinar antes do estudante
        with self.assertRaises(Exception):
            fluxo.receber_assinado(sol, self.aluno_user, "anexo1", "diretor", pdf())

        # 3. o estudante assina os três documentos
        for tipo in ("requerimento", "anexo1", "anexo2"):
            gerado = sol.documentos.get(tipo=tipo, origem="gerado", ativo=True)
            self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "assinado", "tipo": tipo, "arquivo": self.baixar(gerado)})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "ag_diretor")

        # 4. caixa do diretor, devolução e nova assinatura
        self.assertContains(self.diretor.get("/diretor/"), "Maria Exemplo da Silva")
        self.diretor.post(f"/diretor/{pk}/", {"acao": "devolver", "nota": "Corrigir o período."})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "ag_aluno")
        anexo1 = sol.documentos.get(tipo="anexo1", origem="gerado", ativo=False)
        self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "assinado", "tipo": "anexo1", "arquivo": self.baixar(anexo1)})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "ag_diretor")
        self.diretor.post(f"/diretor/{pk}/", {"acao": "assinado", "arquivo": pdf("anexo1_diretor.pdf")})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "pronta", validacao.validar(sol))

        # 5. dossiê e protocolo
        self.aluno.post(f"/s/{pk}/envio/", {"acao": "dossie"})
        dossie = validacao.doc_ativo(sol, "dossie")
        self.assertIsNotNone(dossie)
        self.assertGreaterEqual(dossie.verificacao["paginas"], 14)
        self.assertEqual(self.aluno.get(f"/s/{pk}/pacote.zip").status_code, 200)
        self.aluno.post(f"/s/{pk}/envio/", {"acao": "protocolar", "numero_processo": "23000.000123/2026-11",
                                            "data": timezone.localdate().isoformat()})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "protocolada")

        # 6. análise: indeferir, recurso, deferir
        self.analista.post(f"/analise/{pk}/", {"acao": "iniciar"})
        self.analista.post(f"/analise/{pk}/", {"acao": "indeferir", "justificativa": ""})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "em_analise")  # justificativa obrigatória
        self.analista.post(f"/analise/{pk}/", {"acao": "indeferir", "justificativa": "Falta o Anexo II."})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "indeferida")
        self.assertIsNotNone(fluxo.prazo_recurso(sol))
        self.aluno.post(f"/s/{pk}/envio/", {"acao": "recurso", "recurso": "O Anexo II está no dossiê, página 4."})
        self.analista.post(f"/analise/{pk}/", {"acao": "deferir", "justificativa": "Recurso acatado."})
        for acao in ("pagamento", "pago"):
            self.analista.post(f"/analise/{pk}/", {"acao": acao})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "paga")

        # 7. prestação de contas: só abre depois do fim do evento
        self.aluno.post(f"/s/{pk}/envio/", {"acao": "evento_realizado"})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "paga")
        self.passar_evento()
        self.aluno.post(f"/s/{pk}/envio/", {"acao": "evento_realizado"})
        servidor = {"coordenador_nome": "Ana Orientadora Exemplo", "coordenador_siape": "1234567",
                    "coordenador_email": "ana.orientadora@ifam.edu.br", "coordenador_telefone": "(92) 3333-0000"}
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "gerar", "resultados": "", **servidor})
        self.assertFalse(sol.documentos.filter(tipo="anexo6").exists())  # resultados obrigatórios
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "gerar", "resultados": "Apresentei o artigo e recebi sugestões.", **servidor})
        anexo6 = sol.documentos.get(tipo="anexo6", origem="gerado")
        # só o coordenador assina o Anexo VI
        self.assertEqual(list(sol.assinaturas.filter(tipo_doc="anexo6").values_list("papel", flat=True)), ["servidor"])
        self.assertContains(self.aluno.get(f"/s/{pk}/prestacao/"), "ana.orientadora@ifam.edu.br")
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "assinado_servidor", "arquivo": self.baixar(anexo6)})
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "enviar"})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "ag_prestacao")  # ainda faltam comprovações
        for tipo in ("prest_embarque", "prest_embarque", "prest_hospedagem", "prest_certificado"):
            self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "comprovacao", "tipo": tipo, "arquivo": pdf()})
        self.assertEqual(sol.documentos.filter(tipo="prest_embarque", ativo=True).count(), 2)  # ida e volta
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "enviar"})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "prestacao_analise")
        self.assertIsNotNone(validacao.doc_ativo(sol, "prestacao_pdf"))
        self.assertContains(self.aluno.get(f"/s/{pk}/prestacao/"), "juntada ao processo nº 23000.000123/2026-11")
        # pendência apontada chega ao estudante
        self.analista.post(f"/analise/{pk}/", {"acao": "pendencia", "justificativa": "Falta o certificado assinado."})
        self.assertContains(self.aluno.get(f"/s/{pk}/prestacao/"), "Falta o certificado assinado.")
        self.aluno.post(f"/s/{pk}/prestacao/", {"acao": "enviar"})
        self.analista.post(f"/analise/{pk}/", {"acao": "concluir"})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "concluida")

        # todas as telas respondem
        for url in (f"/s/{pk}/", f"/s/{pk}/dados/", f"/s/{pk}/orcamentos/", f"/s/{pk}/documentos/",
                    f"/s/{pk}/assinaturas/", f"/s/{pk}/envio/", f"/s/{pk}/prestacao/"):
            self.assertEqual(self.aluno.get(url).status_code, 200, url)
        self.assertEqual(self.analista.get(f"/analise/{pk}/").status_code, 200)

    def test_diretor_assina_por_email_e_estudante_envia(self):
        sol, pk = self.sol, self.sol.pk
        self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "gerar"})
        for tipo in ("requerimento", "anexo1", "anexo2"):
            gerado = sol.documentos.get(tipo=tipo, origem="gerado", ativo=True)
            self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "assinado", "tipo": tipo, "arquivo": self.baixar(gerado)})
        r = self.aluno.get(f"/s/{pk}/assinaturas/")
        self.assertContains(r, "david.lima@ifam.edu.br")
        self.aluno.post(f"/s/{pk}/assinaturas/", {"acao": "assinado_diretor", "arquivo": pdf("anexo1_diretor.pdf")})
        sol.refresh_from_db()
        self.assertEqual(sol.status, "pronta")

    def test_cadastro_de_estudante(self):
        c = Client()
        r = c.post("/cadastro/", {"nome_completo": "Ana Teste", "email": "ana@gmail.com",
                                  "senha": "senha-longa-123", "senha2": "senha-longa-123"})
        self.assertContains(r, "@ifam.edu.br")
        r = c.post("/cadastro/", {"nome_completo": "Ana Teste", "email": "Ana@ifam.edu.br",
                                  "senha": "senha-longa-123", "senha2": "senha-longa-123"})
        self.assertRedirects(r, "/perfil/")
        u = User.objects.get(username="ana@ifam.edu.br")
        self.assertEqual(u.perfil.papel, "aluno")
        self.assertEqual(c.get("/solicitacoes/nova/").status_code in (302, 405), True)

    def test_excluir_solicitacao_so_antes_do_protocolo(self):
        pk = self.sol.pk
        self.assertEqual(self.aluno.get("/ajuda/").status_code, 200)
        self.aluno.post(f"/s/{pk}/excluir/")
        self.assertFalse(Solicitacao.objects.filter(pk=pk).exists())

    def test_anexo6_marca_a_natureza_e_traz_os_resultados(self):
        from auxilio.services.documentos import preencher_anexo6
        self.sol.natureza = "olimpiada"
        self.sol.resultados_texto = "Fiquei em segundo lugar."
        self.sol.coordenador_nome = "Ana Orientadora Exemplo"
        self.sol.coordenador_siape = "1234567"
        doc = preencher_anexo6(self.sol)
        ident = doc.tables[0]
        self.assertIn("Maria Exemplo da Silva", ident.rows[2].cells[0].text)  # proponente igual ao Anexo I
        self.assertIn("Não se aplica", ident.rows[2].cells[-1].text)
        textos = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("Coordenador(a), Ana Orientadora Exemplo, SIAPE 1234567", textos)
        self.assertNotIn("Assinatura do(a) estudante", textos)
        opcoes = [p.text for p in doc.tables[1].rows[1].cells[0].paragraphs if p.text.strip().startswith("(")]
        self.assertTrue(opcoes[3].startswith("( X ) Olimpíadas"), opcoes[3])
        self.assertFalse(any(o.startswith("( X") for i, o in enumerate(opcoes) if i != 3))
        self.assertIn("Fiquei em segundo lugar.", doc.tables[1].rows[5].cells[0].text)

    def test_anexo3_mostra_as_diarias_na_hospedagem(self):
        from auxilio.services.documentos import _celulas_unicas, preencher_anexo3
        doc = preencher_anexo3(self.sol)
        linha_hosp = _celulas_unicas(doc.tables[1].rows[1])
        self.assertIn(f"({calculo.diarias_hospedagem(self.sol)} diárias)", linha_hosp[1].text)
        linha_trasl = _celulas_unicas(doc.tables[0].rows[1])
        self.assertNotIn("diárias", linha_trasl[1].text)

    def test_recurso_fora_do_prazo_e_recusado(self):
        self.ir_para("indeferida", decisao_em=timezone.now() - timedelta(days=10))
        r = self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "recurso", "recurso": "Peço revisão."}, follow=True)
        self.assertContains(r, "prazo de recurso terminou")
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "indeferida")

    def test_desistencia_antes_e_depois_do_pagamento(self):
        self.ir_para("deferida")
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "desistir", "motivo": "Problema de saúde na família."})
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "cancelada")
        self.assertContains(self.aluno.get(f"/s/{self.sol.pk}/envio/"), "suspensão do pagamento")

        self.ir_para("paga")
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "desistir", "motivo": "Voo cancelado."})
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "ag_devolucao")
        self.assertTrue(validacao.bloqueios(self.aluno_user))  # item 10.5
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "gru", "arquivo": pdf("gru.pdf")})
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "ag_devolucao")  # o estudante não se desbloqueia sozinho
        self.assertTrue(validacao.bloqueios(self.aluno_user))
        self.analista.post(f"/analise/{self.sol.pk}/", {"acao": "confirmar_gru"})
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "devolvida")
        self.assertFalse(validacao.bloqueios(self.aluno_user))

    def test_prestacao_vencida_bloqueia_nova_solicitacao(self):
        self.passar_evento()
        Solicitacao.objects.filter(pk=self.sol.pk).update(
            status="ag_prestacao", evento_fim=timezone.localdate() - timedelta(days=40))
        self.assertTrue(validacao.bloqueios(self.aluno_user))  # item 10.6
        antes = Solicitacao.objects.count()
        r = self.aluno.post("/solicitacoes/nova/", follow=True)
        self.assertContains(r, "item 10.6")
        self.assertEqual(Solicitacao.objects.count(), antes)

    # ----------------------------------------------------------- grupos e proponente
    def novo_usuario(self, login, nome, papel="aluno", **perfil):
        from auxilio.models import Perfil
        u = User.objects.create_user(login, login, "senha-longa-123")
        Perfil.objects.create(user=u, papel=papel, campus=self.sol.campus, nome_completo=nome, **perfil)
        c = Client()
        c.login(username=login, password="senha-longa-123")
        return u, c

    def texto_pdf(self, doc):
        from pypdf import PdfReader
        doc.arquivo.open("rb")
        try:
            return " ".join(p.extract_text() or "" for p in PdfReader(doc.arquivo).pages)
        finally:
            doc.arquivo.close()

    def test_grupo_de_estudantes_com_convite(self):
        pk = self.sol.pk
        um = calculo.resumo(self.sol)["total"]
        self.aluno.post(f"/s/{pk}/estudantes/", {"acao": "incluir", "nome_completo": "João Colega Exemplo",
                                                 "email": "joao@ifam.edu.br", "matricula": "2024000001"})
        self.assertEqual(self.sol.participantes.count(), 2)
        r = calculo.resumo(self.sol)
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["total"], um * 2)  # item 4.3: valor único por estudante
        joao_p = self.sol.participantes.get(matricula="2024000001")
        msgs = [x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"]
        self.assertTrue(any("João Colega Exemplo: faltam as declarações" in m for m in msgs))
        self.assertTrue(any("Histórico escolar atualizado de João" in m for m in msgs))

        # o colega aceita o convite, confirma as declarações e envia o próprio histórico
        joao, cj = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        r = cj.get(f"/convite/{joao_p.convite_token}/", follow=True)
        self.assertContains(r, "Suas declarações")
        joao_p.refresh_from_db()
        self.assertEqual(joao_p.usuario, joao)
        cj.post(f"/participacao/{joao_p.pk}/", {"acao": "declaracoes", "decl_paes": "on", "decl_frequencia": "on",
                                                "decl_sem_pendencias": "on"})
        cj.post(f"/participacao/{joao_p.pk}/", {"acao": "documento", "tipo": "historico", "arquivo": pdf("hist.pdf")})
        joao_p.refresh_from_db()
        self.assertTrue(joao_p.declaracoes_ok)
        doc_joao = validacao.doc_ativo(self.sol, "historico", joao_p)
        self.assertIsNotNone(doc_joao)
        # o histórico da Maria continua lá, separado
        self.assertIsNotNone(validacao.doc_ativo(self.sol, "historico", self.sol.participantes.get(ordem=1)))

        # acesso: o colega não abre o pedido nem os documentos da Maria, só os dele
        self.assertEqual(cj.get(f"/s/{pk}/").status_code, 404)
        self.assertEqual(cj.get(f"/arquivo/{doc_joao.pk}/").status_code, 200)
        doc_maria = validacao.doc_ativo(self.sol, "historico", self.sol.participantes.get(ordem=1))
        self.assertEqual(cj.get(f"/arquivo/{doc_maria.pk}/").status_code, 404)
        self.assertContains(cj.get("/"), "Pedidos em que você foi incluído")

        # Anexo II lista os dois estudantes
        for tipo in ("identidade", "nada_consta"):
            fluxo.anexar_documento(self.sol, self.aluno_user, tipo, pdf(), participante=joao_p)
        fluxo.gerar_documentos(self.sol, self.aluno_user)
        texto = self.texto_pdf(self.sol.documentos.get(tipo="anexo2", ativo=True))
        self.assertIn("João Colega Exemplo", texto)
        self.assertIn("Maria Exemplo da Silva", texto)

    def test_servidor_proponente_pelo_proprio_login(self):
        serv, cs = self.novo_usuario("ana.prof@ifam.edu.br", "Ana Professora Exemplo", papel="servidor",
                                     siape="7654321", cargo="Professor EBTT")
        cs.post("/solicitacoes/nova/")
        sol = Solicitacao.objects.filter(aluno=serv).first()
        self.assertEqual(sol.proponente_tipo, "servidor")
        self.assertEqual(sol.participantes.count(), 0)
        self.assertTrue(any("Inclua ao menos um estudante" in x["msg"] for x in validacao.validar(sol)))
        self.assertEqual(validacao.documentos_gerados(sol)[:2], ["anexo1", "anexo2"])  # sem requerimento
        self.assertEqual(sol.proponente["nome"], "Ana Professora Exemplo")
        self.assertEqual(sol.proponente["siape"], "7654321")
        # o servidor inclui um estudante sem conta e preenche por ele
        cs.post(f"/s/{sol.pk}/estudantes/", {"acao": "incluir", "nome_completo": "Bia Estudante Exemplo",
                                             "matricula": "2023000002", "email": ""})
        e = sol.participantes.get()
        self.assertFalse(e.usuario_id)
        cs.post(f"/s/{sol.pk}/estudantes/", {"acao": "salvar", "participante": e.pk, f"p{e.pk}-nome_completo": e.nome_completo,
                                             f"p{e.pk}-matricula": "2023000002", f"p{e.pk}-decl_paes": "on",
                                             f"p{e.pk}-decl_frequencia": "on", f"p{e.pk}-decl_sem_pendencias": "on"})
        e.refresh_from_db()
        self.assertTrue(e.declaracoes_ok)
        cs.post(f"/s/{sol.pk}/documentos/", {"tipo": "historico", "participante": e.pk, "arquivo": pdf()})
        self.assertIsNotNone(validacao.doc_ativo(sol, "historico", e))

    def test_estudante_preenche_com_servidor_proponente(self):
        self.sol.proponente_tipo = "servidor"
        self.sol.prop_nome, self.sol.prop_siape = "Carlos Orientador Exemplo", "1112223"
        self.sol.prop_cargo, self.sol.prop_email = "Professor EBTT", "carlos@ifam.edu.br"
        self.sol.save()
        self.assertNotIn("requerimento", validacao.documentos_gerados(self.sol))
        fluxo.gerar_documentos(self.sol, self.aluno_user)
        texto = self.texto_pdf(self.sol.documentos.get(tipo="anexo1", ativo=True))
        self.assertIn("Carlos Orientador Exemplo", texto)
        self.assertIn("1112223", texto)
        r = self.aluno.get(f"/s/{self.sol.pk}/assinaturas/")
        self.assertContains(r, "carlos@ifam.edu.br")  # e-mail para o proponente assinar
        self.assertContains(r, "Enviar PDF assinado pelo(a) proponente")
        # sem os dados do servidor, o pedido não segue
        self.sol.prop_siape = ""
        self.sol.save()
        self.assertTrue(any("SIAPE" in x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"))

    def test_bloqueio_de_um_colega_aparece_no_pedido(self):
        joao, _ = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        from auxilio.models import Participante
        outra = Solicitacao.objects.create(aluno=joao, edital=self.sol.edital, status="ag_prestacao",
                                           evento_fim=timezone.localdate() - timedelta(days=40))
        Participante.objects.create(solicitacao=outra, usuario=joao, nome_completo="João Colega Exemplo")
        Participante.objects.create(solicitacao=self.sol, usuario=joao, ordem=2, nome_completo="João Colega Exemplo",
                                    matricula="2024000001")
        erros = [x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"]
        msg = [m for m in erros if m.startswith("João Colega Exemplo") and "outro pedido" in m]
        self.assertTrue(msg and "10.6" in msg[0])
        self.assertNotIn(outra.codigo, msg[0])  # não expõe o outro pedido do colega

    def test_convite_so_para_o_email_informado(self):
        self.aluno.post(f"/s/{self.sol.pk}/estudantes/", {"acao": "incluir", "nome_completo": "Fulano Convidado",
                                                          "email": "fulano@ifam.edu.br", "matricula": "2024000009"})
        vaga = self.sol.participantes.get(matricula="2024000009")
        eva, ce = self.novo_usuario("eva@ifam.edu.br", "Eva Outra", matricula="2024000077")
        ce.get(f"/convite/{vaga.convite_token}/")
        vaga.refresh_from_db()
        self.assertIsNone(vaga.usuario_id)  # o link sozinho não basta
        fulano, cf = self.novo_usuario("fulano@ifam.edu.br", "Fulano Convidado", matricula="2024000009")
        cf.get(f"/convite/{vaga.convite_token}/")
        vaga.refresh_from_db()
        self.assertEqual(vaga.usuario, fulano)
        # a dona não confirma as declarações de quem tem conta, nem se remove do pedido
        self.aluno.post(f"/s/{self.sol.pk}/estudantes/", {"acao": "salvar", "participante": vaga.pk,
                                                          f"p{vaga.pk}-decl_paes": "on", f"p{vaga.pk}-decl_frequencia": "on",
                                                          f"p{vaga.pk}-decl_sem_pendencias": "on"})
        vaga.refresh_from_db()
        self.assertFalse(vaga.declaracoes_ok)
        dona = self.sol.participantes.get(usuario=self.aluno_user)
        self.aluno.post(f"/s/{self.sol.pk}/estudantes/", {"acao": "remover", "participante": dona.pk})
        self.assertTrue(self.sol.participantes.filter(pk=dona.pk).exists())

    def test_conta_sem_perfil_nao_quebra(self):
        User.objects.create_superuser("admin", "admin@ifam.edu.br", "senha-longa-123")
        c = Client()
        c.login(username="admin", password="senha-longa-123")
        doc = self.sol.documentos.filter(ativo=True).first()
        self.assertEqual(c.get(f"/arquivo/{doc.pk}/").status_code, 404)
        e = self.sol.participantes.first()
        self.assertIn(c.get(f"/convite/{e.convite_token}/").status_code, (302, 200))

    def test_matricula_repetida_menor_proponente_e_bloqueio_sem_conta(self):
        from auxilio.models import Participante
        Participante.objects.create(solicitacao=self.sol, ordem=2, nome_completo="Cópia", matricula="2024000000")
        erros = [x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"]
        self.assertTrue(any("aparece duas vezes" in m for m in erros))
        Participante.objects.filter(nome_completo="Cópia").delete()
        # estudante sem conta, com devolução pendente em outro pedido, é pego pela matrícula
        outra = Solicitacao.objects.create(aluno=self.aluno_user, edital=self.sol.edital, status="paga")
        Participante.objects.create(solicitacao=outra, nome_completo="Aluno Bloqueado", matricula="2020999999",
                                    devolucao_estado="pendente", desistencia_em=timezone.now())
        Participante.objects.create(solicitacao=self.sol, ordem=2, nome_completo="Aluno Bloqueado", matricula="2020999999")
        erros = [x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"]
        self.assertTrue(any(m.startswith("Aluno Bloqueado") and "10.5" in m for m in erros))
        # proponente estudante precisa ter 18 anos (item 7.1)
        p = self.aluno_user.perfil
        p.nascimento = self.sol.viagem_ida.replace(year=self.sol.viagem_ida.year - 17)
        p.save()
        self.sol = Solicitacao.objects.get(pk=self.sol.pk)
        erros = [x["msg"] for x in validacao.validar(self.sol) if x["nivel"] == "erro"]
        self.assertTrue(any("item 7.1" in m for m in erros))

    def test_desistencia_de_um_estudante_do_grupo(self):
        from auxilio.models import Participante
        joao, cj = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        e = Participante.objects.create(solicitacao=self.sol, usuario=joao, ordem=2, nome_completo="João Colega Exemplo",
                                        matricula="2024000001")
        self.ir_para("paga")
        cj.post(f"/participacao/{e.pk}/", {"acao": "desistir", "motivo": "Não consegui liberação no estágio."})
        e.refresh_from_db()
        self.sol.refresh_from_db()
        self.assertTrue(e.desistiu)
        self.assertEqual(e.devolucao_estado, "pendente")
        self.assertEqual(self.sol.status, "paga")  # o pedido segue para a Maria
        self.assertTrue(validacao.bloqueios(joao))  # só quem deve fica bloqueado
        self.assertFalse(validacao.bloqueios(self.aluno_user))
        r = cj.get(f"/participacao/{e.pk}/")
        self.assertContains(r, "R$10.064,69")  # devolve só a própria parte
        cj.post(f"/participacao/{e.pk}/", {"acao": "gru", "arquivo": pdf("gru.pdf")})
        self.assertTrue(validacao.bloqueios(joao))
        self.diretor.post("/diretor/", {"solicitacao": self.sol.pk, "participante": e.pk})
        e.refresh_from_db()
        self.assertEqual(e.devolucao_estado, "confirmada")
        self.assertFalse(validacao.bloqueios(joao))

    def test_furos_da_desistencia_por_estudante(self):
        from auxilio.models import Participante
        joao, cj = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        ana, ca = self.novo_usuario("ana@ifam.edu.br", "Ana Colega Exemplo", matricula="2024000002")
        j = Participante.objects.create(solicitacao=self.sol, usuario=joao, ordem=2, nome_completo="João", matricula="2024000001")
        a = Participante.objects.create(solicitacao=self.sol, usuario=ana, ordem=3, nome_completo="Ana", matricula="2024000002")
        um = calculo.resumo(self.sol)["por_aluno"]["total"]
        # proponente estudante não sai sozinho(a)
        dona = self.sol.participantes.get(usuario=self.aluno_user)
        self.ir_para("ag_pagamento")
        with self.assertRaises(Exception):
            fluxo.desistir_estudante(self.sol, self.aluno_user, dona, "teste")
        # João sai antes do pagamento: não entra no valor a pagar e não fica devendo
        cj.post(f"/participacao/{j.pk}/", {"acao": "desistir", "motivo": "Viagem cancelada."})
        j.refresh_from_db()
        self.assertEqual(j.devolucao_estado, "suspenso")
        r = calculo.resumo(self.sol)
        self.assertEqual(r["total_pago"], um * 2)
        self.assertFalse(validacao.bloqueios(joao))
        # Ana sai depois do pagamento e o analista confirma a GRU dela
        self.ir_para("paga")
        ca.post(f"/participacao/{a.pk}/", {"acao": "desistir", "motivo": "Doença na família."})
        ca.post(f"/participacao/{a.pk}/", {"acao": "gru", "arquivo": pdf("gru_ana.pdf")})
        self.analista.post(f"/analise/{self.sol.pk}/", {"acao": "confirmar_gru_estudante", "participante": a.pk})
        a.refresh_from_db()
        self.assertEqual(a.devolucao_estado, "confirmada")
        # depois a dona desiste do pedido inteiro: a GRU cobra só a parte dela, e só ela fica bloqueada
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "desistir", "motivo": "Evento adiado."})
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "ag_devolucao")
        from auxilio.services import emails
        self.assertIn(calculo.brl(um), emails.devolucao(self.sol)["corpo"])
        self.assertTrue(validacao.bloqueios(self.aluno_user))
        self.assertFalse(validacao.bloqueios(ana))
        self.assertFalse(validacao.bloqueios(joao))

    def test_matricula_com_outra_formatacao_e_desfazer(self):
        from auxilio.models import Participante
        outra = Solicitacao.objects.create(aluno=self.aluno_user, edital=self.sol.edital, status="paga")
        Participante.objects.create(solicitacao=outra, nome_completo="Aluno Bloqueado", matricula="2022000999",
                                    devolucao_estado="pendente", desistencia_em=timezone.now())
        for formato in ("2022.000.999", "02022000999", "2022-000999"):
            self.assertTrue(validacao.bloqueado_por_matricula(formato, exceto=self.sol), formato)
        # desistência registrada por engano pode ser desfeita enquanto não há GRU
        joao, cj = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        j = Participante.objects.create(solicitacao=self.sol, usuario=joao, ordem=2, nome_completo="João", matricula="2024000001")
        self.ir_para("deferida")
        fluxo.desistir_estudante(self.sol, self.aluno_user, j, "engano")
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "desfazer_desistencia", "participante": j.pk})
        j.refresh_from_db()
        self.assertFalse(j.desistiu)
        # depois do pagamento, a dívida só sai pelo DAES
        self.ir_para("paga")
        fluxo.desistir_estudante(self.sol, self.aluno_user, j, "não vai")
        self.aluno.post(f"/s/{self.sol.pk}/envio/", {"acao": "desfazer_desistencia", "participante": j.pk})
        j.refresh_from_db()
        self.assertEqual(j.devolucao_estado, "pendente")
        self.assertTrue(validacao.bloqueios(joao))
        self.analista.post(f"/analise/{self.sol.pk}/", {"acao": "desfazer_desistencia", "participante": j.pk})
        j.refresh_from_db()
        self.assertFalse(j.desistiu)

    def test_prestacao_vencida_bloqueia_so_quem_preenche(self):
        from auxilio.models import Participante
        joao, _ = self.novo_usuario("joao@ifam.edu.br", "João Colega Exemplo", matricula="2024000001")
        Participante.objects.create(solicitacao=self.sol, usuario=joao, ordem=2, nome_completo="João", matricula="2024000001")
        Solicitacao.objects.filter(pk=self.sol.pk).update(status="ag_prestacao",
                                                          evento_fim=timezone.localdate() - timedelta(days=40))
        self.assertTrue(validacao.bloqueios(self.aluno_user))  # requisitante (item 10.6)
        self.assertFalse(validacao.bloqueios(joao))

    def test_perfil_valida_obrigatorios_cpf_e_celular(self):
        url = "/perfil/"
        r = self.aluno.post(url, {"nome_completo": "Maria Exemplo da Silva", "cpf": "111.111.111-11", "celular": "123"})
        self.assertContains(r, "Não foi possível salvar")
        self.assertContains(r, "CPF inválido")
        self.assertContains(r, "Informe DDD e número")
        self.assertContains(r, "com-erro")
        dados = {"nome_completo": "Maria Exemplo da Silva", "matricula": "2024000000", "cpf": "52998224725",
                 "rg": "1234567", "rg_uf": "AM", "nascimento": "2000-01-01", "endereco": "Rua das Flores",
                 "numero": "100", "bairro": "Centro", "celular": "92991829320", "curso": "Engenharia de Software",
                 "serie_periodo": "6P", "turno": "Vespertino"}
        r = self.aluno.post(url, dados)
        self.assertEqual(r.status_code, 302)
        p = self.aluno_user.perfil
        p.refresh_from_db()
        self.assertEqual(p.cpf, "529.982.247-25")
        self.assertEqual(p.celular, "(92) 99182-9320")
        self.assertEqual(p.endereco_completo, "Rua das Flores, 100")

    def test_alterar_dados_invalida_documentos_e_assinaturas(self):
        fluxo.gerar_documentos(self.sol, self.aluno_user)
        self.assertFalse(validacao.desatualizado(self.sol))
        o = Orcamento.objects.filter(solicitacao=self.sol, categoria="hospedagem").first()
        o.valor = Decimal("2000.00")
        o.save()
        self.assertTrue(validacao.desatualizado(self.sol))
        fluxo.atualizar_status(self.sol, self.aluno_user)
        self.sol.refresh_from_db()
        self.assertEqual(self.sol.status, "rascunho")
        fluxo.gerar_documentos(self.sol, self.aluno_user)
        self.assertFalse(validacao.desatualizado(self.sol))
        self.assertTrue(self.sol.assinaturas.filter(estado="pendente").count() == 4)

    def test_inspecao_de_pdf_sem_assinatura(self):
        v = insp.inspecionar(pdf_demo("Sem assinatura"), ["Maria Exemplo da Silva"])
        self.assertEqual(v["assinaturas"], 0)
        self.assertIn("Nenhuma assinatura", insp.resumo_texto(v))
