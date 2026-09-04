from pydantic_settings import BaseSettings
from typing import List, Optional
from urllib.parse import quote_plus
import os


# ============================================================
# HELPER CANONICO - DETECCAO DE AMBIENTE DE PRODUCAO
# ============================================================
# Antes o codigo detectava producao de tres formas inconsistentes:
#   - config.py:  settings.ENVIRONMENT == "production"
#   - main.py:    os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("PRODUCTION")
#   - ad-hoc:     os.getenv("RAILWAY_ENVIRONMENT") em outros lugares
# Esta divergencia causou o deploy de 17/04/2026 a rodar em prod achando que
# era dev (sem SECRET_KEY em env, gerou aleatoria, tokens quebrariam a cada
# reboot). AGORA tudo usa este helper.

def is_production_environment() -> bool:
    """
    Retorna True se estamos rodando em ambiente de producao.

    Fontes aceitas (qualquer uma bate):
      - RAILWAY_ENVIRONMENT setado com valor truthy (Railway tradicional)
      - PRODUCTION=1, true, yes, on (convencao manual)
      - ENVIRONMENT=production (arquivo railway.toml declara via [env])
      - RAILWAY_PROJECT_ID setado (Railway moderno - sempre setado em runtime)
    """
    # Railway tradicional - valores como "production", "staging"
    if os.getenv("RAILWAY_ENVIRONMENT", "").strip():
        return True
    # Railway moderno - sempre setado em deployments
    if os.getenv("RAILWAY_PROJECT_ID", "").strip():
        return True
    # Flag manual
    if os.getenv("PRODUCTION", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    # Flag declarativa (o que o railway.toml seta via [env] ENVIRONMENT="production")
    if os.getenv("ENVIRONMENT", "").strip().lower() == "production":
        return True
    return False


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AdaptAI"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Database MySQL - Opcional se DATABASE_URL estiver definido
    # Railway/produção pode usar DATABASE_URL OU variáveis separadas
    MYSQL_HOST: Optional[str] = None
    MYSQL_PORT: int = 3306
    MYSQL_USER: Optional[str] = None
    MYSQL_PASSWORD: Optional[str] = None
    MYSQL_DATABASE: Optional[str] = None

    # Ou usar DATABASE_URL diretamente (para Railway)
    DATABASE_URL: Optional[str] = None

    @property
    def db_url(self) -> str:
        """
        Constrói URL do banco de dados.
        Prioridade: DATABASE_URL > variáveis separadas
        """
        # Se DATABASE_URL estiver definido, usa ele diretamente
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            return self.DATABASE_URL

        # Senão, monta a URL a partir das partes
        # Valida que todas as variáveis necessárias estão presentes
        if not all([self.MYSQL_HOST, self.MYSQL_USER, self.MYSQL_PASSWORD, self.MYSQL_DATABASE]):
            # Listar quais estao faltando para acelerar diagnostico
            faltando = []
            if not self.MYSQL_HOST: faltando.append("MYSQL_HOST")
            if not self.MYSQL_USER: faltando.append("MYSQL_USER")
            if not self.MYSQL_PASSWORD: faltando.append("MYSQL_PASSWORD")
            if not self.MYSQL_DATABASE: faltando.append("MYSQL_DATABASE")

            # Em producao, mostrar env vars visiveis (sem valores) para ajudar a
            # debugar "mas eu setei no Railway!" vs variavel chegando na container.
            hint_env = ""
            if is_production_environment():
                mysql_vars = [k for k in os.environ if "MYSQL" in k or "DATABASE" in k]
                hint_env = (
                    f"\n\nVars visiveis no container: {sorted(mysql_vars) or 'NENHUMA'}"
                    "\nSe 'NENHUMA', as variaveis NAO estao chegando no servico. "
                    "Confira no Railway dashboard em Variables e faca redeploy."
                )

            raise ValueError(
                f"DATABASE_URL nao definido e variaveis MYSQL_* incompletas.\n"
                f"Faltando: {', '.join(faltando)}.\n"
                f"Defina DATABASE_URL OU todas as variaveis: "
                f"MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE.{hint_env}"
            )

        encoded_password = quote_plus(self.MYSQL_PASSWORD) if self.MYSQL_PASSWORD else ""

        db_url = f"mysql+pymysql://{self.MYSQL_USER}:{encoded_password}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"

        # Log para debug (sem mostrar senha completa)
        if self.DEBUG:
            safe_url = f"mysql+pymysql://{self.MYSQL_USER}:***@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            print(f"[CONFIG] Database URL: {safe_url}")

        return db_url

    # Security
    # SEGURANCA: sem default em producao. Em dev, se SECRET_KEY nao for setada,
    # geramos uma aleatoria a cada boot (tokens quebram a cada restart, o que
    # e bom porque forca o dev a setar a variavel).
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 horas

    # Claude API (Anthropic)
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    # Prompt caching (Anthropic): cacheia o prefixo ESTATICO (system) das
    # chamadas de IA para reduzir custo/latencia quando o mesmo bloco de
    # instrucoes se repete entre chamadas (ex.: corrigir varias redacoes
    # em sequencia). Desligar aqui reverte todas as chamadas ao comportamento
    # anterior, sem tocar em codigo.
    PROMPT_CACHE_ENABLED: bool = True

    # Geracao de imagem (ilustracao de conteudo). Camada trocavel:
    # IMAGE_PROVIDER decide o adapter; hoje 'flux' (Flux via fal.ai).
    # Sem FAL_API_KEY a busca de pictogramas ARASAAC continua funcionando;
    # so a geracao por IA fica indisponivel (erro claro na rota).
    IMAGE_PROVIDER: str = "flux"
    FAL_API_KEY: str = ""
    # Id do modelo no fal.ai. Deixado em config porque os ids mudam de versao;
    # ajuste para o id atual do Flux Pro que voce contratar.
    FLUX_MODEL: str = "fal-ai/flux-2-pro"

    # Email transacional (Resend) - usado para recuperacao de senha
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "AdaptAI <onboarding@resend.dev>"
    # URL base do frontend, usada para montar links em emails (ex: link de reset)
    FRONTEND_URL: str = "http://localhost:5173"

    # Pagamentos (Asaas)
    ASAAS_API_KEY: str = ""
    ASAAS_ENV: str = "sandbox"  # "sandbox" ou "production"
    ASAAS_WEBHOOK_TOKEN: str = ""  # validado no header 'asaas-access-token' do webhook

    # CORS - Origens permitidas (separadas por vírgula)
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,https://*.vercel.app"

    @property
    def cors_origins(self) -> List[str]:
        """Converte a string de CORS em lista"""
        origins = [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",")]
        # Em produção, permite qualquer subdomínio vercel.app
        if self.ENVIRONMENT == "production":
            origins.append("https://*.vercel.app")
        return origins

    @property
    def asaas_base_url(self) -> str:
        """URL base da API Asaas conforme o ambiente (sandbox/producao)."""
        if self.ASAAS_ENV.strip().lower() in ("production", "prod", "producao"):
            return "https://api.asaas.com/v3"
        return "https://api-sandbox.asaas.com/v3"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

        # Permite ler variáveis de ambiente mesmo sem .env
        # Importante para Railway/Docker
        env_file_encoding = 'utf-8'


# Instancia as configuracoes
settings = Settings()

# Normalizar ENVIRONMENT para bater com o helper canonico.
# Se qualquer sinal de producao bateu, forcar ENVIRONMENT="production" para
# que o resto do codigo (que verifica settings.ENVIRONMENT) fique consistente.
_IS_PROD = is_production_environment()
if _IS_PROD and settings.ENVIRONMENT != "production":
    print(
        f"[CONFIG] Sinal de producao detectado (RAILWAY_PROJECT_ID ou similar) "
        f"mas ENVIRONMENT={settings.ENVIRONMENT!r}. Forcando 'production'."
    )
    settings.ENVIRONMENT = "production"

# SEGURANCA: em dev, se SECRET_KEY nao foi setada, gera uma aleatoria
# para que tokens nao usem string previsivel. Em prod isso nunca e alcancado
# porque a validacao abaixo levanta antes.
if not settings.SECRET_KEY:
    if _IS_PROD:
        raise RuntimeError(
            "FATAL: SECRET_KEY nao configurada em producao. "
            "Gere uma nova com: python -c \"import secrets; print(secrets.token_hex(32))\" "
            "e configure no Railway."
        )
    import secrets as _secrets
    settings.SECRET_KEY = _secrets.token_hex(32)
    print("[CONFIG] SECRET_KEY nao definida - gerada aleatoriamente para esta sessao de desenvolvimento.")

# Debug: mostra quais variáveis foram carregadas (apenas em produção para debug)
if _IS_PROD:
    print(f"[DEBUG] DATABASE_URL presente: {bool(settings.DATABASE_URL)}")
    print(f"[DEBUG] DATABASE_URL valor (primeiros 20 chars): {str(settings.DATABASE_URL)[:20] if settings.DATABASE_URL else 'None'}")
    print(f"[DEBUG] MYSQL_HOST: {bool(settings.MYSQL_HOST)}")
    print(f"[DEBUG] MYSQL_USER: {bool(settings.MYSQL_USER)}")
    print(f"[DEBUG] Env vars disponíveis: {', '.join([k for k in os.environ.keys() if 'MYSQL' in k or 'DATABASE' in k])}")

# Validação adicional em produção
if _IS_PROD:
    # Verifica DATABASE_URL OU variáveis MYSQL separadas
    # Trata strings vazias como None
    has_database_url = bool(settings.DATABASE_URL and settings.DATABASE_URL.strip())
    has_mysql_vars = all([
        settings.MYSQL_HOST and settings.MYSQL_HOST.strip(),
        settings.MYSQL_USER and settings.MYSQL_USER.strip(),
        settings.MYSQL_PASSWORD and settings.MYSQL_PASSWORD.strip(),
        settings.MYSQL_DATABASE and settings.MYSQL_DATABASE.strip()
    ])

    if not has_database_url and not has_mysql_vars:
        # Mostra exatamente o que esta chegando na container
        mysql_env = sorted(k for k in os.environ if "MYSQL" in k or "DATABASE" in k)
        raise ValueError(
            "FATAL em producao: Configure DATABASE_URL OU todas as variaveis MYSQL_* "
            "(HOST, USER, PASSWORD, DATABASE) no Railway Dashboard.\n"
            f"Vars visiveis no container: {mysql_env or 'NENHUMA - as vars nao estao chegando!'}\n"
            "Se vazio: Railway Dashboard -> seu servico -> Variables -> adicionar e Redeploy."
        )

    # Verifica SECRET_KEY - FATAL em producao se ainda for o default antigo ou curta
    SECRET_KEY_DEFAULT_LEGACY = "adaptai-secret-key-change-in-production-2024"
    if not settings.SECRET_KEY or settings.SECRET_KEY == SECRET_KEY_DEFAULT_LEGACY:
        raise RuntimeError(
            "FATAL: SECRET_KEY padrao detectada em producao. "
            "Configure SECRET_KEY unica no Railway antes do deploy. "
            "Gere uma nova com: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    # Verifica ANTHROPIC_API_KEY - FATAL em producao se vazia
    if not settings.ANTHROPIC_API_KEY or not settings.ANTHROPIC_API_KEY.strip():
        raise RuntimeError(
            "FATAL: ANTHROPIC_API_KEY nao configurada em producao. "
            "Configure no Railway antes do deploy."
        )

    # Verifica se SECRET_KEY tem tamanho minimo aceitavel (hex de 32 bytes = 64 chars)
    if len(settings.SECRET_KEY) < 32:
        raise RuntimeError(
            "FATAL: SECRET_KEY muito curta (minimo 32 caracteres, recomendado 64). "
            "Gere uma nova com: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
