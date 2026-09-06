"""
`POST /auth/register` criava User(role=TEACHER) sem escola_id (orfao, sem
tenant) - mesma decisao do checkout: autocadastro publico foi desativado,
criacao de conta e sempre manual pelo super admin (ver
docs/ATIVACAO-CONTA-MANUAL.md e tests/test_ativacao_conta_manual.py).

Estes testes travam a correcao: o endpoint agora sempre devolve 403 e NUNCA
cria usuario nenhum, nem para payload valido.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401 - registra TODOS os models no metadata + resolve relationships
from app.models.user import User
from app.api.routes import auth


@pytest.fixture(scope="module")
def db_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture(scope="module")
def TestSession(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture(scope="module")
def client(db_engine, TestSession):
    app = FastAPI()
    app.include_router(auth.router)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


PAYLOAD_VALIDO = {
    "name": "Professor Novo",
    "email": "professor.novo.register@test.com",
    "password": "SenhaForte!12345",
}


class TestRegisterDesativado:
    def test_payload_valido_devolve_403(self, client):
        r = client.post("/auth/register", json=PAYLOAD_VALIDO)
        assert r.status_code == 403
        assert "/planos" in r.json()["detail"]

    def test_nao_cria_usuario_nenhum(self, client, TestSession):
        client.post("/auth/register", json=PAYLOAD_VALIDO)
        client.post("/auth/register", json=PAYLOAD_VALIDO)  # chamar 2x nao muda nada

        db = TestSession()
        try:
            assert db.query(User).filter(User.email == PAYLOAD_VALIDO["email"]).count() == 0
            assert db.query(User).count() == 0
        finally:
            db.close()

    def test_payload_invalido_ainda_valida_schema_antes_do_403(self, client):
        """Body sem 'password' deve dar 422 (validacao de schema), nao 403 -
        confirma que o contrato de request (UserCreate) nao mudou, so o
        comportamento do handler."""
        r = client.post("/auth/register", json={"name": "X", "email": "x@test.com"})
        assert r.status_code == 422
