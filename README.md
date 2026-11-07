# Auxílio Estudantil para Eventos Acadêmicos (IFAM CMZL)

Sistema que organiza a solicitação do Edital 03/2026/PROEN/IFAM. Preenche os anexos, confere a documentação,
acompanha as assinaturas e monta o dossiê em PDF único para enviar ao Protocolo.

**O sistema não assina nada e não envia e-mail.** O estudante assina no gov.br, o Diretor-Geral assina o Anexo I
por cima da assinatura do estudante, e o estudante envia o dossiê ao Protocolo. O sistema monta os rascunhos de e-mail.

## Identidade visual
Segue o Design System Institucional do IFAM (versão 2), com a logo oficial no cabeçalho. Tokens de cor, raios e sombra ficam no começo de `auxilio/static/auxilio/app.css`.

## Casos cobertos
- **Estudante sozinho:** o estudante é o proponente e o único beneficiário (caso do BRACIS).
- **Grupo de estudantes:** um estudante é o proponente e inclui colegas. Cada colega é convidado por link (envia as próprias declarações e documentos) ou preenchido pelo proponente.
- **Servidor proponente:** o servidor cria o pedido com a conta dele e inclui os estudantes, ou um estudante preenche e indica o servidor, que assina o Anexo I e o Anexo II por e-mail. Nesse caso não há requerimento padrão do estudante, porque o servidor abre o processo no SIPAC.
- **Servidor vai junto:** independente de quem é o proponente, entram os Anexos IV e V.
- Até 12 estudantes por pedido (Anexo II), com valor único por estudante (item 4.3).
- **Convite:** só a conta com o e-mail informado pelo proponente aceita a vaga. Colega com conta confirma as próprias declarações.
- **Desistência de um estudante:** o pedido segue para os outros. Antes do pagamento, ele sai do valor a pagar (suspensão, item 10.4.1). Depois, devolve só a própria parte por GRU (10.4.2), e o diretor ou o analista confirma. Pode ser desfeita enquanto não há GRU. O estudante proponente não sai sozinho.
- **Desistência do pedido inteiro:** a GRU cobra só a parte de quem ainda estava no pedido.
- **Bloqueios:** GRU pendente (10.5) bloqueia quem deve. Prestação vencida (10.6) bloqueia quem preenche o pedido. Estudante sem conta é conferido pela matrícula, comparada só pelos dígitos.

## Fluxo
1. O estudante cria a conta em `/cadastro/` com o e-mail @ifam.edu.br, completa o perfil e cria a solicitação.
2. Preenche os dados do evento, a pesquisa de preços (3 orçamentos com comprovante por trecho) e anexa os documentos.
3. O sistema gera o Requerimento (sobre o PDF padrão do campus) e os Anexos I, II e III.
4. O estudante assina Requerimento, Anexo I e II no gov.br e envia os PDFs de volta. O Nada Consta é pedido por e-mail ao Serviço Social (rascunho pronto).
5. O Anexo I é o único que o Diretor-Geral assina. O sistema monta o e-mail para ele. Quando ele devolve o PDF assinado, o estudante envia aqui.
6. Só então o dossiê é liberado. O estudante envia o PDF único ao Protocolo e registra o número do processo.
7. Acompanhamento da análise e do recurso. O recurso só é aceito dentro de 2 dias úteis (item 8.2).
8. Prestação de contas, que abre depois do fim do evento. O estudante informa o coordenador e os resultados, que entram no Anexo VI, envia para o coordenador assinar atestando, anexa as comprovações (vários arquivos por item) e o sistema monta o PDF único da prestação com o e-mail ao Protocolo pedindo a juntada ao processo no SIPAC (item 10.3).
9. Desistência (item 10.4). Antes do pagamento, cancela e gera o e-mail de suspensão à Direção-Geral. Depois do pagamento, abre a devolução por GRU, com e-mail pedindo a guia, envio do comprovante e confirmação pelo analista.
10. Bloqueios. GRU pendente (item 10.5) ou prestação de contas vencida (item 10.6) impedem nova solicitação até a regularização.

Valores: alimentação com meia-diária (R$ 75,00) nos dois dias de embarque e diária inteira (R$ 150,00) nos dias do meio. Se a ida e o retorno ficarem em branco, o sistema usa um dia antes e um dia depois do evento.

## Rodar no seu computador
Precisa de Python 3.12 ou mais novo, Git e LibreOffice (sem ele o sistema abre, mas não gera os PDFs dos anexos).

```
git clone https://github.com/julioclsar/auxilio-eventos-ifam.git
cd auxilio-eventos-ifam
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Depois, no PowerShell (Linux/macOS: `export DJANGO_DEBUG=1`):
```
$env:DJANGO_DEBUG = "1"
python manage.py migrate
python manage.py configurar
python manage.py criar_usuario diretor "Seu Nome" seu.email@ifam.edu.br
python manage.py criar_usuario analista "Seu Nome" outro.email@ifam.edu.br
python manage.py runserver
```
Abra http://127.0.0.1:8000. Estudantes se cadastram em `/cadastro/`.

- `DJANGO_DEBUG=1` é obrigatório para rodar sem chave secreta. Só use no seu computador.
- `criar_usuario` pede a senha no terminal. Rode direto no terminal, sem redirecionar a entrada.
- O banco e os arquivos enviados ficam em `dados/`, que não vai para o Git. Cada pessoa tem o seu banco. Para usar outra pasta: `DJANGO_DATA_DIR`.
- Para conferir que está tudo certo: `python manage.py test auxilio`.

## Colocar no ar (Docker)
O sistema precisa do LibreOffice para converter os anexos em PDF, e a imagem Docker já o traz.

```
cp .env.example .env        # edite: chave secreta, endereço do site
docker compose up -d --build
```
Na primeira subida o sistema cria o banco, cadastra o campus e o edital (`configurar`) e fica em `http://servidor:8000`.
Coloque um proxy com HTTPS na frente (Caddy, Nginx, Cloudflare Tunnel ou o do próprio provedor de hospedagem).
São dados pessoais (CPF, RG, endereço), então não use sem HTTPS.

Depois da primeira subida, crie o administrador e os usuários de apoio:
```
docker compose exec auxilio python manage.py createsuperuser
docker compose exec auxilio python manage.py criar_usuario diretor "David Washington Freitas Lima" david.lima@ifam.edu.br
```
O login do diretor no sistema é opcional. O fluxo normal é por e-mail. Analistas do DAES também são opcionais (`criar_usuario analista ...`).

O painel `/admin/` permite ajustar e-mails do Protocolo, do Serviço Social e do Diretor (em Campus) e as regras do edital (em Edital).

## Colocar no ar sem Docker
```
pip install -r requirements.txt     # e instale o LibreOffice (comando soffice no PATH)
export DJANGO_SECRET_KEY=...  DJANGO_ALLOWED_HOSTS=seu.dominio  DJANGO_CSRF_TRUSTED_ORIGINS=https://seu.dominio
python manage.py migrate
python manage.py configurar
python manage.py collectstatic --noinput
gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --timeout 180
```
Para rodar só no seu computador, sem HTTPS: `DJANGO_DEBUG=1 python manage.py runserver`.

## Backup
Tudo que importa fica em uma pasta: o banco e os arquivos enviados (`/dados` no Docker, `dados/` fora dele, ou a pasta de `DJANGO_DATA_DIR`).
Copie essa pasta com frequência.

## Pontos para conferir
- E-mail do Protocolo cadastrado como `protocolo.cmzl@ifam.edu.br`, por suposição. Confirme e corrija no admin se for outro.
- A conferência de assinatura digital é aproximada e não valida o certificado. Por padrão o sistema aceita PDF sem assinatura detectada e mostra um alerta (`ASSINATURA_ESTRITA=1` recusa).
- Juntar os PDFs no dossiê mantém o carimbo visual das assinaturas, mas perde a validação digital de cada uma. O pacote .zip com os originais assinados é só uma cópia de segurança.
- A conta do estudante não confirma o e-mail. Só aceita endereços @ifam.edu.br e cada estudante enxerga apenas as próprias solicitações.
- O sistema não consulta o SIGAA. As declarações de elegibilidade ficam sob responsabilidade do estudante.
- As janelas de submissão cadastradas do edital vão só até agosto. Quando sair um novo calendário, ajuste em Edital, no admin.

- Prestação de contas segue o Art. 29, I, da Resolução 001-CONSUP/IFAM/2024, citado no item 10.3 do edital. São exigidos o certificado de participação, o relatório (Anexo VI) assinado pelo coordenador e as passagens de ida e volta. O Anexo VI acrescenta o recibo da hospedagem. Para incluir outra comprovação, acrescente o tipo em `COMPROVACOES_PRESTACAO`, em `auxilio/regras.py`.
- No Anexo VI o proponente é o mesmo do Anexo I (o estudante, com SIAPE "Não se aplica"). Só o coordenador assina, atestando os resultados, com nome e SIAPE na linha de assinatura. O sistema monta o e-mail pedindo a assinatura.
- O comprovante da GRU não desbloqueia o estudante sozinho. O analista confirma a devolução no painel dele (ou um administrador muda o status no admin).

## Testes
```
python manage.py test auxilio
```
Os testes usam dados fictícios próprios, que nunca entram no sistema em uso.

## Estrutura
- `auxilio/regras.py` regras do edital como dados
- `auxilio/calculo.py` valores, diárias e menores preços
- `auxilio/validacao.py` pendências e checklist
- `auxilio/services/` geração de documentos, assinatura, fluxo, dossiê e e-mails
- `modelos/` anexos .docx e requerimento .pdf oficiais
