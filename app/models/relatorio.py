"""
Modelo de Relatórios de Terapias e Acompanhamento
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, LargeBinary
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.orm import relationship, deferred
from sqlalchemy.sql import func
from app.database import Base


class Relatorio(Base):
    """
    Relatórios de profissionais de saúde/educação
    (Psicopedagogos, Fonoaudiólogos, Psicólogos, Neurologistas, etc.)
    """
    __tablename__ = "relatorios"

    id = Column(Integer, primary_key=True, index=True)
    
    # Relacionamento com aluno
    student_id = Column(Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    
    # Tipo do relatório
    tipo = Column(String(100), nullable=False)  # Ex: "Relatório Psicopedagógico", "Laudo Neurológico"
    
    # Dados do profissional
    profissional_nome = Column(String(200))
    profissional_registro = Column(String(50))  # CRM, CRP, CRFa, CREFITO, etc.
    profissional_especialidade = Column(String(100))
    
    # Datas
    data_emissao = Column(DateTime)
    data_validade = Column(DateTime)
    
    # Diagnósticos
    cid = Column(String(100))  # Códigos CID separados por vírgula
    
    # Resumo e conteúdo
    resumo = Column(Text)  # Resumo extraído pela IA
    
    # Arquivo original
    arquivo_nome = Column(String(255))
    arquivo_tipo = Column(String(50))  # application/pdf, image/jpeg, etc.
    
    # NOVO: Caminho do arquivo no storage (cache local - ver ARQUITETURA-CONTEUDOS.md secao 4)
    arquivo_path = Column(String(500), nullable=True)  # Ex: "relatorio_1_20241219_abc123.pdf"

    # Bytes do laudo original (migration 031). Fonte de verdade a partir daqui -
    # arquivo_path/disco e cache efemero (Railway), some a cada redeploy. deferred
    # pra nao pesar nas listagens (mesmo motivo de Material.conteudo_gerado).
    arquivo_bytes = deferred(Column(MEDIUMBLOB().with_variant(LargeBinary, "sqlite"), nullable=True))

    # ALTERADO: Agora aceita NULL (sistema antigo usava, novo não usa mais)
    arquivo_base64 = Column(Text, nullable=True)  # Arquivo em base64 (DEPRECATED - usar arquivo_path)
    
    # Dados completos extraídos pela IA (JSON)
    dados_extraidos = Column(JSON)
    
    # Condições identificadas (para facilitar filtros)
    condicoes = Column(JSON)  # {"tea": true, "tdah": false, ...}
    
    # Metadados
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey("users.id"))
    
    # Relacionamentos
    student = relationship("Student", back_populates="relatorios")
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Relatorio {self.id} - {self.tipo} - {self.profissional_nome}>"
