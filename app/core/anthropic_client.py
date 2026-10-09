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
import base64
from functools import cached_property
from typing import Optional
from threading import Lock
from app.core.config import settings


# O LLM e o DeepSeek, falando o protocolo Messages da Anthropic
# (settings.DEEPSEEK_BASE_URL). Tres diferencas tratadas AQUI, num ponto so,
# para os ~45 call sites que leem `response.content[0].text` seguirem intactos:
# 1. DeepSeek responde com um bloco `thinking` antes do texto -> desligado por
#    padrao (quem quiser pensar passa `thinking=` explicito).
# 2. So o LLM_FAST_MODEL (flash) enxerga imagem; o pro ignora em silencio e
#    alucina -> chamada com imagem/documento vai sempre para o flash.
# 3. Nenhum dos dois aceita bloco `document` (PDF) -> cada pagina vira PNG.
_MAX_PAGINAS_PDF = 20  # ponytail: corta PDFs longos; subir se laudos > 20 pags aparecerem


def _pdf_para_imagens(bloco):
    import fitz  # PyMuPDF, ja dependencia (relatorio_extrator_service)
    doc = fitz.open(stream=base64.b64decode(bloco["source"]["data"]), filetype="pdf")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(p.get_pixmap(dpi=110).tobytes("png")).decode(),
            },
        }
        for p in doc.pages(0, min(len(doc), _MAX_PAGINAS_PDF))
    ]


def _adaptar_midia(messages):
    """Devolve (messages com PDFs convertidos em imagens, tem_midia)."""
    tem_midia = False
    saida = []
    for msg in messages:
        conteudo = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(conteudo, list):
            saida.append(msg)
            continue
        novo = []
        for bloco in conteudo:
            tipo = bloco.get("type") if isinstance(bloco, dict) else None
            if tipo in ("image", "document"):
                tem_midia = True
            src = (bloco.get("source") or {}) if tipo == "document" else {}
            if src.get("type") == "base64" and src.get("media_type") == "application/pdf":
                novo.extend(_pdf_para_imagens(bloco))
            else:
                novo.append(bloco)
        saida.append({**msg, "content": novo})
    return saida, tem_midia


def _messages_deepseek():
    from anthropic.resources.messages import Messages

    class _MessagesDeepSeek(Messages):
        def create(self, **kwargs):
            kwargs.setdefault("thinking", {"type": "disabled"})
            msgs, tem_midia = _adaptar_midia(kwargs.get("messages") or [])
            if tem_midia:
                kwargs["messages"] = msgs
                kwargs["model"] = get_fast_model()
            return super().create(**kwargs)

    return _MessagesDeepSeek


def _nova_instancia(**opcoes):
    from anthropic import Anthropic

    class _DeepSeek(Anthropic):
        # with_options()/copy() usam self.__class__, entao derivados herdam isso.
        @cached_property
        def messages(self):
            return _messages_deepseek()(self)

    return _DeepSeek(
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        **opcoes,
    )


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
        RuntimeError: se DEEPSEEK_API_KEY nao estiver configurada.
    """
    global _client
    if _client is None:
        with _client_lock:
            # Double-check apos obter lock
            if _client is None:
                if not settings.DEEPSEEK_API_KEY or not settings.DEEPSEEK_API_KEY.strip():
                    raise RuntimeError(
                        "DEEPSEEK_API_KEY nao configurada. "
                        "Defina no .env ou nas variaveis de ambiente do Railway."
                    )
                # Import lazy - evita erro na inicializacao se anthropic nao estiver instalado
                _client = _instrumentar(_nova_instancia())

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
        return _instrumentar(_nova_instancia(**opcoes))


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
    Retorna o modelo padrao para tarefas complexas (settings.LLM_MODEL).
    So texto: chamadas com imagem sao desviadas para o fast no client.
    """
    return settings.LLM_MODEL or "deepseek-v4-pro"


def get_fast_model() -> str:
    """
    Retorna o modelo rapido/barato (settings.LLM_FAST_MODEL) para tarefas
    simples (classificacao, extracao de campos, resumos curtos). Tambem e o
    unico com visao.
    """
    return settings.LLM_FAST_MODEL or "deepseek-flash"
