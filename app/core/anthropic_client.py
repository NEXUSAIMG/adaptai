"""
Cliente Anthropic centralizado (singleton lazy).

MOTIVACAO: antes deste modulo, o projeto instanciava Anthropic() em 8+ lugares:
- services/ai_materiais_service.py
- services/redacao_ai_service.py
- services/diario_ai_service.py
- services/planejamento_bncc_completo_service.py
- services/relatorio_processor.py (novo processar_relatorio_com_progresso)
- api/routes/pei.py
- api/routes/relatorios.py
- e outros

Cada instanciacao:
- Faz um novo handshake TLS com api.anthropic.com
- Dificulta trocar modelo globalmente
- Impede rate limiting central
- Nao permite rotacao de API key em runtime

Este modulo resolve centralizando em uma unica instancia lazy.

Uso:

    from app.core.anthropic_client import get_anthropic_client, get_default_model
    
    client = get_anthropic_client()
    response = client.messages.create(
        model=get_default_model(),
        max_tokens=2048,
        messages=[{"role": "user", "content": "Ola"}]
    )

Para tarefas rapidas/baratas (classificacao, extracao simples), use o modelo rapido:

    from app.core.anthropic_client import get_fast_model
    client.messages.create(model=get_fast_model(), ...)

Para cache automatico (ECONOMIA DE CREDITOS), use:

    from app.services.ai_cache_service import cached_completion
    text = cached_completion(prompt="...", cache_type="mapa_mental")
"""
from typing import Optional
from threading import Lock
from app.core.config import settings


# Instancia singleton - inicializada sob demanda
_client = None
_client_lock = Lock()


def _instrumentar(client):
    """Envolve o client com o tokenmeter, se o pacote estiver disponivel.

    A partir daqui, TODA chamada feita com este client gera um registro de consumo -
    sem uma linha extra por feature. Este e o unico ponto de instrumentacao do
    projeto; por isso `tokenmeter check` bloqueia a construcao de Anthropic() em
    qualquer outro modulo.

    O import e guardado de proposito: se o pacote nao estiver instalado (ambiente de
    teste, container antigo, rollback), a aplicacao continua funcionando SEM tracking,
    em vez de quebrar no startup. Perder telemetria e aceitavel; derrubar a API nao e.
    """
    try:
        import tokenmeter
        return tokenmeter.wrap(client)
    except Exception:  # pragma: no cover - pacote ausente ou incompativel
        return client


def get_anthropic_client(*, timeout=None, max_retries=None):
    """
    Retorna a instancia unica do cliente Anthropic (ja instrumentada).
    Inicializacao lazy + thread-safe (primeira chamada), segura em multi-thread.

    Args:
        timeout: sobrescreve o timeout so para este uso (nao afeta o singleton).
        max_retries: idem para retries de rede.
        Quando qualquer um dos dois e informado, devolve um client derivado via
        `.with_options()` - que continua instrumentado.

    Raises:
        RuntimeError: se ANTHROPIC_API_KEY nao estiver configurada.
    """
    global _client
    if _client is None:
        with _client_lock:
            # Double-check apos obter lock
            if _client is None:
                if not settings.ANTHROPIC_API_KEY or not settings.ANTHROPIC_API_KEY.strip():
                    raise RuntimeError(
                        "ANTHROPIC_API_KEY nao configurada. "
                        "Defina no .env ou nas variaveis de ambiente do Railway."
                    )
                # Import lazy - evita erro na inicializacao se anthropic nao estiver instalado
                from anthropic import Anthropic
                _client = _instrumentar(Anthropic(api_key=settings.ANTHROPIC_API_KEY))

    if timeout is None and max_retries is None:
        return _client

    opcoes = {}
    if timeout is not None:
        opcoes["timeout"] = timeout
    if max_retries is not None:
        opcoes["max_retries"] = max_retries

    try:
        # `.with_options()` devolve um client derivado; o tokenmeter re-embrulha o
        # resultado, entao o derivado continua sendo medido.
        return _client.with_options(**opcoes)
    except (AttributeError, TypeError):
        # SDK antigo ou sem suporte a with_options: cai para uma instancia propria
        # com as mesmas opcoes. Custa um handshake TLS a mais, mas nao derruba a
        # feature nem perde o tracking - as duas coisas que nao podem acontecer.
        from anthropic import Anthropic
        return _instrumentar(Anthropic(api_key=settings.ANTHROPIC_API_KEY, **opcoes))


def reset_anthropic_client():
    """
    Reseta o singleton. Util para testes ou rotacao de API key.
    """
    global _client
    with _client_lock:
        _client = None


def sistema_cacheado(texto, ttl: str = "5m"):
    """Formata um system prompt ESTATICO para habilitar prompt caching.

    Recebe o texto fixo das instrucoes (rubrica, formato de saida, papel do
    modelo - o que NAO muda entre chamadas) e devolve o bloco no formato que a
    API entende como cacheavel:

        [{"type": "text", "text": <texto>, "cache_control": {"type": "ephemeral"}}]

    Passe o resultado em `system=` e deixe SO o conteudo variavel (a redacao do
    aluno, o material, etc.) em `messages`. Assim o prefixo estatico e lido do
    cache (~10% do custo do token de entrada) nas chamadas seguintes dentro da
    janela do cache (5 min por padrao).

    Regras de seguranca:
    - Se PROMPT_CACHE_ENABLED for False, devolve o texto puro (sem cache_control).
    - Se o texto for vazio, devolve como esta.
    - Cacheia so o system: cachear o prompt inteiro (que muda a cada chamada) nao
      traz ganho e ainda cobra o write - por isso o variavel NUNCA entra aqui.

    OBS: ha um minimo de tokens para o cache valer (~1024 no Sonnet 4.x). Abaixo
    disso a API simplesmente ignora o cache_control (sem erro) - conferir em
    response.usage.cache_read_input_tokens / cache_creation_input_tokens.
    """
    if not texto or not str(texto).strip():
        return texto
    if not getattr(settings, "PROMPT_CACHE_ENABLED", True):
        return str(texto)
    bloco = {"type": "text", "text": str(texto)}
    cache_control = {"type": "ephemeral"}
    if ttl == "1h":
        cache_control["ttl"] = "1h"
    bloco["cache_control"] = cache_control
    return [bloco]


def get_default_model() -> str:
    """
    Retorna o modelo Claude padrao para tarefas complexas.
    Controlado por settings.CLAUDE_MODEL.
    
    Fallback: claude-sonnet-4-6 (modelo balanceado atual; geracao 3.x foi aposentada).
    """
    return settings.CLAUDE_MODEL or "claude-sonnet-4-6"


def get_fast_model() -> str:
    """
    Retorna um modelo Claude rapido/barato para tarefas simples
    (classificacao, extracao de campos, resumos curtos).
    
    Nao e sobrescrito por config - sempre usa Haiku (4.5) por ser o mais rapido.
    """
    return "claude-haiku-4-5-20251001"
