"""
CORS: o front antigo (Vercel) nao e mais origem valida; o front do Railway entra
por FRONTEND_URL e o desenvolvimento local continua liberado.
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings

VERCEL = "https://adaptai-frontend.vercel.app"


def _preflight(origin: str):
    client = TestClient(main.app)
    return client.options(
        "/api/v1/auth/login/json",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_nenhuma_origem_vercel_configurada():
    assert not any("vercel" in o for o in main.origins)
    # cors_origins nao injeta mais curinga *.vercel.app em producao (independe do .env local)
    fake = SimpleNamespace(BACKEND_CORS_ORIGINS="http://a.test, http://b.test", ENVIRONMENT="production")
    assert Settings.cors_origins.fget(fake) == ["http://a.test", "http://b.test"]


def test_preflight_da_vercel_nao_recebe_allow_origin():
    resp = _preflight(VERCEL)
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_do_localhost_continua_liberado_em_dev():
    resp = _preflight("http://localhost:5173")
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
