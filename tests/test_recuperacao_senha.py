"""
Testes do fluxo de recuperacao de senha (app/api/routes/auth.py):
POST /auth/forgot-password -> link no e-mail -> POST /auth/reset-password.

Trava (validacao da migracao do frontend para o Railway, T-16/T-17 do roteiro):
  - o link enviado por e-mail usa FRONTEND_URL (o dominio do frontend vigente);
  - resposta identica exista o e-mail ou nao (anti-enumeracao), e falha no envio
    nao muda a resposta;
  - o token do link redefine a senha e a nova senha passa a valer;
  - o mesmo link nao funciona duas vezes; token invalido e senha fraca = 400.

Estrategia: sqlite em memoria + app minimo so com o router de auth; o envio de
e-mail e substituido por um espiao (nada sai pela rede).
"""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registra todos os models no metadata
from app.api.routes import auth
from app.core.rate_limit import check_rate_limit  # noqa: F401
from app.core.security import get_password_hash, verify_password
from app.database import Base, get_db
from app.models.user import User, UserRole

FRONT = "https://adaptai-frontend-production.up.railway.app"
EMAIL = "prof.teste@escola.example"
SENHA_ANTIGA = "SenhaAntiga@123"
SENHA_NOVA = "NovaSenha@456"
NEUTRA = "Se o e-mail estiver cadastrado, enviamos as instrucoes para redefinir a senha."


@pytest.fixture(scope="module")
def Sessao():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        Base.metadata.create_all(eng)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Schema nao montavel em sqlite: {e}")
    yield sessionmaker(autocommit=False, autoflush=False, bind=eng)
    Base.metadata.drop_all(eng)


@pytest.fixture(scope="module")
def client(Sessao):
    db = Sessao()
    db.add(User(name="Prof Teste", email=EMAIL, hashed_password=get_password_hash(SENHA_ANTIGA),
                role=UserRole.TEACHER, is_active=True))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(auth.router)

    def _db():
        s = Sessao()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.fixture(autouse=True)
def isola(monkeypatch):
    """Espiao de e-mail, FRONTEND_URL do Railway e rate limit desligado (testa o fluxo, nao o limiter)."""
    enviados = []
    monkeypatch.setattr(auth, "send_password_reset_email", lambda para, link: enviados.append((para, link)) or True)
    monkeypatch.setattr(auth.settings, "FRONTEND_URL", FRONT + "/")  # barra final de proposito
    monkeypatch.setattr(auth, "check_rate_limit", lambda *a, **k: None)
    return enviados


def _token_do_link(link):
    return parse_qs(urlparse(link).query)["token"][0]


def _solicita(client, enviados):
    r = client.post("/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 200
    return _token_do_link(enviados[-1][1])


def test_link_do_email_aponta_para_o_frontend_vigente(client, isola):
    r = client.post("/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 200 and r.json()["message"] == NEUTRA
    assert len(isola) == 1 and isola[0][0] == EMAIL
    assert isola[0][1].startswith(f"{FRONT}/redefinir-senha?token=")  # sem barra dupla


def test_email_inexistente_responde_igual_e_nao_envia(client, isola):
    r = client.post("/auth/forgot-password", json={"email": "ninguem@escola.example"})
    assert r.status_code == 200 and r.json()["message"] == NEUTRA
    assert isola == []


def test_falha_no_envio_nao_muda_a_resposta(client, monkeypatch):
    monkeypatch.setattr(auth, "send_password_reset_email", lambda *a: False)
    r = client.post("/auth/forgot-password", json={"email": EMAIL})
    assert r.status_code == 200 and r.json()["message"] == NEUTRA


def test_token_do_link_redefine_a_senha_e_nao_vale_duas_vezes(client, isola, Sessao):
    token = _solicita(client, isola)

    r = client.post("/auth/reset-password", json={"token": token, "nova_senha": SENHA_NOVA})
    assert r.status_code == 200, r.text
    db = Sessao()
    hash_atual = db.query(User).filter(User.email == EMAIL).first().hashed_password
    db.close()
    assert verify_password(SENHA_NOVA, hash_atual)
    assert not verify_password(SENHA_ANTIGA, hash_atual)

    # mesmo link de novo: o fingerprint do hash mudou
    r2 = client.post("/auth/reset-password", json={"token": token, "nova_senha": "Outra@Senha789"})
    assert r2.status_code == 400
    assert "ja foi utilizado" in r2.json()["detail"]


def test_token_invalido_e_senha_fraca_sao_400(client, isola):
    assert client.post("/auth/reset-password", json={"token": "lixo", "nova_senha": SENHA_NOVA}).status_code == 400
    token = _solicita(client, isola)
    r = client.post("/auth/reset-password", json={"token": token, "nova_senha": "123"})
    assert r.status_code == 400  # politica de senha forte
