# ============================================
# ROTAS DE PLANOS E ASSINATURAS - Multi-tenant
# ============================================
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel, Field, EmailStr

from app.database import get_db
from app.api.dependencies import get_current_user
from app.models.user import User, UserRole
from app.models.plano import Plano
from app.models.escola import Escola, ConfiguracaoEscola
from app.models.assinatura import Assinatura, Fatura, StatusAssinatura, StatusFatura
from app.core.tenant import get_tenant_context, TenantContext
from app.core.config import settings
from app.core.security import (
    get_password_hash, create_password_reset_token, password_reset_fingerprint,
)

router = APIRouter(prefix="/planos", tags=["💳 Planos e Assinaturas"])


# ============================================
# SCHEMAS
# ============================================

class PlanoResponse(BaseModel):
    id: int
    nome: str
    slug: str
    descricao: Optional[str] = None
    valor: float
    valor_anual: Optional[float] = None
    limite_alunos: int
    limite_professores: int
    limite_provas_mes: int
    limite_materiais_mes: int
    limite_peis_mes: int
    limite_relatorios_mes: int
    pei_automatico: bool = True
    materiais_adaptativos: bool = True
    mapas_mentais: bool = True
    relatorios_avancados: bool = False
    api_access: bool = False
    suporte_prioritario: bool = False
    treinamento_incluido: bool = False
    integracao_whatsapp: bool = False
    integracao_google: bool = False
    exportacao_pdf: bool = True
    exportacao_excel: bool = True
    destaque: bool = False
    
    class Config:
        from_attributes = True


class AssinaturaResponse(BaseModel):
    id: int
    escola_id: int
    plano_id: int
    status: str
    data_inicio: datetime
    data_fim: Optional[datetime] = None
    data_proxima_cobranca: Optional[datetime] = None
    valor_mensal: float
    dia_vencimento: int
    alunos_ativos: int
    professores_ativos: int
    provas_mes_atual: int
    materiais_mes_atual: int
    peis_mes_atual: int
    
    class Config:
        from_attributes = True


class EscolaCreate(BaseModel):
    nome: str = Field(..., min_length=3)
    email: str
    cnpj: Optional[str] = None
    telefone: Optional[str] = None
    tipo: str = "ESCOLA"
    plano_slug: str = "gratuito"


class AssinaturaUpdate(BaseModel):
    plano_id: Optional[int] = None
    status: Optional[str] = None


class UsoAtualResponse(BaseModel):
    alunos_ativos: int
    limite_alunos: int
    professores_ativos: int
    limite_professores: int
    provas_mes: int
    limite_provas: int
    materiais_mes: int
    limite_materiais: int
    peis_mes: int
    limite_peis: int
    percentual_uso_alunos: float
    percentual_uso_provas: float


# ============================================
# ROTA DE SEED - Criar planos iniciais
# ============================================

@router.post("/seed")
def seed_planos(db: Session = Depends(get_db)):
    """
    🌱 Cria os planos iniciais no banco de dados.
    Pode ser chamado múltiplas vezes (ignora duplicados).
    """
    # Verificar se já existem planos
    count = db.query(Plano).count()
    if count > 0:
        return {
            "message": f"Planos já existem ({count} planos)",
            "action": "skipped"
        }
    
    # Criar tabela se não existir (SQLAlchemy já faz isso, mas garantir)
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS planos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nome VARCHAR(100) NOT NULL UNIQUE,
                slug VARCHAR(100) NOT NULL UNIQUE,
                descricao TEXT,
                valor DECIMAL(10,2) NOT NULL DEFAULT 0,
                valor_anual DECIMAL(10,2),
                limite_alunos INT DEFAULT 50,
                limite_professores INT DEFAULT 5,
                limite_provas_mes INT DEFAULT 100,
                limite_materiais_mes INT DEFAULT 100,
                limite_peis_mes INT DEFAULT 50,
                limite_relatorios_mes INT DEFAULT 50,
                pei_automatico BOOLEAN DEFAULT TRUE,
                materiais_adaptativos BOOLEAN DEFAULT TRUE,
                mapas_mentais BOOLEAN DEFAULT TRUE,
                relatorios_avancados BOOLEAN DEFAULT FALSE,
                api_access BOOLEAN DEFAULT FALSE,
                suporte_prioritario BOOLEAN DEFAULT FALSE,
                treinamento_incluido BOOLEAN DEFAULT FALSE,
                integracao_whatsapp BOOLEAN DEFAULT FALSE,
                integracao_google BOOLEAN DEFAULT FALSE,
                exportacao_pdf BOOLEAN DEFAULT TRUE,
                exportacao_excel BOOLEAN DEFAULT TRUE,
                ativo BOOLEAN DEFAULT TRUE,
                destaque BOOLEAN DEFAULT FALSE,
                ordem INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """))
        db.commit()
    except:
        pass  # Tabela já existe
    
    # Dados dos planos
    planos_data = [
        {
            "nome": "Gratuito",
            "slug": "gratuito",
            "descricao": "Plano gratuito para experimentar a plataforma",
            "valor": 0,
            "valor_anual": 0,
            "limite_alunos": 5,
            "limite_professores": 1,
            "limite_provas_mes": 10,
            "limite_materiais_mes": 10,
            "limite_peis_mes": 5,
            "limite_relatorios_mes": 5,
            "relatorios_avancados": False,
            "api_access": False,
            "suporte_prioritario": False,
            "treinamento_incluido": False,
            "integracao_whatsapp": False,
            "integracao_google": False,
            "destaque": False,
            "ordem": 0
        },
        {
            "nome": "Essencial",
            "slug": "essencial",
            "descricao": "Para professores e pequenas escolas",
            "valor": 297.00,
            "valor_anual": 2851.20,
            "limite_alunos": 30,
            "limite_professores": 3,
            "limite_provas_mes": 50,
            "limite_materiais_mes": 50,
            "limite_peis_mes": 30,
            "limite_relatorios_mes": 30,
            "relatorios_avancados": False,
            "api_access": False,
            "suporte_prioritario": False,
            "treinamento_incluido": False,
            "integracao_whatsapp": False,
            "integracao_google": False,
            "destaque": False,
            "ordem": 1
        },
        {
            "nome": "Profissional",
            "slug": "profissional",
            "descricao": "Para escolas e clinicas. MAIS POPULAR!",
            "valor": 699.00,
            "valor_anual": 6710.40,
            "limite_alunos": 100,
            "limite_professores": 10,
            "limite_provas_mes": 200,
            "limite_materiais_mes": 200,
            "limite_peis_mes": 100,
            "limite_relatorios_mes": 100,
            "relatorios_avancados": True,
            "api_access": False,
            "suporte_prioritario": True,
            "treinamento_incluido": True,
            "integracao_whatsapp": True,
            "integracao_google": True,
            "destaque": True,
            "ordem": 2
        },
        {
            "nome": "Institucional",
            "slug": "institucional",
            "descricao": "Para grandes instituicoes",
            "valor": 1497.00,
            "valor_anual": 14371.20,
            "limite_alunos": 500,
            "limite_professores": 50,
            "limite_provas_mes": 1000,
            "limite_materiais_mes": 1000,
            "limite_peis_mes": 500,
            "limite_relatorios_mes": 500,
            "relatorios_avancados": True,
            "api_access": True,
            "suporte_prioritario": True,
            "treinamento_incluido": True,
            "integracao_whatsapp": True,
            "integracao_google": True,
            "destaque": False,
            "ordem": 3
        },
        {
            "nome": "Enterprise",
            "slug": "enterprise",
            "descricao": "Personalizado para redes de ensino",
            "valor": 2997.00,
            "valor_anual": 28771.20,
            "limite_alunos": 9999,
            "limite_professores": 999,
            "limite_provas_mes": 9999,
            "limite_materiais_mes": 9999,
            "limite_peis_mes": 9999,
            "limite_relatorios_mes": 9999,
            "relatorios_avancados": True,
            "api_access": True,
            "suporte_prioritario": True,
            "treinamento_incluido": True,
            "integracao_whatsapp": True,
            "integracao_google": True,
            "destaque": False,
            "ordem": 4
        }
    ]
    
    # Criar planos
    criados = []
    for p in planos_data:
        plano = Plano(
            nome=p["nome"],
            slug=p["slug"],
            descricao=p["descricao"],
            valor=p["valor"],
            valor_anual=p["valor_anual"],
            limite_alunos=p["limite_alunos"],
            limite_professores=p["limite_professores"],
            limite_provas_mes=p["limite_provas_mes"],
            limite_materiais_mes=p["limite_materiais_mes"],
            limite_peis_mes=p["limite_peis_mes"],
            limite_relatorios_mes=p["limite_relatorios_mes"],
            relatorios_avancados=p["relatorios_avancados"],
            api_access=p["api_access"],
            suporte_prioritario=p["suporte_prioritario"],
            treinamento_incluido=p["treinamento_incluido"],
            integracao_whatsapp=p["integracao_whatsapp"],
            integracao_google=p["integracao_google"],
            destaque=p["destaque"],
            ordem=p["ordem"],
            ativo=True
        )
        db.add(plano)
        criados.append({"nome": p["nome"], "valor": p["valor"]})
    
    db.commit()
    
    return {
        "message": "Planos criados com sucesso!",
        "planos": criados,
        "total": len(criados)
    }


# ============================================
# ROTAS PÚBLICAS (sem autenticação)
# ============================================

@router.get("/publicos", response_model=List[PlanoResponse])
def listar_planos_publicos(db: Session = Depends(get_db)):
    """
    📋 Lista todos os planos disponíveis (público).
    Usado na página de preços do site.
    """
    planos = db.query(Plano).filter(
        Plano.ativo == True
    ).order_by(Plano.ordem).all()
    
    return planos


@router.get("/publicos/{slug}", response_model=PlanoResponse)
def obter_plano_por_slug(slug: str, db: Session = Depends(get_db)):
    """
    📦 Obtém detalhes de um plano pelo slug.
    """
    plano = db.query(Plano).filter(
        Plano.slug == slug,
        Plano.ativo == True
    ).first()
    
    if not plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plano não encontrado"
        )
    
    return plano


# ============================================
# ROTAS AUTENTICADAS
# ============================================

@router.get("/meu-plano", response_model=PlanoResponse)
def meu_plano(
    tenant: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    """
    📦 Retorna o plano atual da escola do usuário.
    """
    if not tenant.assinatura or not tenant.assinatura.plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma assinatura encontrada"
        )
    
    return tenant.assinatura.plano


@router.get("/minha-assinatura", response_model=AssinaturaResponse)
def minha_assinatura(
    tenant: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    """
    📋 Retorna a assinatura atual da escola.
    """
    if not tenant.assinatura:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma assinatura encontrada"
        )
    
    return tenant.assinatura


@router.get("/uso-atual", response_model=UsoAtualResponse)
def uso_atual(
    tenant: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db)
):
    """
    📊 Retorna o uso atual vs limites do plano.
    """
    if not tenant.assinatura or not tenant.assinatura.plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma assinatura encontrada"
        )
    
    assinatura = tenant.assinatura
    plano = assinatura.plano
    
    return {
        "alunos_ativos": assinatura.alunos_ativos,
        "limite_alunos": plano.limite_alunos,
        "professores_ativos": assinatura.professores_ativos,
        "limite_professores": plano.limite_professores,
        "provas_mes": assinatura.provas_mes_atual,
        "limite_provas": plano.limite_provas_mes,
        "materiais_mes": assinatura.materiais_mes_atual,
        "limite_materiais": plano.limite_materiais_mes,
        "peis_mes": assinatura.peis_mes_atual,
        "limite_peis": plano.limite_peis_mes,
        "percentual_uso_alunos": (assinatura.alunos_ativos / plano.limite_alunos * 100) if plano.limite_alunos > 0 else 0,
        "percentual_uso_provas": (assinatura.provas_mes_atual / plano.limite_provas_mes * 100) if plano.limite_provas_mes > 0 else 0
    }


# ============================================
# ROTAS ADMIN (SUPER_ADMIN apenas)
# ============================================

def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    """Verifica se é super admin"""
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas super administradores podem acessar"
        )
    return current_user


@router.get("/admin/todos", response_model=List[PlanoResponse])
def listar_todos_planos_admin(
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """
    👑 [ADMIN] Lista todos os planos incluindo inativos.
    """
    return db.query(Plano).order_by(Plano.ordem).all()


@router.post("/admin/escola", status_code=status.HTTP_201_CREATED)
def criar_escola_admin(
    escola_data: EscolaCreate,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """
    👑 [ADMIN] Cria uma nova escola com assinatura inicial.
    """
    if db.query(Escola).filter(Escola.email == escola_data.email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email já cadastrado"
        )
    
    plano = db.query(Plano).filter(Plano.slug == escola_data.plano_slug).first()
    if not plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plano '{escola_data.plano_slug}' não encontrado"
        )
    
    nova_escola = Escola(
        nome=escola_data.nome,
        email=escola_data.email,
        cnpj=escola_data.cnpj,
        telefone=escola_data.telefone,
        tipo=escola_data.tipo,
        ativa=True
    )
    db.add(nova_escola)
    db.flush()
    
    status_inicial = StatusAssinatura.TRIAL.value if plano.valor == 0 else StatusAssinatura.PENDENTE.value
    data_fim = datetime.now() + timedelta(days=14) if plano.valor == 0 else None
    
    nova_assinatura = Assinatura(
        escola_id=nova_escola.id,
        plano_id=plano.id,
        status=status_inicial,
        valor_mensal=plano.valor,
        data_fim=data_fim,
        data_proxima_cobranca=datetime.now() + timedelta(days=30) if plano.valor > 0 else None
    )
    db.add(nova_assinatura)
    
    config = ConfiguracaoEscola(
        escola_id=nova_escola.id
    )
    db.add(config)
    
    db.commit()
    
    return {
        "message": "Escola criada com sucesso",
        "escola_id": nova_escola.id,
        "plano": plano.nome,
        "status": status_inicial
    }


class AtivarContaManualIn(BaseModel):
    """Payload de app/pages/SuperAdminDashboard.jsx -> AtivarContaTab (frontend
    ja existia antes do backend - commit fe42b40, 2026-09-04).

    Simplificado em 2026-09-05: sem negociar valor por conta - o trial e
    infinito de proposito (produto ainda nao e vendido), entao o super admin
    so escolhe o plano (define os limites de uso) e ativa. `valor_mensal`
    fica opcional (cai pro `Plano.valor` do catalogo quando omitido) so pra
    nao quebrar quem ainda mandar o campo; a UI nao expõe mais esse input.
    """
    escola_nome: str = Field(..., min_length=3, max_length=255)
    escola_cnpj: Optional[str] = None
    escola_tipo: str = "ESCOLA"
    escola_telefone: Optional[str] = None
    admin_nome: str = Field(..., min_length=3, max_length=255)
    admin_email: EmailStr
    plano_id: int
    valor_mensal: Optional[float] = Field(None, ge=0)
    status_inicial: str = "trial"  # "trial" | "ativa" - trial e infinito de proposito hoje
    cep: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None


@router.post("/admin/ativar-conta", status_code=status.HTTP_201_CREATED)
def ativar_conta_manual(
    dados: AtivarContaManualIn,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """
    👑 [SUPER ADMIN] Cria escola + admin + assinatura após negociação manual
    (o lead fala com a equipe via WhatsApp, negocia o valor, e o super admin
    ativa a conta aqui - ver docs/PAINEL-SUPERADMIN-MELHORIAS.md, P5).

    Substitui o autocadastro público: `POST /checkout/iniciar` foi desativado
    (só ele criava conta antes) e o frontend não expõe mais o fluxo de
    checkout (App.jsx redireciona /checkout/:slug para /planos) - agora só
    este endpoint, SUPER_ADMIN only, cria conta nova.

    Sem tabela de preço fixa: `valor_mensal` é o negociado com o cliente;
    `plano_id` só define os LIMITES de uso (alunos/professores/etc do
    catálogo `Plano`), não o preço cobrado - podem divergir do
    `Plano.valor` do catálogo à vontade.

    Não gera senha para o cliente: como em `professores.py` (convite de
    professor), a senha nasce aleatória e descartada - devolve um link de
    "definir senha" (mesmo token de reset de senha, com fingerprint do hash
    atual) para a equipe mandar pelo mesmo WhatsApp da negociação.
    """
    if dados.status_inicial not in ("trial", "ativa"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status_inicial deve ser 'trial' ou 'ativa'")

    if db.query(User).filter(User.email == dados.admin_email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Já existe usuário com este e-mail")
    if db.query(Escola).filter(Escola.email == dados.admin_email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Já existe escola com este e-mail")
    if dados.escola_cnpj and db.query(Escola).filter(Escola.cnpj == dados.escola_cnpj).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Já existe escola com este CNPJ")

    plano = db.query(Plano).filter(Plano.id == dados.plano_id).first()
    if not plano:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plano não encontrado")

    escola = Escola(
        nome=dados.escola_nome,
        cnpj=dados.escola_cnpj,
        tipo=dados.escola_tipo,
        telefone=dados.escola_telefone,
        email=dados.admin_email,  # Email principal = email do admin (mesmo padrao do checkout)
        cep=dados.cep,
        cidade=dados.cidade,
        estado=dados.estado,
        ativa=True,
    )
    db.add(escola)
    db.flush()  # garante escola.id para a assinatura/usuario

    # Senha aleatoria e descartada - ninguem usa. A conta so vira acessivel
    # quando o cliente define a propria senha pelo link (abaixo).
    senha_descartada = get_password_hash(secrets.token_urlsafe(24))
    usuario = User(
        name=dados.admin_nome,
        email=dados.admin_email,
        hashed_password=senha_descartada,
        role=UserRole.ADMIN,
        escola_id=escola.id,
        is_active=True,
    )
    db.add(usuario)
    db.flush()

    agora = datetime.now()
    if dados.status_inicial == "trial":
        status_assinatura = StatusAssinatura.TRIAL.value
        data_fim = agora + timedelta(days=14)
        prox_cobranca = None
    else:
        status_assinatura = StatusAssinatura.ATIVA.value
        data_fim = None
        prox_cobranca = agora + timedelta(days=30)

    assinatura = Assinatura(
        escola_id=escola.id,
        plano_id=plano.id,
        status=status_assinatura,
        data_inicio=agora,
        data_fim=data_fim,
        # Sem negociacao de valor: usa o preco do catalogo do plano escolhido.
        valor_mensal=dados.valor_mensal if dados.valor_mensal is not None else plano.valor,
        dia_vencimento=10,
        data_proxima_cobranca=prox_cobranca,
        alunos_ativos=0,
        professores_ativos=1,  # o admin conta como professor, mesmo padrao do checkout
    )
    db.add(assinatura)

    config = ConfiguracaoEscola(escola_id=escola.id)
    db.add(config)

    db.commit()
    db.refresh(usuario)

    fp = password_reset_fingerprint(usuario.hashed_password)
    token = create_password_reset_token(usuario.email, fp)
    link_definir_senha = f"{settings.FRONTEND_URL.rstrip('/')}/redefinir-senha?token={token}"

    return {
        "escola_id": escola.id,
        "usuario_id": usuario.id,
        "assinatura_id": assinatura.id,
        "status": status_assinatura,
        "link_definir_senha": link_definir_senha,
    }


@router.put("/admin/assinatura/{escola_id}")
def atualizar_assinatura_admin(
    escola_id: int,
    dados: AssinaturaUpdate,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """
    👑 [ADMIN] Atualiza assinatura de uma escola.
    """
    assinatura = db.query(Assinatura).filter(
        Assinatura.escola_id == escola_id
    ).first()
    
    if not assinatura:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assinatura não encontrada"
        )
    
    if dados.plano_id:
        plano = db.query(Plano).filter(Plano.id == dados.plano_id).first()
        if not plano:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plano não encontrado"
            )
        assinatura.plano_id = plano.id
        assinatura.valor_mensal = plano.valor
    
    if dados.status:
        assinatura.status = dados.status
        if dados.status == StatusAssinatura.ATIVA.value:
            assinatura.data_proxima_cobranca = datetime.now() + timedelta(days=30)
        elif dados.status == StatusAssinatura.CANCELADA.value:
            assinatura.cancelada_em = datetime.now()
    
    db.commit()
    
    return {"message": "Assinatura atualizada", "novo_status": assinatura.status}


@router.post("/admin/ativar-plano-699/{escola_id}")
def ativar_plano_699(
    escola_id: int,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """
    👑 [ADMIN] Ativa o plano Profissional (R$ 699) para uma escola.
    """
    plano = db.query(Plano).filter(Plano.slug == "profissional").first()
    if not plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plano Profissional não encontrado. Acesse /api/v1/planos/seed primeiro."
        )
    
    escola = db.query(Escola).filter(Escola.id == escola_id).first()
    if not escola:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Escola não encontrada"
        )
    
    assinatura = db.query(Assinatura).filter(
        Assinatura.escola_id == escola_id
    ).first()
    
    if assinatura:
        assinatura.plano_id = plano.id
        assinatura.valor_mensal = plano.valor
        assinatura.status = StatusAssinatura.ATIVA.value
        assinatura.data_proxima_cobranca = datetime.now() + timedelta(days=30)
    else:
        assinatura = Assinatura(
            escola_id=escola_id,
            plano_id=plano.id,
            status=StatusAssinatura.ATIVA.value,
            valor_mensal=plano.valor,
            data_proxima_cobranca=datetime.now() + timedelta(days=30)
        )
        db.add(assinatura)
    
    db.commit()
    
    return {
        "message": f"Plano Profissional (R$ 699,00) ativado para {escola.nome}",
        "escola_id": escola_id,
        "plano": "Profissional",
        "valor": 699.00,
        "status": "ativa"
    }


@router.get("/admin/escolas-assinaturas")
def listar_escolas_assinaturas(
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db)
):
    """
    👑 [ADMIN] Lista todas as escolas com suas assinaturas.
    """
    escolas = db.query(Escola).all()
    
    resultado = []
    for escola in escolas:
        assinatura = db.query(Assinatura).filter(
            Assinatura.escola_id == escola.id
        ).first()
        
        resultado.append({
            "escola_id": escola.id,
            "nome": escola.nome,
            "email": escola.email,
            "ativa": escola.ativa,
            "assinatura": {
                "plano": assinatura.plano.nome if assinatura and assinatura.plano else "Sem plano",
                "valor": assinatura.valor_mensal if assinatura else 0,
                "status": assinatura.status if assinatura else "Sem assinatura",
                "alunos_ativos": assinatura.alunos_ativos if assinatura else 0,
                "professores_ativos": assinatura.professores_ativos if assinatura else 0
            } if assinatura else None
        })
    
    return resultado
