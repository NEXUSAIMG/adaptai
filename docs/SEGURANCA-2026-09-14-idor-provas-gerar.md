# Segurança — 14/09/2026 · IDOR em `POST /provas/gerar`

> **Status: CORRIGIDO** (commit `fix(seguranca): corrige IDOR em POST
> /provas/gerar`, branch `fix/idor-provas-gerar-seguranca`). Encontrado
> investigando o pedido de multi-professor por aluno (ver
> `docs/analise-viabilidade-multiprofessor-foto-biblioteca-inicial.md` e o
> plano de implementação em andamento) — mexer em `verificar_acesso_aluno`
> levou a reler todo `app/api/routes/provas.py`, e esta rota específica tinha
> ficado de fora da rodada de correção de IDOR que o resto do arquivo já tem
> (`test_idor_ownership.py` já cobria `provas.py:409`/`/provas/{id}`, mas não
> `/provas/gerar`).
>
> Cada item segue o formato **sintoma → causa raiz → correção**, como em
> `docs/CORRECOES-2026-08-18.md`. Testes de regressão em
> `tests/test_gerar_prova_idor.py` — confirmados falhando contra o código
> antigo antes de aplicar a correção.

---

## Contexto — por que essa rota é diferente das outras

O projeto já protege acesso a aluno em quase toda rota sensível via
`verificar_acesso_aluno(db, student_id, current_user)`
(`app/api/dependencies.py:185-225`): SUPER_ADMIN livre, ADMIN/COORDINATOR
restrito à própria escola, TEACHER restrito a `created_by_user_id` (ou, depois
da feature de multi-professor, também a um vínculo ativo). É usada em
`relatorios.py`, `materiais_adaptados.py`, `analytics.py`, `redacoes.py`,
`redacao_feedback.py`, `comunicacao.py`, `planejamento_bncc.py`, e dentro do
próprio `provas.py` em `POST /provas/associar` (linha 409) e
`GET /provas/aluno/{aluno_id}/provas` (linha 448).

`POST /provas/gerar` (`gerar_prova_com_ia`, `provas.py:93-292`) é uma rota
mais nova — o comentário `# PASSO 3 (NOVO)` na linha 241 sinaliza isso — que
recebeu a capacidade de já criar a prova **e** associá-la a alunos numa
tacada só. Ela não passa pelo mesmo pente-fino: usa `aluno_ids` do próprio
corpo da requisição em dois pontos, checando só se o `Student` existe, nunca
se pertence a quem está chamando.

---

## 1. Leitura do diagnóstico clínico sem checar posse

### Sintoma
Quando `aluno_ids` tem exatamente 1 item, a rota lê o diagnóstico do aluno e
usa isso pra moldar o prompt da IA — e faz isso pra **qualquer** `aluno_id`
que o professor mandar, mesmo de um aluno que nunca foi atribuído a ele.

### Causa raiz
`provas.py:153-165`:

```python
if request.aluno_ids and len(request.aluno_ids) == 1:
    _db_j = SessionLocal()
    try:
        _aid = request.aluno_ids[0]
        ctx_jornada = sintese_jornada_service.contexto_para_prompt(_db_j, _aid)
        ctx_estrategias = ""
        _aluno = _db_j.query(Student).filter(Student.id == _aid).first()
        if _aluno is not None:
            ctx_estrategias = estrategias_service.diretrizes_para_diagnostico(
                _db_j, _aluno.diagnosis or {}, getattr(_aluno, "escola_id", None)
            )
    finally:
        _db_j.close()
```

`Student.diagnosis` é dado clínico (TEA, TDAH, dislexia etc.) — o mesmo tipo
de informação que `Relatorio` (laudo médico) já trata como sensível e
protege com `verificar_acesso_aluno` (`test_idor_ownership.py:143-173`
trava exatamente isso pra laudos). Aqui a busca é
`db.query(Student).filter(Student.id == _aid).first()` — sem checar
`created_by_user_id` nem `escola_id`. `Student.id` é inteiro sequencial
(`student.py:11`), então enumerar IDs válidos é trivial — não tem UUID nem
token pra adivinhar.

O diagnóstico não volta cru na resposta HTTP, mas **molda o conteúdo da
prova gerada** (linhas 168-172: injeta `ctx_jornada`/`ctx_estrategias` em
`conteudo_com_adaptacoes`, que vai direto pro prompt da IA em
`prova_ai_service.gerar_questoes`, linha 175). Uma prova visivelmente
simplificada, sem metáforas, com questões curtas, é um sinal indireto de TEA
ou TDAH — o professor sem vínculo legítimo com o aluno acaba recebendo essa
pista mesmo sem ver o campo `diagnosis` diretamente.

**Por que é o achado mais sério dos dois**: é leitura de dado de saúde de
criança sem controle de acesso algum (LGPD/ECA — o próprio `CLAUDE.md` do
projeto já trata isso como sensível nas regras de segurança/QA).

### Correção
Chamar `verificar_acesso_aluno(db, _aid, current_user)` **antes** de ler
`_aluno.diagnosis` — ou seja, no início desse bloco (linha ~157), não só no
laço de associação mais abaixo. Se o professor não tiver acesso, pular a
personalização por diagnóstico pra esse aluno (mesmo caminho que já existe
hoje quando `_aluno is None`: `ctx_estrategias` fica `""`) — não precisa
derrubar a geração da prova inteira, só não usar o diagnóstico de quem não é
dele.

---

## 2. Associação da prova a aluno sem checar posse

### Sintoma
A prova recém-gerada pode ser associada a qualquer `aluno_id` existente,
independente de quem é o dono do aluno ou de qual escola ele é — o aluno
passa a ver essa prova no portal dele, vinda de um professor sem relação
legítima com ele.

### Causa raiz
`provas.py:242-263`:

```python
for aluno_id in request.aluno_ids:
    # Verifica se aluno existe
    aluno = db.query(Student).filter(Student.id == aluno_id).first()   # linha 247
    if aluno:
        ja_associado = db.query(ProvaAluno).filter(
            ProvaAluno.prova_id == nova_prova.id,
            ProvaAluno.aluno_id == aluno_id
        ).first()
        if not ja_associado:
            prova_aluno = ProvaAluno(prova_id=nova_prova.id, aluno_id=aluno_id, ...)
            db.add(prova_aluno)
```

De novo, só existência — nunca posse. Compare com `POST /provas/associar`
(linha 409, a rota "irmã" de associação manual), que já chama
`verificar_acesso_aluno(db, associacao.aluno_id, current_user)` antes de
associar.

**Impacto — pior do que "só escrita não autorizada", puxando o fio**:

1. **O aluno vê a prova sem gate nenhum.** `GET /` do portal do aluno
   (`student_provas.py:76-79`, `listar_minhas_provas`) filtra só por
   `ProvaAluno.aluno_id == current_student.id` — não checa de qual professor
   veio. A prova forjada aparece pro Aluno A igual a qualquer prova legítima.

2. **Existe um oráculo que confirma o ataque e vaza nome+série do aluno.**
   `GET /provas/{prova_id}/alunos` (linhas 458-485, rota de "modo papel" —
   imprimir folha) só checa `_verificar_acesso_prova(prova, current_user)`
   (linha 471), que valida **posse da prova** (`prova.criado_por_id ==
   current_user.id`, linha 84) — nunca posse do aluno. Como o atacante é dono
   da prova que ele mesmo gerou, a checagem passa, e a rota devolve
   `aluno_nome`/`aluno_serie` de cada aluno associado, incluindo o Aluno A.
   Serve também pra enumerar em lote: manda vários `aluno_ids` chutados em
   `/provas/gerar` e usa essa rota pra ver quais "colaram".

3. **Escalada mais séria: acesso às respostas reais do aluno depois.** Toda
   correção/visualização de uma tentativa (`ProvaAluno`) passa por
   `_carregar_prova_aluno_para_correcao` (linhas 1114-1123), que **também**
   usa só `_verificar_acesso_prova` — nunca `verificar_acesso_aluno`. Isso
   alimenta `listar_questoes_pendentes` (linha 1126) e as rotas de correção.
   Ou seja: se o Aluno A responder a prova forjada, o Professor B — dono da
   prova — consegue ver as respostas reais que o aluno escreveu e corrigi-las,
   porque o sistema assume "quem é dono da prova tem acesso a tudo associado a
   ela". Essa suposição só era segura porque toda associação até hoje passava
   por checagem de posse do aluno (`/provas/associar`, linha 409) — a rota de
   geração quebrou essa premissa ao pular a checagem na hora de associar.

Some ao achado 1: como o mesmo `aluno_id` único também alimenta a leitura de
diagnóstico, num pedido com 1 aluno os dois achados disparam juntos.

### Correção
Trocar a linha 247 pela mesma chamada central, dentro de um
`try/except HTTPException: continue` — pula o aluno sem acesso, sem quebrar a
associação dos alunos válidos na mesma chamada (a rota aceita múltiplos
`aluno_ids` de uma vez):

```python
for aluno_id in request.aluno_ids:
    try:
        aluno = verificar_acesso_aluno(db, aluno_id, current_user)
    except HTTPException:
        continue
    ...
```

**Isso fecha a cadeia inteira**, não só a associação: se o `ProvaAluno`
nunca chega a ser criado pra um aluno sem acesso, o atacante nunca vira "dono
de uma prova associada" àquele aluno — e as duas rotas downstream que
confiam só em `_verificar_acesso_prova` (`/provas/{id}/alunos` e a correção
via `_carregar_prova_aluno_para_correcao`) ficam fechadas de graça pra ele,
sem precisar mexer nelas também.

---

## Severidade e exploração

- **Classe**: IDOR / broken access control (CWE-639).
- **Pré-requisito**: qualquer conta autenticada com papel TEACHER (não precisa
  ser admin nem ter qualquer relação com o aluno-alvo).
- **Trivialidade**: `Student.id` sequencial, sem necessidade de descobrir
  nada — só incrementar.
- **Dado exposto**: diagnóstico clínico usado sem retornar cru (achado 1,
  indireto); nome + série do aluno via oráculo em `/provas/{id}/alunos`
  (achado 2, direto); potencialmente as respostas que o aluno escrever na
  prova forjada, via as rotas de correção que só checam posse da prova
  (achado 2, escalada — depende do aluno responder a prova antes de o fix
  entrar).
- **Ação não autorizada**: associação de prova a aluno de outra escola, que
  vira visível pro aluno sem gate nenhum no portal dele (achado 2).
- **Escopo do fix**: os dois pontos ficam na mesma função
  (`gerar_prova_com_ia`) e a mesma chamada (`verificar_acesso_aluno`) resolve
  ambos — é um fix pequeno e isolado, independente de qualquer outra feature
  em andamento no projeto.

## Como testar a correção

Seguir o padrão já existente em `tests/test_idor_ownership.py` (sqlite
in-memory, seed de 2 professores + 2 alunos, tokens JWT reais via
`create_access_token`, `TestClient`) — acrescentar uma classe
`TestGerarProvaIDOR` nesse mesmo arquivo:

- Prof B (sem vínculo com o aluno do Prof A) chama `POST /provas/gerar` com
  `aluno_ids: [aluno_a.id]` → a prova deve ser criada, mas **sem** associação
  ao aluno A (verificar que não existe `ProvaAluno` pra esse par depois da
  chamada) e sem o diagnóstico do aluno A influenciar o prompt.
- Prof A (dono) faz a mesma chamada → associação acontece normalmente,
  comportamento inalterado.

## Relação com o plano de multi-professor

Esta rota não faz parte do fluxo dessa feature (é síncrona/imperativa e roda
antes da tabela `alunos_professores` existir), mas usa a mesma função central
(`verificar_acesso_aluno`) que o plano de multi-professor já estende — por
isso o fix cabe bem como um **commit isolado**, com mensagem própria, dentro
do mesmo período de trabalho. Não depende de aprovar nem implementar a
feature de multi-professor para ser corrigido — pode entrar sozinho a
qualquer momento.
