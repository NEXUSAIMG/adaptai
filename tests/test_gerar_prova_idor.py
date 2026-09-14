"""
Testes de regressao do IDOR corrigido em POST /provas/gerar.

Antes da correcao (ver docs/SEGURANCA-2026-09-14-idor-provas-gerar.md), a rota
so checava se o Student EXISTIA - nunca se pertencia ao professor que chamou.
Dois pontos:
  1. Leitura de diagnostico (linha ~157) para personalizar o prompt da IA.
  2. Associacao da prova gerada ao aluno (linha ~247).

Este arquivo trava os dois: um professor sem acesso ao aluno nao pode mais
usar o diagnostico dele nem receber a prova associada a ele.

Estrategia (autocontida, igual em espirito a tests/test_idor_ownership.py, mas
com uma diferenca importante): `/provas/gerar` NAO usa `Depends(get_db)` nem
`Depends(get_current_active_user)` - abre suas proprias sessoes via
`SessionLocal()` direto (em `provas.py` e em `dependencies.get_user_from_token`),
pensado pra chamada de IA demorada. Por isso o teste monkeypatcha `SessionLocal`
nos dois modulos, em vez de usar `app.dependency_overrides`.

A chamada real a IA (`prova_ai_service.gerar_questoes`) e aos servicos de
jornada/estrategias e substituida por stubs - sem isso o teste faria uma
chamada de IA de verdade a cada execucao (custa dinheiro e o CLAUDE.md do
projeto pede pra nao gerar conteudo de IA em massa "as cegas").
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.models  # noqa: F401 - registra todos os models no metadata
from app.models.user import User, UserRole
from app.models.student import Student
from app.models.prova import ProvaAluno
from app.core.security import create_access_token
from app.api.routes import provas as provas_route
from app.api import dependencies as deps_module


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
def TestSessionLocal(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture(scope="module")
def seed(TestSessionLocal):
    db = TestSessionLocal()
    try:
        prof_a = User(name="Prof A", email="profa_gerar@test.com",
                      hashed_password="x", role=UserRole.TEACHER, is_active=True)
        prof_b = User(name="Prof B", email="profb_gerar@test.com",
                      hashed_password="x", role=UserRole.TEACHER, is_active=True)
        db.add_all([prof_a, prof_b])
        db.commit()
        db.refresh(prof_a)
        db.refresh(prof_b)

        # Aluno A pertence ao Prof A e tem diagnostico (dado sensivel).
        aluno_a = Student(name="Aluno A", grade_level="5o ano",
                          created_by_user_id=prof_a.id, is_active=True,
                          diagnosis={"tipo": "TEA"})
        db.add(aluno_a)
        db.commit()
        db.refresh(aluno_a)

        return {
            "aluno_a_id": aluno_a.id,
            "token_a": create_access_token({"sub": prof_a.email}),
            "token_b": create_access_token({"sub": prof_b.email}),
        }
    finally:
        db.close()


@pytest.fixture()
def client_e_spies(monkeypatch, TestSessionLocal):
    """App minimo so com o router de provas + SessionLocal apontando pro
    banco de teste + AI/jornada/estrategias trocados por stubs espiao."""
    monkeypatch.setattr(provas_route, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(deps_module, "SessionLocal", TestSessionLocal)

    chamadas_jornada = []
    chamadas_estrategias = []

    def _fake_contexto_para_prompt(db, aluno_id):
        chamadas_jornada.append(aluno_id)
        return ""  # "" == comportamento de "sem sintese", nao afeta o resto

    def _fake_diretrizes(db, diagnosis, escola_id):
        chamadas_estrategias.append(diagnosis)
        return ""

    async def _fake_gerar_questoes(**kwargs):
        return [{
            "numero": 1,
            "enunciado": "Quanto e 2 + 2?",
            "tipo": "multipla_escolha",
            "dificuldade": "facil",
            "opcoes": ["A) 3", "B) 4", "C) 5", "D) 6"],
            "resposta_correta": "B",
            "criterios_avaliacao": None,
            "explicacao": "",
            "tags": [],
        }]

    monkeypatch.setattr(
        provas_route.sintese_jornada_service, "contexto_para_prompt", _fake_contexto_para_prompt
    )
    monkeypatch.setattr(
        provas_route.estrategias_service, "diretrizes_para_diagnostico", _fake_diretrizes
    )
    monkeypatch.setattr(provas_route.prova_ai_service, "gerar_questoes", _fake_gerar_questoes)

    app = FastAPI()
    app.include_router(provas_route.router)
    client = TestClient(app)

    return client, chamadas_jornada, chamadas_estrategias


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _payload(aluno_id):
    return {
        "titulo": "Prova de teste",
        "conteudo_prompt": "conteudo de teste com mais de vinte caracteres",
        "materia": "Matematica",
        "quantidade_questoes": 1,
        "aluno_ids": [aluno_id],
    }


class TestGerarProvaIDOR:
    def test_professor_sem_acesso_nao_le_diagnostico(self, client_e_spies, seed):
        client, chamadas_jornada, chamadas_estrategias = client_e_spies
        r = client.post("/provas/gerar", json=_payload(seed["aluno_a_id"]),
                        headers=auth(seed["token_b"]))
        assert r.status_code == 201
        # O diagnostico do Aluno A (TEA) nunca deveria ter sido lido/usado.
        assert chamadas_jornada == []
        assert chamadas_estrategias == []

    def test_professor_sem_acesso_nao_associa_aluno(self, client_e_spies, seed, TestSessionLocal):
        client, _, _ = client_e_spies
        r = client.post("/provas/gerar", json=_payload(seed["aluno_a_id"]),
                        headers=auth(seed["token_b"]))
        assert r.status_code == 201
        prova_id = r.json()["id"]

        db = TestSessionLocal()
        try:
            vinculo = db.query(ProvaAluno).filter(
                ProvaAluno.prova_id == prova_id,
                ProvaAluno.aluno_id == seed["aluno_a_id"],
            ).first()
        finally:
            db.close()
        assert vinculo is None

    def test_dono_le_diagnostico_e_associa_normalmente(self, client_e_spies, seed, TestSessionLocal):
        """Regressao: o caminho legitimo (professor dono do aluno) continua
        funcionando exatamente como antes da correcao."""
        client, chamadas_jornada, chamadas_estrategias = client_e_spies
        r = client.post("/provas/gerar", json=_payload(seed["aluno_a_id"]),
                        headers=auth(seed["token_a"]))
        assert r.status_code == 201
        prova_id = r.json()["id"]

        assert chamadas_jornada == [seed["aluno_a_id"]]
        assert chamadas_estrategias == [{"tipo": "TEA"}]

        db = TestSessionLocal()
        try:
            vinculo = db.query(ProvaAluno).filter(
                ProvaAluno.prova_id == prova_id,
                ProvaAluno.aluno_id == seed["aluno_a_id"],
            ).first()
        finally:
            db.close()
        assert vinculo is not None
