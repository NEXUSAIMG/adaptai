# Ativação manual de conta (Super Admin) — autocadastro público desativado

## Contexto / decisão de produto

O AdaptAI ainda não vende o produto por self-service. O trial de 14 dias é
proposital e "nunca acaba" na prática (nada bloqueia acesso pós-expiração hoje) -
serve para criar contas de teste enquanto o produto não é comercializado.

Decisão: **criação de conta deixou de ser pública e passou a ser sempre
manual**, feita pelo super admin depois de uma conversa com o lead (hoje via
WhatsApp). O frontend já refletia essa decisão desde **2026-09-04**
(commit `fe42b40`, "feat(super-admin): implement manual account activation
process via WhatsApp"):

- `PlanosPage.jsx` virou vitrine + botão "Falar com a gente" (WhatsApp) - sem
  seleção de plano, sem redirecionar pra checkout.
- `App.jsx`: `/checkout/:slug` redireciona para `/planos`; `Checkout.jsx` foi
  removido do roteamento.
- `SuperAdminDashboard.jsx` ganhou a aba **"Ativar Conta"** (`AtivarContaTab`):
  formulário com dados da escola, do responsável, plano (define limites de uso)
  e valor mensal negociado (sem tabela fixa), que chama
  `POST /planos/admin/ativar-conta` e devolve um link de "definir senha" pra
  mandar pro cliente pelo mesmo WhatsApp.

**O que faltava (completado em 2026-09-05):** o endpoint
`POST /planos/admin/ativar-conta` não existia no backend - a tela já estava
pronta, só não tinha para onde chamar. E `POST /checkout/iniciar` (autocadastro
público) continuava **aberto e funcional**, criando conta de verdade se
chamado direto (API/Postman), apesar da SPA não expor mais esse caminho -
mesma classe de bug do usuário órfão em `/auth/register` (a UI escondia o
caminho, não fechava a porta).

## Testado de ponta a ponta (2026-09-05)

Rodei o backend de verdade (uvicorn + sqlite local) e o frontend (vite dev)
e dirigi o fluxo real via HTTP, não só a suíte de testes:

1. `POST /auth/login` como super admin (`admin@adaptai.com.br`, criado por
   `python -m app.scripts.setup_inicial`) → token OK.
2. `POST /planos/seed` + `GET /planos/admin/todos` → catálogo de planos
   disponível pro formulário.
3. `POST /planos/admin/ativar-conta` → 201, `link_definir_senha` retornado.
4. `POST /auth/reset-password` com o token do link → senha definida.
5. `POST /auth/login` com a conta nova + senha definida → token OK,
   `GET /auth/me` confirma `role: admin`, `escola_id` preenchido.
6. `POST /planos/admin/ativar-conta` com o token da conta recém-criada (não
   super admin) → 403 (confirma que só super admin ativa conta).
7. `POST /checkout/iniciar` e `POST /auth/register` com payload válido →
   403 nos dois, e **nenhuma escola/usuário foi criado** (conferido direto
   no banco).

**Achado durante o teste, corrigido na hora:** `POST /auth/register` ainda
estava aberto nesta branch (o fechamento dele tinha ficado só na branch
`feat/superadmin-painel-melhorias`, não mergeada) - um curl direto criou de
verdade um `User(role=TEACHER, escola_id=NULL)` público. Reaplicado aqui o
mesmo fechamento (403, sem criar nada) - ver `tests/test_auth_register_desativado.py`.

## O que foi implementado agora

- **`app/api/routes/planos.py`** — novo `POST /admin/ativar-conta`
  (`require_super_admin`, já existente no arquivo): cria `Escola` + `User`
  (role `ADMIN`, senha aleatória descartada) + `Assinatura` (com
  `valor_mensal` **negociado**, independente do `Plano.valor` do catálogo -
  o plano só define limites de uso) + `ConfiguracaoEscola`. Gera o link de
  definir senha reaproveitando o mesmo mecanismo de token de reset de senha
  já usado no convite de professor (`professores.py`:
  `create_password_reset_token` + `password_reset_fingerprint`), não um
  mecanismo novo.
- **`app/api/routes/checkout.py`** — `POST /iniciar` desativado: sempre
  devolve `403`, não cria nada. Mesmo padrão usado em `/auth/register`
  (P6 de outra rodada): mantém a rota (não 404 mudo), mas fecha a porta de
  fato. Limpeza de imports/helpers que só existiam pra essa lógica removida
  (`criar_token_acesso`, `get_password_hash`, `get_fast_model`,
  `ConfiguracaoEscola`, `AsaasError`, `UserRole`, `CheckoutResponse`,
  `StatusAssinatura` do schema).
- Testes (`tests/test_ativacao_conta_manual.py`, 11 casos): checkout sempre
  403 e nunca cria escola/usuário (schema ainda valida antes do 403); ativação
  manual - 401 sem token, 403 pra ADMIN comum, 201 pra SUPER_ADMIN nos dois
  `status_inicial` (`trial`/`ativa`, cada um com `data_fim`/
  `data_proxima_cobranca` corretos), `valor_mensal` negociado gravado
  independente do `Plano.valor`, link de definir senha com token decodificável
  e `sub` = e-mail correto, e-mail duplicado → 400, plano inexistente → 404,
  `status_inicial` inválido → 400.

## O que NÃO foi mudado nesta rodada

- `POST /planos/admin/escola` (endpoint mais antigo, sem criar usuário) segue
  existindo, sem uso - não foi removido nem o `ativar-conta` novo o substitui
  formalmente, só resolve o caso de uso real que a tela pedia.
- `GET/POST /checkout/verificar-email`, `/verificar-cnpj`, `/resumo-plano`,
  `/webhook/asaas`, `/assinatura/link` continuam públicos/ativos - não criam
  conta, ficaram fora do escopo (só o que criava conta foi fechado).
- Nenhuma integração com Asaas nesse fluxo manual - sem cobrança real
  acontecendo ainda, condizente com o trial que não expira de propósito.

## Como testar

```bash
cd adaptai
python -m pytest tests/test_ativacao_conta_manual.py -v   # só a feature
python -m pytest tests/ -q                                 # suíte completa
```

Fluxo manual (API rodando, super admin logado): `POST /api/v1/planos/admin/ativar-conta`
com `escola_nome`, `admin_nome`, `admin_email`, `plano_id` (de
`GET /api/v1/planos/admin/todos`), `valor_mensal`, `status_inicial` (`trial`
ou `ativa`) → 201 com `link_definir_senha`. No frontend: painel Super Admin →
aba "Ativar Conta".
