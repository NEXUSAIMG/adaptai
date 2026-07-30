from pydantic import BaseModel, Field
from typing import Optional, Dict, List
from datetime import date, datetime

class StudentBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=255)
    birth_date: Optional[date] = None
    grade_level: Optional[str] = Field(None, max_length=50, description="Ex: 1º ano, 2º ano, 5º ano")
    
class StudentCreate(StudentBase):
    # TC-017: no cadastro, a serie/ano e OBRIGATORIA (a coluna no banco e NOT NULL).
    # Sobrescreve o Optional herdado do StudentBase para rejeitar valor vazio/nulo
    # com um 422 claro em vez de estourar no INSERT.
    grade_level: str = Field(..., min_length=1, max_length=50, description="Serie/ano escolar (obrigatorio)")
    email: Optional[str] = Field(default=None, description="Email para login do estudante")
    password: Optional[str] = Field(default=None, min_length=6, description="Senha para login do estudante (mínimo 6 caracteres)")
    turma: Optional[str] = Field(default=None, max_length=50, description="Ex: 'A', 'B', 'Manhã'")
    matricula: Optional[str] = Field(default=None, max_length=50, description="Número de matrícula do aluno")
    diagnosis: Optional[Dict] = Field(default=None, description="Ex: {'tea': {'level': 1}, 'tdah': true}")
    profile_data: Optional[Dict] = Field(default=None, description="Dados do perfil: interesses, estilo de aprendizagem, etc")
    notes: Optional[str] = None

class StudentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=255)
    email: Optional[str] = Field(None, description="Email para login do estudante")
    password: Optional[str] = Field(None, min_length=6, description="Nova senha (deixe vazio para não alterar)")
    birth_date: Optional[date] = None
    grade_level: Optional[str] = Field(None, max_length=50)
    turma: Optional[str] = Field(None, max_length=50)
    matricula: Optional[str] = Field(None, max_length=50)
    diagnosis: Optional[Dict] = None
    profile_data: Optional[Dict] = None
    notes: Optional[str] = None

class StudentResponse(StudentBase):
    id: int
    email: Optional[str] = None
    turma: Optional[str] = None
    matricula: Optional[str] = None
    diagnosis: Optional[Dict] = None
    profile_data: Optional[Dict] = None
    notes: Optional[str] = None
    # Derivado do diagnosis (read-only): publico-alvo da Educacao Especial (AEE)
    publico_aee: bool = False
    is_active: bool = True
    foto_path: Optional[str] = None
    created_by_user_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class StudentListResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    grade_level: str
    turma: Optional[str] = None
    matricula: Optional[str] = None
    diagnosis: Optional[Dict] = None
    publico_aee: bool = False
    is_active: bool = True
    foto_path: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True
