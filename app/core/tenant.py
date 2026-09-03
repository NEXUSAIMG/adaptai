# ============================================
# MIDDLEWARE E DEPENDÊNCIAS MULTI-TENANT
# ============================================
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from app.database import get_db
from app.models.user import User, UserRole
from app.models.escola import Escola
from app.models.assinatura import Assinatura, StatusAssinatura
from app.api.dependencies import get_current_user


class TenantContext:
    """
    Contexto do tenant atual na requisição.
    Contém informações sobre a escola e limites de uso.
    """
    def __init__(
        self,
        escola: Optional[Escola] = None,
        assinatura: Optional[Assinatura] = None,
        user: Optional[User] = None
    ):
        self.escola = escola
        self.assinatura = assinatura
        self.user = user
    
    @property
    def escola_id(self) -> Optional[int]:
        return self.escola.id if self.escola else None
    
    @property
    def escola_nome(self) -> str:
        return self.escola.nome if self.escola else "Sem escola"
    
    @property
    def plano_ativo(self) -> bool:
        if not self.assinatura:
            return False
        return self.assinatura.status in [
            StatusAssinatura.TRIAL.value,
            StatusAssinatura.ATIVA.value
        ]
    
    @property
    def em_trial(self) -> bool:
        if not self.assinatura:
            return False
        return self.assinatura.status == StatusAssinatura.TRIAL.value
    
    def verificar_limite_alunos(self) -> bool:
        """Verifica se pode adicionar mais alunos"""
        if not self.assinatura or not self.assinatura.plano:
            return False
        return self.assinatura.alunos_ativos < self.assinatura.plano.limite_alunos
    
    def verificar_limite_professores(self) -> bool:
        """Verifica se pode adicionar mais professores"""
        if not self.assinatura or not self.assinatura.plano:
            return False
        return self.assinatura.professores_ativos < self.assinatura.plano.limite_professores
    
    def verificar_limite_provas(self) -> bool:
        """Verifica se pode criar mais provas este mês"""
        if not self.assinatura or not self.assinatura.plano:
            return False
        return self.assinatura.provas_mes_atual < self.assinatura.plano.limite_provas_mes
    
    def verificar_limite_materiais(self) -> bool:
        """Verifica se pode criar mais materiais este mês"""
        if not self.assinatura or not self.assinatura.plano:
            return False
        return self.assinatura.materiais_mes_atual < self.assinatura.plano.limite_materiais_mes
    
    def verificar_limite_peis(self) -> bool:
        """Verifica se pode gerar mais PEIs este mês"""
        if not self.assinatura or not self.assinatura.plano:
            return False
        return self.assinatura.peis_mes_atual < self.assinatura.plano.limite_peis_mes


async def get_tenant_context(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> TenantContext:
    """
    Obtém o contexto do tenant (escola) atual.
    Usado para filtrar dados e verificar limites.
    """
    # Super admin não tem escola vinculada obrigatoriamente
    if current_user.role == UserRole.SUPER_ADMIN:
        return TenantContext(user=current_user)
    
    # Busca a escola do usuário
    if not current_user.escola_id:
        # Usuário sem escola - pode ser usuário legado ou erro
        return TenantContext(user=current_user)
    
    escola = db.query(Escola).filter(
        Escola.id == current_user.escola_id,
        Escola.ativa == True
    ).first()
    
    if not escola:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Escola não encontrada ou inativa"
        )
    
    # Busca assinatura da escola
    assinatura = db.query(Assinatura).filter(
        Assinatura.escola_id == escola.id
    ).first()
    
    return TenantContext(
        escola=escola,
        assinatura=assinatura,
        user=current_user
    )


async def require_active_subscription(
    tenant: TenantContext = Depends(get_tenant_context)
) -> TenantContext:
    """
    Requer que a escola tenha uma assinatura ativa.
    Bloqueia acesso se a assinatura estiver cancelada/suspensa.
    """
    # Super admin sempre tem acesso
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.plano_ativo:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Assinatura inativa ou expirada. Por favor, renove seu plano."
        )
    
    return tenant


async def require_escola(
    tenant: TenantContext = Depends(get_tenant_context)
) -> TenantContext:
    """
    Requer que o usuário esteja vinculado a uma escola.
    """
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.escola:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário não está vinculado a nenhuma escola"
        )
    
    return tenant


def check_limite_alunos(tenant: TenantContext = Depends(require_active_subscription)):
    """Verifica limite de alunos antes de criar novo"""
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.verificar_limite_alunos():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Limite de alunos atingido. Faça upgrade do plano."
        )
    return tenant


def check_limite_provas(tenant: TenantContext = Depends(require_active_subscription)):
    """Verifica limite de provas antes de criar nova"""
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.verificar_limite_provas():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Limite de provas mensais atingido. Aguarde o próximo mês ou faça upgrade."
        )
    return tenant


def check_limite_materiais(tenant: TenantContext = Depends(require_active_subscription)):
    """Verifica limite de materiais antes de criar novo"""
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.verificar_limite_materiais():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Limite de materiais mensais atingido. Aguarde o próximo mês ou faça upgrade."
        )
    return tenant


def check_limite_peis(tenant: TenantContext = Depends(require_active_subscription)):
    """Verifica limite de PEIs antes de gerar novo"""
    if tenant.user and tenant.user.role == UserRole.SUPER_ADMIN:
        return tenant
    
    if not tenant.verificar_limite_peis():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Limite de PEIs mensais atingido. Aguarde o próximo mês ou faça upgrade."
        )
    return tenant


# ============================================
# ENFORCEMENT 'SOFT' DE LIMITES (contagem ao vivo)
# ============================================
# Diferente das dependencias check_limite_* acima (que dependem de contadores
# mantidos na Assinatura e de assinatura ativa obrigatoria), estas funcoes:
#   - sao chamadas DENTRO do endpoint (recebem db + user), sem mudar assinatura;
#   - contam o uso REAL no banco (sem contadores que precisam de reset mensal);
#   - fazem grandfather: super_admin e quem nao tem escola com assinatura
#     ativa/trial NAO sao limitados (evita trancar usuarios legados).


def _assinatura_ativa(db: Session, escola_id: Optional[int]) -> Optional[Assinatura]:
    """Retorna a assinatura da escola se estiver ativa ou em trial; senao None."""
    if not escola_id:
        return None
    assinatura = db.query(Assinatura).filter(Assinatura.escola_id == escola_id).first()
    if not assinatura:
        return None
    if assinatura.status not in (StatusAssinatura.TRIAL.value, StatusAssinatura.ATIVA.value):
        return None
    return assinatura


def enforce_limite_alunos(db: Session, user: User) -> None:
    """
    Bloqueia (403) a criacao de aluno se a escola ja atingiu o limite_alunos do plano.

    Enforcement soft: super_admin liberado; usuario sem escola ou sem assinatura
    ativa/trial liberado (grandfather). Conta alunos ativos da escola ao vivo.
    """
    if user.role == UserRole.SUPER_ADMIN:
        return
    assinatura = _assinatura_ativa(db, user.escola_id)
    if not assinatura or not assinatura.plano:
        return  # grandfather: sem plano ativo nao limita

    from app.models.student import Student
    em_uso = db.query(Student).filter(
        Student.escola_id == user.escola_id,
        Student.is_active == True,
    ).count()
    if em_uso >= assinatura.plano.limite_alunos:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Limite de alunos do plano atingido ({assinatura.plano.limite_alunos}). "
                "Faça upgrade do plano para cadastrar mais alunos."
            ),
        )


def enforce_limite_professores(db: Session, user: User) -> None:
    """
    Bloqueia (403) a criacao de professor se a escola ja atingiu o
    limite_professores do plano.

    Enforcement soft, igual a enforce_limite_alunos: super_admin liberado; escola
    sem assinatura ativa/trial liberada (grandfather). Conta ao vivo TODOS os
    usuarios ativos da escola (o admin conta como professor - mesma convencao de
    checkout.py e de Assinatura.professores_ativos).
    """
    if user.role == UserRole.SUPER_ADMIN:
        return
    assinatura = _assinatura_ativa(db, user.escola_id)
    if not assinatura or not assinatura.plano:
        return  # grandfather: sem plano ativo nao limita

    em_uso = db.query(User).filter(
        User.escola_id == user.escola_id,
        User.is_active == True,  # noqa: E712
    ).count()
    if em_uso >= assinatura.plano.limite_professores:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Limite de professores do plano atingido ({assinatura.plano.limite_professores}). "
                "Faça upgrade do plano para cadastrar mais professores."
            ),
        )


def _inicio_do_mes_utc() -> datetime:
    """Primeiro instante do mes corrente em UTC, naive (compativel com as
    colunas DATETIME do MySQL, que sao armazenadas em UTC sem tzinfo)."""
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    return agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _enforce_limite_mensal(db: Session, user: User, *, model, creator_col, date_col, limite_attr: str, rotulo: str) -> None:
    """
    Enforcement soft de um limite MENSAL por escola, contando ao vivo as linhas
    criadas no mes corrente pelos usuarios da escola (recurso -> usuario criador
    -> escola). Grandfather para super_admin e para quem nao tem assinatura ativa.
    """
    if user.role == UserRole.SUPER_ADMIN:
        return
    assinatura = _assinatura_ativa(db, user.escola_id)
    if not assinatura or not assinatura.plano:
        return  # grandfather
    limite = getattr(assinatura.plano, limite_attr, None)
    if not limite:
        return  # limite nao definido / zero = sem restricao

    usados = (
        db.query(model)
        .join(User, creator_col == User.id)
        .filter(User.escola_id == user.escola_id, date_col >= _inicio_do_mes_utc())
        .count()
    )
    if usados >= limite:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Limite mensal de {rotulo} do plano atingido ({limite}). "
                "Aguarde o proximo mes ou faca upgrade do plano."
            ),
        )


def enforce_limite_provas(db: Session, user: User) -> None:
    """Limite mensal de provas geradas por escola."""
    from app.models.prova import Prova
    _enforce_limite_mensal(
        db, user, model=Prova, creator_col=Prova.criado_por_id,
        date_col=Prova.criado_em, limite_attr="limite_provas_mes", rotulo="provas",
    )


def enforce_limite_materiais(db: Session, user: User) -> None:
    """Limite mensal de materiais gerados por escola."""
    from app.models.material import Material
    _enforce_limite_mensal(
        db, user, model=Material, creator_col=Material.criado_por_id,
        date_col=Material.criado_em, limite_attr="limite_materiais_mes", rotulo="materiais",
    )


def enforce_limite_peis(db: Session, user: User) -> None:
    """
    Limite mensal de PEIs por escola (conta a tabela 'peis').

    ATENCAO: atualmente NAO esta ligado em nenhum endpoint, de proposito. O fluxo
    de PEI grava em Student.diagnosis (JSON via PUT /students/{id}), nao na tabela
    'peis', entao esta contagem daria ~0 e o limite seria inerte. Mantido para
    quando os PEIs forem persistidos de fato na tabela 'peis' (ver nota em
    routes/pei.py, endpoint gerar-pei-de-relatorios).
    """
    from app.models.pei import PEI
    _enforce_limite_mensal(
        db, user, model=PEI, creator_col=PEI.created_by,
        date_col=PEI.created_at, limite_attr="limite_peis_mes", rotulo="PEIs",
    )


def enforce_limite_relatorios(db: Session, user: User) -> None:
    """Limite mensal de relatorios enviados por escola."""
    from app.models.relatorio import Relatorio
    _enforce_limite_mensal(
        db, user, model=Relatorio, creator_col=Relatorio.created_by,
        date_col=Relatorio.created_at, limite_attr="limite_relatorios_mes", rotulo="relatorios",
    )
