"""
🏫 AdaptAI - Gestão de professores pela escola (admin do tenant).

Onboarding de professores de uma escola: criar 1 a 1, importar em massa por CSV,
ativar/desativar e reenviar convite. Cada professor nasce com senha provisoria E
recebe (best-effort) um convite por e-mail com link para definir a propria senha.
Escopo: admin/coordenador da escola; sempre amarrado ao escola_id do usuario.
"""
import csv
import io
import secrets

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.dependencies import get_current_active_user
from app.models.user import User, UserRole
from app.core.security import (
    get_password_hash, create_password_reset_token, password_reset_fingerprint,
)
from app.core.config import settings
from app.core.tenant import enforce_limite_professores
from app.services.email_service import _enviar_email

router = APIRouter(prefix="/escolas", tags=["🏫 Escolas (Professores)"])

_ADMIN = (UserRole.ADMIN, UserRole.COORDINATOR, UserRole.SUPER_ADMIN)


def _exigir_admin(current_user: User) -> int:
    if current_user.role not in _ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Apenas admin/coordenador da escola.")
    if not current_user.escola_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Usuario sem escola vinculada.")
    return current_user.escola_id


def _gen_senha() -> str:
    return secrets.token_urlsafe(9)


def _enviar_convite(user: User) -> bool:
    """Best-effort: nunca derruba o fluxo se o e-mail falhar."""
    try:
        fp = password_reset_fingerprint(user.hashed_password)
        token = create_password_reset_token(user.email, fp)
        link = f"{settings.FRONTEND_URL.rstrip('/')}/redefinir-senha?token={token}"
        html = (
            f"<p>Ola, {user.name}!</p>"
            "<p>Voce foi cadastrado(a) no ADAPT AI pela sua escola. "
            "Para acessar, defina sua senha no link abaixo:</p>"
            f'<p><a href="{link}">Definir minha senha</a></p>'
            "<p>Se preferir, use a senha provisoria informada pela sua escola e "
            "troque depois em 'Esqueci minha senha'.</p>"
        )
        return bool(_enviar_email(user.email, "Seu acesso ao ADAPT AI", html))
    except Exception:
        return False


def _criar_professor(db: Session, escola_id: int, nome: str, email: str):
    senha = _gen_senha()
    u = User(
        name=nome, email=email, hashed_password=get_password_hash(senha),
        role=UserRole.TEACHER, escola_id=escola_id, is_active=True,
    )
    db.add(u)
    db.flush()  # garante id + hashed_password para o token do convite
    enviado = _enviar_convite(u)
    return u, senha, enviado


class ProfessorIn(BaseModel):
    nome: str
    email: str


@router.get("/minha/professores")
def listar_professores(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    eid = _exigir_admin(current_user)
    profs = (
        db.query(User)
        .filter(User.escola_id == eid, User.role == UserRole.TEACHER)
        .order_by(User.name)
        .all()
    )
    return [{
        "id": u.id, "nome": u.name, "email": u.email, "ativo": bool(u.is_active),
        "criado_em": u.created_at.isoformat() if u.created_at else None,
    } for u in profs]


@router.post("/minha/professores", status_code=status.HTTP_201_CREATED)
def criar_professor(body: ProfessorIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    eid = _exigir_admin(current_user)
    nome = (body.nome or "").strip()
    email = (body.email or "").strip().lower()
    if len(nome) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nome muito curto.")
    if "@" not in email or "." not in email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "E-mail invalido.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ja existe usuario com este e-mail.")
    enforce_limite_professores(db, current_user)
    u, senha, enviado = _criar_professor(db, eid, nome, email)
    db.commit()
    return {"id": u.id, "nome": u.name, "email": u.email, "senha_provisoria": senha, "convite_enviado": enviado}


def _campo(row, *nomes):
    for k, v in row.items():
        if (k or "").strip().lower() in nomes:
            return (v or "").strip()
    return ""


@router.post("/minha/professores/importar-csv")
def importar_professores_csv(
    arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """CSV com cabecalho: nome (ou name) e email (ambos obrigatorios). ',' ou ';'."""
    eid = _exigir_admin(current_user)
    if not arquivo.filename or not arquivo.filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Envie um arquivo .csv")
    raw = arquivo.file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Arquivo muito grande. Maximo: 5MB")
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")
    if not texto.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Arquivo vazio")
    primeira = texto.splitlines()[0]
    delim = ";" if primeira.count(";") > primeira.count(",") else ","
    reader = csv.DictReader(io.StringIO(texto), delimiter=delim)
    cab = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    if "email" not in cab or not ("nome" in cab or "name" in cab):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "O CSV precisa das colunas 'nome' e 'email'.")

    criados, erros = [], []
    ignorados = 0
    emails_lote = set()
    for i, row in enumerate(reader, start=2):
        nome = _campo(row, "nome", "name")
        email = _campo(row, "email").lower()
        if len(nome) < 3:
            ignorados += 1; erros.append({"linha": i, "motivo": "nome ausente/curto"}); continue
        if "@" not in email or "." not in email:
            ignorados += 1; erros.append({"linha": i, "motivo": "email invalido/ausente"}); continue
        if email in emails_lote:
            ignorados += 1; erros.append({"linha": i, "motivo": f"email duplicado no arquivo: {email}"}); continue
        if db.query(User).filter(User.email == email).first():
            ignorados += 1; erros.append({"linha": i, "motivo": f"email ja cadastrado: {email}"}); continue
        emails_lote.add(email)
        try:
            enforce_limite_professores(db, current_user)
        except HTTPException as e:
            erros.append({"linha": i, "motivo": e.detail}); ignorados += 1
            break  # limite do plano atingido: nao adianta seguir as proximas linhas
        u, senha, enviado = _criar_professor(db, eid, nome, email)
        criados.append({"nome": nome, "email": email, "senha_provisoria": senha, "convite_enviado": enviado})
    db.commit()
    return {"total": len(criados) + ignorados, "criados": criados, "ignorados": ignorados, "erros": erros[:50]}


class AtivoIn(BaseModel):
    ativo: bool


def _get_prof(db, prof_id, eid):
    u = db.query(User).filter(
        User.id == prof_id, User.escola_id == eid, User.role == UserRole.TEACHER
    ).first()
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Professor nao encontrado nesta escola.")
    return u


@router.patch("/minha/professores/{prof_id}")
def alterar_professor(prof_id: int, body: AtivoIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    eid = _exigir_admin(current_user)
    u = _get_prof(db, prof_id, eid)
    u.is_active = body.ativo
    db.commit()
    return {"id": u.id, "ativo": bool(u.is_active)}


@router.post("/minha/professores/{prof_id}/reenviar-convite")
def reenviar_convite(prof_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    eid = _exigir_admin(current_user)
    u = _get_prof(db, prof_id, eid)
    senha = _gen_senha()
    u.hashed_password = get_password_hash(senha)
    db.flush()
    enviado = _enviar_convite(u)
    db.commit()
    return {"id": u.id, "senha_provisoria": senha, "convite_enviado": enviado}
