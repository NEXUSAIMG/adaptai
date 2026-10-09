"""
Contador de consumo de tokens do Claude.

MOTIVACAO: nenhuma chamada a Claude (PEI, jornada terapeutica, planejamento
diario/anual, analise qualitativa, prova de reforco) registrava quantos
tokens cada requisicao consumia - `response.usage` era descartado. Este
modulo centraliza a leitura desse `usage` (contagem EXATA devolvida pela
propria API, nao estimativa) e:
  - loga em JSON estruturado (visivel em producao via app.core.logging_config)
  - persiste uma linha em `ai_usage_log` para consultas agregadas por
    feature/aluno/usuario/periodo (ver app/models/ai_usage_log.py)

Uso (depois de qualquer client.messages.create(...) das features de IA):

    from app.core.ai_usage import registrar_uso_ia

    message = client.messages.create(model=MODELO, max_tokens=..., messages=[...])
    registrar_uso_ia(
        feature="pei_geracao",
        model=MODELO,
        usage=message.usage,
        student_id=student_id,      # opcional
        user_id=current_user.id,    # opcional
        escola_id=current_user.escola_id,  # opcional
    )
"""
from typing import Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)


# Preco em USD por 1 milhao de tokens. Cobre os modelos atualmente resolvidos
# por get_default_model()/get_fast_model() (app/core/anthropic_client.py) e
# alguns predecessores recentes, para o calculo de custo nao quebrar logo
# apos uma troca de modelo. Atualizar aqui quando o pricing da Anthropic mudar
# ou um novo modelo entrar em uso (settings.LLM_MODEL).
PRECOS_USD_POR_MTOK = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00},
    "claude-opus-4-6": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
}

# Leitura de cache custa ~0.1x o preco de input; escrita ~1.25x (TTL de 5min).
# Aproximacao unica (nao diferencia TTL de 1h) - suficiente para visibilidade
# de custo, nao para faturamento fino.
FATOR_CACHE_READ = 0.1
FATOR_CACHE_WRITE = 1.25


def calcular_custo_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> Optional[float]:
    """
    Estima o custo em USD de uma chamada com base em PRECOS_USD_POR_MTOK.

    Retorna None se o modelo nao estiver na tabela - evita mostrar um custo
    errado silenciosamente quando um modelo novo ainda nao foi precificado
    aqui (melhor omitir do que informar valor incorreto).
    """
    precos = PRECOS_USD_POR_MTOK.get(model)
    if precos is None:
        return None

    custo = (
        (input_tokens / 1_000_000) * precos["input"]
        + (output_tokens / 1_000_000) * precos["output"]
        + (cache_creation_input_tokens / 1_000_000) * precos["input"] * FATOR_CACHE_WRITE
        + (cache_read_input_tokens / 1_000_000) * precos["input"] * FATOR_CACHE_READ
    )
    return round(custo, 6)


def registrar_uso_ia(
    feature: str,
    model: str,
    usage,
    student_id: Optional[int] = None,
    user_id: Optional[int] = None,
    escola_id: Optional[int] = None,
) -> None:
    """
    Registra o consumo de tokens de UMA chamada a Claude: loga e persiste.

    `usage` e o objeto `message.usage` devolvido pela API (tem input_tokens,
    output_tokens e, quando prompt caching esta em uso,
    cache_creation_input_tokens/cache_read_input_tokens).

    Abre sua propria sessao de banco (nao reusa a sessao da rota/service que
    chamou) para que um rollback posterior no fluxo da feature (ex.: erro ao
    parsear a resposta da IA) nunca apague o registro de uso - os tokens ja
    foram consumidos e cobrados independente do que acontece depois.

    Nunca lanca excecao: falha ao registrar uso nao pode derrubar a resposta
    da feature para o usuario.
    """
    try:
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        custo_usd = calcular_custo_usd(
            model, input_tokens, output_tokens, cache_creation, cache_read
        )

        logger.info(
            "claude_usage",
            extra={
                "feature": feature,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "total_tokens": input_tokens + output_tokens,
                "cost_usd": custo_usd,
                "student_id": student_id,
                "user_id": user_id,
                "escola_id": escola_id,
            },
        )

        _persistir(
            feature=feature,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            cost_usd=custo_usd,
            student_id=student_id,
            user_id=user_id,
            escola_id=escola_id,
        )
    except Exception:
        logger.exception("Falha ao registrar uso de tokens da IA", extra={"feature": feature})


def _persistir(**campos) -> None:
    """Grava uma linha em ai_usage_log em sessao propria, curta e isolada."""
    # Imports locais: evita ciclo de import no carregamento do modulo
    # (app.database/app.models importam bastante coisa do app).
    from app.database import SessionLocal
    from app.models.ai_usage_log import AIUsageLog

    db = SessionLocal()
    try:
        db.add(AIUsageLog(**campos))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
