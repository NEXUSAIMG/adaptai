"""Painel de consumo em HTML autocontido, gerado a partir do banco.

Sem Metabase, sem serviço extra, sem CDN: um arquivo .html que abre em qualquer
navegador, inclusive offline. Serve para (a) validar tudo localmente antes de subir e
(b) ser o painel de fato enquanto uma ferramenta dedicada não se justificar.

Quando o Metabase entrar, este módulo continua útil como conferência: se o número dele
divergir daqui, um dos dois está errado.
"""
from __future__ import annotations

import datetime as dt
import html
import json
from decimal import Decimal

from sqlalchemy import text

# Paleta validada com scripts/validate_palette.js (ver docs/DECISOES.md).
# Ordem fixa, nunca ciclada: a 6ª categoria vira "Outros".
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
OUTROS_LIGHT, OUTROS_DARK = "#898781", "#898781"
MAX_SERIES = 5
# As séries são referenciadas por variável CSS, nunca por hex literal: um `fill="#2a78d6"`
# inline no SVG não responde a @media (prefers-color-scheme), e o modo escuro tem passos
# próprios — validados contra a superfície escura, não um espelho automático do claro.
SERIES_VAR = [f"var(--s{i})" for i in range(MAX_SERIES)]
OUTROS_VAR = "var(--sx)"

# Declarações do modo escuro, uma vez só, usadas em DOIS seletores distintos:
# a media query (preferência do sistema) e o data-theme (escolha explícita no botão).
# Não dá para uni-los numa lista de seletores — uma at-rule não pode fechar uma lista,
# e o navegador descarta o bloco inteiro em silêncio se você tentar.
_ESCURO = ("--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;"
           "--grid:#2c2c2a;--axis:#383835;--seq:#3987e5;--border:#2c2c2a;"
           + ";".join(f"--s{i}:{c}" for i, c in enumerate(SERIES_DARK))
           + ";--sx:#a3a196")


def _d(v) -> Decimal:
    return Decimal(str(v or 0))


def coletar(store, *, dias: int = 30, service: str | None = None,
            environment: str | None = None, tag_tenant: str = "tenant_id",
            tag_extra: str | None = None, unidade: str | None = None) -> dict:
    """Roda as agregações. Uma consulta por bloco do painel — nenhuma é pesada."""
    ev = f"{store.ev.name}"
    tg = f"{store.tag.name}"
    onde = ["e.occurred_at >= :ini"]
    # dias<=1 é o botão "hoje": janela de meia-noite UTC até agora (dia-calendário,
    # não 24h deslizantes) — é o que se espera de "quanto gastamos hoje". O período
    # anterior vira "ontem" inteiro, então o "vs período anterior" lê "hoje vs ontem".
    if dias <= 1:
        p: dict = {"ini": dt.datetime.combine(dt.datetime.utcnow().date(), dt.time())}
    else:
        p = {"ini": dt.datetime.utcnow() - dt.timedelta(days=dias)}
    # Janela imediatamente anterior, do mesmo tamanho — alimenta o "vs período
    # anterior" nos tiles. Só os dois números do resumo, não um painel inteiro.
    p["ini_ant"] = p["ini"] - dt.timedelta(days=max(dias, 1))
    filtro_extra = ""
    if service:
        onde.append("e.service = :svc"); p["svc"] = service
        filtro_extra += " AND e.service = :svc"
    if environment:
        onde.append("e.environment = :env"); p["env"] = environment
        filtro_extra += " AND e.environment = :env"
    W = " AND ".join(onde)

    with store.connect() as con:
        def q(sql, **extra):
            return [dict(r._mapping) for r in con.execute(text(sql), {**p, **extra})]

        resumo = q(f"""SELECT COUNT(*) AS chamadas, COALESCE(SUM(cost_usd),0) AS custo,
                       COALESCE(SUM(total_tokens),0) AS tokens,
                       COALESCE(SUM(input_tokens),0) AS input_tokens,
                       COALESCE(SUM(output_tokens),0) AS output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                       COALESCE(SUM(cache_write_tokens),0) AS cache_write,
                       AVG(duration_ms) AS lat_media, MAX(duration_ms) AS lat_max,
                       COUNT(DISTINCT run_id) AS execucoes,
                       MIN(occurred_at) AS ini, MAX(occurred_at) AS fim
                       FROM {ev} e WHERE {W}""")[0]
        anterior = q(f"""SELECT COUNT(*) AS chamadas,
                         COALESCE(SUM(cost_usd),0) AS custo,
                         COALESCE(SUM(total_tokens),0) AS tokens
                         FROM {ev} e
                         WHERE e.occurred_at >= :ini_ant AND e.occurred_at < :ini
                         {filtro_extra}""")[0]
        por_dia = q(f"""SELECT e.occurred_date AS dia, e.feature AS k,
                        COALESCE(SUM(e.cost_usd),0) AS custo
                        FROM {ev} e WHERE {W} GROUP BY e.occurred_date, e.feature
                        ORDER BY e.occurred_date""")
        por_feature = q(f"""SELECT e.feature AS k, COUNT(*) AS chamadas,
                            COALESCE(SUM(e.cost_usd),0) AS custo
                            FROM {ev} e WHERE {W} GROUP BY e.feature ORDER BY custo DESC""")
        por_modelo = q(f"""SELECT e.model AS k, COUNT(*) AS chamadas,
                           COALESCE(SUM(e.total_tokens),0) AS tokens,
                           COALESCE(SUM(e.cost_usd),0) AS custo
                           FROM {ev} e WHERE {W} GROUP BY e.model ORDER BY custo DESC""")
        por_tenant = q(f"""SELECT t.tag_value AS k, COUNT(*) AS chamadas,
                           COALESCE(SUM(e.cost_usd),0) AS custo
                           FROM {ev} e JOIN {tg} t
                             ON t.event_id = e.event_id AND t.tag_key = :tk
                           WHERE {W} GROUP BY t.tag_value ORDER BY custo DESC""",
                       tk=tag_tenant)
        # Série diária por tenant, para o sparkline de cada linha da barra.
        # (a de feature sai de por_dia, que já é por dia+feature.)
        por_dia_tenant = q(f"""SELECT e.occurred_date AS dia, t.tag_value AS k,
                               COALESCE(SUM(e.cost_usd),0) AS custo
                               FROM {ev} e JOIN {tg} t
                                 ON t.event_id = e.event_id AND t.tag_key = :tk
                               WHERE {W} GROUP BY e.occurred_date, t.tag_value
                               ORDER BY e.occurred_date""", tk=tag_tenant)
        # Corte por uma segunda dimensao livre, opcional (--tag-extra). Para
        # quando uma unica feature esconde variacao de custo por subtipo — um
        # gerador que produz N formatos sob a mesma feature, p.ex.
        # atividades = COUNT(DISTINCT run_id): quantas execucoes distintas, nao
        # quantas chamadas de IA (uma execucao com imagem dispara varias).
        por_extra = q(f"""SELECT t.tag_value AS k,
                          COUNT(DISTINCT e.run_id) AS atividades,
                          COUNT(*) AS chamadas,
                          COALESCE(SUM(e.cost_usd),0) AS custo
                          FROM {ev} e JOIN {tg} t
                            ON t.event_id = e.event_id AND t.tag_key = :tke
                          WHERE {W} GROUP BY t.tag_value ORDER BY custo DESC""",
                      tke=tag_extra) if tag_extra else []
        cobertura = q(f"""SELECT e.feature_source AS k, COUNT(*) AS n
                          FROM {ev} e WHERE {W} GROUP BY e.feature_source""")
        status = q(f"""SELECT e.status AS k, COUNT(*) AS n FROM {ev} e WHERE {W}
                       GROUP BY e.status""")
        sem_preco = q(f"""SELECT e.model AS k, COUNT(*) AS n FROM {ev} e
                          WHERE {W} AND e.priced = 0 GROUP BY e.model""")
        por_entidade = q(f"""SELECT COUNT(DISTINCT t.tag_value) AS unidades,
                             COALESCE(SUM(e.cost_usd),0) AS custo
                             FROM {ev} e JOIN {tg} t
                               ON t.event_id = e.event_id AND t.tag_key = 'entity_id'
                             WHERE {W}""")[0]
        servicos = q(f"""SELECT e.service AS k, COUNT(*) AS chamadas,
                         COALESCE(SUM(e.cost_usd),0) AS custo
                         FROM {ev} e WHERE {W} GROUP BY e.service ORDER BY custo DESC""")
        # Composição de tokens por feature: entrada, saída e cache são colunas
        # distintas desde sempre (store.py). Somá-las num total esconde o que
        # decide a ação — saída custa ~5x entrada.
        tokens_feature = q(f"""SELECT e.feature AS k, COUNT(*) AS chamadas,
                               COALESCE(SUM(e.input_tokens),0) AS input_tokens,
                               COALESCE(SUM(e.output_tokens),0) AS output_tokens,
                               COALESCE(SUM(e.cache_read_tokens),0) AS cache_read,
                               COALESCE(SUM(e.cache_write_tokens),0) AS cache_write,
                               COALESCE(SUM(e.cost_usd),0) AS custo
                               FROM {ev} e WHERE {W} GROUP BY e.feature
                               ORDER BY custo DESC""")
        # AVG/MAX e não percentil: `PERCENTILE_CONT` não existe no SQLite e a lib
        # se compromete com três bancos (store.py). Média + pior caso são
        # portáteis e agregam no banco, sem trazer linha bruta para a memória.
        latencia = q(f"""SELECT e.feature AS k, COUNT(e.duration_ms) AS n,
                         AVG(e.duration_ms) AS media, MAX(e.duration_ms) AS maximo
                         FROM {ev} e WHERE {W} AND e.duration_ms IS NOT NULL
                         GROUP BY e.feature ORDER BY media DESC""")
        # provider+model no group by: a tarifa de cache depende dos dois.
        cache_modelo = q(f"""SELECT e.provider AS provider, e.model AS model,
                             COALESCE(SUM(e.cache_read_tokens),0) AS cache_read,
                             COALESCE(SUM(e.cache_write_tokens),0) AS cache_write,
                             COALESCE(SUM(e.input_tokens),0) AS input_tokens
                             FROM {ev} e WHERE {W} GROUP BY e.provider, e.model""")
        # Chamada que falhou já queimou os tokens de entrada — o custo dela está
        # hoje diluído no total, sem rótulo.
        erros = q(f"""SELECT COALESCE(e.error_type,'(sem tipo)') AS k, COUNT(*) AS n,
                      COALESCE(SUM(e.cost_usd),0) AS custo,
                      COALESCE(SUM(e.total_tokens),0) AS tokens
                      FROM {ev} e WHERE {W} AND e.status <> 'ok'
                      GROUP BY e.error_type ORDER BY n DESC""")
        # request_path vem do middleware HTTP, gravado em toda chamada. Corta o
        # custo por rota da API, que não coincide com o corte por feature: a
        # mesma feature é acionada por rotas diferentes.
        por_rota = q(f"""SELECT t.tag_value AS k, COUNT(*) AS chamadas,
                         COALESCE(SUM(e.cost_usd),0) AS custo
                         FROM {ev} e JOIN {tg} t
                           ON t.event_id = e.event_id AND t.tag_key = 'request_path'
                         WHERE {W} GROUP BY t.tag_value ORDER BY custo DESC""")
        # Geração de imagem não tokeniza (fal.ai etc.): input/output_tokens ficam
        # 0 nesses eventos, então "quantidade" aqui é COUNT(*), não soma de
        # tokens como nas outras seções. `operation` já existe no schema desde
        # sempre - só ninguém tinha gravado nada além de 'chat' até agora.
        imagens = q(f"""SELECT e.model AS k, COUNT(*) AS imagens,
                        COALESCE(SUM(e.cost_usd),0) AS custo,
                        SUM(CASE WHEN e.status <> 'ok' THEN 1 ELSE 0 END) AS falhas,
                        COALESCE(SUM(e.priced),0) AS precificadas
                        FROM {ev} e WHERE {W} AND e.operation = 'image_generation'
                        GROUP BY e.model ORDER BY imagens DESC""")
        # Execuções mais caras: a média por request esconde o outlier. Um run
        # que disparou 200 chamadas por bug de retry custa 20x um normal e some
        # no "custo médio". run_id NULL = chamada solta fora de context(), não
        # entra aqui (não é uma "execução"). MIN(feature): um run tem uma só.
        top_runs = q(f"""SELECT e.run_id AS run_id, MIN(e.feature) AS feature,
                         COUNT(*) AS chamadas,
                         COALESCE(SUM(e.total_tokens),0) AS tokens,
                         COALESCE(SUM(e.cost_usd),0) AS custo
                         FROM {ev} e WHERE {W} AND e.run_id IS NOT NULL
                         GROUP BY e.run_id ORDER BY custo DESC LIMIT 10""")
        # Custo por unidade de negócio arbitrária (--unit): o mesmo que
        # por_entidade, mas a chave de tag é escolhida (custo por laudo, por
        # aluno...). É a métrica-assinatura da lib, que só existia em código.
        uk = unidade.split(".", 1)[1] if unidade and unidade.startswith("tags.") else unidade
        por_unidade = q(f"""SELECT COUNT(DISTINCT t.tag_value) AS unidades,
                            COUNT(*) AS chamadas,
                            COALESCE(SUM(e.cost_usd),0) AS custo
                            FROM {ev} e JOIN {tg} t
                              ON t.event_id = e.event_id AND t.tag_key = :uk
                            WHERE {W}""", uk=uk)[0] if uk else None
    return {"dias": dias, "resumo": resumo, "anterior": anterior, "por_dia": por_dia,
            "por_feature": por_feature,
            "por_modelo": por_modelo, "por_tenant": por_tenant, "cobertura": cobertura,
            "status": status, "sem_preco": sem_preco, "por_entidade": por_entidade,
            "servicos": servicos, "tag_tenant": tag_tenant,
            "por_extra": por_extra, "tag_extra": tag_extra,
            "por_dia_tenant": por_dia_tenant,
            "top_runs": top_runs, "por_unidade_custom": por_unidade, "unidade": uk,
            "tokens_feature": tokens_feature, "latencia": latencia,
            "cache_modelo": cache_modelo, "erros": erros, "por_rota": por_rota,
            "imagens": imagens}


def _topn(linhas: list[dict], n: int = MAX_SERIES) -> tuple[list[str], dict]:
    """Top N por custo; o resto vira 'Outros'. Nunca gera cor nova."""
    ordenado = sorted(linhas, key=lambda r: _d(r["custo"]), reverse=True)
    top = [str(r["k"]) for r in ordenado[:n]]
    resto = sum(_d(r["custo"]) for r in ordenado[n:])
    return top, {"Outros": resto} if resto > 0 else {}


def _fmt(v: Decimal, casas: int = 2) -> str:
    s = f"{v:,.{casas}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _int(v) -> str:
    """Inteiro com separador de milhar brasileiro."""
    return f"{int(v or 0):,}".replace(",", ".")


def _ms(v) -> str:
    """Duração legível: abaixo de 1s em ms, acima em segundos."""
    n = float(v or 0)
    return f"{n:.0f} ms" if n < 1000 else _fmt(Decimal(str(n / 1000)), 1) + " s"


def _variacao(atual, anterior) -> str:
    """"▲ 42% vs anterior" — string vazia quando não há período anterior com dado."""
    a = float(anterior or 0)
    if a <= 0:
        return ""
    p = 100 * (float(atual) - a) / a
    return f"{'▲' if p >= 0 else '▼'} {abs(p):.0f}% vs período anterior"


def _decompor_custo(atual: dict, anterior: dict) -> dict | None:
    """Por que o custo mudou vs o período anterior.

    custo = chamadas × (tokens/chamada) × (custo/token). A variação de cada
    fator diz o que mexeu: mais uso (chamadas), prompt/resposta maior
    (tokens/chamada), ou preço/mix de modelo (custo/token). Os três se
    multiplicam de volta na variação total.

    `None` quando não dá para decompor (período anterior sem dado, ou algum
    fator zero — ex.: só geração de imagem, que não tokeniza) ou quando o
    custo mexeu menos de 5% (ruído).
    """
    c1, k1, u1 = (float(atual.get("chamadas") or 0), float(atual.get("tokens") or 0),
                  float(atual.get("custo") or 0))
    c0, k0, u0 = (float(anterior.get("chamadas") or 0), float(anterior.get("tokens") or 0),
                  float(anterior.get("custo") or 0))
    if min(c0, c1, k0, k1, u0, u1) <= 0:
        return None
    var_total = u1 / u0 - 1
    if abs(var_total) < 0.05:
        return None
    tpc1, tpc0 = k1 / c1, k0 / c0                 # tokens por chamada
    upt1, upt0 = u1 / k1, u0 / k0                 # custo por token
    return {
        "total": var_total,
        "chamadas": c1 / c0 - 1,
        "tokens_chamada": tpc1 / tpc0 - 1,
        "custo_token": upt1 / upt0 - 1,
    }


def _pico_diario(por_dia: list[dict], fator: float = 3.0) -> dict | None:
    """Dia mais caro contra a mediana diária. `None` se há menos de 7 dias com
    dado (amostra pequena demais para chamar de anomalia) ou nenhum pico.

    Mediana, não média: um único dia de pico não desloca a mediana, então o
    'fator' compara contra o dia típico, não contra um patamar já contaminado.
    """
    diario: dict[str, Decimal] = {}
    for x in por_dia:
        diario[str(x["dia"])] = diario.get(str(x["dia"]), Decimal(0)) + _d(x["custo"])
    if len(diario) < 7:
        return None
    vals = sorted(diario.values())
    n = len(vals)
    mediana = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    dia_pico, custo_pico = max(diario.items(), key=lambda kv: kv[1])
    if mediana <= 0 or custo_pico <= mediana * Decimal(str(fator)):
        return {"pico": False, "mediana": mediana}
    return {"pico": True, "mediana": mediana, "dia": dia_pico,
            "custo": custo_pico, "mult": custo_pico / mediana}


def _economia_cache(cache_modelo: list[dict]) -> dict:
    """Quanto o prompt caching poupou, contra ter pago entrada cheia.

    A tarifa de leitura de cache é ~10% da de entrada (pricing.yaml), então o
    desconto por token é `input_per_mtok - cache_read_per_mtok`.

    ESTIMATIVA, não fatura: o painel agrega o período inteiro e resolve a tarifa
    pelo instante de agora. A tabela tem faixas com validade, então uma virada de
    preço dentro da janela desloca o número. Serve para dimensionar ordem de
    grandeza.
    """
    lido = sum(int(r["cache_read"] or 0) for r in cache_modelo)
    gravado = sum(int(r["cache_write"] or 0) for r in cache_modelo)
    entrada = sum(int(r["input_tokens"] or 0) for r in cache_modelo)
    base = {"lido": lido, "gravado": gravado, "entrada": entrada,
            "economia": None, "em_uso": lido > 0 or gravado > 0}
    # Denominador é todo token que entrou no modelo por um caminho ou outro.
    base["taxa"] = (100.0 * lido / (lido + entrada)) if (lido + entrada) else 0.0
    if not base["em_uso"]:
        return base
    try:
        from .pricing import MTOK, PriceBook
        pb = PriceBook()
    except Exception:
        return base                      # sem tabela de preço, mostra só os tokens
    agora = dt.datetime.utcnow()
    economia = Decimal(0)
    for r in cache_modelo:
        rate = pb.resolve(str(r["provider"]), str(r["model"]), agora)
        if rate is None or rate.cache_read_per_mtok is None:
            continue
        desconto = rate.input_per_mtok - rate.cache_read_per_mtok
        economia += Decimal(int(r["cache_read"] or 0)) * desconto / MTOK
    base["economia"] = economia
    return base


def _fim_do_mes() -> str:
    h = dt.date.today()
    prox = dt.date(h.year + (h.month == 12), (h.month % 12) + 1, 1)
    return (prox - dt.timedelta(days=1)).strftime("%d/%m")


def _svg_area(por_dia, feats, cores, proj_por_dia: Decimal, dias_restantes: int) -> str:
    """Área empilhada acumulada + projeção tracejada. Uma escala, um eixo."""
    dias = sorted({str(r["dia"]) for r in por_dia})
    if not dias:
        return '<p class="vazio">Sem dados no período.</p>'
    acc = {f: Decimal(0) for f in feats}
    serie = {f: [] for f in feats}
    total_dia = []
    for d in dias:
        for f in feats:
            acc[f] += sum(_d(r["custo"]) for r in por_dia
                          if str(r["dia"]) == d and (str(r["k"]) == f or
                             (f == "Outros" and str(r["k"]) not in feats)))
            serie[f].append(acc[f])
        total_dia.append(sum(acc.values()))

    W, H, PL, PR, PT, PB = 900, 300, 62, 16, 16, 34
    proj_total = total_dia[-1] + proj_por_dia * dias_restantes
    ymax = float(max(proj_total, total_dia[-1])) or 1.0
    n = len(dias)
    npro = dias_restantes if dias_restantes > 0 else 0
    total_pts = n + npro
    def X(i): return PL + (W - PL - PR) * (i / max(total_pts - 1, 1))
    def Y(v): return H - PB - (H - PT - PB) * (float(v) / ymax)

    out = []
    # grade + eixo Y
    for t in range(5):
        v = ymax * t / 4
        y = Y(v)
        out.append(f'<line class="grid" x1="{PL}" y1="{y:.1f}" x2="{W-PR}" y2="{y:.1f}"/>')
        out.append(f'<text class="ax" x="{PL-8}" y="{y+4:.1f}" text-anchor="end">'
                   f'US$ {_fmt(Decimal(v))}</text>')
    # áreas empilhadas, de baixo para cima, com 2px de respiro entre faixas.
    #
    # Com UM único dia, um polígono de área é degenerado: largura zero, nada aparece,
    # e a legenda passa a nomear três séries que não estão em lugar nenhum da tela.
    # É exatamente a forma do primeiro dia em produção. Nesse caso vira coluna.
    base = [Decimal(0)] * n
    if n == 1:
        LARG = 46
        x = PL + 10 + LARG / 2          # encostada no eixo, inteira dentro da área útil
        for idx, f in enumerate(feats):
            topo = base[0] + serie[f][0]
            y0, y1 = Y(topo), Y(base[0])
            alt = max(y1 - y0 - 2, 1.0)          # 2px de respiro entre faixas
            out.append(f'<rect class="area" fill="{cores[idx]}" x="{x-LARG/2:.1f}" '
                       f'y="{y0:.1f}" width="{LARG}" height="{alt:.1f}" rx="2"/>')
            base = [topo]
    else:
        for idx, f in enumerate(feats):
            topo = [base[i] + serie[f][i] for i in range(n)]
            pts_top = " ".join(f"{X(i):.1f},{Y(topo[i]):.1f}" for i in range(n))
            pts_bot = " ".join(f"{X(i):.1f},{Y(base[i]):.1f}" for i in reversed(range(n)))
            out.append(f'<polygon class="area" fill="{cores[idx]}" points="{pts_top} {pts_bot}"/>')
            out.append(f'<polyline class="borda" points="{pts_top}"/>')
            base = topo
    # projeção
    if npro:
        y0 = Y(total_dia[-1])
        x1, y1 = X(total_pts - 1), Y(proj_total)
        x0 = (PL + 10 + 46) if n == 1 else X(n - 1)
        out.append(f'<line class="proj" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}"/>')
        out.append(f'<circle class="pt" cx="{x1:.1f}" cy="{y1:.1f}" r="4"/>')
        # o ponto de projeção fica SEMPRE no topo da escala (proj_total == ymax), então
        # um rótulo acima dele colide com o rótulo do eixo. Vai abaixo da linha.
        # o eixo X só rotula o trecho com dado; sem a data do fim, o leitor não sabe
        # até quando a tracejada vai.
        fim_mes = _fim_do_mes()
        out.append(f'<text class="rot" x="{x1-6:.1f}" y="{y1+18:.1f}" text-anchor="end">'
                   f'projeção {fim_mes}: US$ {_fmt(proj_total)}</text>')
    # eixo X: primeiro, meio, último — sem repetir quando há poucos dias
    for i in sorted({0, n // 2, n - 1}):
        out.append(f'<text class="ax" x="{X(i) if n > 1 else (PL + 10 + 23):.1f}" '
                   f'y="{H-12}" text-anchor="middle">{dias[i][5:]}</text>')
    # camada de hover: a faixa acompanha o espaçamento real entre pontos. Com 12px
    # fixos e uma janela de um ano os alvos se sobrepõem, e o dia sob o cursor
    # deixa de ser o dia que o tooltip mostra.
    passo = (W - PL - PR) / max(total_pts - 1, 1)
    for i, d in enumerate(dias):
        cx = X(i) if n > 1 else (PL + 10 + 23)
        larg = min(12.0, max(passo, 1.5)) if n > 1 else 50
        out.append(f'<rect class="hit" x="{cx-larg/2:.1f}" y="{PT}" width="{larg}" '
                   f'height="{H-PT-PB}" data-t="{html.escape(d)} · US$ '
                   f'{_fmt(total_dia[i], 4)} acumulado"/>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">{"".join(out)}</svg>'


def _sparkline(valores: list[float], w: int = 64, h: int = 16) -> str:
    """Mini-série do custo diário de uma linha. Só a forma: sem eixo, sem rótulo.
    Vazio (ou um ponto só) não desenha nada — não haveria tendência a mostrar."""
    v = [float(x or 0) for x in valores]
    if len(v) < 2 or max(v) <= 0:
        return ""
    vmax = max(v)
    pts = " ".join(
        f"{i / (len(v) - 1) * (w - 2) + 1:.1f},{h - 1 - x / vmax * (h - 2):.1f}"
        for i, x in enumerate(v))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'aria-hidden="true"><polyline points="{pts}"/></svg>')


def _serie_diaria(por_dia: list[dict]) -> dict[str, list[float]]:
    """`[{dia,k,custo}]` -> `{k: [custo por dia, em ordem de data]}`, preenchendo
    com 0 os dias sem evento daquela chave (senão o sparkline mente a inclinação)."""
    dias = sorted({str(r["dia"]) for r in por_dia})
    idx = {d: i for i, d in enumerate(dias)}
    out: dict[str, list[float]] = {}
    for r in por_dia:
        s = out.setdefault(str(r["k"]), [0.0] * len(dias))
        s[idx[str(r["dia"])]] += float(r["custo"] or 0)
    return out


def _svg_barras(linhas, cores=None, rotulo="", limite=8, *, chave="custo",
                fmt=None, tt=None, vazio="Sem dados.", series=None) -> str:
    """Barras horizontais com rótulo direto — atende a regra de relevo do contraste.

    `chave`/`fmt` deixam a mesma barra servir custo (US$) e latência (ms). Para
    grandeza que não é categórica — latência —, passe `cores=None`: a cor vira a
    sequencial única, porque matiz por categoria ali sugeriria um agrupamento
    que não existe.

    `series` (dict k -> lista de custo diário) adiciona um sparkline por linha —
    mostra QUEM está crescendo, que a barra de total não mostra.
    """
    fmt = fmt or (lambda v: f"US$ {_fmt(_d(v), 4)}")
    tt = tt or (lambda r: f"{r['k']} · {int(r.get('chamadas') or 0)} chamada(s)")
    dados = [(str(r["k"]), float(r[chave] or 0), tt(r)) for r in linhas[:limite]]
    if not dados:
        return f'<p class="vazio">{html.escape(vazio)}</p>'
    vmax = max(v for _, v, _ in dados) or 1.0
    linhas_html = []
    for i, (k, v, dica) in enumerate(dados):
        pct = 100 * v / vmax
        cor = (cores[i % len(cores)] if cores else "var(--seq)")
        spark = _sparkline(series.get(k, [])) if series else ""
        cls = "brow spk" if series else "brow"
        linhas_html.append(
            f'<div class="{cls}" data-t="{html.escape(dica)}">'
            f'<span class="blabel" title="{html.escape(k)}">{html.escape(k)}</span>'
            f'<span class="btrack"><span class="bfill" style="width:{pct:.1f}%;'
            f'background:{cor}"></span></span>'
            + (f'<span class="bspk">{spark}</span>' if series else "")
            + f'<span class="bval">{html.escape(fmt(v))}</span></div>')
    return f'<div class="bars" aria-label="{html.escape(rotulo)}">{"".join(linhas_html)}</div>'


def _stack(segmentos) -> str:
    """Barra empilhada única, para composição de um todo. Rótulo percentual na
    legenda: a fatia estreita não comporta texto dentro e some se depender dele."""
    segs = [(r, int(v or 0), c) for r, v, c in segmentos if int(v or 0) > 0]
    if not segs:
        return '<p class="vazio">Sem tokens no período.</p>'
    total = sum(v for _, v, _ in segs)
    pct = lambda v: _fmt(Decimal(str(100 * v / total)), 1)      # noqa: E731
    barra = "".join(
        f'<span class="sseg" style="width:{100 * v / total:.2f}%;background:{c}" '
        f'data-t="{html.escape(r)} · {_int(v)} tokens ({pct(v)}%)"></span>'
        for r, v, c in segs)
    leg = "".join(
        f'<span class="lg"><i style="background:{c}"></i>{html.escape(r)} '
        f'<b>{pct(v)}%</b></span>' for r, v, c in segs)
    return f'<div class="stack">{barra}</div><div class="legend">{leg}</div>'


def _tabela(linhas, colunas) -> str:
    th = "".join(f"<th>{html.escape(c[1])}</th>" for c in colunas)
    tr = []
    for r in linhas:
        tds = []
        for chave, _ in colunas:
            v = r.get(chave)
            tds.append(f"<td>{html.escape(str(v if v is not None else '—'))}</td>")
        tr.append(f"<tr>{''.join(tds)}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(tr)}</tbody></table>"


def _miolo(dados: dict, orcamento: float | None = None) -> str:
    """Tiles e seções de UM período.

    O shell — CSS, script, cabeçalho — fica em `render()`, porque é único; isto
    aqui se repete uma vez por período no arquivo final. Separar os dois é o que
    permite trocar de janela sem servidor: o HTML já traz todas as versões
    prontas e o clique só decide qual fica visível.
    """
    r = dados["resumo"]
    custo = _d(r["custo"])
    hoje = dt.date.today()
    dias_periodo = max(dados["dias"], 1)
    media_dia = custo / dias_periodo
    fim_mes = (hoje.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    # Projetar o fim do mês pela média de um período longo é enganoso: a média de
    # 365 dias dilui crescimento e sazonalidade, e a tracejada sugeriria uma
    # previsão que o número não sustenta. Acima de um mês o painel vira histórico.
    # "hoje" (dias<=1) não projeta: extrapolar um dia parcial para o mês é ruído.
    projetar = 2 <= dias_periodo <= 31
    restantes = max((fim_mes - hoje).days, 0) if projetar else 0

    feats_top, outros = _topn(dados["por_feature"])
    feats = feats_top + (["Outros"] if outros else [])

    # --- alertas: ícone + rótulo, nunca cor sozinha ---
    alertas = []
    cob = {str(x["k"]): int(x["n"]) for x in dados["cobertura"]}
    tot_cob = sum(cob.values()) or 1
    bem = cob.get("explicit", 0) + cob.get("context", 0)
    pct_cob = 100 * bem / tot_cob
    alertas.append(("good" if pct_cob >= 90 else "warning" if pct_cob >= 60 else "serious",
                    "Cobertura da atribuição", f"{pct_cob:.0f}% com contexto explícito",
                    f"{cob.get('inferred',0)} inferido(s), {cob.get('unknown',0)} sem origem"))
    if dados["sem_preco"]:
        m = ", ".join(str(x["k"]) for x in dados["sem_preco"][:3])
        alertas.append(("serious", "Modelo sem preço",
                        f"{sum(int(x['n']) for x in dados['sem_preco'])} evento(s)",
                        f"custo NULL em: {m}"))
    else:
        alertas.append(("good", "Precificação", "todos os modelos precificados", ""))
    n_erros = sum(int(x["n"]) for x in dados["status"] if str(x["k"]) != "ok")
    custo_erro = sum(_d(x["custo"]) for x in dados["erros"])
    tipos_erro = ", ".join(str(x["k"]) for x in dados["erros"][:2])
    alertas.append(("good" if n_erros == 0 else "critical", "Chamadas com erro",
                    f"{n_erros} de {r['chamadas']}",
                    # Falha não sai de graça: a entrada já foi enviada e cobrada.
                    f"US$ {_fmt(custo_erro, 4)} queimados · {tipos_erro}"
                    if n_erros else "nenhuma"))
    # O painel já se audita para cobertura e preço; aqui ele se audita para
    # CUSTO. Um dia 4x acima do típico é o que se quer ver sem procurar.
    pico = _pico_diario(dados["por_dia"])
    if pico and pico["pico"]:
        alertas.append(("warning" if pico["mult"] < 5 else "serious",
                        "Pico de custo diário",
                        f"{str(pico['dia'])[5:]} · US$ {_fmt(pico['custo'], 2)}",
                        f"{_fmt(pico['mult'], 1)}× a mediana diária "
                        f"(US$ {_fmt(pico['mediana'], 2)}/dia)"))
    elif pico:
        alertas.append(("good", "Custo diário", "sem picos no período",
                        f"mediana US$ {_fmt(pico['mediana'], 2)}/dia"))

    ent = dados["por_entidade"]
    unidades = int(ent["unidades"] or 0)
    por_unidade = (_d(ent["custo"]) / unidades) if unidades else None

    # --unit: custo por unidade de negócio escolhida (custo por laudo, por aluno)
    uc = dados.get("por_unidade_custom")
    uc_n = int(uc["unidades"] or 0) if uc else 0
    uc_custo = (_d(uc["custo"]) / uc_n) if uc_n else None
    execucoes = int(r["execucoes"] or 0)
    por_execucao = (custo / execucoes) if execucoes else None
    chamadas = int(r["chamadas"] or 0)
    # Fan-out: quantas chamadas de IA um único request dispara. Sai dos mesmos
    # dois números que já alimentam o "custo por request".
    fanout = (chamadas / execucoes) if execucoes else None
    cache = _economia_cache(dados["cache_modelo"])

    total_imagens = sum(int(x["imagens"] or 0) for x in dados["imagens"])
    custo_imagens = sum(_d(x["custo"]) for x in dados["imagens"])

    ant = dados.get("anterior") or {}
    var_custo = _variacao(custo, ant.get("custo"))
    var_chamadas = _variacao(chamadas, ant.get("chamadas"))
    decomp = _decompor_custo(
        {"chamadas": chamadas, "tokens": r["tokens"], "custo": custo}, ant)

    def tiles():
        t = [("Custo total", f"US$ {_fmt(custo, 2)}",
              var_custo or _rotulo_periodo(dados["dias"])),
             ("Média diária", f"US$ {_fmt(media_dia, 2)}",
              f"projeção do mês: US$ {_fmt(custo + media_dia * restantes, 2)}" if projetar
              else "no dia até agora" if dias_periodo <= 1
              else f"média dos {dados['dias']} dias"),
             ("Chamadas", _int(chamadas),
              f"{_int(r['tokens'])} tokens · {var_chamadas}" if var_chamadas
              else _int(r["tokens"]) + " tokens")]
        if por_execucao is not None:
            t.append(("Custo por request", f"US$ {_fmt(por_execucao, 4)}",
                      f"{execucoes} execuç(ões) · "
                      f"{_fmt(Decimal(str(fanout)), 1)} chamada(s) cada"))
        if por_unidade is not None:
            t.append(("Custo por entidade", f"US$ {_fmt(por_unidade, 4)}",
                      f"{unidades} unidade(s) distintas"))
        if uc_custo is not None:
            rot = str(dados.get("unidade"))
            t.append((f"Custo por {rot}", f"US$ {_fmt(uc_custo, 4)}",
                      f"{uc_n} {rot}(s) distinto(s)"))
        if r["lat_media"] is not None:
            t.append(("Latência média", _ms(r["lat_media"]),
                      f"pior caso: {_ms(r['lat_max'])}"))
        if cache["em_uso"]:
            eco = (f" · economia US$ {_fmt(cache['economia'], 4)}"
                   if cache["economia"] is not None else " · sem tabela de preço")
            t.append(("Cache de prompt", f"{cache['taxa']:.0f}%",
                      f"da entrada{eco}"))
        if total_imagens:
            det_img = (f"US$ {_fmt(custo_imagens, 4)}" if custo_imagens
                       else "sem preço configurado")
            t.append(("Imagens geradas", _int(total_imagens), det_img))
        if orcamento:
            pct = 100 * float(custo) / orcamento
            t.append(("Orçamento", f"{pct:.0f}%", f"de US$ {_fmt(Decimal(orcamento), 2)}"))
        return "".join(
            f'<div class="tile"><div class="tl">{html.escape(a)}</div>'
            f'<div class="tv">{html.escape(b)}</div>'
            f'<div class="ts">{html.escape(c)}</div></div>' for a, b, c in t)

    legenda = "".join(
        f'<span class="lg"><i style="background:{SERIES_VAR[i % len(SERIES_VAR)]}"></i>'
        f'{html.escape(f)}</span>' for i, f in enumerate(feats_top)) + (
        f'<span class="lg"><i style="background:{OUTROS_VAR}"></i>Outros</span>' if outros else "")

    spark_feat = _serie_diaria(dados["por_dia"])
    spark_tenant = _serie_diaria(dados.get("por_dia_tenant") or [])

    area = _svg_area(dados["por_dia"], feats,
                     SERIES_VAR[:len(feats_top)] + ([OUTROS_VAR] if outros else []),
                     media_dia, restantes)

    # ---- por que o custo mudou vs o período anterior --------------------
    decomp_html = ""
    if decomp:
        def _p(v):
            return f"{'+' if v >= 0 else '-'}{abs(v) * 100:.0f}%"
        fatores = [("mais/menos chamadas", decomp["chamadas"]),
                   ("tokens por chamada", decomp["tokens_chamada"]),
                   ("custo por token (preço / mix de modelo)", decomp["custo_token"])]
        itens = "".join(
            f'<li><b>{_p(v)}</b> {html.escape(nome)}</li>'
            for nome, v in sorted(fatores, key=lambda x: -abs(x[1])))
        decomp_html = f"""<section class="card" style="margin-top:14px">
          <h2>Por que o custo mudou</h2>
          <p class="sub">Custo = chamadas × tokens/chamada × custo/token. Variação
          total <b>{_p(decomp['total'])}</b> vs o período anterior, decomposta —
          os três fatores se multiplicam de volta:</p>
          <ul class="decomp">{itens}</ul></section>"""

    n_dias_com_dado = len({str(x["dia"]) for x in dados["por_dia"]})
    if dias_periodo <= 1:
        sub_area = "Consumo de hoje, por feature (desde 00:00 UTC)."
    elif n_dias_com_dado <= 1:
        sub_area = ("Um único dia com dado — sem série temporal ainda. A projeção "
                    "extrapola esse dia e vale pouco; ela fica confiável depois de "
                    "alguns dias.")
    elif projetar:
        sub_area = ("Empilhado. A linha tracejada projeta o fim do mês pela média "
                    f"diária dos {n_dias_com_dado} dias com dado.")
    else:
        sub_area = (f"Empilhado, {n_dias_com_dado} dias com dado. Sem projeção: "
                    "numa janela desta largura a média diária esconde tendência "
                    "demais para extrapolar.")

    tabela_feat = _tabela(
        [{"feature": x["k"], "chamadas": x["chamadas"], "custo": f"US$ {_fmt(_d(x['custo']),6)}"}
         for x in dados["por_feature"]],
        [("feature", "Feature"), ("chamadas", "Chamadas"), ("custo", "Custo")])
    tabela_mod = _tabela(
        [{"model": x["k"], "chamadas": x["chamadas"], "tokens": x["tokens"],
          "custo": f"US$ {_fmt(_d(x['custo']),6)}"} for x in dados["por_modelo"]],
        [("model", "Modelo"), ("chamadas", "Chamadas"), ("tokens", "Tokens"), ("custo", "Custo")])

    alerta_html = "".join(
        f'<div class="al {sev}"><span class="ico" aria-hidden="true">'
        f'{"✓" if sev=="good" else "!" if sev in ("warning","serious") else "×"}</span>'
        f'<div><div class="an">{html.escape(nome)}</div>'
        f'<div class="av">{html.escape(val)}</div>'
        f'<div class="ad">{html.escape(det)}</div></div></div>'
        for sev, nome, val, det in alertas)

    servicos_html = ""
    if len(dados["servicos"]) > 1:
        servicos_html = f"""<section class="card"><h2>Por projeto</h2>
          <p class="sub">Cada aplicação que emite eventos para este banco.</p>
          {_svg_barras(dados['servicos'], SERIES_LIGHT, 'custo por projeto')}</section>"""

    # ---- composição dos tokens -------------------------------------------
    composicao = _stack([
        ("Entrada", r["input_tokens"], SERIES_VAR[0]),
        ("Saída", r["output_tokens"], SERIES_VAR[1]),
        ("Cache lido", r["cache_read"], SERIES_VAR[2]),
        ("Cache gravado", r["cache_write"], SERIES_VAR[3]),
    ])
    tabela_tok = _tabela(
        [{"feature": x["k"], "entrada": _int(x["input_tokens"]),
          "saida": _int(x["output_tokens"]), "cache": _int(x["cache_read"]),
          "custo": f"US$ {_fmt(_d(x['custo']), 6)}"} for x in dados["tokens_feature"]],
        [("feature", "Feature"), ("entrada", "Entrada"), ("saida", "Saída"),
         ("cache", "Cache lido"), ("custo", "Custo")])

    # ---- latência ---------------------------------------------------------
    lat_html = _svg_barras(
        dados["latencia"], None, "latência média por feature", chave="media",
        fmt=lambda v: _ms(v),
        tt=lambda x: (f"{x['k']} · média {_ms(x['media'])} · pior caso "
                      f"{_ms(x['maximo'])} · {int(x['n'])} chamada(s)"),
        vazio="Nenhuma chamada com duração registrada no período.")

    # ---- cache ------------------------------------------------------------
    if cache["em_uso"]:
        cache_corpo = _svg_barras(
            [x for x in dados["tokens_feature"] if int(x["cache_read"] or 0) > 0],
            None, "tokens lidos do cache por feature", chave="cache_read",
            fmt=lambda v: _int(v) + " tok",
            tt=lambda x: (f"{x['k']} · {_int(x['cache_read'])} lidos do cache · "
                          f"{_int(x['input_tokens'])} de entrada cheia"))
        eco = (f"Economia estimada de <b>US$ {_fmt(cache['economia'], 4)}</b> contra "
               f"pagar entrada cheia." if cache["economia"] is not None
               else "Sem tabela de preço para estimar a economia.")
        cache_sub = (f"{_int(cache['lido'])} tokens vieram do cache e "
                     f"{_int(cache['entrada'])} de entrada cheia. {eco}")
    else:
        cache_corpo = ('<p class="vazio">Nenhum token de cache no período — '
                       'todas as chamadas pagaram entrada cheia.</p>')
        cache_sub = ("Prompt caching não está em uso. Os campos existem e são "
                     "medidos; o que falta é a chamada declarar o trecho "
                     "cacheável. Um cache do lado da aplicação, se houver, não "
                     "aparece aqui — ele evita a chamada inteira, e o que não "
                     "chega ao provedor não vira evento.")

    # ---- rota -------------------------------------------------------------
    rota_corpo = _svg_barras(
        dados["por_rota"], SERIES_VAR + [OUTROS_VAR], "custo por rota",
        vazio="Nenhum evento com a tag request_path.")
    rota_sub = ("Corte por endpoint HTTP. Não coincide com o corte por feature: "
                "a mesma feature costuma ser acionada por rotas diferentes."
                if dados["por_rota"] else
                "Depende da tag request_path, gravada por um escopo por request "
                "no middleware HTTP da aplicação.")

    # ---- erros ------------------------------------------------------------
    erros_html = ""
    if dados["erros"]:
        tabela_err = _tabela(
            [{"tipo": x["k"], "n": x["n"], "tokens": _int(x["tokens"]),
              "custo": f"US$ {_fmt(_d(x['custo']), 6)}"} for x in dados["erros"]],
            [("tipo", "Tipo de erro"), ("n", "Chamadas"), ("tokens", "Tokens"),
             ("custo", "Custo queimado")])
        erros_html = f"""<section class="card" style="margin-top:14px">
          <h2>Desperdício</h2>
          <p class="sub">Chamadas que falharam já haviam enviado — e pago — a
          entrada. Este custo está incluído no total acima, sem rótulo próprio.</p>
          {tabela_err}</section>"""

    # ---- segunda dimensão livre (--tag-extra) ---------------------------
    # Tabela, não barra: pode haver dezenas de valores e o top-8 de uma barra
    # esconderia a cauda — que é justamente onde mora "esse subtipo custa caro".
    extra_html = ""
    if dados.get("por_extra"):
        linhas_extra = []
        for x in dados["por_extra"]:
            ativ = int(x["atividades"] or 0)
            c = _d(x["custo"])
            linhas_extra.append({
                "k": x["k"],
                "atividades": _int(ativ),
                "chamadas": _int(x["chamadas"]),
                "custo": f"US$ {_fmt(c, 6)}",
                "media": f"US$ {_fmt(c / ativ, 6)}" if ativ else "—",
            })
        tabela_extra = _tabela(linhas_extra, [
            ("k", dados["tag_extra"]), ("atividades", "Atividades"),
            ("chamadas", "Chamadas de IA"), ("custo", "Custo"),
            ("media", "Custo/atividade")])
        extra_html = f"""<section class="card" style="margin-top:14px">
          <h2>Por {html.escape(str(dados['tag_extra']))}</h2>
          <p class="sub">Segunda dimensão livre. "Atividades" conta execuções
          distintas (run_id), não chamadas de IA — um subtipo com imagem dispara
          várias chamadas por atividade. Custo/atividade é o número comparável
          entre subtipos.</p>
          {tabela_extra}</section>"""

    # ---- execuções mais caras -----------------------------------------
    runs_html = ""
    if dados.get("top_runs"):
        tabela_runs = _tabela(
            [{"run": str(x["run_id"])[:8], "feature": x["feature"],
              "chamadas": _int(x["chamadas"]), "tokens": _int(x["tokens"]),
              "custo": f"US$ {_fmt(_d(x['custo']), 6)}"} for x in dados["top_runs"]],
            [("run", "run_id"), ("feature", "Feature"), ("chamadas", "Chamadas"),
             ("tokens", "Tokens"), ("custo", "Custo")])
        runs_html = f"""<section class="card" style="margin-top:14px">
          <h2>Execuções mais caras</h2>
          <p class="sub">Top 10 por custo. Um <code>run_id</code> agrupa as chamadas
          de um mesmo pipeline; muitas chamadas numa execução só costuma ser fan-out
          inesperado (retry, laço). A média por request não mostra isto.</p>
          {tabela_runs}</section>"""

    imagens_html = ""
    if dados["imagens"]:
        tabela_img = _tabela(
            [{"modelo": x["k"], "imagens": _int(x["imagens"]),
              "falhas": _int(x["falhas"]),
              "custo": (f"US$ {_fmt(_d(x['custo']), 4)}"
                        if int(x["precificadas"] or 0) else "sem preço")}
             for x in dados["imagens"]],
            [("modelo", "Modelo"), ("imagens", "Imagens geradas"),
             ("falhas", "Falhas"), ("custo", "Custo")])
        imagens_html = f"""<section class="card" style="margin-top:14px">
          <h2>Imagens geradas</h2>
          <p class="sub">Geração de imagem (ilustração por IA) não tokeniza —
          é cobrada por unidade gerada, não por entrada/saída de texto. Uma
          linha "sem preço" significa que a tarifa por chamada ainda não foi
          confirmada em pricing.yaml, não que a imagem saiu de graça.</p>
          {tabela_img}</section>"""

    return f"""<div class="tiles">{tiles()}</div>
{decomp_html}
<section class="card"><h2>Custo acumulado por feature</h2>
<p class="sub">{html.escape(sub_area)}</p>
{area}<div class="legend">{legenda}</div></section>

<div class="grid2">
<section class="card"><h2>Por feature</h2><p class="sub">Onde o dinheiro vai.</p>
{_svg_barras(dados['por_feature'], SERIES_VAR + [OUTROS_VAR], 'custo por feature', series=spark_feat)}
<details><summary>Ver como tabela</summary>{tabela_feat}</details></section>

<section class="card"><h2>Por modelo</h2>
<p class="sub">Nunca some tokens entre modelos — tokenizadores diferentes.</p>
{_svg_barras(dados['por_modelo'], SERIES_VAR + [OUTROS_VAR], 'custo por modelo')}
<details><summary>Ver como tabela</summary>{tabela_mod}</details></section>
</div>

<div class="grid2">
<section class="card"><h2>Composição dos tokens</h2>
<p class="sub">Saída custa múltiplas vezes a entrada — a proporção decide onde cortar.</p>
{composicao}
<details><summary>Ver como tabela</summary>{tabela_tok}</details></section>

<section class="card"><h2>Latência por feature</h2>
<p class="sub">Média por chamada. Passe o mouse para o pior caso.</p>
{lat_html}</section>
</div>

<div class="grid2">
<section class="card"><h2>Cache de prompt</h2>
<p class="sub">{cache_sub}</p>
{cache_corpo}</section>

<section class="card"><h2>Por rota</h2>
<p class="sub">{html.escape(rota_sub)}</p>
{rota_corpo}</section>
</div>

<section class="card" style="margin-top:14px"><h2>Por {html.escape(dados['tag_tenant'])}</h2>
<p class="sub">Dimensão livre de atribuição.</p>
{_svg_barras(dados['por_tenant'], SERIES_VAR + [OUTROS_VAR], 'custo por tenant', series=spark_tenant)}</section>

{extra_html}
{servicos_html}
{runs_html}
{imagens_html}
{erros_html}

<h2 style="margin-top:24px">Saúde da medição</h2>
<p class="sub">O painel também mede a si mesmo.</p>
<div class="alerts">{alerta_html}</div>"""


def _rotulo_periodo(dias: int) -> str:
    """Nome curto do botão. Meses/anos aproximados: o corte real é sempre em dias."""
    if dias <= 1:
        return "hoje"
    if dias % 365 == 0 and dias >= 365:
        n = dias // 365
        return "1 ano" if n == 1 else f"{n} anos"
    if dias % 30 == 0 and dias >= 60:
        return f"{dias // 30} meses"
    return f"{dias} dias"


def render(paineis: list[dict], *, titulo: str = "Consumo de IA",
           orcamento: float | None = None, inicial: int | None = None,
           atualizar_s: int | None = None) -> str:
    """Documento completo. `paineis` é uma lista de saídas de `coletar()`, uma
    por período; a barra de botões alterna entre elas sem nova consulta.

    `atualizar_s`: se dado, a página se recarrega sozinha nesse intervalo
    (segundos) via <meta http-equiv=refresh>. O período selecionado sobrevive
    ao reload (vai no location.hash). Só faz sentido quando servida por HTTP —
    num arquivo local o reload relê o mesmo HTML."""
    paineis = sorted(paineis, key=lambda d: d["dias"])
    if inicial is None or not any(d["dias"] == inicial for d in paineis):
        inicial = paineis[0]["dias"] if paineis else 30

    # Concatenação em vez de expressão com aspas escapadas dentro da f-string:
    # backslash em expressão de f-string só é aceito a partir do 3.12, e esta lib
    # roda embarcada em projetos que podem estar em runtime mais antigo.
    def _marca(d, atributo):
        return atributo if int(d["dias"]) == inicial else ""

    botoes = "".join(
        '<button type="button" class="pb" data-p="' + str(d["dias"]) + '" '
        + _marca(d, 'aria-current="true"') + '>'
        + html.escape(_rotulo_periodo(int(d["dias"]))) + "</button>"
        for d in paineis)
    corpos = "".join(
        '<div class="periodo" data-p="' + str(d["dias"]) + '"'
        + ("" if int(d["dias"]) == inicial else " hidden") + ">"
        + _miolo(d, orcamento) + "</div>"
        for d in paineis)

    gerado = dt.datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    meta_refresh = (f'<meta http-equiv="refresh" content="{int(atualizar_s)}">'
                    if atualizar_s and int(atualizar_s) > 0 else "")
    nota_refresh = (f" · recarrega sozinho a cada {int(atualizar_s) // 60} min"
                    if atualizar_s and int(atualizar_s) >= 60 else
                    (f" · recarrega sozinho a cada {int(atualizar_s)}s"
                     if atualizar_s and int(atualizar_s) > 0 else ""))
    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{meta_refresh}
<title>{html.escape(titulo)}</title>
<style>
:root{{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--seq:#2a78d6;--good:#0ca30c;--warning:#fab219;
--serious:#ec835a;--critical:#d03b3b;--border:#e1e0d9;
--s0:#2a78d6;--s1:#eb6834;--s2:#1baf7a;--s3:#eda100;--s4:#e87ba4;--sx:#898781}}
@media (prefers-color-scheme:dark){{:root:where(:not([data-theme="light"])){{{_ESCURO}}}}}
html[data-theme="dark"]{{{_ESCURO}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--plane);color:var(--ink);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 56px;position:relative}}
#tema{{position:absolute;top:28px;right:20px;background:var(--surface);color:var(--ink2);
border:1px solid var(--border);border-radius:6px;padding:5px 11px;font-size:12px;
cursor:pointer;font-family:inherit}}
#tema:hover{{color:var(--ink)}}
h1{{font-size:22px;margin:0 0 2px}} h2{{font-size:15px;margin:0 0 2px}}
.sub{{color:var(--ink2);margin:0 0 16px;font-size:13px}}
.meta{{color:var(--muted);font-size:12px;margin-bottom:22px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px}}
.card,.tile{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px}}
.tl{{color:var(--ink2);font-size:12px}} .tv{{font-size:26px;font-weight:600;margin:4px 0 2px;
font-variant-numeric:tabular-nums}} .ts{{color:var(--muted);font-size:12px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}}
@media(max-width:820px){{.grid2{{grid-template-columns:1fr}}}}
.chart{{width:100%;height:auto;margin-top:8px}}
.grid{{stroke:var(--grid);stroke-width:1}} .ax{{fill:var(--muted);font-size:11px}}
.area{{fill-opacity:.9}} .borda{{fill:none;stroke:var(--surface);stroke-width:2}}
.proj{{stroke:var(--muted);stroke-width:2;stroke-dasharray:5 4}}
.pt{{fill:var(--muted);stroke:var(--surface);stroke-width:2}}
/* halo da cor da superfície: o rótulo cruza a linha tracejada e precisa continuar legível */
.rot{{fill:var(--ink2);font-size:11px;paint-order:stroke;stroke:var(--surface);
stroke-width:3px;stroke-linejoin:round}}
.hit{{fill:transparent}} .hit:hover{{fill:var(--ink);fill-opacity:.05}}
.legend{{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:12px;color:var(--ink2)}}
.lg{{display:flex;align-items:center;gap:6px}}
.lg i{{width:10px;height:10px;border-radius:3px;display:inline-block}}
.bars{{margin-top:6px}}
/* Barra empilhada de composição: uma faixa só, segmentos proporcionais. Sem
   rótulo dentro — a fatia estreita não comporta texto e o número sumiria. */
.stack{{display:flex;height:22px;border-radius:6px;overflow:hidden;margin-top:12px;
background:var(--grid)}}
.sseg{{display:block;height:100%}} .sseg:hover{{filter:brightness(1.12)}}
.legend b{{font-variant-numeric:tabular-nums;font-weight:600;color:var(--ink)}}
.brow{{display:grid;grid-template-columns:150px 1fr 96px;gap:10px;align-items:center;padding:5px 0}}
/* linha com sparkline: coluna extra de 64px antes do valor */
.brow.spk{{grid-template-columns:150px 1fr 64px 96px}}
@media(max-width:820px){{.brow.spk{{grid-template-columns:150px 1fr 96px}} .bspk{{display:none}}}}
.blabel{{color:var(--ink2);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.btrack{{background:var(--grid);border-radius:4px;height:14px;overflow:hidden}}
.bfill{{display:block;height:100%;border-radius:0 4px 4px 0}}
.bspk{{display:flex;align-items:center}}
.spark{{display:block}} .spark polyline{{fill:none;stroke:var(--axis);stroke-width:1.25;
stroke-linejoin:round;stroke-linecap:round}}
.bval{{text-align:right;font-size:12px;font-variant-numeric:tabular-nums;color:var(--ink)}}
.alerts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-top:14px}}
.al{{display:flex;gap:10px;background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:14px 16px}}
.ico{{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;color:#fff;
font-size:12px;font-weight:700;flex:none}}
.al.good .ico{{background:var(--good)}} .al.warning .ico{{background:var(--warning);color:#0b0b0b}}
.al.serious .ico{{background:var(--serious)}} .al.critical .ico{{background:var(--critical)}}
.an{{font-size:12px;color:var(--ink2)}} .av{{font-weight:600}} .ad{{font-size:12px;color:var(--muted)}}
details{{margin-top:12px}} summary{{cursor:pointer;color:var(--ink2);font-size:13px}}
table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:12px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}}
th{{color:var(--ink2);font-weight:600}} td{{font-variant-numeric:tabular-nums}}
.vazio{{color:var(--muted);padding:20px 0}}
.decomp{{margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--ink2)}}
.decomp li{{margin:3px 0}} .decomp b{{color:var(--ink);font-variant-numeric:tabular-nums}}
#tt{{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);
padding:6px 9px;border-radius:6px;font-size:12px;opacity:0;transition:opacity .1s;z-index:9}}
/* Seletor de período: botões, não <select>. São poucos e mutuamente exclusivos,
   e o estado atual precisa ficar visível sem abrir nada. */
.periodos{{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 20px}}
.pb{{background:var(--surface);color:var(--ink2);border:1px solid var(--border);
border-radius:7px;padding:6px 13px;font:inherit;font-size:13px;cursor:pointer}}
.pb:hover{{color:var(--ink);border-color:var(--axis)}}
/* Selecionado marcado por peso + borda, nunca só por cor. */
.pb[aria-current="true"]{{background:var(--ink);color:var(--plane);
border-color:var(--ink);font-weight:600}}
.pb:focus-visible{{outline:2px solid var(--seq);outline-offset:2px}}
</style></head><body><div class="wrap">
<button id="tema" type="button" aria-label="Alternar tema claro/escuro">tema</button>
<h1>{html.escape(titulo)}</h1>
<p class="sub">Consumo de IA no período selecionado.</p>
<p class="meta">Gerado em {gerado} · fonte: tabela de eventos do tokenmeter · valores em USD{nota_refresh}</p>

<div class="periodos" role="group" aria-label="Período">{botoes}</div>

{corpos}
</div><div id="tt"></div>
<script>
document.getElementById('tema').addEventListener('click',()=>{{
const r=document.documentElement;
const escuro=getComputedStyle(r).getPropertyValue('--surface').trim()==='#1a1a19';
r.dataset.theme=escuro?'light':'dark';}});
const tt=document.getElementById('tt');
document.addEventListener('mouseover',e=>{{const el=e.target.closest('[data-t]');
if(!el)return;tt.textContent=el.getAttribute('data-t');tt.style.opacity=1;}});
document.addEventListener('mousemove',e=>{{if(tt.style.opacity==='1'){{
tt.style.left=Math.min(e.clientX+14,innerWidth-tt.offsetWidth-8)+'px';
tt.style.top=(e.clientY+16)+'px';}}}});
document.addEventListener('mouseout',e=>{{if(e.target.closest('[data-t]'))tt.style.opacity=0;}});
// Troca de período: todos os corpos já estão no documento; o clique só decide
// qual fica visível. `hidden` em vez de display:none para o conteudo escondido
// sair também da arvore de acessibilidade.
document.querySelectorAll('.pb').forEach(b=>b.addEventListener('click',()=>{{
const p=b.dataset.p;
document.querySelectorAll('.pb').forEach(o=>
  o.getAttribute('data-p')===p?o.setAttribute('aria-current','true')
                              :o.removeAttribute('aria-current'));
document.querySelectorAll('.periodo').forEach(s=>s.hidden=s.dataset.p!==p);
location.hash='d'+p;}}));
// Recupera o período do hash: recarregar a página (ou compartilhar o link do
// arquivo) mantém a janela escolhida em vez de voltar ao padrão.
const h=(location.hash.match(/^#d(\\d+)$/)||[])[1];
if(h)document.querySelector('.pb[data-p="'+h+'"]')?.click();
</script></body></html>"""


# Janelas oferecidas por padrão. "hoje" (1) é o único dia-calendário — desde
# 00:00 UTC; as demais (semana, mês, trimestre, semestre, ano) são deslizantes
# a partir de agora, como o resto do painel.
PERIODOS_PADRAO = (1, 7, 30, 90, 180, 365)


def gerar(store, caminho: str, **kw) -> str:
    """Gera o arquivo. `periodos` define as janelas disponíveis no seletor e
    `dias` qual delas abre selecionada."""
    titulo = kw.pop("titulo", "Consumo de IA")
    orcamento = kw.pop("orcamento", None)
    periodos = kw.pop("periodos", None) or list(PERIODOS_PADRAO)
    inicial = kw.pop("dias", 30)
    # atualizar_s só tem efeito servido por HTTP (o endpoint da aplicação passa
    # isto direto para render()); num arquivo local o reload relê o mesmo HTML.
    atualizar_s = kw.pop("atualizar_s", None)
    # O período inicial sempre existe no seletor, mesmo se vier de fora da lista
    # (`--days 45`): sem isto o botão marcado não teria corpo correspondente.
    janelas = sorted(set(periodos) | {inicial})
    paineis = [coletar(store, dias=d, **kw) for d in janelas]
    from pathlib import Path
    Path(caminho).write_text(
        render(paineis, titulo=titulo, orcamento=orcamento, inicial=inicial,
               atualizar_s=atualizar_s),
        encoding="utf-8")
    return caminho
