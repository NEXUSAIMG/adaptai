"""
Rotas de Materiais Adaptados para o Portal do Aluno.

TC-027/028/081 (ponte Material -> Aluno): os materiais adaptados gerados pelo
professor sao gravados em `materiais_adaptados_gerados` com o `student_id` do
aluno, mas ate entao NAO havia nenhuma rota que os expusesse no portal do
aluno (o portal so lia a tabela de junção `materiais_alunos`). Estes endpoints
fecham essa lacuna: o aluno logado passa a listar e abrir os materiais
adaptados que foram gerados para ele.

Sem alteracao de schema: reutiliza a coluna student_id ja existente.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.student import Student
from app.models.material_adaptado_gerado import MaterialAdaptadoGerado
from app.api.dependencies import get_current_student

router = APIRouter(
    prefix="/student/materiais-adaptados",
    tags=["Student - Materiais Adaptados"],
)


@router.get("/")
async def listar_meus_materiais_adaptados(
    current_student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Lista os materiais adaptados gerados para o aluno logado (mais recentes
    primeiro). Retorna apenas os metadados - o conteudo completo vem no detalhe.
    """
    materiais = (
        db.query(MaterialAdaptadoGerado)
        .filter(MaterialAdaptadoGerado.student_id == current_student.id)
        .order_by(MaterialAdaptadoGerado.created_at.desc())
        .all()
    )

    return [
        {
            "id": m.id,
            "disciplina": m.disciplina,
            "serie": m.serie,
            "conteudo": m.conteudo,
            "tipos_material": m.tipos_material,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in materiais
    ]


@router.get("/{material_id}")
async def obter_meu_material_adaptado(
    material_id: int,
    current_student: Student = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Retorna um material adaptado completo (resultado_json) para o aluno logado.

    SEGURANCA: so devolve se o material pertencer ao proprio aluno autenticado
    (evita IDOR - um aluno abrir material de outro trocando o id na URL).
    """
    material = (
        db.query(MaterialAdaptadoGerado)
        .filter(MaterialAdaptadoGerado.id == material_id)
        .first()
    )

    if not material or material.student_id != current_student.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material nao encontrado",
        )

    return {
        "id": material.id,
        "disciplina": material.disciplina,
        "serie": material.serie,
        "conteudo": material.conteudo,
        "tipos_material": material.tipos_material,
        "resultado": material.resultado_json,
        "created_at": material.created_at.isoformat() if material.created_at else None,
    }
