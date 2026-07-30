"""
Rotas para Estudantes - Provas
Endpoints para estudantes verem e fazerem suas provas
"""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.student import Student
from app.models.prova import ProvaAluno, Prova, QuestaoGerada, RespostaAluno, StatusProvaAluno
from app.api.dependencies import get_current_student
from app.core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/student/provas", tags=["Student - Provas"])


# NOTA: get_current_student agora vem de app.api.dependencies (C4 - centralizado).
# Antes estava duplicado aqui. Mesma assinatura e comportamento.


class ResponderRequest(BaseModel):
    """Schema para responder uma questao.

    FIX: antes o endpoint aceitava `dict` cru, entao frontend enviando
    `{"questao_id": 1}` (sem resposta) explodia em `None.strip()`.
    Agora Pydantic valida antes de chegar na logica.
    """
    questao_id: int = Field(..., gt=0)
    resposta: str = Field(..., min_length=1, max_length=5000)


@router.get("/")
def listar_minhas_provas(current_student: Student = Depends(get_current_student), db: Session = Depends(get_db)):
    """Listar todas as provas atribuídas ao estudante"""
    provas_aluno = db.query(ProvaAluno).filter(ProvaAluno.aluno_id == current_student.id).all()
    
    resultado = []
    for pa in provas_aluno:
        prova = db.query(Prova).filter(Prova.id == pa.prova_id).first()
        resultado.append({
            "prova_aluno_id": pa.id,
            "prova_id": prova.id,
            "titulo": prova.titulo,
            "descricao": prova.descricao,
            "materia": prova.materia,
            "serie_nivel": prova.serie_nivel,
            "quantidade_questoes": prova.quantidade_questoes,
            "tempo_limite_minutos": prova.tempo_limite_minutos,
            "pontuacao_total": prova.pontuacao_total,
            "status": pa.status.value,
            "data_atribuicao": pa.data_atribuicao,
            "nota_final": pa.nota_final,
            "aprovado": pa.aprovado
        })
    
    return resultado


@router.get("/{prova_aluno_id}")
def obter_prova_detalhe(prova_aluno_id: int, current_student: Student = Depends(get_current_student), db: Session = Depends(get_db)):
    """Obter detalhes de uma prova específica do estudante"""
    prova_aluno = db.query(ProvaAluno).filter(
        ProvaAluno.id == prova_aluno_id,
        ProvaAluno.aluno_id == current_student.id
    ).first()

    if not prova_aluno:
        raise HTTPException(status_code=404, detail="Prova não encontrada")

    prova = db.query(Prova).filter(Prova.id == prova_aluno.prova_id).first()

    return {
        "prova_aluno_id": prova_aluno.id,
        "prova_id": prova.id,
        "titulo": prova.titulo,
        "descricao": prova.descricao,
        "materia": prova.materia,
        "serie_nivel": prova.serie_nivel,
        "quantidade_questoes": prova.quantidade_questoes,
        "tempo_limite_minutos": prova.tempo_limite_minutos,
        "pontuacao_total": prova.pontuacao_total,
        "nota_minima_aprovacao": prova.nota_minima_aprovacao,
        "status": prova_aluno.status.value,
        "data_atribuicao": prova_aluno.data_atribuicao,
        "data_inicio": prova_aluno.data_inicio,
        "data_conclusao": prova_aluno.data_conclusao,
        "nota_final": prova_aluno.nota_final,
        "aprovado": prova_aluno.aprovado,
        "tempo_gasto_minutos": prova_aluno.tempo_gasto_minutos,
    }


@router.post("/{prova_aluno_id}/iniciar")
def iniciar_prova(prova_aluno_id: int, current_student: Student = Depends(get_current_student), db: Session = Depends(get_db)):
    """Iniciar prova"""
    prova_aluno = db.query(ProvaAluno).filter(ProvaAluno.id == prova_aluno_id, ProvaAluno.aluno_id == current_student.id).first()
    
    if not prova_aluno:
        raise HTTPException(status_code=404, detail="Prova não encontrada")
    if prova_aluno.status != StatusProvaAluno.PENDENTE:
        raise HTTPException(status_code=400, detail=f"Prova já está: {prova_aluno.status.value}")
    
    prova_aluno.status = StatusProvaAluno.EM_ANDAMENTO
    prova_aluno.data_inicio = datetime.now(timezone.utc)
    db.commit()
    
    return {"message": "Prova iniciada!", "status": "em_andamento"}


@router.get("/{prova_aluno_id}/questoes")
def obter_questoes(prova_aluno_id: int, current_student: Student = Depends(get_current_student), db: Session = Depends(get_db)):
    """Obter questões da prova"""
    prova_aluno = db.query(ProvaAluno).filter(ProvaAluno.id == prova_aluno_id, ProvaAluno.aluno_id == current_student.id).first()
    
    if not prova_aluno:
        raise HTTPException(status_code=404, detail="Prova não encontrada")
    if prova_aluno.status == StatusProvaAluno.PENDENTE:
        raise HTTPException(status_code=400, detail="Inicie a prova primeiro")
    
    questoes = db.query(QuestaoGerada).filter(QuestaoGerada.prova_id == prova_aluno.prova_id).order_by(QuestaoGerada.numero).all()
    
    respostas_dadas = db.query(RespostaAluno).filter(RespostaAluno.prova_aluno_id == prova_aluno_id).all()
    respostas_dict = {r.questao_id: r.resposta_aluno for r in respostas_dadas}

    # TC-151: expor o tempo limite e a acomodacao de "tempo estendido".
    # A prova define o tempo base (tempo_limite_minutos, opcional). Alunos
    # laudados (TDAH, TEA, dislexia, discalculia) tem direito a 1.5x. O tempo
    # efetivo ja vem calculado para o frontend so exibir o cronometro.
    prova = db.query(Prova).filter(Prova.id == prova_aluno.prova_id).first()
    tempo_limite = prova.tempo_limite_minutos if prova else None
    diag = current_student.diagnosis or {}
    tem_tempo_estendido = any(bool(diag.get(k)) for k in ("tdah", "tea", "dislexia", "discalculia"))
    tempo_efetivo = None
    if tempo_limite:
        tempo_efetivo = int(round(tempo_limite * 1.5)) if tem_tempo_estendido else int(tempo_limite)

    return {
        "prova_aluno_id": prova_aluno_id,
        "status": prova_aluno.status.value,
        "tempo_limite_minutos": tempo_limite,
        "tempo_estendido": tem_tempo_estendido,
        "tempo_efetivo_minutos": tempo_efetivo,
        "questoes": [{"id": q.id, "numero": q.numero, "enunciado": q.enunciado, "opcoes": q.opcoes, "resposta_ja_dada": respostas_dict.get(q.id)} for q in questoes]
    }


@router.post("/{prova_aluno_id}/responder")
async def responder(
    prova_aluno_id: int,
    payload: ResponderRequest,
    current_student: Student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """Responder questao. Body validado via ResponderRequest."""
    prova_aluno = db.query(ProvaAluno).filter(
        ProvaAluno.id == prova_aluno_id,
        ProvaAluno.aluno_id == current_student.id
    ).first()
    if not prova_aluno or prova_aluno.status != StatusProvaAluno.EM_ANDAMENTO:
        raise HTTPException(status_code=400, detail="Prova indisponivel")

    questao = db.query(QuestaoGerada).filter(
        QuestaoGerada.id == payload.questao_id,
        QuestaoGerada.prova_id == prova_aluno.prova_id
    ).first()
    if not questao:
        raise HTTPException(status_code=404, detail="Questao nao encontrada")

    # FIX: questoes dissertativas podem nao ter resposta_correta definida.
    # Antes quebrava em `None.strip()`. Agora marcamos `esta_correta=None`
    # (correcao manual/IA pos-finalizacao) e zeramos pontuacao.
    resposta_correta = questao.resposta_correta
    if resposta_correta is None:
        esta_correta = None
        pontuacao_obtida = 0
    else:
        esta_correta = (payload.resposta.strip().upper() == resposta_correta.strip().upper())
        pontuacao_obtida = questao.pontuacao if esta_correta else 0

    agora = datetime.now(timezone.utc)
    resposta_obj = db.query(RespostaAluno).filter(
        RespostaAluno.prova_aluno_id == prova_aluno_id,
        RespostaAluno.questao_id == payload.questao_id
    ).first()
    if resposta_obj:
        resposta_obj.resposta_aluno = payload.resposta
        resposta_obj.esta_correta = esta_correta
        resposta_obj.pontuacao_obtida = pontuacao_obtida
        resposta_obj.respondido_em = agora
    else:
        nova = RespostaAluno(
            prova_aluno_id=prova_aluno_id,
            questao_id=payload.questao_id,
            resposta_aluno=payload.resposta,
            esta_correta=esta_correta,
            pontuacao_obtida=pontuacao_obtida,
            pontuacao_maxima=questao.pontuacao,
            respondido_em=agora
        )
        db.add(nova)

    db.commit()
    return {"message": "Resposta salva!"}


@router.post("/{prova_aluno_id}/finalizar")
async def finalizar(
    prova_aluno_id: int,
    background_tasks: BackgroundTasks,
    current_student: Student = Depends(get_current_student),
    db: Session = Depends(get_db)
):
    """
    Finalizar prova e calcular nota.

    Automacao pos-finalizacao (em background DEPOIS da resposta ser enviada):
    1. Gera analise qualitativa
    2. Se houver pontos fracos, gera prova de reforco
    3. Associa a prova de reforco ao aluno

    FIX: antes usava asyncio.create_task sem persistir a referencia (task
    podia ser garbage-collected no meio da execucao) e misturava datetime
    naive com aware. Agora usa BackgroundTasks do FastAPI (gerenciado,
    executa depois da resposta) e datetime timezone-aware.
    """
    prova_aluno = db.query(ProvaAluno).filter(
        ProvaAluno.id == prova_aluno_id,
        ProvaAluno.aluno_id == current_student.id
    ).first()
    if not prova_aluno:
        raise HTTPException(status_code=404, detail="Prova nao encontrada")

    # FIX: proteger contra double-click/double-submit. Antes, duas chamadas
    # concorrentes criavam duas analises qualitativas e duas provas de reforco.
    if prova_aluno.status != StatusProvaAluno.EM_ANDAMENTO:
        raise HTTPException(
            status_code=400,
            detail=f"Prova nao pode ser finalizada (status atual: {prova_aluno.status.value})"
        )

    respostas = db.query(RespostaAluno).filter(
        RespostaAluno.prova_aluno_id == prova_aluno_id
    ).all()
    pontuacao_obtida = sum((r.pontuacao_obtida or 0) for r in respostas)
    prova = db.query(Prova).filter(Prova.id == prova_aluno.prova_id).first()

    pontuacao_total = prova.pontuacao_total or 0
    nota_final = (pontuacao_obtida / pontuacao_total) * 10 if pontuacao_total > 0 else 0
    nota_minima = prova.nota_minima_aprovacao or 6.0
    aprovado = nota_final >= nota_minima

    agora = datetime.now(timezone.utc)
    # FIX: data_inicio foi setado com datetime.now(timezone.utc) em iniciar_prova.
    # Usar datetime.utcnow() (naive) aqui quebrava a subtracao quando o driver
    # preservava timezone. Agora normalizamos os dois lados como aware.
    if prova_aluno.data_inicio:
        inicio = prova_aluno.data_inicio
        # Se o driver devolveu naive (MySQL DateTime sem tz), tratar como UTC
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=timezone.utc)
        tempo_gasto = int((agora - inicio).total_seconds() / 60)
    else:
        tempo_gasto = None

    prova_aluno.status = StatusProvaAluno.CORRIGIDA
    prova_aluno.data_conclusao = agora
    prova_aluno.data_correcao = agora
    prova_aluno.pontuacao_obtida = pontuacao_obtida
    prova_aluno.pontuacao_maxima = pontuacao_total
    prova_aluno.nota_final = nota_final
    prova_aluno.aprovado = aprovado
    prova_aluno.tempo_gasto_minutos = tempo_gasto
    db.commit()
    db.refresh(prova_aluno)

    acertos = sum(1 for r in respostas if r.esta_correta)
    percentual = round((acertos / len(respostas) * 100) if len(respostas) > 0 else 0, 1)

    # FIX: BackgroundTasks (em vez de asyncio.create_task) garante que a
    # funcao roda DEPOIS do response ser enviado, gerenciada pelo FastAPI
    # (sem risco de garbage collection). A funcao agora e sincrona para
    # nao bloquear o event loop chamando SDK sincrono do Anthropic de
    # dentro de uma task async.
    background_tasks.add_task(processar_pos_prova, prova_aluno_id)

    return {
        "message": "Prova finalizada! Gerando analise e prova de reforco automaticamente...",
        "nota_final": round(nota_final, 2),
        "aprovado": aprovado,
        "acertos": acertos,
        "total_questoes": len(respostas),
        "percentual": percentual,
        "processando_ia": True
    }


def processar_pos_prova(prova_aluno_id: int):
    """
    Executa em background (via FastAPI BackgroundTasks) depois que o
    endpoint /finalizar ja respondeu ao aluno:

    1. Gera analise qualitativa da prova
    2. Se houver pontos fracos e nota < 7.0, gera prova de reforco
    3. Associa prova de reforco ao aluno

    FIX: antes era async com asyncio.sleep e criava uma engine propria
    a partir da URL do DB passada por argumento (desperdica TLS handshake,
    risco de vazar engines, e `db.bind.url` devolve URL object, nao string).
    Agora e sincrona e reusa SessionLocal da app (pool compartilhado).
    Logger substitui prints soltos para que os eventos apareram em Railway.
    """
    from app.database import SessionLocal
    from app.services.analise_qualitativa_service import analise_service
    from app.services.prova_adaptativa_service import prova_adaptativa_service
    from app.models.analise_qualitativa import AnaliseQualitativa

    db = SessionLocal()
    try:
        prova_aluno = db.query(ProvaAluno).filter(ProvaAluno.id == prova_aluno_id).first()
        if not prova_aluno:
            logger.warning(
                "processar_pos_prova: prova_aluno nao encontrada",
                extra={"prova_aluno_id": prova_aluno_id},
            )
            return

        logger.info(
            "processar_pos_prova iniciado",
            extra={"prova_aluno_id": prova_aluno_id, "aluno_id": prova_aluno.aluno_id},
        )

        # 1. Gerar analise qualitativa
        try:
            analise_ia = analise_service.gerar_analise(prova_aluno)

            # Idempotencia: se ja existe analise (ex: retry manual), substituir
            analise_existente = db.query(AnaliseQualitativa).filter(
                AnaliseQualitativa.prova_aluno_id == prova_aluno_id
            ).first()
            if analise_existente:
                db.delete(analise_existente)
                db.commit()

            nova_analise = AnaliseQualitativa(
                prova_aluno_id=prova_aluno_id,
                pontos_fortes=analise_ia.get('pontos_fortes', ''),
                pontos_fracos=analise_ia.get('pontos_fracos', ''),
                conteudos_revisar=analise_ia.get('conteudos_revisar', []),
                recomendacoes=analise_ia.get('recomendacoes', ''),
                analise_por_conteudo=analise_ia.get('analise_por_conteudo', {}),
                nivel_dominio=analise_ia.get('nivel_dominio', 'regular'),
                areas_prioridade=analise_ia.get('areas_prioridade', []),
            )
            db.add(nova_analise)
            db.commit()
            db.refresh(nova_analise)

            logger.info(
                "analise qualitativa criada",
                extra={"analise_id": nova_analise.id, "prova_aluno_id": prova_aluno_id},
            )

            # 2. Gerar prova de reforco se houver pontos fracos E nota baixa
            nota = prova_aluno.nota_final or 0
            if (
                nova_analise.conteudos_revisar
                and len(nova_analise.conteudos_revisar) > 0
                and nota < 7.0
            ):
                try:
                    prova_reforco = prova_adaptativa_service.gerar_prova_reforco(
                        db=db,
                        prova_aluno_id=prova_aluno_id,
                        analise_id=nova_analise.id,
                    )
                    prova_aluno_reforco = prova_adaptativa_service.associar_prova_ao_aluno(
                        db=db,
                        prova_id=prova_reforco.id,
                        aluno_id=prova_aluno.aluno_id,
                    )
                    logger.info(
                        "prova de reforco gerada e associada",
                        extra={
                            "prova_reforco_id": prova_reforco.id,
                            "prova_aluno_reforco_id": prova_aluno_reforco.id,
                            "aluno_id": prova_aluno.aluno_id,
                        },
                    )
                except Exception:
                    logger.exception(
                        "erro ao gerar prova de reforco",
                        extra={"prova_aluno_id": prova_aluno_id},
                    )
            else:
                logger.info(
                    "prova de reforco nao necessaria (nota >= 7.0 ou sem pontos fracos)",
                    extra={"prova_aluno_id": prova_aluno_id, "nota": float(nota)},
                )

        except Exception:
            logger.exception(
                "erro ao gerar analise qualitativa",
                extra={"prova_aluno_id": prova_aluno_id},
            )
    finally:
        db.close()


@router.get("/{prova_aluno_id}/resultado")
def ver_resultado(prova_aluno_id: int, current_student: Student = Depends(get_current_student), db: Session = Depends(get_db)):
    """Ver resultado completo da prova"""
    prova_aluno = db.query(ProvaAluno).filter(ProvaAluno.id == prova_aluno_id, ProvaAluno.aluno_id == current_student.id).first()
    
    if not prova_aluno:
        raise HTTPException(status_code=404, detail="Prova não encontrada")
    if prova_aluno.status not in [StatusProvaAluno.CONCLUIDA, StatusProvaAluno.CORRIGIDA]:
        raise HTTPException(status_code=400, detail="Prova ainda não foi finalizada")
    
    prova = db.query(Prova).filter(Prova.id == prova_aluno.prova_id).first()
    questoes = db.query(QuestaoGerada).filter(QuestaoGerada.prova_id == prova.id).order_by(QuestaoGerada.numero).all()
    respostas = db.query(RespostaAluno).filter(RespostaAluno.prova_aluno_id == prova_aluno_id).all()
    respostas_dict = {r.questao_id: r for r in respostas}
    
    questoes_com_respostas = []
    for q in questoes:
        resposta = respostas_dict.get(q.id)
        questoes_com_respostas.append({
            "numero": q.numero,
            "enunciado": q.enunciado,
            "opcoes": q.opcoes,
            "resposta_correta": q.resposta_correta,
            "resposta_aluno": resposta.resposta_aluno if resposta else None,
            "esta_correta": resposta.esta_correta if resposta else False,
            "explicacao": q.explicacao
        })
    
    acertos = sum(1 for r in respostas if r.esta_correta)
    
    return {
        "prova": {
            "titulo": prova.titulo,
            "materia": prova.materia,
            "serie_nivel": prova.serie_nivel
        },
        "resultado": {
            "nota_final": prova_aluno.nota_final,
            "aprovado": prova_aluno.aprovado,
            "acertos": acertos,
            "total_questoes": len(questoes),
            "percentual": round((acertos / len(questoes) * 100) if len(questoes) > 0 else 0, 1),
            "tempo_gasto_minutos": prova_aluno.tempo_gasto_minutos,
            "data_conclusao": prova_aluno.data_conclusao
        },
        "questoes": questoes_com_respostas,
        "feedback_ia": prova_aluno.feedback_ia,
        "analise_ia": prova_aluno.analise_ia
    }
