"""
🏥 AdaptAI - Rotas do vertical CLINICA (Fase 0 + Modulo 1).

API minima da gestao clinica multidisciplinar: paciente, profissional, equipe
do caso, plano terapeutico (PTI) + objetivos, sessao e evolucao.

Isolamento e licenciamento:
  - O router inteiro exige o modulo CLINICA (requer_modulo). Um tenant sem o
    modulo licenciado recebe 403 em qualquer rota daqui.
  - Acesso a paciente e validado por equipe do caso (acesso_clinico), padrao
    anti-IDOR (404) igual ao resto do projeto.

Regra "IA rascunha, humano assina": a evolucao pode nascer como rascunho
(rascunho_ia) e so vale apos ser assinada por um profissional habilitado.
"""
import hashlib
import secrets
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.dependencies import get_current_user
from app.models.user import User, UserRole
from app.core.entitlements import requer_modulo, Modulo
from app.services import acesso_clinico
from app.services import tradutor_familia_service
from app.services import relatorio_evolucao_service
from app.services import evolucao_service
from app.services import pti_service
from app.models.clinica_core import (
    Profissional, Paciente, EquipeCaso,
    Especialidade, Conselho, PapelProfissional, PapelNoCaso, StatusPaciente,
    AcaoAuditoria, AuditoriaAcesso,
    Consentimento, TipoConsentimento,
)
from app.models.clinica_terapia import (
    PlanoTerapeutico, ObjetivoTerapeutico, Sessao, Evolucao,
    StatusPlanoTerapeutico, StatusObjetivoTerapeutico, Presenca,
)

router = APIRouter(
    prefix="/clinica",
    tags=["🏥 Clínica"],
    dependencies=[Depends(requer_modulo(Modulo.CLINICA))],
)


def _agora():
    return datetime.now(timezone.utc)


def _escola_id(current_user: User) -> int:
    if not current_user.escola_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuario sem escola/clinica vinculada.",
        )
    return current_user.escola_id


# ============================================================================
# Schemas (inline, no estilo de ilustracoes.py)
# ============================================================================
class PacienteCriar(BaseModel):
    nome: str = Field(..., min_length=1, max_length=255)
    data_nascimento: Optional[date] = None
    responsavel_nome: Optional[str] = None
    responsavel_contato: Optional[str] = None


class PacienteStatus(BaseModel):
    status: StatusPaciente


class ConsentimentoCriar(BaseModel):
    tipo: TipoConsentimento
    versao_texto: str = Field(..., min_length=1, max_length=100)
    concedido_por: str = Field(..., min_length=1, max_length=255)


class ProfissionalCriar(BaseModel):
    usuario_id: int
    nome: str = Field(..., min_length=1, max_length=255)
    especialidade: Especialidade
    conselho_tipo: Optional[Conselho] = None
    conselho_numero: Optional[str] = None
    papel: PapelProfissional = PapelProfissional.TERAPEUTA


class EquipeMembroCriar(BaseModel):
    profissional_id: int
    papel_no_caso: PapelNoCaso = PapelNoCaso.COTERAPEUTA


class PlanoCriar(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=255)
    periodo_inicio: Optional[date] = None
    periodo_fim: Optional[date] = None


class ObjetivoCriar(BaseModel):
    especialidade: Especialidade
    descricao: str = Field(..., min_length=1)
    criterio_mastery: Optional[str] = None
    linha_base: Optional[float] = None


class SessaoCriar(BaseModel):
    profissional_id: int
    especialidade: Especialidade
    data_sessao: Optional[datetime] = None
    duracao_min: Optional[int] = None
    presenca: Presenca = Presenca.PRESENTE
    observacao: Optional[str] = None


class EvolucaoCriar(BaseModel):
    texto: str = Field(..., min_length=1)
    sessao_id: Optional[int] = None
    profissional_id: Optional[int] = None
    especialidade: Optional[Especialidade] = None
    rascunho_ia: bool = False


# ============================================================================
# Serializadores (dict — evita depender da versao do Pydantic p/ ORM)
# ============================================================================
def _v(e):
    return e.value if hasattr(e, "value") else e


def _paciente_dict(p: Paciente) -> dict:
    return {
        "id": p.id, "nome": p.nome, "status": _v(p.status),
        "data_nascimento": str(p.data_nascimento) if p.data_nascimento else None,
        "responsavel_nome": p.responsavel_nome,
        "responsavel_contato": p.responsavel_contato,
    }


def _profissional_dict(pr: Profissional) -> dict:
    return {
        "id": pr.id, "nome": pr.nome, "especialidade": _v(pr.especialidade),
        "papel": _v(pr.papel), "conselho_tipo": _v(pr.conselho_tipo),
        "conselho_numero": pr.conselho_numero, "ativo": pr.ativo,
    }


def _plano_dict(pl: PlanoTerapeutico) -> dict:
    return {
        "id": pl.id, "paciente_id": pl.paciente_id, "titulo": pl.titulo,
        "status": _v(pl.status),
        "periodo_inicio": str(pl.periodo_inicio) if pl.periodo_inicio else None,
        "periodo_fim": str(pl.periodo_fim) if pl.periodo_fim else None,
    }


# ============================================================================
# Pacientes
# ============================================================================
@router.post("/pacientes", status_code=status.HTTP_201_CREATED)
def criar_paciente(
    body: PacienteCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = Paciente(
        escola_id=_escola_id(current_user),
        nome=body.nome,
        data_nascimento=body.data_nascimento,
        responsavel_nome=body.responsavel_nome,
        responsavel_contato=body.responsavel_contato,
        status=StatusPaciente.EM_AVALIACAO,
        criado_em=_agora(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _paciente_dict(p)


@router.get("/pacientes")
def listar_pacientes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Paciente)
    if current_user.role != UserRole.SUPER_ADMIN:
        q = q.filter(Paciente.escola_id == _escola_id(current_user))
    return [_paciente_dict(p) for p in q.order_by(Paciente.nome).all()]


@router.get("/pacientes/{paciente_id}")
def obter_paciente(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(
        db, paciente_id, current_user,
        acao=AcaoAuditoria.VISUALIZAR, recurso="prontuario",
    )
    return _paciente_dict(p)


@router.patch("/pacientes/{paciente_id}/status")
def atualizar_status_paciente(
    paciente_id: int,
    body: PacienteStatus,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    p.status = body.status
    p.atualizado_em = _agora()
    db.commit()
    db.refresh(p)
    return _paciente_dict(p)


@router.post("/pacientes/{paciente_id}/token-familia")
def gerar_token_familia(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Gera (ou rotaciona) o token read-only do Portal da Família."""
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    p.token_familia = secrets.token_urlsafe(24)
    p.atualizado_em = _agora()
    db.commit()
    db.refresh(p)
    return {"token_familia": p.token_familia, "url": f"/familia/{p.token_familia}"}


@router.get("/pacientes/{paciente_id}/relatorio-evolucao")
def relatorio_evolucao(
    paciente_id: int,
    de: Optional[datetime] = Query(None, description="inicio do periodo (ISO)"),
    ate: Optional[datetime] = Query(None, description="fim do periodo (ISO)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Consolida as evolucoes ASSINADAS do periodo num rascunho de relatorio (IA)."""
    p = acesso_clinico.verificar_acesso_paciente(
        db, paciente_id, current_user,
        acao=AcaoAuditoria.EXPORTAR, recurso="relatorio_evolucao",
    )
    q = db.query(Evolucao).filter(
        Evolucao.paciente_id == p.id, Evolucao.assinado_em.isnot(None)
    )
    if de:
        q = q.filter(Evolucao.assinado_em >= de)
    if ate:
        q = q.filter(Evolucao.assinado_em <= ate)
    evs = q.order_by(Evolucao.assinado_em).all()
    dados = [
        {"data": str(e.assinado_em) if e.assinado_em else None,
         "especialidade": _v(e.especialidade), "texto": e.texto}
        for e in evs
    ]
    periodo = None
    if de or ate:
        periodo = "%s a %s" % (str(de)[:10] if de else "...", str(ate)[:10] if ate else "...")
    texto = relatorio_evolucao_service.gerar_relatorio_consolidado(dados, periodo)
    # Base legal (LGPD): informa quais consentimentos de compartilhamento estao
    # vigentes no momento da geracao — nao bloqueia, apenas orienta o profissional.
    consentimentos = {
        "tratamento_dados": _consent_vigente(db, p.id, TipoConsentimento.TRATAMENTO_DADOS),
        "compartilha_escola": _consent_vigente(db, p.id, TipoConsentimento.COMPARTILHA_ESCOLA),
        "compartilha_convenio": _consent_vigente(db, p.id, TipoConsentimento.COMPARTILHA_CONVENIO),
    }
    return {"paciente_id": p.id, "periodo": periodo,
            "total_evolucoes": len(dados), "texto": texto,
            "consentimentos": consentimentos}


@router.get("/pacientes/{paciente_id}/auditoria")
def auditoria_paciente(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trilha de acesso ao prontuário (últimos 100 eventos)."""
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    regs = (db.query(AuditoriaAcesso)
            .filter(AuditoriaAcesso.paciente_id == p.id)
            .order_by(AuditoriaAcesso.id.desc()).limit(100).all())
    return [{
        "id": r.id, "usuario_id": r.usuario_id, "acao": _v(r.acao),
        "recurso": r.recurso, "recurso_id": r.recurso_id,
        "criado_em": str(r.criado_em) if r.criado_em else None,
    } for r in regs]


# ============================================================================
# PTI por IA (a partir do contexto clínico / laudo em texto)
# ============================================================================
class PtiSugerir(BaseModel):
    contexto: str = Field(..., min_length=1)
    especialidades: Optional[List[str]] = None


class PtiObjetivoIn(BaseModel):
    especialidade: Especialidade
    descricao: str = Field(..., min_length=1)
    criterio_mastery: Optional[str] = None


class PtiAplicar(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=255)
    objetivos: List[PtiObjetivoIn]


@router.post("/pacientes/{paciente_id}/pti/sugerir")
def pti_sugerir(
    paciente_id: int,
    body: PtiSugerir,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A IA sugere objetivos a partir do contexto clínico. NÃO persiste."""
    acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    return pti_service.sugerir_objetivos(body.contexto, body.especialidades)


@router.post("/pacientes/{paciente_id}/pti/aplicar", status_code=status.HTTP_201_CREATED)
def pti_aplicar(
    paciente_id: int,
    body: PtiAplicar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cria um PTI (plano + objetivos) a partir do rascunho revisado."""
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    plano = PlanoTerapeutico(
        escola_id=p.escola_id, paciente_id=p.id, titulo=body.titulo,
        status=StatusPlanoTerapeutico.RASCUNHO, criado_por_id=current_user.id,
        criado_em=_agora(),
    )
    db.add(plano)
    db.commit()
    db.refresh(plano)
    ordem = 0
    for o in body.objetivos:
        db.add(ObjetivoTerapeutico(
            plano_id=plano.id, especialidade=o.especialidade, descricao=o.descricao,
            criterio_mastery=o.criterio_mastery, status=StatusObjetivoTerapeutico.BASELINE,
            ordem=ordem, criado_em=_agora(),
        ))
        ordem += 1
    db.commit()
    return {"plano_id": plano.id, "paciente_id": p.id,
            "titulo": plano.titulo, "objetivos_criados": len(body.objetivos)}


# ============================================================================
# Profissionais
# ============================================================================
@router.post("/profissionais", status_code=status.HTTP_201_CREATED)
def criar_profissional(
    body: ProfissionalCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pr = Profissional(
        escola_id=_escola_id(current_user),
        usuario_id=body.usuario_id,
        nome=body.nome,
        especialidade=body.especialidade,
        conselho_tipo=body.conselho_tipo,
        conselho_numero=body.conselho_numero,
        papel=body.papel,
        ativo=True,
        criado_em=_agora(),
    )
    db.add(pr)
    db.commit()
    db.refresh(pr)
    return _profissional_dict(pr)


@router.get("/profissionais")
def listar_profissionais(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Profissional)
    if current_user.role != UserRole.SUPER_ADMIN:
        q = q.filter(Profissional.escola_id == _escola_id(current_user))
    return [_profissional_dict(pr) for pr in q.order_by(Profissional.nome).all()]


# ============================================================================
# Equipe do caso
# ============================================================================
@router.post("/pacientes/{paciente_id}/equipe", status_code=status.HTTP_201_CREATED)
def adicionar_membro_equipe(
    paciente_id: int,
    body: EquipeMembroCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    prof = db.query(Profissional).filter(
        Profissional.id == body.profissional_id,
        Profissional.escola_id == p.escola_id,
    ).first()
    if not prof:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profissional nao encontrado")
    membro = EquipeCaso(
        escola_id=p.escola_id,
        paciente_id=p.id,
        profissional_id=prof.id,
        papel_no_caso=body.papel_no_caso,
        ativo=True,
        criado_em=_agora(),
    )
    db.add(membro)
    db.commit()
    db.refresh(membro)
    return {"id": membro.id, "paciente_id": p.id, "profissional_id": prof.id,
            "papel_no_caso": _v(membro.papel_no_caso)}


@router.get("/pacientes/{paciente_id}/equipe")
def listar_equipe(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    membros = db.query(EquipeCaso).filter(
        EquipeCaso.paciente_id == p.id, EquipeCaso.ativo.is_(True)
    ).all()
    return [{"id": m.id, "profissional_id": m.profissional_id,
             "papel_no_caso": _v(m.papel_no_caso)} for m in membros]


# ============================================================================
# Plano Terapeutico (PTI) + objetivos
# ============================================================================
@router.post("/pacientes/{paciente_id}/planos", status_code=status.HTTP_201_CREATED)
def criar_plano(
    paciente_id: int,
    body: PlanoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    plano = PlanoTerapeutico(
        escola_id=p.escola_id,
        paciente_id=p.id,
        titulo=body.titulo,
        periodo_inicio=body.periodo_inicio,
        periodo_fim=body.periodo_fim,
        status=StatusPlanoTerapeutico.RASCUNHO,
        criado_por_id=current_user.id,
        criado_em=_agora(),
    )
    db.add(plano)
    db.commit()
    db.refresh(plano)
    return _plano_dict(plano)


@router.get("/pacientes/{paciente_id}/planos")
def listar_planos(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    planos = db.query(PlanoTerapeutico).filter(
        PlanoTerapeutico.paciente_id == p.id
    ).order_by(PlanoTerapeutico.id.desc()).all()
    return [_plano_dict(pl) for pl in planos]


def _plano_com_acesso(db, plano_id, current_user) -> PlanoTerapeutico:
    plano = db.query(PlanoTerapeutico).filter(PlanoTerapeutico.id == plano_id).first()
    if not plano:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plano nao encontrado")
    # acesso via paciente dono do plano (anti-IDOR)
    acesso_clinico.verificar_acesso_paciente(db, plano.paciente_id, current_user)
    return plano


@router.post("/planos/{plano_id}/objetivos", status_code=status.HTTP_201_CREATED)
def criar_objetivo(
    plano_id: int,
    body: ObjetivoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plano = _plano_com_acesso(db, plano_id, current_user)
    obj = ObjetivoTerapeutico(
        plano_id=plano.id,
        especialidade=body.especialidade,
        descricao=body.descricao,
        criterio_mastery=body.criterio_mastery,
        linha_base=body.linha_base,
        status=StatusObjetivoTerapeutico.BASELINE,
        criado_em=_agora(),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return {"id": obj.id, "plano_id": plano.id, "especialidade": _v(obj.especialidade),
            "descricao": obj.descricao, "status": _v(obj.status),
            "criterio_mastery": obj.criterio_mastery,
            "linha_base": float(obj.linha_base) if obj.linha_base is not None else None}


# ============================================================================
# Sessao
# ============================================================================
@router.post("/pacientes/{paciente_id}/sessoes", status_code=status.HTTP_201_CREATED)
def criar_sessao(
    paciente_id: int,
    body: SessaoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    sessao = Sessao(
        escola_id=p.escola_id,
        paciente_id=p.id,
        profissional_id=body.profissional_id,
        especialidade=body.especialidade,
        data_sessao=body.data_sessao or _agora(),
        duracao_min=body.duracao_min,
        presenca=body.presenca,
        observacao=body.observacao,
        criado_em=_agora(),
    )
    db.add(sessao)
    db.commit()
    db.refresh(sessao)
    return {"id": sessao.id, "paciente_id": p.id, "profissional_id": sessao.profissional_id,
            "especialidade": _v(sessao.especialidade), "presenca": _v(sessao.presenca),
            "data_sessao": str(sessao.data_sessao) if sessao.data_sessao else None}


@router.get("/pacientes/{paciente_id}/sessoes")
def listar_sessoes(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    sessoes = db.query(Sessao).filter(Sessao.paciente_id == p.id).order_by(
        Sessao.data_sessao.desc()
    ).all()
    return [{"id": s.id, "profissional_id": s.profissional_id,
             "especialidade": _v(s.especialidade), "presenca": _v(s.presenca),
             "data_sessao": str(s.data_sessao) if s.data_sessao else None} for s in sessoes]


# ============================================================================
# Evolucao (IA rascunha; humano assina)
# ============================================================================
def _hash_evolucao(e: Evolucao) -> str:
    base = "%s|%s|%s|%s" % (
        e.id, e.texto or "", e.assinado_por_id or "",
        e.assinado_em.isoformat() if e.assinado_em else "",
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _evolucao_dict(e: Evolucao) -> dict:
    return {
        "id": e.id, "paciente_id": e.paciente_id, "sessao_id": e.sessao_id,
        "texto": e.texto, "rascunho_ia": bool(e.rascunho_ia),
        "assinado_por_id": e.assinado_por_id,
        "assinado_em": str(e.assinado_em) if e.assinado_em else None,
        "assinada": e.assinado_em is not None,
        "assinatura_hash": e.assinatura_hash,
        "resumo_familia": e.resumo_familia,
    }


@router.post("/pacientes/{paciente_id}/evolucoes", status_code=status.HTTP_201_CREATED)
def criar_evolucao(
    paciente_id: int,
    body: EvolucaoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(
        db, paciente_id, current_user, acao=AcaoAuditoria.CRIAR, recurso="evolucao"
    )
    ev = Evolucao(
        escola_id=p.escola_id,
        paciente_id=p.id,
        sessao_id=body.sessao_id,
        profissional_id=body.profissional_id,
        especialidade=body.especialidade,
        texto=body.texto,
        rascunho_ia=body.rascunho_ia,
        criado_em=_agora(),
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return _evolucao_dict(ev)


@router.post("/evolucoes/{evolucao_id}/assinar")
def assinar_evolucao(
    evolucao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ev = db.query(Evolucao).filter(Evolucao.id == evolucao_id).first()
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evolucao nao encontrada")
    # acesso via paciente dono (anti-IDOR) + auditoria
    acesso_clinico.verificar_acesso_paciente(
        db, ev.paciente_id, current_user,
        acao=AcaoAuditoria.EDITAR, recurso="evolucao", recurso_id=ev.id,
    )
    if ev.assinado_em is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Evolucao ja assinada")
    ev.assinado_por_id = current_user.id
    ev.assinado_em = _agora()
    ev.assinatura_hash = _hash_evolucao(ev)
    db.commit()
    db.refresh(ev)
    return _evolucao_dict(ev)


@router.get("/pacientes/{paciente_id}/evolucoes")
def listar_evolucoes(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    evs = (db.query(Evolucao).filter(Evolucao.paciente_id == p.id)
           .order_by(Evolucao.id.desc()).limit(200).all())
    return [_evolucao_dict(e) for e in evs]


@router.get("/evolucoes/{evolucao_id}/verificar")
def verificar_evolucao(
    evolucao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ev = db.query(Evolucao).filter(Evolucao.id == evolucao_id).first()
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evolucao nao encontrada")
    acesso_clinico.verificar_acesso_paciente(db, ev.paciente_id, current_user)
    if ev.assinado_em is None or not ev.assinatura_hash:
        return {"assinada": False, "valido": False, "hash": None}
    recalculado = _hash_evolucao(ev)
    return {
        "assinada": True,
        "valido": recalculado == ev.assinatura_hash,
        "hash": ev.assinatura_hash,
        "assinado_em": str(ev.assinado_em), "assinado_por_id": ev.assinado_por_id,
    }


class ResumoFamiliaIn(BaseModel):
    texto: Optional[str] = None


@router.post("/evolucoes/{evolucao_id}/resumo-familia")
def gerar_resumo_familia(
    evolucao_id: int,
    body: ResumoFamiliaIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Gera (IA) ou salva (edicao) o resumo em linguagem simples p/ a familia.
    So para evolucao ja assinada. IA rascunha; profissional aprova/edita."""
    ev = db.query(Evolucao).filter(Evolucao.id == evolucao_id).first()
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evolucao nao encontrada")
    p = acesso_clinico.verificar_acesso_paciente(
        db, ev.paciente_id, current_user,
        acao=AcaoAuditoria.EDITAR, recurso="evolucao", recurso_id=ev.id,
    )
    if ev.assinado_em is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Assine a evolucao antes de gerar o resumo para a familia.")
    if body.texto is not None:
        ev.resumo_familia = (body.texto or "").strip() or None
    else:
        primeiro = (p.nome or "").strip().split(" ")[0] if (p and p.nome) else ""
        ev.resumo_familia = tradutor_familia_service.traduzir(ev.texto, primeiro)
    db.commit()
    db.refresh(ev)
    return {"id": ev.id, "resumo_familia": ev.resumo_familia}


class DitadoIn(BaseModel):
    texto: str = Field(..., min_length=1)
    especialidade: Optional[Especialidade] = None


@router.post("/evolucoes/estruturar")
def estruturar_evolucao(
    body: DitadoIn,
    current_user: User = Depends(get_current_user),
):
    """Estrutura um ditado (voz->texto) numa nota de evolucao. Nao persiste."""
    esp = _v(body.especialidade) if body.especialidade else None
    texto = evolucao_service.estruturar_ditado(body.texto, esp)
    return {"texto": texto}



# ============================================================================
# Consentimentos LGPD (base legal do tratamento de dados do paciente)
# ============================================================================
def _consent_vigente(db: Session, paciente_id: int, tipo: TipoConsentimento) -> bool:
    """True se ha consentimento do tipo ainda nao revogado."""
    c = (db.query(Consentimento)
         .filter(Consentimento.paciente_id == paciente_id,
                 Consentimento.tipo == tipo,
                 Consentimento.revogado_em.is_(None))
         .first())
    return c is not None


def _consentimento_dict(c: Consentimento) -> dict:
    return {
        "id": c.id, "paciente_id": c.paciente_id,
        "tipo": c.tipo.value if hasattr(c.tipo, "value") else c.tipo,
        "versao_texto": c.versao_texto,
        "concedido_por": c.concedido_por,
        "concedido_em": str(c.concedido_em) if c.concedido_em else None,
        "revogado_em": str(c.revogado_em) if c.revogado_em else None,
        "vigente": c.revogado_em is None,
    }


@router.get("/pacientes/{paciente_id}/consentimentos")
def listar_consentimentos(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    cs = (db.query(Consentimento).filter(Consentimento.paciente_id == p.id)
          .order_by(Consentimento.id.desc()).all())
    return [_consentimento_dict(c) for c in cs]


@router.post("/pacientes/{paciente_id}/consentimentos", status_code=status.HTTP_201_CREATED)
def registrar_consentimento(
    paciente_id: int,
    body: ConsentimentoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(
        db, paciente_id, current_user, acao=AcaoAuditoria.CRIAR, recurso="consentimento"
    )
    c = Consentimento(
        escola_id=p.escola_id,
        paciente_id=p.id,
        tipo=body.tipo,
        versao_texto=body.versao_texto,
        concedido_por=body.concedido_por,
        concedido_em=_agora(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _consentimento_dict(c)


@router.post("/consentimentos/{consentimento_id}/revogar")
def revogar_consentimento(
    consentimento_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(Consentimento).filter(Consentimento.id == consentimento_id).first()
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consentimento nao encontrado")
    acesso_clinico.verificar_acesso_paciente(
        db, c.paciente_id, current_user,
        acao=AcaoAuditoria.EDITAR, recurso="consentimento", recurso_id=c.id,
    )
    if c.revogado_em is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Consentimento ja revogado")
    c.revogado_em = _agora()
    db.commit()
    db.refresh(c)
    return _consentimento_dict(c)
