"""
🎓 ROTAS DE ESTUDANTES - AdaptAI
Gerenciamento de alunos com filtro por professor/escola
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, asc, desc
from typing import List, Literal, Optional
import csv
import io
import uuid
from pathlib import Path
from datetime import datetime

from app.database import get_db
from app.models.student import Student
from app.models.user import User, UserRole
from app.schemas.student import StudentCreate, StudentUpdate, StudentResponse, StudentListResponse
from app.api.dependencies import get_current_active_user
from app.core.tenant import enforce_limite_alunos

router = APIRouter(prefix="/students", tags=["Students"])

# Diretorio das fotos dos alunos (backend/storage/student_photos)
STUDENT_PHOTOS_DIR = Path(__file__).parent.parent.parent.parent / "storage" / "student_photos"
STUDENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# Formatos de imagem aceitos para a foto do aluno
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _parse_data(valor: str):
    """Interpreta data em YYYY-MM-DD, DD/MM/YYYY ou DD-MM-YYYY. Retorna None se invalida."""
    valor = (valor or "").strip()
    if not valor:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor, fmt).date()
        except ValueError:
            continue
    return None


def get_students_query(db: Session, current_user: User):
    """
    Retorna query base de alunos baseado no papel do usuário:
    - SUPER_ADMIN: Todos os alunos
    - ADMIN/COORDINATOR: Alunos da escola
    - TEACHER: Apenas seus próprios alunos
    """
    base_query = db.query(Student)
    
    if current_user.role == UserRole.SUPER_ADMIN:
        # Super admin vê todos
        return base_query
    
    elif current_user.role in [UserRole.ADMIN, UserRole.COORDINATOR]:
        # Admin/Coord vê alunos da sua escola
        if current_user.escola_id:
            return base_query.filter(Student.escola_id == current_user.escola_id)
        else:
            # Se não tem escola, vê só os seus
            return base_query.filter(Student.created_by_user_id == current_user.id)
    
    else:
        # Professor vê apenas seus alunos
        return base_query.filter(Student.created_by_user_id == current_user.id)


@router.post("/", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(
    student_data: StudentCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    ➕ Criar um novo estudante (com ou sem login)
    
    O aluno será automaticamente associado ao professor que o criou
    e à escola do professor (se houver).
    """
    # Limite de plano (soft): bloqueia se a escola atingiu o limite de alunos.
    # Nao afeta usuarios sem escola/assinatura ativa (grandfather).
    enforce_limite_alunos(db, current_user)

    from app.core.security import get_password_hash
    
    # Verificar se email já existe
    if student_data.email:
        existing = db.query(Student).filter(Student.email == student_data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email já cadastrado"
            )
    
    # Hash da senha se fornecida
    hashed_password = None
    if student_data.password:
        hashed_password = get_password_hash(student_data.password)
    
    new_student = Student(
        name=student_data.name,
        email=student_data.email,
        hashed_password=hashed_password,
        is_active=True,
        birth_date=student_data.birth_date,
        grade_level=student_data.grade_level,  # TC-017: agora obrigatorio no schema (StudentCreate)
        turma=student_data.turma if hasattr(student_data, 'turma') else None,
        matricula=student_data.matricula if hasattr(student_data, 'matricula') else None,
        diagnosis=student_data.diagnosis,
        profile_data=student_data.profile_data,
        notes=student_data.notes,
        created_by_user_id=current_user.id,
        escola_id=current_user.escola_id  # Herda a escola do professor
    )
    
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    
    return new_student


@router.get("/", response_model=List[StudentListResponse])
def list_students(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    grade_level: str = Query(None, description="Filtrar por ano escolar"),
    turma: str = Query(None, description="Filtrar por turma"),
    search: str = Query(None, description="Buscar por nome"),
    todos: bool = Query(False, description="Admin: listar todos da escola"),
    arquivados: bool = Query(False, description="Listar apenas alunos arquivados (inativos)"),
    ordenar_por: Literal["name", "grade_level", "created_at"] = Query(
        "name", description="Campo de ordenação"
    ),
    direcao: Literal["asc", "desc"] = Query("asc", description="Direção da ordenação"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📋 Listar estudantes

    - **Professor**: Vê apenas seus próprios alunos
    - **Coordenador/Admin**: Vê alunos da escola (ou só seus se `todos=False`)
    - **Super Admin**: Vê todos os alunos do sistema

    Filtros disponíveis:
    - `grade_level`: Filtrar por série (ex: "5º ano")
    - `turma`: Filtrar por turma (ex: "A", "Manhã")
    - `search`: Buscar por nome
    - `todos`: Admin pode ver todos da escola

    Ordenação (`ordenar_por` + `direcao`, default `name`/`asc`, comportamento
    anterior preservado): TC-107.
    """
    # Obter query base baseada no papel
    if todos and current_user.role in [UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN]:
        query = get_students_query(db, current_user)
    else:
        # Professor sempre vê só seus alunos
        query = db.query(Student).filter(Student.created_by_user_id == current_user.id)
    
    # Arquivados: por padrao mostra apenas ativos (esconde arquivados/soft-deleted)
    if arquivados:
        query = query.filter(Student.is_active.is_(False))
    else:
        query = query.filter(Student.is_active.isnot(False))
    
    # Filtros
    if grade_level:
        query = query.filter(Student.grade_level == grade_level)
    
    if turma:
        query = query.filter(Student.turma == turma)
    
    if search:
        query = query.filter(Student.name.ilike(f"%{search}%"))
    
    # Ordenar (allowlist via Literal do parametro - TC-107)
    coluna = getattr(Student, ordenar_por)
    query = query.order_by(desc(coluna) if direcao == "desc" else asc(coluna))

    students = query.offset(skip).limit(limit).all()
    return students


@router.get("/meus", response_model=List[StudentListResponse])
def list_my_students(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str = Query(None, description="Buscar por nome"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    👨‍🏫 Listar APENAS meus alunos (professor)
    
    Útil para admins que também são professores e querem ver
    apenas os alunos que eles mesmos cadastraram.
    """
    query = db.query(Student).filter(
        Student.created_by_user_id == current_user.id,
        Student.is_active.isnot(False)
    )
    
    if search:
        query = query.filter(Student.name.ilike(f"%{search}%"))
    
    query = query.order_by(Student.name)
    
    students = query.offset(skip).limit(limit).all()
    return students


@router.get("/escola", response_model=List[StudentListResponse])
def list_school_students(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    grade_level: str = Query(None, description="Filtrar por ano escolar"),
    turma: str = Query(None, description="Filtrar por turma"),
    professor_id: int = Query(None, description="Filtrar por professor"),
    search: str = Query(None, description="Buscar por nome"),
    arquivados: bool = Query(False, description="Listar apenas alunos arquivados (inativos)"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🏫 Listar todos os alunos da escola (Admin/Coordenador)
    
    Apenas Admin, Coordenador e Super Admin podem acessar.
    """
    # Verificar permissão
    if current_user.role not in [UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas Admin ou Coordenador podem ver todos os alunos da escola"
        )
    
    query = get_students_query(db, current_user)
    
    # Arquivados: por padrao apenas ativos
    if arquivados:
        query = query.filter(Student.is_active.is_(False))
    else:
        query = query.filter(Student.is_active.isnot(False))
    
    # Filtros
    if grade_level:
        query = query.filter(Student.grade_level == grade_level)
    
    if turma:
        query = query.filter(Student.turma == turma)
    
    if professor_id:
        query = query.filter(Student.created_by_user_id == professor_id)
    
    if search:
        query = query.filter(Student.name.ilike(f"%{search}%"))
    
    query = query.order_by(Student.name)
    
    students = query.offset(skip).limit(limit).all()
    return students


@router.get("/stats")
def get_students_stats(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📊 Estatísticas dos alunos
    """
    from sqlalchemy import func
    
    # Meus alunos
    meus_alunos = db.query(func.count(Student.id)).filter(
        Student.created_by_user_id == current_user.id
    ).scalar()
    
    # Total da escola (se admin)
    total_escola = 0
    if current_user.role in [UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN]:
        query = get_students_query(db, current_user)
        total_escola = query.count()
    
    # Por série (meus alunos)
    por_serie = db.query(
        Student.grade_level,
        func.count(Student.id)
    ).filter(
        Student.created_by_user_id == current_user.id
    ).group_by(Student.grade_level).all()
    
    # Por turma (meus alunos)
    por_turma = db.query(
        Student.turma,
        func.count(Student.id)
    ).filter(
        Student.created_by_user_id == current_user.id,
        Student.turma.isnot(None)
    ).group_by(Student.turma).all()
    
    return {
        "meus_alunos": meus_alunos,
        "total_escola": total_escola,
        "por_serie": {serie: count for serie, count in por_serie if serie},
        "por_turma": {turma: count for turma, count in por_turma if turma}
    }


@router.get("/turmas")
def listar_minhas_turmas(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🧑‍🏫 Minhas turmas — agrupa os alunos por (série, turma).

    Visão derivada: não existe entidade Turma no banco. A "turma" do professor é
    o conjunto série+turma dos alunos que ele acompanha. Mesmo escopo de
    get_students_query (professor vê os seus; admin/coordenador vê a escola;
    super admin vê tudo). Só alunos ativos.
    """
    alunos = (
        get_students_query(db, current_user)
        .filter(Student.is_active.isnot(False))
        .order_by(Student.name)
        .all()
    )

    grupos: dict = {}
    for aluno in alunos:
        chave = (aluno.grade_level or "Sem série", aluno.turma or "Sem turma")
        grupos.setdefault(chave, []).append({
            "id": aluno.id,
            "name": aluno.name,
            "email": aluno.email,
            "grade_level": aluno.grade_level,
            "turma": aluno.turma,
            "foto_path": aluno.foto_path,
        })

    return [
        {
            "serie": serie,
            "turma": turma,
            "total_alunos": len(lista),
            "alunos": lista,
        }
        for (serie, turma), lista in sorted(grupos.items())
    ]


@router.post("/importar-csv")
def importar_alunos_csv(
    arquivo: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📥 Importar alunos em lote a partir de um arquivo CSV.

    Colunas aceitas no cabecalho (em qualquer ordem): name (obrigatorio), email,
    grade_level, turma, matricula, birth_date (YYYY-MM-DD ou DD/MM/YYYY), notes.
    Aceita separador ',' ou ';'. Linhas invalidas ou duplicadas sao puladas e
    reportadas no resultado.
    """
    # Limite de plano: falha rapido se a escola ja esta no limite de alunos
    enforce_limite_alunos(db, current_user)

    if not arquivo.filename or not arquivo.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Envie um arquivo .csv")

    raw = arquivo.file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande. Máximo: 5MB")

    # Decodificar (utf-8 com BOM e o caso comum do Excel; latin-1 como fallback)
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")

    if not texto.strip():
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    # Detectar separador (',' ou ';') pela primeira linha
    primeira_linha = texto.splitlines()[0]
    delimiter = ";" if primeira_linha.count(";") > primeira_linha.count(",") else ","

    reader = csv.DictReader(io.StringIO(texto), delimiter=delimiter)
    cabecalhos = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    if "name" not in cabecalhos and "nome" not in cabecalhos:
        raise HTTPException(
            status_code=400,
            detail="O CSV precisa de um cabecalho com ao menos a coluna 'name' (ou 'nome')"
        )

    def _campo(row, *nomes):
        for k, v in row.items():
            if (k or "").strip().lower() in nomes:
                return (v or "").strip()
        return ""

    total = 0
    criados = 0
    ignorados = 0
    erros = []
    emails_no_lote = set()

    for i, row in enumerate(reader, start=2):  # linha 1 = cabecalho
        total += 1
        nome = _campo(row, "name", "nome")
        if not nome or len(nome) < 3:
            ignorados += 1
            erros.append({"linha": i, "motivo": "nome ausente ou muito curto"})
            continue

        email = _campo(row, "email") or None
        if email:
            if email.lower() in emails_no_lote:
                ignorados += 1
                erros.append({"linha": i, "motivo": f"email duplicado no arquivo: {email}"})
                continue
            if db.query(Student).filter(Student.email == email).first():
                ignorados += 1
                erros.append({"linha": i, "motivo": f"email ja cadastrado: {email}"})
                continue
            emails_no_lote.add(email.lower())

        nascimento = None
        bd = _campo(row, "birth_date", "data_nascimento", "nascimento")
        if bd:
            nascimento = _parse_data(bd)
            if nascimento is None:
                erros.append({"linha": i, "motivo": f"data de nascimento ignorada (formato invalido): {bd}"})

        novo = Student(
            name=nome,
            email=email,
            grade_level=_campo(row, "grade_level", "serie", "ano") or "Não especificado",
            turma=_campo(row, "turma") or None,
            matricula=_campo(row, "matricula") or None,
            birth_date=nascimento,
            notes=_campo(row, "notes", "observacoes") or None,
            is_active=True,
            created_by_user_id=current_user.id,
            escola_id=current_user.escola_id,
        )
        db.add(novo)
        criados += 1

    db.commit()
    return {
        "success": True,
        "total_linhas": total,
        "criados": criados,
        "ignorados": ignorados,
        "erros": erros,
    }


@router.get("/{student_id}", response_model=StudentResponse)
def get_student(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🔍 Obter detalhes de um estudante específico
    """
    # Verificar acesso baseado no papel
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado ou você não tem permissão para acessá-lo"
        )
    
    return student


@router.put("/{student_id}", response_model=StudentResponse)
def update_student(
    student_id: int,
    student_data: StudentUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    ✏️ Atualizar dados de um estudante (incluindo email e senha)
    """
    from app.core.security import get_password_hash
    
    # Verificar acesso
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado ou você não tem permissão"
        )
    
    # Verificar se o novo email já existe (se estiver alterando)
    if student_data.email and student_data.email != student.email:
        existing = db.query(Student).filter(
            Student.email == student_data.email,
            Student.id != student_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email já cadastrado por outro estudante"
            )
    
    # Atualizar campos (exceto password que precisa de hash)
    update_data = student_data.model_dump(exclude_unset=True)
    
    # Tratar senha separadamente
    if 'password' in update_data:
        password = update_data.pop('password')
        if password:  # Só atualiza se não for vazio
            student.hashed_password = get_password_hash(password)
    
    # Atualizar outros campos
    for field, value in update_data.items():
        setattr(student, field, value)
    
    db.commit()
    db.refresh(student)
    
    return student


@router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🗑️ Deletar um estudante
    
    Marca o aluno como inativo (is_active=False) em vez de apagar, preservando
    todo o historico vinculado (relatorios, redacoes, provas, materiais). Use
    POST /{id}/restaurar para reverter, ou DELETE /{id}/permanente para apagar
    de vez. Apenas o professor que criou ou Admin pode arquivar.
    """
    # Verificar se é o criador ou admin
    student = db.query(Student).filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado"
        )
    
    # Verificar permissão
    pode_deletar = (
        student.created_by_user_id == current_user.id or
        current_user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]
    )
    
    if not pode_deletar:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para deletar este aluno"
        )
    
    # Soft-delete: arquiva o aluno (preserva relatorios, redacoes, provas, materiais).
    student.is_active = False
    db.commit()

    return None


@router.post("/{student_id}/restaurar", response_model=StudentResponse)
def restore_student(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """♻️ Restaurar um aluno arquivado (is_active=True)."""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não encontrado")
    pode = (
        student.created_by_user_id == current_user.id or
        current_user.role in [UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN]
    )
    if not pode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para restaurar este aluno")
    student.is_active = True
    db.commit()
    db.refresh(student)
    return student


@router.delete("/{student_id}/permanente", status_code=status.HTTP_204_NO_CONTENT)
def hard_delete_student(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    ⛔ Excluir um aluno DEFINITIVAMENTE (e todo o historico vinculado).

    Acao irreversivel. Apenas o professor que criou ou Admin pode executar.
    """
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não encontrado")
    pode = (
        student.created_by_user_id == current_user.id or
        current_user.role in [UserRole.ADMIN, UserRole.SUPER_ADMIN]
    )
    if not pode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão para excluir este aluno")

    # Remove a foto do disco, se houver
    if student.foto_path:
        caminho = STUDENT_PHOTOS_DIR / student.foto_path
        try:
            if caminho.exists():
                caminho.unlink()
        except OSError:
            pass

    db.delete(student)
    db.commit()
    return None


@router.post("/{student_id}/transferir")
def transfer_student(
    student_id: int,
    novo_professor_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🔄 Transferir aluno para outro professor (Admin/Coordenador)
    """
    # Verificar permissão
    if current_user.role not in [UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas Admin ou Coordenador podem transferir alunos"
        )
    
    # Buscar aluno
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado"
        )
    
    # Verificar novo professor
    novo_professor = db.query(User).filter(User.id == novo_professor_id).first()
    
    if not novo_professor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Professor não encontrado"
        )
    
    # Verificar se professor é da mesma escola (se não for super admin)
    if current_user.role != UserRole.SUPER_ADMIN:
        if novo_professor.escola_id != current_user.escola_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="O professor deve ser da mesma escola"
            )
    
    # Transferir
    professor_anterior = student.created_by_user_id
    student.created_by_user_id = novo_professor_id
    db.commit()
    
    return {
        "success": True,
        "message": f"Aluno {student.name} transferido com sucesso",
        "professor_anterior_id": professor_anterior,
        "novo_professor_id": novo_professor_id
    }


@router.get("/{student_id}/applications")
def get_student_applications(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📋 Obter todas as aplicações de um estudante
    """
    from app.models.application import Application
    
    # Verificar acesso
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado ou você não tem permissão"
        )
    
    applications = db.query(Application).filter(
        Application.student_id == student_id
    ).all()
    
    return applications


@router.get("/{student_id}/performance")
def get_student_performance_history(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📊 Obter histórico de performance de um estudante
    """
    from app.models.performance import PerformanceAnalysis
    
    # Verificar acesso
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aluno não encontrado ou você não tem permissão"
        )
    
    analyses = db.query(PerformanceAnalysis).filter(
        PerformanceAnalysis.student_id == student_id
    ).order_by(PerformanceAnalysis.analyzed_at.desc()).all()
    
    return analyses


@router.post("/{student_id}/foto", response_model=StudentResponse)
def upload_foto_aluno(
    student_id: int,
    arquivo: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """📷 Enviar/atualizar a foto do aluno (JPG, PNG ou WebP, max 5MB)."""
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não encontrado ou sem permissão")

    extensao = ALLOWED_IMAGE_TYPES.get(arquivo.content_type)
    if not extensao:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use JPG, PNG ou WebP.")

    conteudo = arquivo.file.read()
    if len(conteudo) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande. Máximo: 5MB")

    # Remove a foto anterior, se houver
    if student.foto_path:
        antigo = STUDENT_PHOTOS_DIR / student.foto_path
        try:
            if antigo.exists():
                antigo.unlink()
        except OSError:
            pass

    nome_arquivo = f"aluno_{student_id}_{uuid.uuid4().hex[:8]}{extensao}"
    destino = STUDENT_PHOTOS_DIR / nome_arquivo
    with open(destino, "wb") as f:
        f.write(conteudo)

    student.foto_path = nome_arquivo
    db.commit()
    db.refresh(student)
    return student


@router.get("/{student_id}/foto")
def get_foto_aluno(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """🖼️ Retorna a imagem da foto do aluno."""
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não encontrado ou sem permissão")
    if not student.foto_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não tem foto")
    caminho = STUDENT_PHOTOS_DIR / student.foto_path
    if not caminho.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo da foto não encontrado")
    return FileResponse(str(caminho))


@router.delete("/{student_id}/foto", status_code=status.HTTP_204_NO_CONTENT)
def delete_foto_aluno(
    student_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """🗑️ Remove a foto do aluno."""
    query = get_students_query(db, current_user)
    student = query.filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aluno não encontrado ou sem permissão")
    if student.foto_path:
        caminho = STUDENT_PHOTOS_DIR / student.foto_path
        try:
            if caminho.exists():
                caminho.unlink()
        except OSError:
            pass
        student.foto_path = None
        db.commit()
    return None
