"""
Ativação manual de conta pelo super admin (P5, ver
docs/PAINEL-SUPERADMIN-MELHORIAS.md).

Duas metades de uma mesma decisão de produto: autocadastro público
desativado (o produto ainda não é vendido por self-service) + criação de
conta passa a ser sempre manual, pelo super admin, depois de uma conversa
via WhatsApp com o cliente.

- TestCheckoutDesativado: `POST /checkout/iniciar` sempre 403, nunca cria
  escola/usuário (mesmo padrão de tests/test_auth_register_desativado.py
  para `/auth/register` - mesma classe de bug, endpoint continuava aberto
  mesmo com a UI já redirecionando pra outro lugar).
- TestAtivarContaManual: `POST /planos/admin/ativar-conta` só SUPER_ADMIN,
  cria escola + admin + assinatura com valor negociado (independente do
  catálogo `Plano`), devolve link de definir senha.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401 - registra TODOS os models no metadata + resolve relationships
from app.models.user import User, UserRole
from app.models.escola import Escola
from app.models.plano import Plano
from app.models.assinatura import Assinatura, StatusAssinatura
from app.core.security import create_access_token, decode_password_reset_token
from app.api.routes import checkout, planos


# ============================================================
# FIXTURES
# ============================================================

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
    app.include_router(checkout.router)
    app.include_router(planos.router)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture(scope="module")
def seed(TestSession):
    db = TestSession()
    try:
        plano = Plano(nome="Essencial Teste", slug="essencial-teste-ativacao", valor=99.0)
        db.add(plano)
        db.commit()
        db.refresh(plano)

        super_admin = User(name="Super", email="super_ativacao@test.com",
                            hashed_password="x", role=UserRole.SUPER_ADMIN, is_active=True)
        outra_escola = Escola(nome="Outra Escola", email="outraescola_ativacao@test.com")
        db.add_all([super_admin, outra_escola])
        db.commit()
        db.refresh(super_admin)
        db.refresh(outra_escola)

        admin_comum = User(name="Admin Comum", email="admin_comum_ativacao@test.com",
                            hashed_password="x", role=UserRole.ADMIN, is_active=True,
                            escola_id=outra_escola.id)
        db.add(admin_comum)
        db.commit()

        return {
            "plano_id": plano.id,
            "token_super": create_access_token({"sub": super_admin.email}),
            "token_admin_comum": create_access_token({"sub": admin_comum.email}),
        }
    finally:
        db.close()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


PAYLOAD_CHECKOUT_VALIDO = {
    "plano_id": 1,
    "escola_nome": "Escola Nova Teste",
    "escola_tipo": "ESCOLA",
    "admin_nome": "Diretor Teste",
    "admin_email": "diretor.checkout.p5@test.com",
    "admin_senha": "SenhaForte123456",
}


# ============================================================
# Checkout público desativado
# ============================================================

class TestCheckoutDesativado:
    def test_payload_valido_devolve_403(self, client, seed):
        payload = {**PAYLOAD_CHECKOUT_VALIDO, "plano_id": seed["plano_id"]}
        r = client.post("/checkout/iniciar", json=payload)
        assert r.status_code == 403

    def test_nao_cria_escola_nem_usuario(self, client, seed, TestSession):
        payload = {**PAYLOAD_CHECKOUT_VALIDO, "plano_id": seed["plano_id"]}
        client.post("/checkout/iniciar", json=payload)

        db = TestSession()
        try:
            assert db.query(User).filter(User.email == payload["admin_email"]).count() == 0
            assert db.query(Escola).filter(Escola.nome == payload["escola_nome"]).count() == 0
        finally:
            db.close()

    def test_payload_invalido_ainda_valida_schema_antes_do_403(self, client, seed):
        """Senha curta (viola validar_senha_forte) deve dar 422, nao 403 -
        confirma que so o comportamento do handler mudou, nao o contrato."""
        payload = {**PAYLOAD_CHECKOUT_VALIDO, "plano_id": seed["plano_id"], "admin_senha": "curta"}
        r = client.post("/checkout/iniciar", json=payload)
        assert r.status_code == 422


# ============================================================
# Ativação manual pelo super admin
# ============================================================

def _payload_ativacao(plano_id, email="cliente.novo.p5@test.com", **overrides):
    base = {
        "escola_nome": "Escola Ativada Manual",
        "escola_tipo": "ESCOLA",
        "admin_nome": "Responsável Cliente",
        "admin_email": email,
        "plano_id": plano_id,
        "valor_mensal": 250.0,
        "status_inicial": "trial",
    }
    base.update(overrides)
    return base


class TestAtivarContaManual:
    def test_sem_token_401(self, client, seed):
        r = client.post("/planos/admin/ativar-conta", json=_payload_ativacao(seed["plano_id"]))
        assert r.status_code == 401

    def test_admin_comum_403(self, client, seed):
        r = client.post(
            "/planos/admin/ativar-conta",
            json=_payload_ativacao(seed["plano_id"], email="outro1@test.com"),
            headers=auth(seed["token_admin_comum"]),
        )
        assert r.status_code == 403

    def test_sem_valor_mensal_usa_valor_do_plano(self, client, seed, TestSession):
        """Painel simplificado (2026-09-05): sem negociar valor - so plano e
        ativar. valor_mensal omitido cai pro Plano.valor do catalogo (99.0,
        ver fixture seed)."""
        payload = _payload_ativacao(seed["plano_id"], email="cliente.semvalor.p5@test.com")
        payload.pop("valor_mensal")
        payload.pop("status_inicial")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r.status_code == 201

        db = TestSession()
        try:
            assinatura = db.query(Assinatura).filter(Assinatura.escola_id == r.json()["escola_id"]).first()
            assert assinatura.valor_mensal == 99.0  # Plano.valor da fixture
            assert assinatura.status == StatusAssinatura.TRIAL.value  # default
        finally:
            db.close()

    def test_super_admin_cria_conta_trial(self, client, seed, TestSession):
        payload = _payload_ativacao(seed["plano_id"], email="cliente.trial.p5@test.com", status_inicial="trial")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "trial"
        assert "link_definir_senha" in body and "token=" in body["link_definir_senha"]

        db = TestSession()
        try:
            usuario = db.query(User).filter(User.email == payload["admin_email"]).first()
            assert usuario is not None
            assert usuario.role == UserRole.ADMIN
            assert usuario.escola_id == body["escola_id"]

            escola = db.query(Escola).filter(Escola.id == body["escola_id"]).first()
            assert escola.nome == payload["escola_nome"]
            assert escola.email == payload["admin_email"]  # mesmo padrao do checkout

            assinatura = db.query(Assinatura).filter(Assinatura.escola_id == escola.id).first()
            assert assinatura.status == StatusAssinatura.TRIAL.value
            assert assinatura.valor_mensal == 250.0  # negociado, nao o Plano.valor (99.0)
            assert assinatura.data_fim is not None
            assert assinatura.data_proxima_cobranca is None
        finally:
            db.close()

    def test_super_admin_cria_conta_ja_ativa(self, client, seed, TestSession):
        payload = _payload_ativacao(seed["plano_id"], email="cliente.ativa.p5@test.com", status_inicial="ativa")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r.status_code == 201
        assert r.json()["status"] == "ativa"

        db = TestSession()
        try:
            escola_id = r.json()["escola_id"]
            assinatura = db.query(Assinatura).filter(Assinatura.escola_id == escola_id).first()
            assert assinatura.status == StatusAssinatura.ATIVA.value
            assert assinatura.data_fim is None
            assert assinatura.data_proxima_cobranca is not None
        finally:
            db.close()

    def test_link_definir_senha_tem_token_valido(self, client, seed):
        payload = _payload_ativacao(seed["plano_id"], email="cliente.token.p5@test.com")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        link = r.json()["link_definir_senha"]
        token = link.split("token=", 1)[1]
        decodificado = decode_password_reset_token(token)
        assert decodificado is not None
        assert decodificado["sub"] == payload["admin_email"]

    def test_email_duplicado_400(self, client, seed):
        payload = _payload_ativacao(seed["plano_id"], email="cliente.duplicado.p5@test.com")
        r1 = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r1.status_code == 201
        r2 = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r2.status_code == 400

    def test_plano_inexistente_404(self, client, seed):
        payload = _payload_ativacao(999999, email="cliente.semplano.p5@test.com")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r.status_code == 404

    def test_status_inicial_invalido_400(self, client, seed):
        payload = _payload_ativacao(seed["plano_id"], email="cliente.statusinvalido.p5@test.com",
                                     status_inicial="qualquer_coisa")
        r = client.post("/planos/admin/ativar-conta", json=payload, headers=auth(seed["token_super"]))
        assert r.status_code == 400
