import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pathlib import Path
from app.core.config import settings
from app.core.logging_config import setup_logging, get_logger
from app.database import engine, Base

# Fonte unica da verdade para deteccao de producao. Ver comentario em
# config.py::is_production_environment para motivacao.
from app.core.config import is_production_environment

# Configurar logging antes de qualquer outro import que possa logar
setup_logging(level="INFO")
logger = get_logger(__name__)

# Importar TODOS os modelos para o SQLAlchemy criar as tabelas
from app.models import *  # Isso importa todos os modelos
from app.models.relatorio import Relatorio  # Garantir que Relatorio seja importado
from app.models.redacao import TemaRedacao, RedacaoAluno  # REDAÇÕES ENEM

# Importar rotas
from app.api.routes import auth, students, questions, applications, analytics
from app.api.routes import provas  # NOVA ROTA DE PROVAS
from app.api.routes import student_provas  # ROTAS ESTUDANTES
from app.api.routes import professor_analytics  # ANALYTICS DE PROVAS PARA PROFESSORES
from app.api.routes import materiais, student_materiais  # MATERIAIS DE ESTUDO COM IA
from app.api.routes import analise_qualitativa  # ANÁLISE QUALITATIVA COM IA
from app.api.routes import prova_adaptativa  # PROVA ADAPTATIVA (REFORÇO)
from app.api.routes import pei  # PEI COM IA
from app.api.routes import relatorios  # RELATÓRIOS DE TERAPIAS
from app.api.routes import relatorios_analise  # ANÁLISE CONSOLIDADA DE RELATÓRIOS
from app.api.routes import materiais_adaptados  # MATERIAIS ADAPTADOS COM IA
from app.api.routes import student_materiais_adaptados  # PONTE: MATERIAIS ADAPTADOS NO PORTAL DO ALUNO (TC-027/028)
from app.api.routes import planos  # PLANOS E ASSINATURAS MULTI-TENANT
from app.api.routes import escolas  # ESCOLAS (TENANTS)
from app.api.routes import planejamento_bncc  # PLANEJAMENTO BNCC E PEI
from app.api.routes import calendario_atividades  # CALENDÁRIO DE ATIVIDADES PEI
from app.api.routes import student_pei  # PEI PARA PORTAL DO ALUNO
from app.api.routes import diario_aprendizagem  # DIÁRIO DE APRENDIZAGEM COM IA
from app.api.routes import agenda  # AGENDA DO PROFESSOR
from app.api.routes import registro_diario  # REGISTRO DIÁRIO DE AULAS
from app.api.routes import conteudos_aluno  # CONTEÚDOS DO ALUNO (INTEGRAÇÃO)
from app.api.routes import redacoes  # REDAÇÕES ENEM COM IA
from app.api.routes import checkout  # CHECKOUT / ONBOARDING DE NOVAS ESCOLAS
# NOTA: relatorios_v2 e websocket REMOVIDOS (subsistema WebSocket morto - o frontend
# usa polling via GET /relatorios/{id}). Arquivos arquivados em
# backend/_archive/websocket_subsystem/. Ver historico git para restaurar.
from app.api.routes import admin_monitoring  # ADMIN - MONITORAMENTO (cache IA, background tasks)
from app.api.routes import seduc  # PAINEL SEDUC - VISAO DE REDE (agregado de escolas)
from app.api.routes import comunicacao  # COMUNICACAO COM A FAMILIA (IA)
from app.api.routes import plano_aula  # PLANO DE AULA SIMPLES (IA - mobile)
from app.api.routes import redacao_feedback  # REDACAO FEEDBACK FORMATIVO (IA - mobile)

# Criar tabelas APENAS em dev. Em producao, o schema e versionado via Alembic
# e NAO deve ser alterado pelo app no startup - rodar create_all em prod mascara
# schema drift, pode recriar indices silenciosamente e e uma pegadinha classica
# em incidentes ("por que a tabela X voltou?"). Ver docs/migrations.md.
if not is_production_environment():
    Base.metadata.create_all(bind=engine)
    logger.info("Base.metadata.create_all executado (dev)")
else:
    logger.info("Pulando create_all em producao - use Alembic para migrations")

# ============================================
# LIFESPAN (startup + shutdown) - substitui @app.on_event deprecado
# ============================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ========== STARTUP ==========
    IS_PRODUCTION_STARTUP = is_production_environment()
    logger.info(
        "AdaptAI backend starting",
        extra={
            "version": settings.VERSION,
            "ai_model": settings.CLAUDE_MODEL,
            "production": IS_PRODUCTION_STARTUP,
        },
    )
    
    # Cleanup de jobs travados no startup
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        from datetime import datetime, timezone, timedelta
        
        db = SessionLocal()
        try:
            # Buscar e marcar jobs travados (usa timezone-aware datetime)
            timeout = datetime.now(timezone.utc) - timedelta(minutes=5)
            result = db.execute(text("""
                UPDATE planejamento_jobs 
                SET status = 'failed',
                    ultimo_erro = 'Job travado - cleanup no startup',
                    completed_at = NOW()
                WHERE status = 'processing'
                AND (
                    last_heartbeat < :timeout
                    OR last_heartbeat IS NULL
                    OR updated_at < :timeout
                )
            """), {"timeout": timeout})
            db.commit()
            
            if result.rowcount > 0:
                logger.warning(
                    "Jobs travados marcados como FAILED no startup",
                    extra={"count": result.rowcount},
                )
            else:
                logger.info("Nenhum job travado encontrado")
        finally:
            db.close()
    except Exception as e:
        logger.warning("Erro no cleanup de jobs travados", exc_info=True)
    
    # Cleanup de background_tasks antigos (E2 - mantem so ultimos 7 dias)
    try:
        from app.services.background_tasks import task_manager
        task_manager.cleanup_old_tasks()
    except Exception as e:
        logger.warning("Erro no cleanup de background_tasks", exc_info=True)
    
    yield
    
    # ========== SHUTDOWN ==========
    logger.info("AdaptAI backend shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="""
    🎓 **AdaptAI API - Sistema de Educacao Inclusiva com IA**
    
    Sistema inteligente para geracao automatica de provas e questoes adaptadas.
    """,
    contact={
        "name": "AdaptAI Team",
        "email": "contact@adaptai.com",
    },
    license_info={
        "name": "MIT",
    },
    lifespan=lifespan,
)

# ============================================
# CORS - Configuracao robusta para producao
# ============================================

# Origens permitidas em desenvolvimento
ALLOWED_ORIGINS_DEV = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://localhost:5177",
    "http://localhost:5178",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]

# Origens permitidas em producao.
# SEGURANCA: lista FECHADA. Adicione aqui os dominios reais de frontend.
# Tambem aceita FRONTEND_URL via env var para flexibilidade em multi-deploy.
ALLOWED_ORIGINS_PROD = [
    "https://adaptai-frontend.vercel.app",
    # Adicione outros dominios de producao aqui se houver (dominio proprio, staging, etc)
]

# Permitir adicionar origem extra via env var (ex: dominio proprio)
_extra_origin = os.getenv("FRONTEND_URL")
if _extra_origin:
    ALLOWED_ORIGINS_PROD.append(_extra_origin.strip())

IS_PRODUCTION = is_production_environment()

if IS_PRODUCTION:
    origins = ALLOWED_ORIGINS_PROD
    print(f"[CORS] Modo producao - origens permitidas: {origins}")
else:
    origins = ALLOWED_ORIGINS_DEV + ALLOWED_ORIGINS_PROD
    print(f"[CORS] Modo desenvolvimento - origens permitidas: {origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,  # consistente em dev e prod (nao trocar para False)
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
    max_age=86400,
)

# Preflight OPTIONS eh tratado automaticamente pelo CORSMiddleware.
# NAO adicionar handler manual aqui - ele sobrescreve a configuracao segura do CORS.


# ============================================
# Middleware de request tracking (observabilidade)
# ============================================
# Gera request_id unico por request e loga duracao/status.
# Util para debugar "o que aconteceu naquele request?" rastreando logs pelo X-Request-ID.

import uuid
import time
from starlette.middleware.base import BaseHTTPMiddleware


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Aceita X-Request-ID do cliente (permite rastreio ponta-a-ponta) ou gera um
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        
        start = time.perf_counter()
        
        # Evita logar rotas de health/static (poluicao)
        path = request.url.path
        is_noisy = path in ("/health", "/", "/favicon.ico") or path.startswith("/storage/")
        
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            response.headers["X-Request-ID"] = request_id
            
            # Log apenas requests interessantes (nao health checks)
            if not is_noisy:
                log_level = logger.warning if response.status_code >= 500 else (
                    logger.info if response.status_code >= 400 else logger.debug
                )
                log_level(
                    "request processed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "path": path,
                        "status": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.error(
                "request failed with exception",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "duration_ms": duration_ms,
                },
                exc_info=True,
            )
            raise


app.add_middleware(RequestTrackingMiddleware)


# ============================================
# Security headers middleware
# ============================================
# Aplica headers conservadores em todas as respostas: X-Content-Type-Options,
# Referrer-Policy, Permissions-Policy, X-Frame-Options (seletivo), e HSTS em
# producao. Ver app/core/security_headers.py para detalhes.

from app.core.security_headers import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware, is_production=IS_PRODUCTION)


# ============================================
# Storage para arquivos estáticos
# ============================================

storage_materiais_path = Path(__file__).parent.parent / "storage" / "materiais"
storage_materiais_path.mkdir(parents=True, exist_ok=True)
app.mount("/storage/materiais", StaticFiles(directory=str(storage_materiais_path)), name="materiais_storage")

# ATENÇÃO: /storage/relatorios NÃO é mais montado como estático.
# Laudos médicos sao dados sensiveis de saude (LGPD art. 11).
# Acesso agora via endpoint autenticado: GET /api/v1/relatorios/{id}/arquivo
# (ver app/api/routes/relatorios.py::baixar_arquivo_relatorio)
storage_relatorios_path = Path(__file__).parent.parent / "storage" / "relatorios"
storage_relatorios_path.mkdir(parents=True, exist_ok=True)
# NAO montar StaticFiles aqui - usar endpoint autenticado.

storage_registros_path = Path(__file__).parent.parent / "storage" / "registros_diarios"
storage_registros_path.mkdir(parents=True, exist_ok=True)

# ============================================
# Incluir rotas
# ============================================

app.include_router(auth.router, prefix="/api/v1", tags=["🔐 Authentication"])
app.include_router(students.router, prefix="/api/v1", tags=["👥 Students"])
app.include_router(questions.router, prefix="/api/v1", tags=["📝 Questions"])
app.include_router(applications.router, prefix="/api/v1", tags=["📋 Applications"])
app.include_router(analytics.router, prefix="/api/v1", tags=["📊 Analytics"])
app.include_router(provas.router, prefix="/api/v1", tags=["🎓 Provas"])
app.include_router(student_provas.router, prefix="/api/v1", tags=["🎓 Provas Estudantes"])
app.include_router(professor_analytics.router, prefix="/api/v1", tags=["📊 Analytics Provas"])
app.include_router(materiais.router, prefix="/api/v1", tags=["📚 Materiais"])
app.include_router(student_materiais.router, prefix="/api/v1", tags=["📚 Student Materiais"])
app.include_router(analise_qualitativa.router, prefix="/api/v1", tags=["🤖 Análise Qualitativa IA"])
app.include_router(prova_adaptativa.router, prefix="/api/v1", tags=["🎯 Prova Adaptativa"])
app.include_router(pei.router, prefix="/api/v1", tags=["❤️ PEI com IA"])
app.include_router(relatorios.router, prefix="/api/v1", tags=["📋 Relatórios"])
app.include_router(relatorios_analise.router, prefix="/api/v1", tags=["🎨 Jornada Terapêutica"])
app.include_router(materiais_adaptados.router, prefix="/api/v1", tags=["🎨 Materiais Adaptados"])
app.include_router(student_materiais_adaptados.router, prefix="/api/v1", tags=["🎨 Student Materiais Adaptados"])
app.include_router(planos.router, prefix="/api/v1", tags=["💳 Planos"])
app.include_router(escolas.router, prefix="/api/v1", tags=["🏫 Escolas"])
app.include_router(planejamento_bncc.router, prefix="/api/v1", tags=["📚 Planejamento BNCC"])
app.include_router(calendario_atividades.router, prefix="/api/v1", tags=["📅 Calendário"])
app.include_router(student_pei.router, prefix="/api/v1/student", tags=["🎯 PEI Estudante"])
app.include_router(diario_aprendizagem.router, prefix="/api/v1", tags=["📔 Diário"])
app.include_router(agenda.router, prefix="/api/v1", tags=["📅 Agenda"])
app.include_router(registro_diario.router, prefix="/api/v1", tags=["📚 Registro Diário"])
app.include_router(conteudos_aluno.router, prefix="/api/v1", tags=["📚 Conteúdos Aluno"])
app.include_router(redacoes.router, prefix="/api/v1", tags=["✍️ Redações ENEM"])
app.include_router(checkout.router, prefix="/api/v1", tags=["🛒 Checkout"])
app.include_router(admin_monitoring.router, prefix="/api/v1", tags=["⚙️ Admin Monitoring"])
app.include_router(seduc.router, prefix="/api/v1", tags=["🗺️ Painel SEDUC"])
app.include_router(comunicacao.router, prefix="/api/v1", tags=["💬 Comunicação"])
app.include_router(plano_aula.router, prefix="/api/v1", tags=["📖 Plano de aula"])
app.include_router(redacao_feedback.router, prefix="/api/v1", tags=["✍️ Redação (feedback)"])

# ============================================
# Rotas principais
# ============================================

@app.get("/", tags=["Root"])
def root():
    return {
        "message": "🎓 AdaptAI API - Sistema de Educação Inclusiva com IA",
        "version": settings.VERSION,
        "status": "running",
        "docs": "/docs"
    }

@app.get("/health", tags=["Health"])
def health_check():
    # Expor backend do rate limiter em dev; em prod, so se DEBUG=true.
    try:
        from app.core.rate_limit import get_active_backend_name
        rl_backend = get_active_backend_name()
    except Exception:
        rl_backend = "unknown"
    payload = {"status": "healthy", "service": "AdaptAI Backend", "version": settings.VERSION}
    if not IS_PRODUCTION or settings.DEBUG:
        payload["rate_limit_backend"] = rl_backend
    return payload

@app.get("/info", tags=["Info"])
def info():
    return {"name": settings.APP_NAME, "version": settings.VERSION, "production": bool(IS_PRODUCTION)}

# NOTA: Eventos on_event (startup/shutdown) foram migrados para 'lifespan' no topo do arquivo.
# Ver https://fastapi.tiangolo.com/advanced/events/ para documentacao.

