"""
Endpoints administrativos para monitoramento do sistema.

Acesso restrito a ADMIN ou SUPER_ADMIN.
"""
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.api.dependencies import require_admin
from app.core.config import settings
from app.core.security import verify_password
from app.models.user import User, UserRole
from app.models.background_task import BackgroundTask, BackgroundTaskStatus
from app.models.ai_usage_log import AIUsageLog
from app.services.ai_cache_service import cache_stats, cleanup_old_cache
from app.services.background_tasks import task_manager


router = APIRouter(prefix="/admin", tags=["Admin - Monitoramento"])


@router.get("/ai-cache/stats")
def obter_stats_cache_ia(current_user: User = Depends(require_admin)):
    """
    Retorna estatisticas do cache de IA.
    
    Util para acompanhar economia de creditos Anthropic:
    - total_entries: quantas respostas unicas foram cacheadas
    - total_hits: quantas vezes o cache foi reutilizado (= chamadas economizadas)
    - top_types: quais tipos de material mais se beneficiam
    """
    return cache_stats()


@router.post("/ai-cache/cleanup")
def limpar_cache_ia_antigo(
    ttl_hours: int = 672,
    current_user: User = Depends(require_admin),
):
    """
    Remove entradas de cache nao usadas ha mais de ttl_hours.
    Default: 4 semanas (672h).
    """
    removidos = cleanup_old_cache(ttl_hours=ttl_hours)
    return {
        "removidos": removidos,
        "ttl_hours": ttl_hours,
    }


@router.get("/background-tasks/stats")
def obter_stats_background_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Retorna estatisticas de tarefas em background.
    Util para ver se ha tasks travadas, taxa de falhas, etc.
    """
    por_status = (
        db.query(BackgroundTask.status, func.count(BackgroundTask.id))
        .group_by(BackgroundTask.status)
        .all()
    )
    
    por_tipo = (
        db.query(BackgroundTask.task_type, func.count(BackgroundTask.id))
        .group_by(BackgroundTask.task_type)
        .limit(20)
        .all()
    )
    
    # Ultimas 10 tasks falhas (debugging)
    falhas_recentes = (
        db.query(BackgroundTask)
        .filter(BackgroundTask.status == BackgroundTaskStatus.FAILED)
        .order_by(BackgroundTask.created_at.desc())
        .limit(10)
        .all()
    )
    
    return {
        "por_status": [
            {"status": s.value if s else "null", "count": c}
            for s, c in por_status
        ],
        "por_tipo": [
            {"tipo": t or "null", "count": c}
            for t, c in por_tipo
        ],
        "falhas_recentes": [
            {
                "task_id": t.task_id,
                "task_type": t.task_type,
                "error": (t.error or "")[:200],
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in falhas_recentes
        ],
    }


@router.post("/background-tasks/cleanup")
def limpar_background_tasks_antigas(current_user: User = Depends(require_admin)):
    """Remove tasks mais antigas que o TTL configurado (default 7 dias)."""
    removidos = task_manager.cleanup_old_tasks()
    return {"removidos": removidos or 0}


@router.get("/ai-usage/stats")
def obter_stats_uso_ia(
    dias: int = 30,
    feature: str = None,
    user_id: int = None,
    student_id: int = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Retorna consumo de tokens/custo da IA (Claude) agregado por feature e por
    modelo, na janela dos ultimos `dias` (default 30).

    Filtros opcionais (combinaveis): `feature` (ex.: "jornada_terapeutica"),
    `user_id`, `student_id`. Uteis para responder perguntas pontuais, ex.:
    "quanto a jornada terapeutica custou para o usuario 42 nos ultimos 90 dias"
    -> ?feature=jornada_terapeutica&user_id=42&dias=90

    Nem toda feature grava user_id/student_id (depende do que cada chamada a
    registrar_uso_ia informou) - ver docs/API_AI_USAGE_STATS.md.

    Fonte: tabela ai_usage_log, gravada por app.core.ai_usage.registrar_uso_ia
    apos cada chamada real a Claude nas features de IA (PEI, jornada
    terapeutica, planejamento, analise qualitativa, prova de reforco).
    """
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    base = db.query(AIUsageLog).filter(AIUsageLog.created_at >= desde)

    if feature is not None:
        base = base.filter(AIUsageLog.feature == feature)
    if user_id is not None:
        base = base.filter(AIUsageLog.user_id == user_id)
    if student_id is not None:
        base = base.filter(AIUsageLog.student_id == student_id)

    def agregados(coluna):
        return (
            base.with_entities(
                coluna,
                func.count(AIUsageLog.id),
                func.sum(AIUsageLog.input_tokens),
                func.sum(AIUsageLog.output_tokens),
                func.sum(AIUsageLog.cost_usd),
            )
            .group_by(coluna)
            .order_by(func.sum(AIUsageLog.cost_usd).desc())
            .all()
        )

    def linha(chave, chamadas, input_tokens, output_tokens, custo):
        return {
            "chamadas": int(chamadas or 0),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "custo_usd": float(custo) if custo is not None else None,
            **chave,
        }

    total_chamadas, total_input, total_output, total_custo = base.with_entities(
        func.count(AIUsageLog.id),
        func.sum(AIUsageLog.input_tokens),
        func.sum(AIUsageLog.output_tokens),
        func.sum(AIUsageLog.cost_usd),
    ).first()

    recentes = base.order_by(AIUsageLog.id.desc()).limit(20).all()

    return {
        "periodo_dias": dias,
        "filtros": {"feature": feature, "user_id": user_id, "student_id": student_id},
        "total": {
            "chamadas": int(total_chamadas or 0),
            "input_tokens": int(total_input or 0),
            "output_tokens": int(total_output or 0),
            "custo_usd": float(total_custo) if total_custo is not None else None,
        },
        "por_feature": [
            linha({"feature": feature}, chamadas, in_tok, out_tok, custo)
            for feature, chamadas, in_tok, out_tok, custo in agregados(AIUsageLog.feature)
        ],
        "por_modelo": [
            linha({"model": model}, chamadas, in_tok, out_tok, custo)
            for model, chamadas, in_tok, out_tok, custo in agregados(AIUsageLog.model)
        ],
        "chamadas_recentes": [
            {
                "id": r.id,
                "feature": r.feature,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "custo_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                "student_id": r.student_id,
                "user_id": r.user_id,
                "escola_id": r.escola_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recentes
        ],
    }


# ============================================================================
# Painel HTML de consumo de IA
# ============================================================================
# Por que Basic Auth aqui, e nao o require_admin do resto do arquivo:
# o projeto autentica por OAuth2 Bearer (dependencies.py:9), com o JWT indo no
# header Authorization. Navegador nenhum envia esse header ao abrir uma URL
# digitada, entao uma rota protegida por require_admin devolveria 401 para o
# unico modo de uso que esta rota tem - ser aberta no navegador. Basic Auth e o
# esquema que o navegador sabe negociar sozinho, e aqui ele valida contra o
# MESMO usuario admin (email + hash bcrypt), sem criar credencial nova.
#
# A alternativa comum - token na query string - poe o JWT no historico do
# navegador e nos logs de acesso do Railway. Nao compensa.

_basic = HTTPBasic(realm="Painel de consumo de IA", auto_error=False)

# Hash descartavel usado quando o e-mail nao existe: sem ele, a resposta para
# usuario inexistente volta muito mais rapido que a de senha errada, e essa
# diferenca de tempo enumera contas de admin.
#
# Precisa ser um hash bcrypt VALIDO. Com um valor malformado, o checkpw lanca,
# verify_password captura e devolve False de imediato - o atalho reintroduz
# justamente a diferenca de tempo que a constante existe para eliminar.
# E o hash de uma string aleatoria descartada; nenhuma senha o satisfaz, e
# ainda que satisfizesse, o `user is None` abaixo barra antes.
_HASH_DUMMY = "$2b$12$/w/WSwTK71pAmv8XzCMzCue759mevaDv9HXUTGNNWtWTMkymAqqIu"

# Cache em memoria do HTML gerado. Montar o painel dispara ~55 agregacoes (uma
# por bloco, vezes os periodos do seletor); sem cache, cada F5 refaz tudo.
# Por processo: com varios workers cada um tem o seu, o que so significa que o
# primeiro acesso de cada worker paga a geracao.
_PAINEL_TTL_S = 300
_painel_cache: dict[tuple, tuple[float, str]] = {}
_painel_lock = threading.Lock()


def _admin_por_basic(credentials: HTTPBasicCredentials | None, db: Session) -> User:
    """Valida Basic Auth contra um User ADMIN/SUPER_ADMIN ativo."""
    naoautorizado = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais invalidas",
        # Sem este header o navegador nao abre o dialogo de usuario/senha - ele
        # so mostraria o corpo do erro.
        headers={"WWW-Authenticate": 'Basic realm="Painel de consumo de IA"'},
    )
    if credentials is None:
        raise naoautorizado

    user = db.query(User).filter(User.email == credentials.username).first()
    # verify_password roda sempre, inclusive sem usuario, para o tempo de
    # resposta nao denunciar quais e-mails existem.
    senha_ok = verify_password(
        credentials.password, user.hashed_password if user else _HASH_DUMMY)
    if user is None or not senha_ok:
        raise naoautorizado
    if not user.is_active:
        raise naoautorizado
    if user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        # 403 e nao 401: a credencial esta certa, o papel e que nao basta.
        # Devolver 401 aqui faria o navegador pedir a senha de novo em loop.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions. Admin role required.",
        )
    return user


@router.get("/ai-usage/painel", response_class=HTMLResponse)
def obter_painel_uso_ia(
    dias: int = 30,
    orcamento: float = None,
    tag_tenant: str = "tenant_id",
    tag_extra: str = "material_tipo",
    unidade: str = "",
    refresh: bool = False,
    atualizar: int = 300,
    token: str = "",
    credentials: HTTPBasicCredentials = Depends(_basic),
    db: Session = Depends(get_db),
):
    """
    Painel HTML de consumo de IA (mesmo gerado pelo `tokenmeter panel`).

    Duas formas de acesso:
    - no navegador: usuario e senha de um admin via Basic Auth;
    - `?token=<PAINEL_TOKEN>`: para abrir de fora do app sem sessao (usado
      pelo proxy /consumo-ia do frontend, no Railway). So funciona se PAINEL_TOKEN estiver
      configurado no backend.

    - `dias`: janela que abre selecionada no seletor de periodo (default 30)
    - `orcamento`: teto em USD, so para exibir o percentual consumido
    - `tag_tenant`: qual tag corta a dimensao livre (default "tenant_id")
    - `tag_extra`: 2a dimensao, tabelada por atividade/custo (default
      "material_tipo"; vazio desativa a secao)
    - `unidade`: chave de tag para o tile "custo por <unidade>" (ex.:
      "aluno_id", "material_id"; vazio omite o tile)
    - `refresh=1`: ignora o cache de 5 minutos e regenera na hora
    - `atualizar`: intervalo (s) do auto-reload da pagina; default 300,
      `atualizar=0` desliga. O reload pega dados no maximo 5 min atrasados
      (cache do backend); combine com `refresh=1` para sempre recalcular.
    """
    # isascii(): compare_digest levanta TypeError com str nao-ASCII (o token
    # gerado por token_urlsafe e sempre ASCII) - um ?token=cafe viraria 500.
    token_ok = bool(settings.PAINEL_TOKEN) and token.isascii() and \
        secrets.compare_digest(token, settings.PAINEL_TOKEN)
    if not token_ok:
        _admin_por_basic(credentials, db)

    chave = (dias, orcamento, tag_tenant, tag_extra, unidade, atualizar)
    agora = time.monotonic()

    if not refresh:
        with _painel_lock:
            item = _painel_cache.get(chave)
        if item and (agora - item[0]) < _PAINEL_TTL_S:
            # Header no proprio HTMLResponse, nao num `Response` injetado: ao
            # devolver uma Response propria o FastAPI usa ela como resultado
            # final, e os headers postos no objeto injetado se perdem.
            # Idade em segundos ajuda a distinguir "o painel esta errado" de
            # "o painel esta servindo o que gerou ha 4 minutos".
            return HTMLResponse(item[1], headers={
                "X-Painel-Cache": f"hit; age={int(agora - item[0])}s"})

    try:
        import tokenmeter as tm
        from tokenmeter.panel import PERIODOS_PADRAO, coletar, render
    except Exception:
        raise HTTPException(status_code=503, detail="tokenmeter indisponivel")

    try:
        store = tm._require()
    except Exception:
        # O configure() do startup falha em silencio por desenho (main.py) - sem
        # tokenmeter a aplicacao roda, so nao mede. Aqui a falha precisa aparecer.
        raise HTTPException(
            status_code=503,
            detail="tokenmeter nao configurado - confira o log de startup",
        )

    janelas = sorted(set(PERIODOS_PADRAO) | {dias})
    paineis = [coletar(store, dias=d, tag_tenant=tag_tenant,
                       tag_extra=tag_extra or None, unidade=unidade or None)
               for d in janelas]
    html = render(paineis, titulo="AdaptAI - consumo de IA",
                  orcamento=orcamento, inicial=dias,
                  atualizar_s=atualizar if atualizar > 0 else None)

    with _painel_lock:
        _painel_cache[chave] = (agora, html)
    return HTMLResponse(html, headers={"X-Painel-Cache": "miss"})
