"""
Testes da gestao de professores pela escola (app/api/routes/professores.py, do
time) + o enforcement de limite_professores do plano (adicionado sobre ela) + a
visao derivada "minhas turmas" (GET /students/turmas).

Trava:
  - admin/coordenador da escola cria/lista/ativa-desativa professor, sempre
    escopado ao proprio escola_id (professor nasce com escola_id do admin);
  - quem nao e admin/coord nao acessa (403);
  - admin de outra escola nao ve nem edita professores de fora (404);
  - enforce_limite_professores barra acima do limite do plano;
  - /students/turmas agrupa os alunos do professor por serie+turma.

Estrategia: engine sqlite em memoria + create_all; app minimo com os 2 routers.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
import app.models  # noqa: F401 - registra todos os models no metadata
from app.models.user import User, UserRole
from app.models.student import Student
from app.models.escola import Escola
from app.models.plano import Plano
from app.models.assinatura import Assinatura, StatusAssinatura
from app.core.security import create_access_token, get_password_hash
from app.api.routes import professores, students


@pytest.fixture(scope="module")
def db_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(eng)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Schema nao montavel em sqlite: {e}")
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture(scope="module")
def TestSession(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture(scope="module")
def seed(TestSession):
    db = TestSession()
    try:
        escola_a = Escola(nome="Escola A", email="a@esc.com")
        escola_b = Escola(nome="Escola B", email="b@esc.com")
        db.add_all([escola_a, escola_b])
        db.commit()
        db.refresh(escola_a)
        db.refresh(escola_b)

        # Plano com limite baixo (3) para exercitar o enforcement.
        plano = Plano(nome="Teste", slug="teste", valor=0, limite_alunos=100, limite_professores=3)
        db.add(plano)
        db.commit()
        db.refresh(plano)
        db.add(Assinatura(
            escola_id=escola_a.id, plano_id=plano.id,
            status=StatusAssinatura.ATIVA.value, valor_mensal=0,
        ))

        senha = get_password_hash("SenhaForte123")
        admin_a = User(name="Admin A", email="admin_a@esc.com", hashed_password=senha,
                       role=UserRole.ADMIN, escola_id=escola_a.id, is_active=True)
        teacher_a = User(name="Prof A1", email="prof_a1@esc.com", hashed_password=senha,
                         role=UserRole.TEACHER, escola_id=escola_a.id, is_active=True)
        admin_b = User(name="Admin B", email="admin_b@esc.com", hashed_password=senha,
                       role=UserRole.ADMIN, escola_id=escola_b.id, is_active=True)
        db.add_all([admin_a, teacher_a, admin_b])
        db.commit()
        db.refresh(admin_a)
        db.refresh(teacher_a)
        db.refresh(admin_b)

        db.add_all([
            Student(name="Aluno 1", grade_level="5º ano", turma="A",
                    created_by_user_id=teacher_a.id, escola_id=escola_a.id, is_active=True),
            Student(name="Aluno 2", grade_level="5º ano", turma="A",
                    created_by_user_id=teacher_a.id, escola_id=escola_a.id, is_active=True),
            Student(name="Aluno 3", grade_level="6º ano", turma="B",
                    created_by_user_id=teacher_a.id, escola_id=escola_a.id, is_active=True),
        ])
        db.commit()

        return {
            "escola_a_id": escola_a.id,
            "teacher_a_id": teacher_a.id,
            "token_admin_a": create_access_token({"sub": admin_a.email}),
            "token_teacher_a": create_access_token({"sub": teacher_a.email}),
            "token_admin_b": create_access_token({"sub": admin_b.email}),
        }
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(TestSession):
    app = FastAPI()
    app.include_router(professores.router)
    app.include_router(students.router)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestProfessores:
    def test_admin_cria_professor_herda_escola(self, client, seed):
        r = client.post("/escolas/minha/professores", headers=auth(seed["token_admin_a"]),
                        json={"nome": "Prof Novo", "email": "prof_novo@esc.com"})
        assert r.status_code == 201, r.text
        assert r.json()["email"] == "prof_novo@esc.com"
        # confere escola_id via listagem
        lista = client.get("/escolas/minha/professores", headers=auth(seed["token_admin_a"])).json()
        assert any(p["email"] == "prof_novo@esc.com" for p in lista)

    def test_nao_admin_nao_acessa(self, client, seed):
        r = client.post("/escolas/minha/professores", headers=auth(seed["token_teacher_a"]),
                        json={"nome": "X", "email": "x@esc.com"})
        assert r.status_code == 403

    def test_email_duplicado_400(self, client, seed):
        r = client.post("/escolas/minha/professores", headers=auth(seed["token_admin_a"]),
                        json={"nome": "Dup", "email": "prof_a1@esc.com"})
        assert r.status_code == 400

    def test_admin_b_nao_edita_professor_de_a(self, client, seed):
        r = client.patch(f"/escolas/minha/professores/{seed['teacher_a_id']}",
                         headers=auth(seed["token_admin_b"]), json={"ativo": False})
        assert r.status_code == 404

    def test_limite_do_plano_bloqueia(self, client, seed):
        # Plano limite_professores=3. Ativos: admin_a + prof_a1 + prof_novo = 3.
        r = client.post("/escolas/minha/professores", headers=auth(seed["token_admin_a"]),
                        json={"nome": "Excedente", "email": "excedente@esc.com"})
        assert r.status_code == 403
        assert "Limite de professores" in r.json()["detail"]


class TestMinhasTurmas:
    def test_agrupa_por_serie_e_turma(self, client, seed):
        r = client.get("/students/turmas", headers=auth(seed["token_teacher_a"]))
        assert r.status_code == 200
        turmas = {(t["serie"], t["turma"]): t["total_alunos"] for t in r.json()}
        assert turmas[("5º ano", "A")] == 2
        assert turmas[("6º ano", "B")] == 1
