# Problema — 17/09/2026 · Planejamento/PEI gera "0 objetivos" com sucesso fingido

> **Status: investigado, causa raiz confirmada com dados de produção. Correção
> NÃO desenhada neste documento — só os achados.**
> Motivado por um problema real relatado pelo usuário em
> `/students/191` → `/students/191/planejamento`: a geração carrega e volta
> imediatamente com "0 objetivos", sem erro nenhum na tela.

---

## O sintoma

Professor abre o Planejamento Curricular BNCC de um aluno, clica em "Gerar
Planejamento com IA", e a tela volta quase instantaneamente com:

- "Objetivos Adaptados — 0 objetivos"
- Nenhum erro exibido — a tela trata como sucesso
- `/students/{id}/peis` depois mostra "Nenhum PEI encontrado para este aluno"
- `/students/{id}/calendario` fica zerado (Total/Concluídas/Pendentes/Atrasadas = 0)

O "carrega e volta na hora" é o primeiro sinal de que a IA nunca foi chamada —
uma geração real leva até 2 minutos.

## Causa raiz 1 — formato de série incompatível entre aluno e currículo

`app/services/planejamento_bncc_completo_service.py:890` (e o equivalente em
`planejamento_bncc_service.py`) busca habilidades da BNCC com:

```python
CurriculoNacional.ano_escolar == ano_escolar   # ano_escolar = student.grade_level
```

Comparação exata de string. Mas os dois lados usam formatos diferentes:

- `students.grade_level` vem do dropdown `GRADE_LEVELS` em
  `adaptai-frontend/src/pages/StudentForm.jsx:8-26` — formato longo, ex.:
  `"2º Ano - Ensino Fundamental"`, `"1ª Série - Ensino Médio"`,
  `"EJA - Ensino Médio"`. Esse dropdown existe desde o commit `e0ee7fad`
  (19/12/2025).
- `curriculo_nacional.ano_escolar` usa formato curto, ex.: `"1º ano"`,
  `"1º ano EM"`. A tabela foi criada em produção em **03/07/2026**
  (`SHOW TABLE STATUS`) — mais de 6 meses **depois** do dropdown já usar o
  formato longo.

Quando `buscar_todas_habilidades` não encontra nenhuma habilidade pro
componente, o código só loga um aviso e pula (`planejamento_bncc_completo_service.py:892-894`):

```python
if not habilidades_db:
    logger.warning(f"[AVISO] Nenhuma habilidade para {componente} no {ano_escolar}")
    continue
```

Se isso acontece pra **todos** os componentes selecionados, o job termina
como `COMPLETED`, com `resultados_parciais = {}` e `total_objetivos_gerados: 0`
— sem nenhuma chamada de IA e sem nenhuma validação de "resultado vazio"
nesse caminho (`gerar_planejamento_completo`, linhas 996-1004).

**Query que confirma o descasamento** (rodada em produção):

```sql
SELECT DISTINCT s.grade_level
FROM students s
LEFT JOIN curriculo_nacional c ON c.ano_escolar = s.grade_level
WHERE c.id IS NULL;
```

Resultado: **todo** valor no formato longo aparece sem match — não só
`"EJA - Ensino Médio"` (o caso que originou a investigação), mas também
`"2º Ano - Ensino Fundamental"`, `"7º Ano - Ensino Fundamental"`,
`"1ª Série - Ensino Médio"` etc. O bug atinge Fundamental e Médio da mesma
forma, não é peculiaridade de EJA.

## Causa raiz 2 — corrupção de encoding (mojibake) em `curriculo_nacional`

Além do formato, boa parte do conteúdo da tabela está com UTF-8
duplamente codificado (bytes UTF-8 lidos como Latin-1 e regravados como
UTF-8). Exemplo, checado via `HEX()`:

```
valor exibido: "1Âº ano"
bytes reais:   31 C3 82 C2 BA 20 61 6E 6F
decodificação: '1' + 'Â'(C382) + 'º'(C2BA) + ' ano'
```

Reversível de forma limpa com `valor.encode('latin1').decode('utf8')` —
testado manualmente em `habilidade_descricao` real:

```
"Utilizar nÃºmeros naturais... situaÃ§Ãµes cotidianas."
→ "Utilizar números naturais... situações cotidianas."
```

Frase gramaticalmente correta após a reversão, sem sinal de corrupção dupla.

**Escopo medido** (748 linhas em `curriculo_nacional`):

| coluna | linhas corrompidas | % |
|---|---|---|
| `ano_escolar` | 563 | 75% |
| `componente` | 550 | 74% |
| `habilidade_descricao` | 687 | 92% |
| `objeto_conhecimento` | 493 | 66% |
| `eixo_tematico` | 167 | 22% |
| `campo_experiencia` | 162 | 22% |
| `exemplos_atividades` (JSON) | 0 | coluna 100% vazia, não populada |

A mesma corrupção existe em `students.grade_level`, mas só nos 147 (de 190)
alunos com valor no **formato curto legado** (`"3º ano"`, `"5º ano"`...,
cadastrados antes de 19/12/2025 ou via seed). Isso explica por que esses
alunos "funcionam" hoje: os dois lados da comparação estão corrompidos do
mesmo jeito, e batem por coincidência.

Formatos que **não** são corrupção, e sim convenção diferente:
- `"6 ano"`, `"7 ano"`, `"8 ano"`, `"9 ano"` — sem "º" nenhum (bytes
  confirmam: nunca existiu o caractere ali, não foi removido de um valor
  corrompido).
- `"1º ao 5º"`, `"3º ao 5º"`, `"1º e 2º"` — habilidades da BNCC que a própria
  base curricular agrupa por faixa de anos, não por série única. Existem no
  currículo mas nunca vão bater contra uma busca por série individual — é
  uma característica de modelagem à parte, não este bug.

**Origem da corrupção** — confirmada pelo histórico do git. Os scripts que
popularam a tabela (`importar_bncc.py`, `importar_bncc_completo.py` e mais
seis arquivos) foram commitados em `398cf50`, autor **marciogoes**
(`33270240+marciogoes@users.noreply.github.com`), **06/01/2026**, e depois
removidos do repositório em `9833cd6` (junto da adoção do Alembic pra
migrations — limpeza normal de script de seed pontual).

O texto-fonte desses scripts, visto direto no histórico do git, está
**limpo** — UTF-8 correto: `"ano_escolar": "1º ano"`,
`"componente": "Matemática"`, descrições com acentuação correta. A corrupção
**não está no código**. Ela aconteceu na hora de rodar o script contra o
banco (padrão clássico de conexão MySQL sem `utf8mb4` forçado no charset).
Como a tabela só foi criada em produção em 03/07/2026 — seis meses depois do
commit dos scripts — a execução real que gerou os dados corrompidos foi bem
posterior ao código em si.

## Causa raiz 3 — importação de alunos por CSV aceita qualquer texto

`POST /students/importar-csv` (`app/api/routes/students.py:363-469`) monta o
`Student` direto a partir da linha do CSV, **sem passar pelo schema
`StudentCreate`**:

```python
grade_level=_campo(row, "grade_level", "serie", "ano") or "Não especificado",
```

Nenhuma validação contra os 16 valores do dropdown — o que estiver na
célula do CSV é gravado como está. O próprio modelo de CSV oferecido pra
download (`adaptai-frontend/src/pages/Students.jsx:166-170`) usa um
**terceiro formato** (`"5º ano"`, `"6º ano"`), diferente tanto do dropdown de
cadastro individual quanto do formato salvo em `curriculo_nacional`.

`app/schemas/student.py:8,15,27` define `grade_level` como `str` livre
(`max_length=50`), sem enum — qualquer chamador da API (não só o CSV) pode
gravar qualquer string.

## Escopo real — quem tem currículo e quem não tem

A lista completa de `ano_escolar` distintos em `curriculo_nacional` (15
valores) cobre só Ensino Fundamental (1º ao 9º ano) e Ensino Médio (1º ao 3º
ano EM). **Zero linhas de Educação Infantil.**

- **Fundamental e Médio**: o dado da BNCC existe e está completo — o
  problema é 100% de descasamento (causas 1 e 2), não falta de conteúdo.
- **EJA** (Fundamental ou Médio): não tem código BNCC próprio na tabela —
  nenhuma linha com esse rótulo em `ano_escolar`.
- **Educação Infantil**: a BNCC organiza esse nível por campo de experiência
  × faixa etária (códigos tipo `EI0XCE0X`), estrutura diferente da que
  `curriculo_nacional` usa (`ano_escolar` + `componente`, modelo de
  Fundamental/Médio). Não existe linha nenhuma pra Infantil na tabela hoje.
- **`"Não especificado"`**: valor de fallback do importador CSV quando a
  célula de série vem vazia — nunca vai ter currículo correspondente.

## Quem é afetado, e desde quando

Todo aluno cadastrado pelo formulário individual **desde 19/12/2025**
(commit `e0ee7fad`, quando o dropdown passou a usar o formato longo) está
sujeito ao bug, para qualquer série de Fundamental ou Médio — não é um caso
raro de EJA, é o caminho normal de cadastro. Só os alunos legados (formato
curto, anteriores a essa data ou vindos de seed/demo) geram planejamento
normalmente hoje, e só por coincidência das duas pontas estarem corrompidas
da mesma forma.
