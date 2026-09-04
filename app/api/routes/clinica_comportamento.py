"""
🏥 AdaptAI - Rotas de registro de comportamento ABC (vertical CLINICA).

Registro ABC (Antecedente-Comportamento-Consequencia) + metricas. Gated CLINICA;
acesso por paciente (equipe do caso).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.core.entitlements import requer_modulo, Modulo
from app.services import acesso_clinico, analise_funcional_service
from app.models.clinica_core import AcaoAuditoria
from app.models.clinica_comportamento import RegistroComportamento, Intensidade

router = APIRouter(
    prefix="/clinica",
    tags=["🏥 Clínica (Comportamento)"],
    dependencies=[Depends(requer_modulo(Modulo.CLINICA))],
)


def _v(e):
    return e.value if hasattr(e, "value") else e


def _dict(r: RegistroComportamento) -> dict:
    return {
        "id": r.id, "comportamento": r.comportamento,
        "antecedente": r.antecedente, "consequencia": r.consequencia,
        "frequencia": r.frequencia, "duracao_seg": r.duracao_seg,
        "intensidade": _v(r.intensidade), "sessao_id": r.sessao_id,
        "data_hora": r.data_hora.isoformat() if r.data_hora else None,
    }


class ComportamentoCriar(BaseModel):
    comportamento: str = Field(..., min_length=1, max_length=255)
    antecedente: Optional[str] = None
    consequencia: Optional[str] = None
    frequencia: Optional[int] = Field(None, ge=0)
    duracao_seg: Optional[int] = Field(None, ge=0)
    intensidade: Optional[Intensidade] = None
    sessao_id: Optional[int] = None
    data_hora: Optional[datetime] = None


@router.post("/pacientes/{paciente_id}/comportamentos", status_code=status.HTTP_201_CREATED)
def criar_comportamento(
    paciente_id: int,
    body: ComportamentoCriar,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(
        db, paciente_id, current_user, acao=AcaoAuditoria.CRIAR, recurso="comportamento")
    r = RegistroComportamento(
        escola_id=p.escola_id, paciente_id=p.id, sessao_id=body.sessao_id,
        comportamento=body.comportamento, antecedente=body.antecedente,
        consequencia=body.consequencia, frequencia=body.frequencia,
        duracao_seg=body.duracao_seg, intensidade=body.intensidade,
        data_hora=body.data_hora or datetime.now(timezone.utc),
        criado_por_id=current_user.id, criado_em=datetime.now(timezone.utc),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _dict(r)


@router.get("/pacientes/{paciente_id}/comportamentos")
def listar_comportamentos(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    regs = (db.query(RegistroComportamento)
            .filter(RegistroComportamento.paciente_id == p.id)
            .order_by(RegistroComportamento.id.desc()).limit(200).all())
    return [_dict(r) for r in regs]


@router.get("/pacientes/{paciente_id}/comportamentos/analise-funcional")
def analise_funcional(
    paciente_id: int,
    comportamento: Optional[str] = Query(None, description="focar um comportamento-alvo"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hipotese de FUNCAO do comportamento por IA (ABC -> funcao + estrategias)."""
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    q = db.query(RegistroComportamento).filter(RegistroComportamento.paciente_id == p.id)
    if comportamento:
        q = q.filter(RegistroComportamento.comportamento == comportamento)
    regs = q.order_by(RegistroComportamento.id.desc()).limit(50).all()
    if len(regs) < 2:
        return {
            "funcao_provavel": "INDETERMINADA", "confianca": None,
            "padrao": "Registre ao menos 2 ocorrencias ABC para uma analise confiavel.",
            "estrategias": [], "sem_dados": True,
        }
    dados = [{
        "comportamento": r.comportamento, "antecedente": r.antecedente,
        "consequencia": r.consequencia, "frequencia": r.frequencia,
        "duracao_seg": r.duracao_seg, "intensidade": _v(r.intensidade),
    } for r in regs]
    return analise_funcional_service.analisar(comportamento, dados)

