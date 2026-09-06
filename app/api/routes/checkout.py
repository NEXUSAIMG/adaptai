# ============================================
# ROTAS DE CHECKOUT / ONBOARDING - AdaptAI
# ============================================
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.models.escola import Escola
from app.models.user import User
from app.models.plano import Plano
from app.models.assinatura import Assinatura, Fatura, StatusFatura, StatusAssinatura as StatusAssinaturaDB
from app.core.config import settings
from app.core.rate_limit import check_rate_limit
from app.services.asaas_service import asaas_service
from app.api.dependencies import get_current_active_user
from app.schemas.multitenant import CheckoutRequest
from pydantic import BaseModel, EmailStr

router = APIRouter(prefix="/checkout", tags=["🛒 Checkout"])


@router.post("/iniciar", status_code=status.HTTP_403_FORBIDDEN)
async def iniciar_checkout(dados: CheckoutRequest):
    """
    DESATIVADO (P5, ver docs/PAINEL-SUPERADMIN-MELHORIAS.md). Não cria mais
    escola nenhuma - só devolve 403.

    Autocadastro público foi desativado de propósito: o produto ainda não é
    vendido por self-service, e criação de conta passou a ser sempre manual,
    pelo super admin, depois de uma conversa com o cliente (ver
    `POST /planos/admin/ativar-conta`). O frontend já refletia essa decisão
    desde 2026-09-04 (commit fe42b40: PlanosPage.jsx virou vitrine + WhatsApp,
    App.jsx redireciona `/checkout/:slug` para `/planos`) - só o endpoint em
    si continuava aberto, criando escola de verdade se chamado direto
    (API/Postman), fora do fluxo da SPA. Mesma classe de bug do P6
    (`/auth/register`): a UI escondia o caminho, não fechava a porta.

    Rota mantida (não removida) só pra devolver a explicação acima em vez de
    um 404 mudo, caso algum cliente antigo ainda bata aqui.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Cadastro público direto foi desativado. Fale com a equipe (ver /planos) "
            "para ativarmos sua conta."
        ),
    )


# ============================================
# Schemas para endpoints de verificacao (POST - dados nao vazam em logs de URL)
# ============================================

class VerificarEmailRequest(BaseModel):
    email: EmailStr


class VerificarCnpjRequest(BaseModel):
    cnpj: str


@router.post("/verificar-email")
async def verificar_email_disponivel(
    payload: VerificarEmailRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    ✉️ Verifica se um email esta disponivel para cadastro.
    
    SEGURANCA: trocado de GET para POST para evitar vazamento de email
    em logs de servidor, proxy e browser history.
    Tambem rate limited para evitar enumeracao (harvesting de emails).
    """
    check_rate_limit(
        request, key="verificar_email", max_requests=10, window_seconds=60,
        error_message="Muitas verificacoes. Aguarde um momento."
    )
    
    usuario = db.query(User).filter(User.email == payload.email).first()
    
    return {
        "disponivel": usuario is None,
        "mensagem": "Email disponivel" if not usuario else "Email ja cadastrado"
    }


@router.post("/verificar-cnpj")
async def verificar_cnpj_disponivel(
    payload: VerificarCnpjRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    🏢 Verifica se um CNPJ esta disponivel para cadastro.
    
    SEGURANCA: trocado de GET para POST pelos mesmos motivos de privacidade.
    """
    check_rate_limit(
        request, key="verificar_cnpj", max_requests=10, window_seconds=60,
        error_message="Muitas verificacoes. Aguarde um momento."
    )
    
    escola = db.query(Escola).filter(Escola.cnpj == payload.cnpj).first()
    
    return {
        "disponivel": escola is None,
        "mensagem": "CNPJ disponivel" if not escola else "CNPJ ja cadastrado"
    }


# NOTA: Endpoints GET legados mantidos abaixo para nao quebrar frontend ja deployado.
# Remover em proxima versao apos atualizar frontend para usar os POSTs acima.

@router.get("/verificar-email/{email}", deprecated=True)
async def verificar_email_disponivel_legacy(
    email: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    DEPRECATED: Use POST /verificar-email.
    Este endpoint sera removido em versao futura por razoes de privacidade (LGPD).
    """
    check_rate_limit(
        request, key="verificar_email_legacy", max_requests=10, window_seconds=60
    )
    usuario = db.query(User).filter(User.email == email).first()
    return {
        "email": email,
        "disponivel": usuario is None,
        "mensagem": "Email disponivel" if not usuario else "Email ja cadastrado"
    }


@router.get("/verificar-cnpj/{cnpj}", deprecated=True)
async def verificar_cnpj_disponivel_legacy(
    cnpj: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    DEPRECATED: Use POST /verificar-cnpj.
    """
    check_rate_limit(
        request, key="verificar_cnpj_legacy", max_requests=10, window_seconds=60
    )
    escola = db.query(Escola).filter(Escola.cnpj == cnpj).first()
    return {
        "cnpj": cnpj,
        "disponivel": escola is None,
        "mensagem": "CNPJ disponivel" if not escola else "CNPJ ja cadastrado"
    }


@router.get("/resumo-plano/{plano_id}")
async def obter_resumo_plano(
    plano_id: int,
    db: Session = Depends(get_db)
):
    """
    📋 Obtém resumo do plano para exibir no checkout
    """
    plano = db.query(Plano).filter(
        Plano.id == plano_id,
        Plano.ativo == True
    ).first()
    
    if not plano:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plano não encontrado"
        )
    
    return {
        "id": plano.id,
        "nome": plano.nome,
        "valor_mensal": plano.valor,
        "valor_anual": plano.valor_anual,
        "economia_anual": (plano.valor * 12 - plano.valor_anual) if plano.valor_anual else 0,
        "trial_dias": 14,
        "funcionalidades": {
            "limite_alunos": plano.limite_alunos,
            "limite_professores": plano.limite_professores,
            "limite_provas_mes": plano.limite_provas_mes,
            "limite_materiais_mes": plano.limite_materiais_mes,
            "limite_peis_mes": plano.limite_peis_mes,
            "pei_automatico": plano.pei_automatico,
            "materiais_adaptativos": plano.materiais_adaptativos,
            "mapas_mentais": plano.mapas_mentais,
            "relatorios_avancados": plano.relatorios_avancados,
            "suporte_prioritario": plano.suporte_prioritario,
            "exportacao_pdf": plano.exportacao_pdf,
            "exportacao_excel": plano.exportacao_excel
        }
    }


# ============================================
# WEBHOOK ASAAS + LINK DE PAGAMENTO
# ============================================

def _parse_date_iso(valor):
    """Converte 'YYYY-MM-DD' (ou datetime ISO) em datetime; None se falhar."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _upsert_fatura_paga(db: Session, assinatura: Assinatura, pagamento: dict):
    """Cria ou atualiza uma Fatura PAGA a partir de um pagamento do Asaas."""
    payment_id = pagamento.get("id")
    fatura = None
    if payment_id:
        fatura = db.query(Fatura).filter(Fatura.asaas_payment_id == payment_id).first()

    if not fatura:
        numero = f"ASAAS-{payment_id}" if payment_id else f"ASAAS-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        fatura = Fatura(
            assinatura_id=assinatura.id,
            numero=numero,
            valor=float(pagamento.get("value") or assinatura.valor_mensal or 0),
            data_vencimento=_parse_date_iso(pagamento.get("dueDate")) or datetime.now(),
            asaas_payment_id=payment_id,
        )
        db.add(fatura)

    fatura.status = StatusFatura.PAGA.value
    fatura.valor_pago = float(pagamento.get("value") or fatura.valor or 0)
    fatura.data_pagamento = datetime.now()
    fatura.metodo_pagamento = pagamento.get("billingType")
    if pagamento.get("invoiceUrl"):
        fatura.link_pagamento = pagamento.get("invoiceUrl")


@router.post("/webhook/asaas")
async def webhook_asaas(request: Request, db: Session = Depends(get_db)):
    """
    Recebe eventos de cobranca do Asaas e atualiza a assinatura/faturas.

    SEGURANCA: se ASAAS_WEBHOOK_TOKEN estiver configurado, exige o mesmo valor
    no header 'asaas-access-token' (configurado no painel do Asaas).

    Responde sempre 200 para eventos conhecidos (o Asaas reenvia em caso de erro).
    """
    # Validacao do token do webhook (se configurado)
    if settings.ASAAS_WEBHOOK_TOKEN:
        token_recebido = request.headers.get("asaas-access-token", "")
        if token_recebido != settings.ASAAS_WEBHOOK_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de webhook invalido",
            )

    try:
        corpo = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload invalido")

    evento = corpo.get("event")
    pagamento = corpo.get("payment") or {}
    subscription_id = pagamento.get("subscription")
    customer_id = pagamento.get("customer")

    # Localiza a assinatura pelo subscription_id (preferencial) ou customer_id
    assinatura = None
    if subscription_id:
        assinatura = db.query(Assinatura).filter(
            Assinatura.asaas_subscription_id == subscription_id
        ).first()
    if not assinatura and customer_id:
        assinatura = db.query(Assinatura).filter(
            Assinatura.asaas_customer_id == customer_id
        ).first()

    if not assinatura:
        # Nao conhecemos essa assinatura: responde 200 para o Asaas nao reenviar.
        return {"received": True, "handled": False}

    eventos_pagos = {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}
    eventos_atraso = {"PAYMENT_OVERDUE"}
    eventos_problema = {"PAYMENT_REFUNDED", "PAYMENT_DELETED", "PAYMENT_CHARGEBACK_REQUESTED"}

    if evento in eventos_pagos:
        assinatura.status = StatusAssinaturaDB.ATIVA.value
        if pagamento.get("billingType"):
            assinatura.forma_pagamento = pagamento.get("billingType")
        proximo = _parse_date_iso(pagamento.get("dueDate"))
        if proximo:
            assinatura.data_proxima_cobranca = proximo
        _upsert_fatura_paga(db, assinatura, pagamento)
    elif evento in eventos_atraso:
        assinatura.status = StatusAssinaturaDB.ATRASADA.value
    elif evento in eventos_problema:
        assinatura.status = StatusAssinaturaDB.PENDENTE.value

    db.commit()
    return {"received": True, "handled": True, "event": evento}


@router.get("/assinatura/link")
async def obter_link_pagamento_atual(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Retorna o link da cobranca atual da assinatura da escola do usuario logado.
    Pode ser None durante o trial (ainda sem cobranca gerada).
    """
    if not current_user.escola_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Usuario sem escola vinculada")

    assinatura = db.query(Assinatura).filter(
        Assinatura.escola_id == current_user.escola_id
    ).first()
    if not assinatura:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura nao encontrada")

    link = None
    if asaas_service.esta_configurado() and assinatura.asaas_subscription_id:
        link = asaas_service.obter_link_pagamento_assinatura(assinatura.asaas_subscription_id)

    return {
        "status": assinatura.status,
        "link_pagamento": link,
        "data_proxima_cobranca": assinatura.data_proxima_cobranca.isoformat()
        if assinatura.data_proxima_cobranca else None,
    }
