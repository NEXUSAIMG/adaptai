"""
🏥 AdaptAI - Model de anamnese/admissao (vertical CLINICA).

Espelha 022_clinica_anamnese.sql. Uma anamnese por paciente (a porta de entrada
do prontuario). Campos em blocos, todos texto livre no MVP.
"""
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from datetime import datetime, timezone
from app.database import Base


def _agora():
    return datetime.now(timezone.utc)


class Anamnese(Base):
    __tablename__ = "anamneses"

    id = Column(Integer, primary_key=True, index=True)
    escola_id = Column(Integer, ForeignKey("escolas.id", ondelete="CASCADE"), nullable=False)
    paciente_id = Column(Integer, ForeignKey("pacientes.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    queixa_principal = Column(Text, nullable=True)
    historico_gestacional = Column(Text, nullable=True)
    historico_desenvolvimento = Column(Text, nullable=True)
    historico_medico = Column(Text, nullable=True)
    historico_familiar = Column(Text, nullable=True)
    rotina = Column(Text, nullable=True)
    comunicacao = Column(Text, nullable=True)
    comportamento = Column(Text, nullable=True)
    escolaridade = Column(Text, nullable=True)
    terapias_anteriores = Column(Text, nullable=True)
    medicacoes = Column(Text, nullable=True)
    observacoes = Column(Text, nullable=True)
    # Secoes estruturadas adicionais (028) - blocos que faltavam na ficha.
    sono = Column(Text, nullable=True)
    alimentacao = Column(Text, nullable=True)
    perfil_sensorial = Column(Text, nullable=True)
    autonomia_avds = Column(Text, nullable=True)
    uso_telas = Column(Text, nullable=True)
    contexto_social_familiar = Column(Text, nullable=True)
    objetivos_familia = Column(Text, nullable=True)
    preenchido_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    criado_em = Column(DateTime, default=_agora)
    atualizado_em = Column(DateTime, default=_agora, onupdate=_agora)
