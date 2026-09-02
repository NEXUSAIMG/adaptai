# Vertical Clínica — governança de acesso e ponte Escola↔Clínica

> **Status:** proposta, não implementada. Nenhuma linha de código foi alterada.
> **Data:** 2026-09-01 · **Base:** `origin/main` (`306f733`) · **Branch:** `docs/plano-clinica-governanca-ponte`
> **Análise a ser retomada** antes de qualquer execução — em especial a Fase 0 e as
> duas decisões em aberto (§1.5).
>
> Leia junto com `docs/VERTICAL_CLINICA.md`, que descreve o vertical como ele foi
> construído. Este documento cobre o que ficou de fora dele.

## Contexto

O módulo Clínica (`origin/main`, commits `dfec26c`/`a1f76f1`/`370c835`) foi construído por outro
dev como **vertical isolado sobre kernel compartilhado**: 11 routers, 9 models, migrations
011→021, ~15 páginas no front. O desenho está correto e documentado em `docs/VERTICAL_CLINICA.md`.

Duas lacunas motivam este trabalho:

1. **Governança.** O entitlement (`escola_modulos` + `requer_modulo`) decide se o *tenant* tem o
   módulo, e o guard `acesso_clinico.verificar_acesso_paciente` decide quem vê *um* prontuário.
   Falta a camada do meio: **quem, dentro de um tenant licenciado, é do time clínico**. Sem ela há
   uma escalação de privilégio real e vazamento de dados de saúde nas listagens (§1).
2. **A ponte Aluno↔Paciente.** A tabela `vinculo_aluno_paciente` foi criada na migration 011 e o
   model `VinculoAlunoPaciente` está mapeado e exportado, mas **nenhuma rota, service, schema ou
   tela a usa** — é um placeholder. Hoje uma criança que é aluna e paciente no mesmo tenant existe
   como dois cadastros sem correlação, e o laudo que a escola já tem (`Relatorio.resumo`, `cid`,
   `condicoes`) precisa ser copiado à mão para o `<textarea>` do PTI-IA.

**Ordem decidida:** governança primeiro, ponte depois. A ponte faz dado clínico atravessar para o
lado escolar; abrir esse caminho antes de acertar quem pode ver o quê amplia o vazamento atual.

**Restrição de desenho da ponte (definida pelo usuário):** a interligação é **aditiva e opcional**.
O cadastro manual de paciente continua funcionando exatamente como hoje, sem vínculo. Cada camada
da ponte pode ser ligada ou não, sem quebrar a de baixo.

---

## Fase 0 — Pré-requisito bloqueante (read-only, antes de qualquer código)

`run_migrations.py` (commit `3aa37f6`, posterior ao commit da clínica) tem lógica de BASELINE: se o
banco já tem `users` e `schema_migrations` está vazia, marca **todas** as migrations como aplicadas
sem executar nenhuma. Em produção `create_all` não roda (`app/main.py:76-80`). Se esse deploy rodou
num banco pré-existente onde 011→021 nunca foram aplicadas, as tabelas clínicas **não existem** e
nenhum deploy futuro vai criá-las.

```sql
SELECT filename, applied_at FROM schema_migrations WHERE filename LIKE '%clinica%' ORDER BY filename;
SHOW TABLES LIKE 'pacientes';
SHOW TABLES LIKE 'escola_modulos';
```

Se as linhas existem em `schema_migrations` mas as tabelas não: remover essas linhas e deixar o
runner reaplicar (as migrations são idempotentes, `CREATE TABLE IF NOT EXISTS`). **Sem isso, nada
do resto do plano é testável.**

---

## Fase 1 — Governança e administração do módulo

### 1.1 Camada de perfil clínico (a peça que falta)

Estender `app/services/acesso_clinico.py` — o arquivo já concentra a lógica e já tem
`profissional_do_usuario()` e `_PAPEIS_AMPLOS`. Reusar, não criar arquivo novo.

- `perfil_clinico(db, current_user) -> PerfilClinico` — dataclass com `profissional`,
  `is_amplo`, `is_admin_sistema`, `escola_id`. Consolida a lógica hoje repetida em `_autorizar`.
- `requer_profissional()` — dependency FastAPI: 403 se o usuário não tem `Profissional` ativo na
  escola **e** não é `ADMIN`/`COORDINATOR`/`SUPER_ADMIN`. É o gate "sou do time clínico".
- `pacientes_visiveis(db, current_user) -> Query` — query base de `Paciente` já filtrada por
  tenant + `equipe_caso` (ou sem filtro de equipe, se papel amplo). **Este é o helper central**:
  toda listagem passa a partir dele em vez de filtrar `escola_id` na mão.
- `tem_consentimento_vigente(db, paciente_id, tipo)` — mover para cá o helper privado
  `_tem_consentimento_vigente` de `app/api/routes/familia.py:44`, que a Fase 2 vai reusar.
  `familia.py` passa a importar daqui (sem mudar comportamento).

### 1.2 Fechar a escalação de privilégio

`POST /clinica/profissionais` (`app/api/routes/clinica.py:362`) não tem **nenhuma** checagem de
papel. Qualquer usuário logado num tenant com CLINICA cria um `Profissional` com
`usuario_id` = ele mesmo e `papel=ADMIN_CLINICA`, que `_PAPEIS_AMPLOS` trata como acesso a todos os
prontuários do tenant.

- Exigir `require_admin` (`app/api/dependencies.py:165`) **ou** profissional com papel amplo.
- Bloquear criação de papel amplo (`ADMIN_CLINICA`/`RESPONSAVEL_TECNICO`/`COORDENADOR`) por quem não
  é `ADMIN`/`SUPER_ADMIN`.
- Mesmo tratamento em `POST /clinica/pacientes` (`clinica.py:163`), hoje aberto a qualquer usuário:
  passa a exigir `requer_profissional()`.

### 1.3 Fechar o vazamento nas listagens

Todas as rotas *por paciente* estão corretamente guardadas por `verificar_acesso_paciente` (auditei
as ~50 rotas clínicas). O buraco está nas **coleções**, que filtram só por `escola_id` e devolvem
nome de paciente para qualquer usuário do tenant:

| Rota | Arquivo | O que vaza |
|---|---|---|
| `GET /clinica/pacientes` | `clinica.py:184` | nome, `responsavel_nome`, `responsavel_contato` |
| `GET /clinica/dashboard` | `clinica_dashboard.py:38` | `paciente_nome` nos alertas |
| `GET /clinica/faturamento/itens` | `clinica_faturamento.py:235` | `paciente_nome` |
| `GET /clinica/agendamentos` | `clinica_agenda.py:96` | agenda por paciente |
| `GET /clinica/pranchas` | `clinica_pranchas.py:124` | pranchas por paciente |

Trocar o filtro manual por `pacientes_visiveis()`. Adicionalmente, minimização: `_paciente_dict`
(`clinica.py`) deixa de expor `responsavel_nome`/`responsavel_contato` na **listagem** — mantém no
detalhe, que já é auditado.

### 1.4 SUPER_ADMIN travado fora da clínica

`get_tenant_context` (`app/core/tenant.py:96`) devolve `escola=None` para `SUPER_ADMIN` →
`modulos_ativos(db, None)` retorna `set()` → `requer_modulo` responde **403**. Hoje o super admin
não acessa nenhuma rota clínica, embora todas as rotas internas já tenham `is_super` bypass.

Corrigir em `app/core/entitlements.py`: `requer_modulo` recebe o `current_user` e libera
`SUPER_ADMIN` antes de consultar `escola_modulos`.

### 1.5 Licenciamento por tenant — **DECISÃO EM ABERTO**

`PUT /tenant/{escola_id}/modulos/{modulo}` (`app/api/routes/modulos.py:57`) usa `require_admin`,
que checa **só o papel** — não compara `escola_id` do path com o do usuário. Um ADMIN da escola 5
liga/desliga o módulo CLINICA da escola 9. Isso precisa ser corrigido de todo jeito; o que está em
aberto é *quem* fica com a chave:

| Opção | Efeito | Trade-off |
|---|---|---|
| **A — SUPER_ADMIN exclusivo** *(recomendada)* | Só a AdaptAI liga/desliga módulo. `ADMIN` da escola perde o `PUT`. | Alinha com "entitlement = camada comercial" do próprio `entitlements.py`. Custo: toda ativação passa pelo time da AdaptAI. |
| **B — ADMIN da escola, com escopo** | `ADMIN` só altera a própria escola; `SUPER_ADMIN` altera qualquer uma. | Mantém autonomia do cliente. Risco: o cliente liga um módulo que não contratou — o entitlement deixa de valer como controle comercial. |

Recomendo **A**, com `GET /tenant/modulos` (leitura, gate de navegação) seguindo aberto a qualquer
usuário logado, como já é.

Segunda decisão, independente: **quem enxerga a clínica dentro de um tenant licenciado**. A `1.1`
entrega `requer_profissional()` pronto para aplicar como `dependencies=[...]` em todos os routers
clínicos. Recomendo aplicar — professor comum não deve ver prontuário só porque a escola contratou
o módulo. Se preferir manter aberto, basta não aplicar a dependency; a `1.3` sozinha já corta o
vazamento das listagens.

### 1.6 Administração utilizável (ovo-e-galinha)

`ClinicaAdminModulos.jsx` é a tela que **liga** o CLINICA, mas só aparece no menu quando
`temModulo('CLINICA')` já é verdadeiro (`Layout.jsx:110`). Por isso existe o `_ativar_clinica.py`
na raiz do repo — script descartável com `ESCOLA_ID = 1` hardcoded.

- Mover a entrada de menu de "Módulos" do grupo Clínica para o menu **Admin**, condicionada a papel
  (não a módulo).
- Trocar o input "digite o ID da escola" por um select alimentado por `GET /escolas/admin/todas`
  (`app/api/routes/escolas.py:176`), que já existe e já é restrito a super admin.
- Remover `_ativar_clinica.py` do repositório.

### 1.7 Gate de rota no frontend, não só de menu

`App.jsx:310-323` registra todas as rotas clínicas dentro do `Layout` sem checar `temModulo`. Sem o
módulo o menu some, mas a URL direta continua montando a página (que então recebe 403 e quebra
feio). Envolver o bloco clínico num guard que usa `useModulos()` (`src/hooks/useModulos.js`) e
redireciona para `/dashboard` quando o módulo não está ativo — respeitando o `loading` do hook para
não redirecionar antes da resposta.

### 1.8 Testes

`tests/test_clinica_acesso.py` já existe e roda em SQLite sem IA/rede — é o lugar natural. Casos
novos: professor sem `Profissional` recebe 403; profissional comum não vê paciente de outra equipe
na **listagem**; criação de `Profissional` com papel amplo por não-admin é rejeitada; `SUPER_ADMIN`
passa pelo `requer_modulo`; `ADMIN` da escola A não altera módulos da escola B.

---

## Fase 2 — Ponte Aluno↔Paciente (aditiva, em camadas)

Cada camada é independente e opcional. **Camada 0 é a única obrigatória**; sem ela as outras não
existem. Nenhuma camada altera o fluxo de cadastro manual.

### Camada 0 — Identidade (o vínculo em si)

Ativar `VinculoAlunoPaciente` (`app/models/clinica_core.py:191`, tabela já criada com
`UNIQUE(aluno_id, paciente_id)`). Rotas novas em `app/api/routes/clinica.py`:

- `GET /clinica/pacientes/{id}/vinculo` — vínculo atual (ou `null`).
- `POST /clinica/pacientes/{id}/vinculo` `{aluno_id}` — valida que o aluno é do mesmo `escola_id`,
  grava, audita (`AcaoAuditoria.EDITAR`, recurso `"vinculo_aluno"`).
- `DELETE /clinica/pacientes/{id}/vinculo` — desvincula (o cadastro clínico permanece intacto).
- `GET /clinica/alunos-vinculaveis?q=` — alunos do tenant para o seletor. Devolve **só**
  `id`, `name`, `grade_level`, `turma` — nunca `diagnosis`.

**Aditividade:** `PacienteCriar` (`clinica.py:69`) ganha um campo **opcional** `aluno_id`. Com ele,
`POST /clinica/pacientes` cria paciente + vínculo na mesma transação e pré-preenche
`nome`/`data_nascimento` a partir do `Student`. Sem ele, o comportamento atual é byte-a-byte o
mesmo. Todas as rotas seguem guardadas por `verificar_acesso_paciente`.

Frontend: seletor opcional de aluno no cadastro de paciente; selo "vinculado ao aluno X" em
`ClinicaPacienteDetail.jsx`; selo recíproco na tela do aluno, visível apenas para quem também é
profissional clínico.

### Camada 1 — Contexto clínico (o ganho real, Escola→Clínica)

O laudo já está do lado escola: `Relatorio` (`app/models/relatorio.py`) tem `resumo` extraído por
IA, `cid`, `condicoes`, `dados_extraidos`, `profissional_especialidade`; `Student` tem `diagnosis`
e `profile_data` em JSON. E `pti_service.sugerir_objetivos(contexto, especialidades)`
(`app/services/pti_service.py:71`) pede exatamente um texto de contexto — hoje digitado à mão no
`<textarea>` de `ClinicaPtiIA.jsx:20`.

- `GET /clinica/pacientes/{id}/contexto-escolar` — monta um texto a partir do aluno vinculado.
  Retorna `{disponivel: false, motivo}` quando não há vínculo ou não há consentimento.
- **Gate LGPD:** exige consentimento `TipoConsentimento.COMPARTILHA_ESCOLA` vigente — o enum já
  existe em `clinica_core.py` e nunca foi usado; é exatamente este o caso de uso. Reusa
  `tem_consentimento_vigente()` da §1.1, mesmo padrão que o Portal da Família aplica hoje.
- **Minimização:** o texto montado **não inclui o nome** do aluno/paciente, mantendo a regra que
  todos os serviços de IA clínicos já seguem.
- Frontend: botão "Puxar do laudo do aluno" em `ClinicaPtiIA.jsx` que preenche o textarea. O
  textarea segue editável e plenamente funcional sem vínculo nenhum.

### Camada 2 — PEI↔PTI (não planejar agora)

`PEIObjetivo` (`app/models/pei.py:104`) tem `meta_especifica`, `criterio_medicao`, `valor_alvo`,
`valor_atual` — estruturalmente paralelo a `ObjetivoTerapeutico` (`descricao`, `criterio_mastery`,
`linha_base`, ciclo `BASELINE→EM_AQUISICAO→MASTERY→MANUTENCAO→GENERALIZACAO`). O espelhamento é
viável, mas envolve decisões clínicas e jurídicas (quem é dono da meta, o que a escola vê de uma
evolução assinada, o que acontece na revogação do consentimento) que não são de engenharia.
Fica registrado como Fase D do roadmap do `docs/VERTICAL_CLINICA.md`, a decidir com a equipe clínica.

---

## Arquivos críticos

**Backend** — `app/services/acesso_clinico.py` (núcleo da Fase 1), `app/core/entitlements.py`,
`app/api/routes/modulos.py`, `app/api/routes/clinica.py`, e o mesmo ajuste de listagem em
`clinica_dashboard.py`, `clinica_faturamento.py`, `clinica_agenda.py`, `clinica_pranchas.py`.
`app/api/routes/familia.py` só perde o helper movido.

**Frontend** — `src/App.jsx` (guard de rota), `src/components/Layout.jsx` (menu Módulos),
`src/pages/ClinicaAdminModulos.jsx` (select de escolas), `src/pages/ClinicaPtiIA.jsx` (Camada 1),
`src/pages/ClinicaPacienteDetail.jsx` (selo de vínculo), `src/services/clinica.js`.

**Sem migration nova em nenhuma fase** — `vinculo_aluno_paciente` e `consentimentos` já existem
desde a 011.

**Documentação** — atualizar `docs/VERTICAL_CLINICA.md` (§1 entitlement, §6 guard de acesso) e o
`CLAUDE.md`, que hoje não menciona o vertical clínico nem o caminho real do frontend
(`/home/dex/Documentos/Projetos/AYIO/Adaptai/adaptai-frontend`, não o path Windows registrado).

---

## Verificação

**Pré-condição:** rebase de `feat/geometria-ilustracao-e-correcoes` sobre `origin/main` (o branch
local está 16 commits atrás e não contém o módulo clínica).

1. `python -c "import app.main"` — pega erro de import/mapper.
2. `pytest tests/test_clinica_acesso.py tests/test_clinica_models.py tests/test_clinica_services.py
   tests/test_clinica_consentimento.py -q` — suíte clínica existente, SQLite, sem IA/rede.
3. Fluxo manual em homologação, com CLINICA ligado em um tenant:
   - login como professor comum → menu Clínica ausente **e** `/clinica` por URL direta redireciona;
   - login como profissional de uma equipe → vê só os pacientes das suas equipes na listagem;
   - tentar `POST /clinica/profissionais` com `papel=ADMIN_CLINICA` como professor → 403;
   - `ADMIN` da escola A tentar `PUT /tenant/<B>/modulos/CLINICA` → 403;
   - `SUPER_ADMIN` acessa `GET /clinica/dashboard` → 200 (hoje 403).
4. Fase 2: criar paciente **sem** `aluno_id` (deve seguir idêntico), depois criar **com**;
   sem consentimento `COMPARTILHA_ESCOLA`, `contexto-escolar` retorna `disponivel: false`;
   após registrar o consentimento, o botão no PTI-IA preenche o textarea.
5. Opcionalmente, rodar o roteiro do §10 do `docs/VERTICAL_CLINICA.md` pelo navegador com a skill
   `claude-in-chrome` — o doc registra que o vertical nunca foi validado em runtime.
