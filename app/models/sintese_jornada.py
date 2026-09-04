"""
🧭 AdaptAI - Sintese da Jornada Terapeutica (perfil vivo do aluno/paciente).

A "jornada terapeutica" consolida os relatorios/laudos do aluno numa leitura
temporal. Ate aqui isso era efemero (so cache). Este model PERSISTE uma sintese
compacta e reutilizavel por aluno, que:
  - vira historico (versionada por data de atualizacao);
  - alimenta como PARAMETRO os geradores de tarefa (materiais, provas,
    atividades do PEI, programa de casa) via sintese_jornada_service.contexto_para_prompt.

`fonte_hash` guarda um hash dos relatorios usados; quando novos relatorios
entram, o hash muda e a sintese e considerada desatualizada (regenera sob demanda
ou no botao "atualizar").
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from datetime import datetime, timezone
from app.database import Base


def _agora():
    return datetime.now(timezone.utc)


class SinteseJornada(Base):
    __tablename__ = "sinteses_jornada"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)

    # Texto compacto, pronto para injetar em prompts de geracao (~150 palavras).
    resumo = Column(Text, nullable=True)
    # Estrutura completa (linha do tempo, pontos fortes, dificuldades, recomendacoes)
    # para a visualizacao e para quem quiser detalhar.
    dados_json = Column(JSON, nullable=True)

    # Controle de atualizacao: hash das fontes + quantos relatorios entraram.
    fonte_hash = Column(String(64), nullable=True)
    n_relatorios = Column(Integer, default=0)

    gerado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    criado_em = Column(DateTime, default=_agora)
    atualizado_em = Column(DateTime, default=_agora, onupdate=_agora)
