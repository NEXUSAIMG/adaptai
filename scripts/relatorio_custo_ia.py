"""Relatório de custo de IA por feature — custo de cada artefato gerado.

Fonte: tabela `tm_usage_event` (tokenmeter). Agrega por `run_id` (um artefato =
uma execução, mesmo que sejam N chamadas à IA). **Não descarta nada** — mostra:

  - mediana por artefato   -> custo típico (robusto a outlier, sem cutoff mágico)
  - média por artefato      -> custo médio real, com os casos ruins
  - p95 por artefato        -> "quão caro fica num dia ruim"
  - total no período        -> orçamento
  - nº execuções > 3× a mediana -> as anômalas, pra investigar (bug de fan-out?)
  - tudo isso também reprecificado ao pricing.yaml de HOJE
  - Biblioteca vs Materiais Adaptados (separados pela rota)
  - Ilustração por IA à parte (preço por imagem, não por token)

Uso:
    DATABASE_URL=mysql+pymysql://... python scripts/relatorio_custo_ia.py
    python scripts/relatorio_custo_ia.py --dsn "mysql+pymysql://..." \
        --desde 2026-08-01 --out relatorio.md   # grava .md e .csv

Sem --dsn usa DATABASE_URL (o mesmo do backend).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tokenmeter.pricing import PriceBook  # noqa: E402


def _dsn(args) -> str:
    d = args.dsn or os.environ.get("DATABASE_URL")
    if not d:
        sys.exit("erro: informe --dsn ou defina DATABASE_URL")
    # normaliza o esquema que o Railway às vezes entrega como "mysql://"
    if d.startswith("mysql://"):
        d = "mysql+pymysql://" + d[len("mysql://"):]
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn")
    ap.add_argument("--prefix", default="tm_", help="prefixo das tabelas (padrão tm_)")
    ap.add_argument("--desde", default=None, help="data ISO; padrão: todo o histórico")
    ap.add_argument("--environment", default="production")
    ap.add_argument("--out", default=None, help="grava o relatório principal em CSV")
    args = ap.parse_args()

    ev = f"{args.prefix}usage_event"
    tag = f"{args.prefix}usage_event_tag"
    eng = create_engine(_dsn(args), pool_pre_ping=True)
    pb = PriceBook()
    agora = dt.datetime.utcnow()

    filtros = ["e.environment = :env"]
    params: dict = {"env": args.environment}
    if args.desde:
        filtros.append("e.occurred_at >= :desde")
        params["desde"] = args.desde
    W = " AND ".join(filtros)

    with eng.connect() as con:
        def q(sql, **extra):
            return [dict(r._mapping) for r in con.execute(text(sql), {**params, **extra})]

        # ---- 0. janela + qualidade da atribuição ----
        janela = q(f"""SELECT MIN(occurred_at) AS desde, MAX(occurred_at) AS ate,
                       COUNT(*) AS eventos, COUNT(DISTINCT run_id) AS execucoes
                       FROM {ev} e WHERE {W}""")[0]
        cobertura = q(f"""SELECT feature_source AS k, COUNT(*) AS n,
                          COALESCE(SUM(cost_usd),0) AS custo
                          FROM {ev} e WHERE {W} GROUP BY feature_source""")

        # ---- linhas por (feature, run_id, model): base do relatório principal ----
        # request_path só é usado para separar biblioteca de material adaptado.
        linhas = q(f"""SELECT e.feature AS feature, e.run_id AS run_id, e.model AS model,
                       COALESCE(SUM(e.input_tokens),0)  AS in_tok,
                       COALESCE(SUM(e.output_tokens),0) AS out_tok,
                       COALESCE(SUM(e.cache_write_tokens),0) AS cw_tok,
                       COALESCE(SUM(e.cache_read_tokens),0)  AS cr_tok,
                       COALESCE(SUM(e.total_tokens),0)  AS tokens,
                       COALESCE(SUM(e.cost_usd),0)      AS custo_congelado,
                       COUNT(*) AS chamadas,
                       MAX(CASE WHEN rp.tag_value LIKE '/api/v1/materiais-adaptados%%'
                                THEN 1 ELSE 0 END) AS eh_adaptado
                       FROM {ev} e
                       LEFT JOIN {tag} rp
                         ON rp.event_id = e.event_id AND rp.tag_key = 'request_path'
                       WHERE {W} AND e.status = 'ok' AND e.cost_usd IS NOT NULL
                         AND e.run_id IS NOT NULL
                         AND e.operation <> 'image_generation'
                       GROUP BY e.feature, e.run_id, e.model""")

        # ---- imagens (preço por unidade, não por token) ----
        imagens = q(f"""SELECT e.model AS model, COUNT(*) AS imagens,
                        COALESCE(AVG(e.cost_usd),0) AS custo_medio,
                        COALESCE(SUM(e.cost_usd),0) AS custo_total,
                        SUM(CASE WHEN e.priced=0 THEN 1 ELSE 0 END) AS sem_preco
                        FROM {ev} e WHERE {W} AND e.operation = 'image_generation'
                        GROUP BY e.model""")

    # ---- agrega por run, reprecificando ao pricing de hoje ----
    runs: dict[tuple, dict] = {}
    for r in linhas:
        # nome efetivo: separa biblioteca de material adaptado
        feat = r["feature"]
        if feat == "material_adaptado":
            feat = "material_adaptado" if r["eh_adaptado"] else "material_biblioteca"
        key = (feat, r["run_id"])
        agg = runs.setdefault(key, {
            "feature": feat, "tokens": 0, "in_tok": 0, "out_tok": 0,
            "chamadas": 0, "custo_congelado": Decimal(0), "custo_hoje": Decimal(0),
        })
        agg["tokens"] += int(r["tokens"])
        agg["in_tok"] += int(r["in_tok"])
        agg["out_tok"] += int(r["out_tok"])
        agg["chamadas"] += int(r["chamadas"])
        agg["custo_congelado"] += Decimal(str(r["custo_congelado"]))
        c_hoje, _ = pb.cost("anthropic", r["model"], agora,
                            input_tokens=int(r["in_tok"]), output_tokens=int(r["out_tok"]),
                            cache_write_tokens=int(r["cw_tok"]),
                            cache_read_tokens=int(r["cr_tok"]))
        agg["custo_hoje"] += (c_hoje if c_hoje is not None
                              else Decimal(str(r["custo_congelado"])))  # fallback

    por_feature: dict[str, list] = defaultdict(list)
    for agg in runs.values():
        por_feature[agg["feature"]].append(agg)

    def _p(vals: list[float], q: float) -> float:
        """Percentil q (0..1) por interpolação linear. Robusto a n pequeno."""
        if not vals:
            return 0.0
        s = sorted(vals)
        if len(s) == 1:
            return s[0]
        pos = q * (len(s) - 1)
        lo = int(pos)
        frac = pos - lo
        hi = min(lo + 1, len(s) - 1)
        return s[lo] * (1 - frac) + s[hi] * frac

    relatorio = []
    for feat, lst in por_feature.items():
        n = len(lst)
        c_cong = [float(a["custo_congelado"]) for a in lst]
        c_hoje = [float(a["custo_hoje"]) for a in lst]
        toks = [a["tokens"] for a in lst]
        cham = [a["chamadas"] for a in lst]
        med_cong = _p(c_cong, 0.5)
        # "outliers a investigar": execuções acima de 3x a mediana (mesma régua
        # do alerta de pico do painel). NÃO são descartadas de nenhuma conta.
        acima_3x = sum(1 for v in c_cong if med_cong > 0 and v > 3 * med_cong)
        soma_tok = sum(toks) or 1
        soma_in = sum(a["in_tok"] for a in lst) or 1
        relatorio.append({
            "feature": feat,
            "execucoes": n,
            "mediana_cong": med_cong,
            "mediana_hoje": _p(c_hoje, 0.5),
            "media_cong": sum(c_cong) / n,
            "media_hoje": sum(c_hoje) / n,
            "p95_cong": _p(c_cong, 0.95),
            "total_cong": sum(c_cong),
            "total_hoje": sum(c_hoje),
            "tokens_mediana": round(_p(toks, 0.5)),
            "chamadas_mediana": round(_p(cham, 0.5), 1),
            "usd_por_mtok": sum(c_cong) / soma_tok * 1_000_000,
            "ratio_saida_entrada": round(sum(a["out_tok"] for a in lst) / soma_in, 2),
            "acima_3x_mediana": acima_3x,
        })
    relatorio.sort(key=lambda x: x["total_cong"], reverse=True)

    # ================= saída =================
    out_lines: list[str] = []
    def w(s: str = "") -> None:
        out_lines.append(s)

    w(f"# Relatório de custo de IA — AdaptAI ({args.environment})")
    w()
    w(f"Janela: **{janela['desde']} → {janela['ate']}**  ·  "
      f"{janela['eventos']:,} chamadas  ·  {janela['execucoes']:,} execuções")
    w()
    tot = sum(x["n"] for x in cobertura) or 1
    bem = sum(x["n"] for x in cobertura if x["k"] in ("explicit", "context"))
    detalhe = ", ".join(f"{x['k']}={x['n']}" for x in cobertura)
    w(f"Atribuição confiável: **{100*bem/tot:.0f}%** ({detalhe})")
    w()
    w("Todos os custos por **artefato** (agregados por `run_id`). Nada é descartado: "
      "a mediana é o custo típico, a coluna `>3×` conta as execuções anômalas.")
    w()
    w("## Custo por feature — preço CONGELADO no evento")
    w()
    w("| feature | exec | mediana | média | p95 | total | tokens (med) | cham/artef | US$/Mtok | saída÷ent | >3× med |")
    w("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for x in relatorio:
        w(f"| {x['feature']} | {x['execucoes']:,} "
          f"| {x['mediana_cong']:.6f} | {x['media_cong']:.6f} | {x['p95_cong']:.6f} "
          f"| {x['total_cong']:.2f} | {x['tokens_mediana']:,} | {x['chamadas_mediana']} "
          f"| {x['usd_por_mtok']:.3f} | {x['ratio_saida_entrada']} | {x['acima_3x_mediana']} |")
    w()
    w("## Custo por feature — reprecificado ao pricing.yaml de HOJE")
    w()
    w("| feature | mediana (hoje) | média (hoje) | total (hoje) | Δ total vs congelado |")
    w("|---|--:|--:|--:|--:|")
    for x in relatorio:
        delta = (x["total_hoje"] / x["total_cong"] - 1) * 100 if x["total_cong"] else 0
        w(f"| {x['feature']} | {x['mediana_hoje']:.6f} | {x['media_hoje']:.6f} "
          f"| {x['total_hoje']:.2f} | {delta:+.0f}% |")
    w()
    tc = sum(x["total_cong"] for x in relatorio)
    th = sum(x["total_hoje"] for x in relatorio)
    w(f"**Total do período (texto):** US$ {tc:.2f} congelado · US$ {th:.2f} a preço de hoje")
    w()

    if imagens:
        ti = sum(float(x["custo_total"]) for x in imagens)
        w("## Ilustração por IA (preço por imagem, não por token)")
        w()
        w("| modelo | imagens | custo médio/imagem | custo total | sem preço |")
        w("|---|--:|--:|--:|--:|")
        for x in imagens:
            w(f"| {x['model']} | {x['imagens']:,} | {float(x['custo_medio']):.4f} "
              f"| {float(x['custo_total']):.2f} | {x['sem_preco']} |")
        w()
        w(f"**Total ilustração:** US$ {ti:.2f}")
        w()

    texto = "\n".join(out_lines)
    print(texto)

    if args.out:
        base = args.out.rsplit(".", 1)[0]
        Path(base + ".md").write_text(texto, encoding="utf-8")
        with open(base + ".csv", "w", newline="", encoding="utf-8") as fh:
            cw = csv.writer(fh)
            cw.writerow(["feature", "execucoes", "mediana_usd", "media_usd", "p95_usd",
                         "total_usd", "mediana_hoje_usd", "total_hoje_usd", "tokens_mediana",
                         "chamadas_mediana", "usd_por_mtok", "ratio_saida_entrada",
                         "execucoes_acima_3x_mediana"])
            for x in relatorio:
                cw.writerow([x["feature"], x["execucoes"], f"{x['mediana_cong']:.8f}",
                             f"{x['media_cong']:.8f}", f"{x['p95_cong']:.8f}",
                             f"{x['total_cong']:.6f}", f"{x['mediana_hoje']:.8f}",
                             f"{x['total_hoje']:.6f}", x["tokens_mediana"],
                             x["chamadas_mediana"], f"{x['usd_por_mtok']:.6f}",
                             x["ratio_saida_entrada"], x["acima_3x_mediana"]])
        print(f"\nArquivos: {base}.md  ·  {base}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
