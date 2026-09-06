# AdaptAI — Contexto do projeto

Este repositório (`adaptai`) é o **backend** (FastAPI). O **frontend** é um repositório
separado, em outra pasta na mesma máquina:

```
C:\Users\Nexus\Downloads\Projetos\adaptai-frontend
```

## Frontend — visão rápida

- Stack: React 18 + Vite + React Router + Tailwind + Axios + Recharts.
- Consome a API deste backend via `VITE_API_URL` (`.env.example`), default
  `http://localhost:8000/api/v1` — mesmo prefixo `/api/v1` usado pelas rotas FastAPI aqui.
- Cliente HTTP central: `src/services/api.js`.
- Estrutura de `src/`: `pages/` (inclui `pages/materiaisAdaptados`, `pages/peiForm`,
  `pages/testeNegocio`), `components/` (+ `components/ui`), `contexts/`, `hooks/`,
  `services/`, `constants/`, `accessibility/`.

## Como usar os dois repos juntos

Ao investigar um bug ou TC da planilha de QA que envolva UI (ex.: "opção não aparece pro
professor", "tela em branco", "botão não funciona"), vale checar **os dois lados**:
- Backend: rota/endpoint em `app/api/routes/`, lógica em `app/services/`.
- Frontend: página/componente correspondente em `adaptai-frontend/src/pages/` ou
  `src/components/`, e a chamada de API em `src/services/`.

Não assumir que um problema é só de um lado sem checar ambos os repositórios quando a
causa raiz não estiver clara.

## Regras de segurança/QA (herdadas da planilha de testes)

- Não alterar/criar prompts de IA "às cegas" (sem validação com poucos exemplos) — mudanças
  de comportamento de IA têm custo e precisam de aprovação.
- Não gerar conteúdo de IA em massa para testes.
- Ver `docs/qa-agente-relatorio.md` para o histórico de investigação por TC da planilha de QA.

## Documentos de arquitetura (leia antes de mexer em conteúdo)

- `docs/ATIVACAO-CONTA-MANUAL.md` — autocadastro público (`/checkout/iniciar`,
  `/auth/register`) está **desativado de propósito**: o produto ainda não é
  vendido por self-service. Criação de conta é sempre manual, pelo super admin
  (`POST /planos/admin/ativar-conta`, aba "Ativar Conta" do painel), depois de
  negociação via WhatsApp. Leia antes de mexer em checkout, registro público
  ou no painel Super Admin.
- `docs/ARQUITETURA-CONTEUDOS.md` — diferença entre **Materiais** (biblioteca
  reutilizável do professor, N:N via `MaterialAluno`) e **Materiais Adaptados**
  (geração sob medida por aluno, usa `student.diagnosis`). Explica o padrão
  **conteúdo × ponte** que todo artefato deve seguir: nenhum conteúdo gerado por IA
  carrega `student_id` como *posse* — posse vive na tabela ponte.
- `docs/CORRECOES-2026-08-11.md` — rodada de correções (materiais adaptados,
  planejamento 404, encoding, toasts, rótulos). Traz sintoma → causa raiz → correção
  de cada item e a lista de pendências conscientes.
- `docs/CORRECOES-2026-08-17.md` — conteúdo da Biblioteca sai do disco efêmero
  para `Material.conteudo_gerado` (migration 012) + atividade de geometria com
  SVG sanitizado.
- `docs/CORRECOES-2026-08-18.md` — rodada seguinte, sobre o **peso** desse
  conteúdo: fim do `1038 Out of sort memory` nas listagens (colunas grandes
  viraram `deferred`, SELECTs explícitos), N+1 removidos, piso de tokens por
  tipo e destino explícito na tela de criação. Traz a
  `migrations/013_indices_listagem_materiais.sql`.

## Conteúdo de material NUNCA vai para disco

O serviço web do Railway roda em **disco efêmero**: qualquer arquivo em
`storage/` some no próximo redeploy, enquanto a linha no banco continua dizendo
que está tudo pronto. Já queimou duas vezes (ilustrações → migration 011;
materiais → migration 012).

Ao gravar qualquer artefato gerado, use uma coluna:
`Material.conteudo_gerado` (lido por `app/services/material_conteudo.py`) ou
`Ilustracao.imagem_bytes`. Colunas grandes devem nascer `deferred` e ficar fora
dos SELECTs de listagem — se entrarem num `SELECT *` com `ORDER BY`, o MySQL
responde `1038 Out of sort memory` e a listagem inteira cai.

## Homologação de tipos de material adaptado

Nem todos os 37 tipos estão liberados. A allowlist vive em **dois** lugares e
precisa ficar em sincronia:

- `app/api/routes/materiais_adaptados.py` → `TIPOS_HABILITADOS` (**fonte de verdade**;
  rejeita a geração com 422 antes de gastar crédito de IA)
- `adaptai-frontend/src/pages/materiaisAdaptados/config.js` → `TIPOS_HABILITADOS`
  (controla o que a UI deixa clicar; o resto aparece com selo "Em breve")

Tipos bloqueados **mantêm** prompt, viewer e histórico — só não podem ser gerados.
Para liberar um tipo, adicione o id nas duas listas.
