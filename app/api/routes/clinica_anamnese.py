"""
🏥 AdaptAI - Rotas de anamnese/admissao do paciente (vertical CLINICA).

Gated CLINICA; acesso por paciente (equipe do caso, anti-IDOR). Uma anamnese por
paciente: GET devolve a ficha (ou vazia) e PUT faz upsert.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.dependencies import get_current_user
from app.models.user import User
from app.core.entitlements import requer_modulo, Modulo
from app.services import acesso_clinico
from app.models.clinica_anamnese import Anamnese

router = APIRouter(
    prefix="/clinica",
    tags=["🏥 Clínica (Anamnese)"],
    dependencies=[Depends(requer_modulo(Modulo.CLINICA))],
)

_CAMPOS = [
    "queixa_principal", "historico_gestacional", "historico_desenvolvimento",
    "historico_medico", "historico_familiar", "rotina", "comunicacao",
    "comportamento", "escolaridade", "terapias_anteriores", "medicacoes",
    "observacoes",
    # secoes estruturadas adicionais (028)
    "sono", "alimentacao", "perfil_sensorial", "autonomia_avds", "uso_telas",
    "contexto_social_familiar", "objetivos_familia",
]


class AnamneseIn(BaseModel):
    queixa_principal: Optional[str] = None
    historico_gestacional: Optional[str] = None
    historico_desenvolvimento: Optional[str] = None
    historico_medico: Optional[str] = None
    historico_familiar: Optional[str] = None
    rotina: Optional[str] = None
    comunicacao: Optional[str] = None
    comportamento: Optional[str] = None
    escolaridade: Optional[str] = None
    terapias_anteriores: Optional[str] = None
    medicacoes: Optional[str] = None
    observacoes: Optional[str] = None
    sono: Optional[str] = None
    alimentacao: Optional[str] = None
    perfil_sensorial: Optional[str] = None
    autonomia_avds: Optional[str] = None
    uso_telas: Optional[str] = None
    contexto_social_familiar: Optional[str] = None
    objetivos_familia: Optional[str] = None


def _dict(a: Optional[Anamnese]) -> dict:
    if not a:
        return {c: None for c in _CAMPOS} | {"preenchida": False, "atualizado_em": None}
    d = {c: getattr(a, c) for c in _CAMPOS}
    d["preenchida"] = True
    d["atualizado_em"] = str(a.atualizado_em) if a.atualizado_em else None
    return d


@router.get("/pacientes/{paciente_id}/anamnese")
def obter_anamnese(
    paciente_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    a = db.query(Anamnese).filter(Anamnese.paciente_id == p.id).first()
    return _dict(a)


@router.put("/pacientes/{paciente_id}/anamnese")
def salvar_anamnese(
    paciente_id: int,
    body: AnamneseIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = acesso_clinico.verificar_acesso_paciente(db, paciente_id, current_user)
    a = db.query(Anamnese).filter(Anamnese.paciente_id == p.id).first()
    dados = body.model_dump()
    if a:
        for c in _CAMPOS:
            setattr(a, c, dados.get(c))
    else:
        a = Anamnese(
            escola_id=p.escola_id, paciente_id=p.id,
            preenchido_por_id=current_user.id,
            criado_em=datetime.now(timezone.utc),
            **{c: dados.get(c) for c in _CAMPOS},
        )
        db.add(a)
    db.commit()
    db.refresh(a)
    return _dict(a)
