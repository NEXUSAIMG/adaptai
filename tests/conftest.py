"""
Conftest - fixtures compartilhadas entre testes.
"""
import os
import pytest

# Marca ambiente como teste ANTES de qualquer import do app
# para que config.py nao levante RuntimeError por SECRET_KEY default.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only-do-not-use-in-prod-min-32-chars")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_adaptai.db")
os.environ.pop("RAILWAY_ENVIRONMENT", None)
os.environ.pop("PRODUCTION", None)


@pytest.fixture(scope="session", autouse=True)
def setup_logging_silent():
    """Silencia logs durante testes."""
    import logging
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)
