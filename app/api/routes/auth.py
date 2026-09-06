from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlalchemy.orm import Session
from datetime import timedelta, datetime, timezone

from app.database import get_db
from app.models.user import User
from app.models.revoked_token import RevokedToken
from app.schemas.user import UserCreate, UserResponse, UserLogin, Token
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    create_password_reset_token,
    decode_password_reset_token,
    password_reset_fingerprint,
)
from app.schemas.user import validar_senha_forte
from app.services.email_service import send_password_reset_email
from app.core.config import settings
from app.core.rate_limit import check_rate_limit
from app.api.dependencies import get_current_active_user
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/auth", tags=["Authentication"])

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# SEGURANCA: hash dummy para garantir tempo constante no login.
# Quando o email nao existe, ainda verificamos a senha contra este hash para
# evitar enumeracao de usuarios por timing attack.
_DUMMY_PASSWORD_HASH = get_password_hash("dummy-password-for-timing-safety-never-used")

@router.post("/register", status_code=status.HTTP_403_FORBIDDEN)
def register(user_data: UserCreate):
    """
    DESATIVADO (ver docs/ATIVACAO-CONTA-MANUAL.md). Não cria mais usuário
    nenhum - só devolve 403.

    Mesma decisão do checkout (`POST /checkout/iniciar`): autocadastro
    público foi desativado, criação de conta é sempre manual, pelo super
    admin (`POST /planos/admin/ativar-conta`). Este endpoint em particular
    criava `User(role=TEACHER)` **sem `escola_id`** (fica `NULL`) - um
    professor órfão, sem tenant, que nenhuma tela do sistema (escopada por
    escola) consegue depois listar ou gerenciar. Continuava aberto e criando
    esse órfão se chamado direto (API/Postman), mesmo sem nenhuma tela do
    frontend expor esse caminho.

    Rota mantida (não removida) só pra devolver a explicação acima em vez de
    um 404 mudo, caso algum cliente antigo ainda bata aqui.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Cadastro público direto foi desativado. Para criar uma conta, "
            "fale com a equipe (ver /planos)."
        ),
    )

@router.post("/login", response_model=Token)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Login e obtencao de token JWT.
    
    SEGURANCA: 
    - Rate limited a 10 tentativas por minuto por IP (anti brute-force).
    - Usa tempo constante para evitar enumeracao de usuarios.
    - Mesma mensagem de erro e mesmo tempo de resposta para email invalido e senha errada.
    """
    check_rate_limit(
        request, key="login", max_requests=10, window_seconds=60,
        error_message="Muitas tentativas de login. Aguarde um minuto."
    )
    
    user = db.query(User).filter(User.email == form_data.username).first()
    
    # SEGURANCA: SEMPRE executar verify_password (mesmo quando user nao existe)
    # contra um hash dummy, para manter tempo de resposta constante.
    if user:
        valid = verify_password(form_data.password, user.hashed_password)
    else:
        verify_password(form_data.password, _DUMMY_PASSWORD_HASH)
        valid = False
    
    if not valid or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Bloquear login de usuario desativado
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario desativado. Entre em contato com o administrador."
        )
    
    # Criar tokens (A10: access curto + refresh longo)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(data={"sub": user.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
    }

@router.post("/login/json", response_model=Token)
def login_json(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    """
    Login com JSON (alternativa ao form).
    
    SEGURANCA: rate limited + tempo constante (mesmas protecoes do /login).
    """
    check_rate_limit(
        request, key="login", max_requests=10, window_seconds=60,
        error_message="Muitas tentativas de login. Aguarde um minuto."
    )
    
    user = db.query(User).filter(User.email == user_data.email).first()
    
    # Tempo constante - ver comentario em login()
    if user:
        valid = verify_password(user_data.password, user.hashed_password)
    else:
        verify_password(user_data.password, _DUMMY_PASSWORD_HASH)
        valid = False
    
    if not valid or not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario desativado. Entre em contato com o administrador."
        )
    
    # Criar tokens (A10)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user_data.email},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(data={"sub": user_data.email})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
    }

@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """
    Obter informações do usuário atual
    """
    return current_user

@router.get("/test-token")
def test_token(current_user: User = Depends(get_current_active_user)):
    """
    Testar se o token e valido
    """
    return {
        "message": "Token is valid",
        "user_id": current_user.id,
        "email": current_user.email,
        "role": current_user.role
    }


# ============================================
# REFRESH TOKEN (A10)
# ============================================

class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/refresh", response_model=RefreshResponse)
def refresh_access_token(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Troca um refresh token valido por um novo access token.
    
    Fluxo esperado:
    1. Cliente detecta que access token expirou (401)
    2. Cliente chama POST /auth/refresh com o refresh_token guardado
    3. Se valido, recebe novo access_token e continua
    4. Se invalido (expirado ou malformado), cliente precisa fazer login de novo
    """
    # Rate limit - impede abuso do endpoint de refresh
    check_rate_limit(
        request, key="refresh", max_requests=30, window_seconds=60,
        error_message="Muitas renovacoes de token. Aguarde um minuto."
    )
    
    refresh_payload = decode_refresh_token(payload.refresh_token)
    if not refresh_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalido ou expirado. Faca login novamente.",
        )
    
    # Revogacao server-side: um refresh token marcado como revogado (logout) e recusado
    jti = refresh_payload.get("jti")
    if jti and db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessao encerrada. Faca login novamente.",
        )
    
    email: str = refresh_payload.get("sub", "")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token malformado.",
        )
    
    # Verificar se o usuario/aluno ainda existe e esta ativo
    if email.startswith("student:"):
        from app.models.student import Student
        student_email = email.replace("student:", "")
        student = db.query(Student).filter(Student.email == student_email).first()
        if not student or not student.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conta desativada. Faca login novamente.",
            )
        
        new_access = create_access_token(
            data={"sub": email, "student_id": student.id},
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        )
    else:
        user = db.query(User).filter(User.email == email).first()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conta desativada. Faca login novamente.",
            )
        
        new_access = create_access_token(
            data={"sub": email},
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        )
    
    return RefreshResponse(access_token=new_access)


# ============================================
# LOGOUT (revogacao de refresh token)
# ============================================

class LogoutRequest(BaseModel):
    refresh_token: str


@router.post("/logout")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    """
    Logout server-side: revoga o refresh token informado.

    Best-effort e idempotente - responde 200 mesmo se o token ja estiver
    invalido/expirado (nao vaza informacao). Apos o logout, o refresh token
    nao pode mais ser usado em /auth/refresh.
    """
    data = decode_refresh_token(payload.refresh_token)
    if data:
        jti = data.get("jti")
        if jti:
            ja_revogado = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
            if not ja_revogado:
                exp = data.get("exp")
                expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
                db.add(RevokedToken(jti=jti, expires_at=expires_at))
                db.commit()
            # Limpeza oportunista de tokens ja expirados (mantem a tabela enxuta)
            try:
                db.query(RevokedToken).filter(
                    RevokedToken.expires_at.isnot(None),
                    RevokedToken.expires_at < datetime.now(timezone.utc),
                ).delete(synchronize_session=False)
                db.commit()
            except Exception:
                db.rollback()
    return {"message": "Logout efetuado."}


# ============= ENDPOINTS PARA ESTUDANTES =============

@router.post("/student/login", response_model=Token)
def student_login(user_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    """
    Login para estudantes.
    
    SEGURANCA: rate limited + tempo constante.
    """
    from app.models.student import Student
    
    check_rate_limit(
        request, key="student_login", max_requests=10, window_seconds=60,
        error_message="Muitas tentativas de login. Aguarde um minuto."
    )
    
    student = db.query(Student).filter(Student.email == user_data.email).first()
    
    # SEGURANCA: tempo constante - ver comentario em login()
    if student and student.hashed_password:
        valid = verify_password(user_data.password, student.hashed_password)
    else:
        verify_password(user_data.password, _DUMMY_PASSWORD_HASH)
        valid = False
    
    if not valid or not student:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou senha incorretos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not student.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Estudante inativo. Entre em contato com o professor.",
        )
    
    # Criar tokens com prefixo student: para diferenciar (A10)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": f"student:{student.email}", "student_id": student.id},
        expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": f"student:{student.email}", "student_id": student.id}
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
    }

@router.get("/student/me")
def get_current_student_info(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Obter informações do estudante atual
    """
    from app.models.student import Student
    from app.core.security import decode_access_token
    
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    email: str = payload.get("sub")
    if not email or not email.startswith("student:"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token não é de estudante",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Remover prefixo student:
    email = email.replace("student:", "")
    student = db.query(Student).filter(Student.email == email).first()
    
    if not student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Estudante não encontrado"
        )
    
    return {
        "id": student.id,
        "name": student.name,
        "email": student.email,
        "grade_level": student.grade_level,
        "birth_date": student.birth_date,
        "diagnosis": student.diagnosis,
        "profile_data": student.profile_data,
        "is_active": student.is_active
    }


# ============================================
# RECUPERACAO DE SENHA (forgot / reset)
# ============================================

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    nova_senha: str


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """
    Solicita redefinicao de senha. Envia um email com link de reset SE o email existir.

    SEGURANCA:
    - Rate limited (5/hora por IP).
    - Resposta SEMPRE igual (nao revela se o email existe) - anti-enumeracao.
    - Falha no envio de email nao altera a resposta nem quebra o fluxo.
    """
    check_rate_limit(
        request, key="forgot_password", max_requests=5, window_seconds=3600,
        error_message="Muitas solicitacoes de redefinicao. Aguarde 1 hora."
    )

    user = db.query(User).filter(User.email == payload.email).first()
    if user and user.is_active:
        fp = password_reset_fingerprint(user.hashed_password)
        token = create_password_reset_token(user.email, fp)
        reset_link = f"{settings.FRONTEND_URL.rstrip('/')}/redefinir-senha?token={token}"
        send_password_reset_email(user.email, reset_link)

    # Resposta neutra, independente de o email existir ou nao (anti-enumeracao)
    return {"message": "Se o e-mail estiver cadastrado, enviamos as instrucoes para redefinir a senha."}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """
    Redefine a senha a partir de um token de reset valido.

    SEGURANCA:
    - Rate limited (10/hora por IP).
    - Token validado por tipo, expiracao e fingerprint do hash atual
      (o token deixa de valer apos a senha mudar - uso unico de fato).
    """
    check_rate_limit(
        request, key="reset_password", max_requests=10, window_seconds=3600,
        error_message="Muitas tentativas. Aguarde 1 hora."
    )

    data = decode_password_reset_token(payload.token)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Link de redefinicao invalido ou expirado. Solicite um novo."
        )

    email = data.get("sub")
    user = db.query(User).filter(User.email == email).first() if email else None
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Link de redefinicao invalido ou expirado. Solicite um novo."
        )

    # O fingerprint do token deve bater com o hash atual. Se nao bate, o link ja
    # foi usado (a senha mudou) ou e de uma senha antiga.
    if data.get("fp") != password_reset_fingerprint(user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este link ja foi utilizado. Solicite um novo."
        )

    # Valida a politica de senha forte (mensagem limpa em caso de falha)
    try:
        validar_senha_forte(payload.nova_senha)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user.hashed_password = get_password_hash(payload.nova_senha)
    db.commit()

    return {"message": "Senha redefinida com sucesso. Voce ja pode entrar com a nova senha."}
