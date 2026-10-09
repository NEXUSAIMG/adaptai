# Problema — 14/09/2026 · Aluno só pode ser vinculado a um professor

> **Status: analisado, correção desenhada, ainda NÃO implementada.**
> Motivado por um problema real em produção. Desenho completo (schema, código,
> ordem de implementação) em
> `docs/analise-viabilidade-multiprofessor-foto-biblioteca-inicial.md` — este
> documento é o resumo do porquê, das consequências e do resultado esperado.
>
> **Fora deste documento, por decisão do usuário**: limite de geração por
> professor/matéria (fazia parte do pedido original, ficou pra depois — não é
> tratado aqui).

---

## O problema

Hoje, no AdaptAI, **um aluno só pode estar vinculado a um único professor**.

A causa é estrutural: `Student.created_by_user_id`
(`app/models/student.py:44`) é uma FK simples pra `users.id` — uma relação
**1:N direta**. Não existe (e nunca existiu) uma tabela ponte
professor↔aluno. O próprio nome da relationship já entrega a suposição:
`teacher = relationship("User", back_populates="students")`
(`student.py:52`) — no singular, um professor "dono" por aluno.

Isso funciona bem quando cada aluno tem, na prática, um professor de
referência. Mas quebra assim que um aluno é atendido por **mais de um
professor de matérias diferentes** — que é o caso normal de uma escola: o
mesmo aluno tem professor de Matemática, de Português, etc., e cada um
precisa gerar prova/material adaptado/PEI pra ele.

Toda checagem de acesso no sistema — a dependency central
`verificar_acesso_aluno` (`app/api/dependencies.py:185-225`), a função de
listagem `get_students_query` (`app/api/routes/students.py:51-74`), e as
~9 rotas que reimplementam a mesma checagem inline — segue essa mesma regra
pra TEACHER: `aluno.created_by_user_id == current_user.id`. Se não for o
dono original, não tem acesso. Não existe hoje nenhum caminho pra um segundo
professor ganhar acesso legítimo ao mesmo aluno.

## Consequências observadas em produção

**1. Professor não consegue enviar prova/material pro aluno de outro
professor.**
Mesmo aluno, mesma escola: Professor de Matemática não consegue enviar prova
nem material adaptado pro aluno que já foi cadastrado pelo Professor de
Português. Três pontos bloqueiam isso, todos comparando contra o mesmo dono
único:
- `app/api/routes/materiais.py:304-313` — 404 "não pertencem a você" ao criar
  material pra aluno de outro professor.
- `app/api/routes/provas.py:409` → `verificar_acesso_aluno` — 403 ao tentar
  associar prova manualmente.
- `app/api/routes/materiais_adaptados.py:219` — mesma checagem, mesmo 403.

**2. Nem dá pra recadastrar o mesmo aluno.**
`Student.email` é `unique=True` **global** (`student.py:20`, sem escopo de
escola nem de professor) — é necessário pra login do aluno
(`POST /auth/student/login`, `app/api/routes/auth.py:329-379`). Quando o
Professor de Matemática tenta cadastrar o mesmo aluno (mesmo e-mail) pra
"ter ele na sua lista", `POST /students/` barra com 400 "Email já cadastrado"
(`students.py:95-102`) — a checagem é uma consulta global, sem considerar
que o e-mail já existe *na mesma escola*, só que pertencendo a outro
professor. O único jeito de mudar o dono hoje é
`POST /students/{id}/transferir` (`students.py:646-700`), que **substitui**
o professor — não adiciona um segundo, então usar essa rota tiraria o acesso
do Professor de Português pra dar ao de Matemática, o que não resolve nada.

**Resultado prático**: os dois professores legítimos do mesmo aluno não
conseguem coexistir no sistema — um bloqueia o outro, e não existe uma saída
dentro do produto hoje (só via banco de dados direto).

## Resultado esperado após a correção

- Um aluno pode ter **vários professores vinculados ativos** ao mesmo tempo,
  cada um mantendo acesso independente (gerar prova, material, PEI, ver
  relatórios etc. pra aquele aluno) — sem que um afete o acesso do outro.
- Quando um professor tenta cadastrar um aluno cujo e-mail já existe **na
  mesma escola**, o sistema oferece vincular-se a ele em vez de só recusar —
  o professor aceita e passa a ter acesso, sem precisar de aprovação de
  ninguém (decisão já fechada: self-service, mesma escola).
- O professor "dono original" (quem cadastrou primeiro) não perde nada —
  continua com acesso normal, sem qualquer mudança de comportamento.
- Um professor **sem vínculo nenhum** continua bloqueado, exatamente como
  hoje — a correção adiciona uma segunda forma de ganhar acesso legítimo, não
  remove a checagem existente.
- A visão de turma pro diretor não muda (já funciona hoje, é agrupamento por
  escola — não é afetada por este problema nem pela correção).

## Como corrigir (resumo — desenho completo no doc de viabilidade)

1. **Tabela ponte `alunos_professores`** (`aluno_id`, `professor_id`,
   `origem`, unique composto) — mesmo padrão que `MaterialAluno`
   (`app/models/material.py:111-132`) já usa pra material↔aluno, só que
   faltando pra professor↔aluno. Populada, na migração inicial, com 1 vínculo
   por aluno já existente (a partir do `created_by_user_id` atual) — não
   quebra ninguém que já usa o sistema.
2. **Estender, não recriar**, a checagem de acesso: `verificar_acesso_aluno`
   e `get_students_query` passam a aceitar dono original **OU** vínculo
   ativo na tabela nova. Como `get_students_query` já alimenta as listagens
   de aluno em várias telas, essa única mudança resolve a maior parte do
   problema sem tocar no frontend.
3. **Endpoint de auto-vínculo** (`POST /students/{id}/vincular-me`) — só
   TEACHER, só mesma escola, idempotente — chamado quando o cadastro bate no
   e-mail duplicado (que passa a devolver 409 estruturado em vez de 400 puro,
   com o id do aluno existente, pra oferecer o vínculo).
4. **Refactor mecânico** dos ~9 arquivos que hoje reimplementam a checagem
   inline em vez de usar a função central — pra que o vínculo novo valha em
   todas as rotas, não só nas que já usavam `verificar_acesso_aluno`.

Esforço estimado: ~4–4,5 dias. Detalhe arquivo-por-arquivo, ordem de
implementação e plano de testes em
`docs/analise-viabilidade-multiprofessor-foto-biblioteca-inicial.md`.
