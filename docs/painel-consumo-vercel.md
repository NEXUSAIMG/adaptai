# Painel de consumo de IA na Vercel

> **Obsoleto (nov/2026).** O proxy agora é o `server.js` do frontend, no Railway
> (`/consumo-ia?key=...`, variáveis `PAINEL_URL`, `PAINEL_TOKEN` e `PAINEL_KEY` no
> serviço do frontend). A Vercel e o `api/painel.js` foram removidos. O texto abaixo
> fica como histórico.

O painel (`tokenmeter`) é gerado pelo backend (Python + MySQL, no Railway) e sai
como **um HTML autocontido**. Para acompanhá-lo por uma URL na Vercel — sem abrir
o app, sem o diálogo de usuário/senha do Basic Auth — há uma function de proxy.

```
navegador  ──/consumo-ia?key=XXX──►  Vercel (api/painel.js)  ──?token=YYY──►  Railway  /api/v1/admin/ai-usage/painel
```

Dois segredos, de propósito:

- **`PAINEL_TOKEN`** (`YYY`): backend ↔ Vercel. Nunca chega ao navegador.
- **`PAINEL_KEY`** (`XXX`): navegador ↔ Vercel. É o que você põe no bookmark.
  Vaza mais fácil (histórico, print); rotaciona sem tocar no Railway.

## Setup

### 1. Backend (Railway)

Gere um token e configure:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Railway → Variables:

| Variável       | Valor                          |
|----------------|--------------------------------|
| `PAINEL_TOKEN` | o token gerado acima           |

Sem `PAINEL_TOKEN` o endpoint continua só com Basic Auth (comportamento antigo);
`?token=` é ignorado.

### 2. Vercel (repo do frontend)

`api/painel.js` já está no repo. Vercel → Project Settings → Environment Variables:

| Variável      | Valor                                                                    |
|---------------|-------------------------------------------------------------------------|
| `PAINEL_URL`  | `https://SEU-BACKEND.railway.app/api/v1/admin/ai-usage/painel`          |
| `PAINEL_TOKEN`| **mesmo** valor do Railway                                              |
| `PAINEL_KEY`  | outro segredo (`token_urlsafe(24)`); sem ele, qualquer um com o link vê |

Redeploy para as variáveis valerem.

### 3. Acesso

```
https://SEU-APP.vercel.app/consumo-ia?key=XXX
```

Aceita os mesmos query params do painel: `dias`, `orcamento`, `tag_tenant`,
`tag_extra`, `refresh=1`, `atualizar`. Ex.: `/consumo-ia?key=XXX&dias=7&refresh=1`.

O painel **se recarrega sozinho a cada 5 min** (`atualizar=300`, default). O
período selecionado sobrevive ao reload. `atualizar=0` desliga; combine com
`refresh=1` se quiser recálculo forçado a cada ciclo (mais caro).

O caminho `/api/painel?key=XXX` funciona igual — `/consumo-ia` é só um alias
(rewrite no `vercel.json`).

## Notas

- **Cache:** só uma camada — o backend regenera no máximo a cada 5 min (cache em
  memória, por worker). A function da Vercel manda `no-store` de propósito: com
  o auto-reload de 5 min, uma segunda camada de cache na CDN faria o dado exibido
  chegar a ~10 min de atraso. `refresh=1` fura o cache do backend.
- **Acesso direto ao Railway** continua existindo: abrir
  `https://SEU-BACKEND.railway.app/api/v1/admin/ai-usage/painel` no navegador
  pede Basic Auth de um admin, como antes. O token é um caminho a mais, não um
  substituto.
- **Proteção extra:** se tiver Vercel Pro, dá pra ligar Password Protection no
  deployment e dispensar o `PAINEL_KEY`.
- **Desligar:** remova `PAINEL_TOKEN` do Railway. A function passa a receber 401
  do backend e o painel na Vercel para de responder; o acesso por Basic Auth
  segue intacto.
