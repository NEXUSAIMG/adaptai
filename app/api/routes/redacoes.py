"""
📝 AdaptAI - Rotas de Redação ENEM
Endpoints para gerenciamento de redações com correção por IA
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone

from app.database import get_db, SessionLocal
from app.models.user import User
from app.models.student import Student
from app.models.redacao import TemaRedacao, RedacaoAluno, StatusRedacao
from app.schemas.redacao import (
    GerarTemaRequest,
    TemaRedacaoCreate,
    TemaRedacaoResponse,
    TemaListResponse,
    IniciarRedacaoRequest,
    SalvarRascunhoRequest,
    SubmeterRedacaoRequest,
    RedacaoAlunoResponse,
    RedacaoListResponse,
    CorrecaoENEMResponse,
    CompetenciaENEM,
    HistoricoRedacoesResponse
)
from app.services.redacao_ai_service import redacao_ai_service
from app.api.dependencies import get_current_user, oauth2_scheme, get_user_from_token, verificar_acesso_aluno
from app.core.pagination import PaginationParams, build_page
from app.core.rate_limit import check_rate_limit

router = APIRouter(prefix="/redacoes", tags=["Redações ENEM"])


# ============================================
# ENDPOINTS DO PROFESSOR/ADMIN
# ============================================

@router.post("/gerar-tema", response_model=TemaRedacaoResponse, status_code=status.HTTP_201_CREATED)
async def gerar_tema_com_ia(
    request: GerarTemaRequest,
    http_request: Request,
    token: str = Depends(oauth2_scheme)
):
    """
    🎯 Gerar tema de redação ATUAL com IA
    
    A IA escolhe um tema relevante e contemporâneo,
    cria textos motivadores e a proposta completa.

    Anti-abuso: limitado a 20 gerações/hora por IP (cada geração custa tokens de IA).
    """
    check_rate_limit(
        http_request, key="gerar_tema_redacao", max_requests=20, window_seconds=3600,
        error_message="Muitas gerações de tema em pouco tempo. Aguarde e tente novamente."
    )
    current_user = get_user_from_token(token)
    
    try:
        print(f"[GERANDO] Tema de redação com IA...")
        
        # Gerar tema com IA
        tema_data = await redacao_ai_service.gerar_tema_atual(
            area_tematica=request.area_tematica,
            nivel_dificuldade=request.nivel_dificuldade
        )
        
        print(f"[OK] Tema gerado: {tema_data.get('titulo', '')[:50]}...")
        
        # Salvar no banco
        db = SessionLocal()
        try:
            novo_tema = TemaRedacao(
                titulo=tema_data.get("titulo", "Tema de Redação"),
                tema=tema_data.get("tema", ""),
                proposta=tema_data.get("proposta", ""),
                texto_motivador_1=tema_data.get("texto_motivador_1"),
                texto_motivador_2=tema_data.get("texto_motivador_2"),
                texto_motivador_3=tema_data.get("texto_motivador_3"),
                texto_motivador_4=tema_data.get("texto_motivador_4"),
                area_tematica=tema_data.get("area_tematica"),
                palavras_chave=tema_data.get("palavras_chave"),
                nivel_dificuldade=request.nivel_dificuldade,
                criado_por_id=current_user.id
            )
            
            db.add(novo_tema)
            db.flush()
            
            # Associar alunos se fornecidos (com verificacao de ownership)
            if request.aluno_ids:
                # SEGURANCA: verifica acesso a TODOS os alunos antes de atribuir (evita IDOR)
                for aluno_id in request.aluno_ids:
                    aluno = verificar_acesso_aluno(db, aluno_id, current_user)
                    redacao_aluno = RedacaoAluno(
                        tema_id=novo_tema.id,
                        aluno_id=aluno_id,
                        status=StatusRedacao.RASCUNHO
                    )
                    db.add(redacao_aluno)
                    print(f"   [OK] Tema atribuido ao aluno: {aluno.name}")
            
            db.commit()
            db.refresh(novo_tema)
            
            return novo_tema
            
        finally:
            db.close()
        
    except HTTPException:
        raise
    except Exception as e:
        # SEGURANCA: nao vazar mensagem de erro interna
        print(f"[ERRO] Erro ao gerar tema: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao gerar tema. Tente novamente."
        )


@router.post("/temas", response_model=TemaRedacaoResponse, status_code=status.HTTP_201_CREATED)
async def criar_tema_manual(
    tema_data: TemaRedacaoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📝 Criar tema de redação manualmente
    """
    novo_tema = TemaRedacao(
        titulo=tema_data.titulo,
        tema=tema_data.tema,
        proposta=tema_data.proposta,
        texto_motivador_1=tema_data.texto_motivador_1,
        texto_motivador_2=tema_data.texto_motivador_2,
        texto_motivador_3=tema_data.texto_motivador_3,
        texto_motivador_4=tema_data.texto_motivador_4,
        area_tematica=tema_data.area_tematica,
        nivel_dificuldade=tema_data.nivel_dificuldade,
        criado_por_id=current_user.id
    )
    
    db.add(novo_tema)
    db.flush()
    
    # Associar alunos (com verificacao de ownership)
    if tema_data.aluno_ids:
        for aluno_id in tema_data.aluno_ids:
            # SEGURANCA: verifica acesso antes de atribuir (evita IDOR)
            verificar_acesso_aluno(db, aluno_id, current_user)
            redacao_aluno = RedacaoAluno(
                tema_id=novo_tema.id,
                aluno_id=aluno_id,
                status=StatusRedacao.RASCUNHO
            )
            db.add(redacao_aluno)
    
    db.commit()
    db.refresh(novo_tema)
    
    return novo_tema


@router.get("/temas")
def listar_temas(
    pagination: PaginationParams = Depends(),
    skip: int = None,
    limit: int = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lista temas de redacao (paginado).
    
    Query params preferidos:
    - page: pagina (default 1)
    - size: itens por pagina (default 20, max 100)
    
    Compatibilidade retroativa:
    - skip/limit ainda sao aceitos (se enviados, sobrescrevem page/size)
    
    Resposta: {items, meta, total, temas} - frontend novo usa items, antigo usa array na raiz
    """
    # Compat: se passou skip/limit, calcular page/size equivalentes
    if skip is not None or limit is not None:
        real_skip = skip or 0
        real_limit = min(limit or 50, 100)
        real_page = (real_skip // real_limit) + 1 if real_limit > 0 else 1
        # Sobrescrever pagination
        pagination.page = real_page
        pagination.size = real_limit
    
    query = db.query(TemaRedacao).filter(TemaRedacao.ativo == True).order_by(TemaRedacao.criado_em.desc())
    
    total = query.count()
    temas = query.offset(pagination.offset).limit(pagination.limit).all()
    
    # Evitar N+1: calcular contadores em uma query agregada
    tema_ids = [t.id for t in temas]
    contadores = {}
    if tema_ids:
        rows = (
            db.query(
                RedacaoAluno.tema_id,
                func.count(RedacaoAluno.id).label("total"),
                func.sum(
                    func.case((RedacaoAluno.status == StatusRedacao.CORRIGIDA, 1), else_=0)
                ).label("corrigidas"),
            )
            .filter(RedacaoAluno.tema_id.in_(tema_ids))
            .group_by(RedacaoAluno.tema_id)
            .all()
        )
        for tema_id, total_, corrigidas in rows:
            contadores[tema_id] = (total_ or 0, int(corrigidas or 0))
    
    items = []
    for tema in temas:
        total_redacoes, total_corrigidas = contadores.get(tema.id, (0, 0))
        items.append({
            "id": tema.id,
            "titulo": tema.titulo,
            "area_tematica": tema.area_tematica,
            "nivel_dificuldade": tema.nivel_dificuldade,
            "criado_em": tema.criado_em,
            "total_redacoes": total_redacoes,
            "total_corrigidas": total_corrigidas
        })
    
    page = build_page(items=items, total=total, pagination=pagination)
    page["total"] = total
    page["items"] = items
    return page


@router.get("/temas/{tema_id}", response_model=TemaRedacaoResponse)
def obter_tema(
    tema_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📄 Obter detalhes de um tema
    """
    tema = db.query(TemaRedacao).filter(TemaRedacao.id == tema_id).first()
    
    if not tema:
        raise HTTPException(status_code=404, detail="Tema não encontrado")
    
    return tema


@router.get("/temas/{tema_id}/redacoes")
def listar_redacoes_do_tema(
    tema_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📋 Listar todas as redações de um tema
    """
    # SEGURANCA: so o dono do tema (ou super_admin) ve as redacoes/alunos dele
    tema = db.query(TemaRedacao).filter(TemaRedacao.id == tema_id).first()
    if not tema:
        raise HTTPException(status_code=404, detail="Tema não encontrado")
    from app.models.user import UserRole
    if current_user.role != UserRole.SUPER_ADMIN and tema.criado_por_id != current_user.id:
        raise HTTPException(status_code=403, detail="Sem permissão para ver as redações deste tema")
    redacoes = db.query(RedacaoAluno).filter(RedacaoAluno.tema_id == tema_id).all()
    
    resultado = []
    for r in redacoes:
        resultado.append({
            "id": r.id,
            "aluno_id": r.aluno_id,
            "aluno_nome": r.aluno.name if r.aluno else "N/A",
            "status": r.status,
            "nota_final": r.nota_final,
            "quantidade_palavras": r.quantidade_palavras,
            "submetido_em": r.submetido_em,
            "corrigido_em": r.corrigido_em
        })
    
    return resultado


@router.post("/atribuir/{tema_id}/{aluno_id}", status_code=status.HTTP_201_CREATED)
def atribuir_tema_aluno(
    tema_id: int,
    aluno_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    👨‍🎓 Atribuir tema a um aluno
    """
    # Verificar se tema existe
    tema = db.query(TemaRedacao).filter(TemaRedacao.id == tema_id).first()
    if not tema:
        raise HTTPException(status_code=404, detail="Tema não encontrado")
    
    # SEGURANCA: verificar acesso ao aluno (evita IDOR entre escolas)
    aluno = verificar_acesso_aluno(db, aluno_id, current_user)
    
    # Verificar se já está atribuído
    existe = db.query(RedacaoAluno).filter(
        RedacaoAluno.tema_id == tema_id,
        RedacaoAluno.aluno_id == aluno_id
    ).first()
    
    if existe:
        raise HTTPException(status_code=400, detail="Tema já atribuído a este aluno")
    
    redacao_aluno = RedacaoAluno(
        tema_id=tema_id,
        aluno_id=aluno_id,
        status=StatusRedacao.RASCUNHO
    )
    
    db.add(redacao_aluno)
    db.commit()
    
    return {"message": f"Tema atribuído ao aluno {aluno.name}"}


# ============================================
# ENDPOINTS DO ALUNO
# ============================================

@router.get("/aluno/temas-disponiveis", response_model=List[TemaRedacaoResponse])
def listar_temas_disponiveis_aluno(
    aluno_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📚 Listar temas disponíveis para o aluno
    """
    # SEGURANCA: valida acesso ao aluno (evita IDOR)
    verificar_acesso_aluno(db, aluno_id, current_user)
    redacoes = db.query(RedacaoAluno).filter(
        RedacaoAluno.aluno_id == aluno_id,
        RedacaoAluno.status.in_([StatusRedacao.RASCUNHO, StatusRedacao.SUBMETIDA])
    ).all()
    
    temas = [r.tema for r in redacoes if r.tema]
    return temas


@router.get("/aluno/{aluno_id}/redacao/{tema_id}", response_model=RedacaoAlunoResponse)
def obter_redacao_aluno(
    aluno_id: int,
    tema_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📄 Obter redação do aluno para um tema
    """
    # SEGURANCA: valida acesso ao aluno (evita IDOR)
    verificar_acesso_aluno(db, aluno_id, current_user)
    redacao = db.query(RedacaoAluno).filter(
        RedacaoAluno.aluno_id == aluno_id,
        RedacaoAluno.tema_id == tema_id
    ).first()
    
    if not redacao:
        raise HTTPException(status_code=404, detail="Redação não encontrada")
    
    # Converter para response e adicionar tema
    resultado = {
        "id": redacao.id,
        "tema_id": redacao.tema_id,
        "aluno_id": redacao.aluno_id,
        "titulo_redacao": redacao.titulo_redacao,
        "texto": redacao.texto,
        "quantidade_linhas": redacao.quantidade_linhas,
        "quantidade_palavras": redacao.quantidade_palavras,
        "status": redacao.status,
        "iniciado_em": redacao.iniciado_em,
        "submetido_em": redacao.submetido_em,
        "corrigido_em": redacao.corrigido_em,
        "nota_competencia_1": redacao.nota_competencia_1,
        "nota_competencia_2": redacao.nota_competencia_2,
        "nota_competencia_3": redacao.nota_competencia_3,
        "nota_competencia_4": redacao.nota_competencia_4,
        "nota_competencia_5": redacao.nota_competencia_5,
        "nota_final": redacao.nota_final,
        "feedback_competencia_1": redacao.feedback_competencia_1,
        "feedback_competencia_2": redacao.feedback_competencia_2,
        "feedback_competencia_3": redacao.feedback_competencia_3,
        "feedback_competencia_4": redacao.feedback_competencia_4,
        "feedback_competencia_5": redacao.feedback_competencia_5,
        "feedback_geral": redacao.feedback_geral,
        "pontos_fortes": redacao.pontos_fortes,
        "pontos_melhoria": redacao.pontos_melhoria,
        "sugestoes": redacao.sugestoes,
        "tema": redacao.tema,
        "aluno_nome": redacao.aluno.name if redacao.aluno else None
    }
    
    return resultado


@router.post("/aluno/salvar-rascunho")
def salvar_rascunho(
    request: SalvarRascunhoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    💾 Salvar rascunho da redação
    """
    redacao = db.query(RedacaoAluno).filter(RedacaoAluno.id == request.redacao_id).first()
    
    if not redacao:
        raise HTTPException(status_code=404, detail="Redação não encontrada")
    
    # SEGURANCA: valida acesso ao aluno dono da redacao (evita IDOR)
    verificar_acesso_aluno(db, redacao.aluno_id, current_user)
    
    if redacao.status not in [StatusRedacao.RASCUNHO]:
        raise HTTPException(status_code=400, detail="Redação já foi submetida")
    
    # Atualizar
    redacao.titulo_redacao = request.titulo_redacao
    redacao.texto = request.texto
    redacao.quantidade_linhas = len([l for l in request.texto.split('\n') if l.strip()])
    redacao.quantidade_palavras = len(request.texto.split())
    
    db.commit()
    
    return {"message": "Rascunho salvo com sucesso"}


@router.post("/aluno/submeter", response_model=CorrecaoENEMResponse)
async def submeter_redacao(
    request: SubmeterRedacaoRequest,
    http_request: Request,
    token: str = Depends(oauth2_scheme)
):
    """
    ✅ Submeter redação para correção pela IA
    
    A IA corrige a redação nas 5 competências do ENEM
    e retorna nota de 0 a 1000 com feedback detalhado.

    Anti-abuso: limitado a 30 correções/hora por IP (cada correção custa tokens de IA).
    """
    check_rate_limit(
        http_request, key="corrigir_redacao", max_requests=30, window_seconds=3600,
        error_message="Muitas correções em pouco tempo. Aguarde e tente novamente."
    )
    current_user = get_user_from_token(token)
    
    db = SessionLocal()
    try:
        redacao = db.query(RedacaoAluno).filter(RedacaoAluno.id == request.redacao_id).first()
        
        if not redacao:
            raise HTTPException(status_code=404, detail="Redação não encontrada")
        
        # SEGURANCA: valida acesso ao aluno dono da redacao (evita IDOR)
        verificar_acesso_aluno(db, redacao.aluno_id, current_user)
        
        if redacao.status == StatusRedacao.CORRIGIDA:
            raise HTTPException(status_code=400, detail="Redação já foi corrigida")
        
        # Atualizar texto
        redacao.titulo_redacao = request.titulo_redacao
        redacao.texto = request.texto
        redacao.quantidade_linhas = len([l for l in request.texto.split('\n') if l.strip()])
        redacao.quantidade_palavras = len(request.texto.split())
        redacao.status = StatusRedacao.SUBMETIDA
        redacao.submetido_em = datetime.now(timezone.utc)
        
        db.commit()
        
        # Preparar dados do tema
        tema = redacao.tema
        tema_data = {
            "titulo": tema.titulo,
            "tema": tema.tema,
            "proposta": tema.proposta,
            "texto_motivador_1": tema.texto_motivador_1,
            "texto_motivador_2": tema.texto_motivador_2,
            "texto_motivador_3": tema.texto_motivador_3
        }
        
        # Preparar dados do aluno
        aluno = redacao.aluno
        aluno_info = {
            "nome": aluno.name,
            "serie": aluno.grade_level,
            "diagnostico": aluno.diagnosis
        } if aluno else None
        
        print(f"[CORRIGINDO] Redação {redacao.id} do aluno {aluno.name if aluno else 'N/A'}...")
        
        # Corrigir com IA
        correcao = await redacao_ai_service.corrigir_redacao_enem(
            texto_redacao=request.texto,
            tema=tema_data,
            aluno_info=aluno_info
        )
        
        print(f"[OK] Nota final: {correcao.get('nota_final', 0)}")
        
        # Salvar correção
        redacao.nota_competencia_1 = correcao.get("nota_competencia_1", 0)
        redacao.nota_competencia_2 = correcao.get("nota_competencia_2", 0)
        redacao.nota_competencia_3 = correcao.get("nota_competencia_3", 0)
        redacao.nota_competencia_4 = correcao.get("nota_competencia_4", 0)
        redacao.nota_competencia_5 = correcao.get("nota_competencia_5", 0)
        redacao.nota_final = correcao.get("nota_final", 0)
        
        redacao.feedback_competencia_1 = correcao.get("feedback_competencia_1", "")
        redacao.feedback_competencia_2 = correcao.get("feedback_competencia_2", "")
        redacao.feedback_competencia_3 = correcao.get("feedback_competencia_3", "")
        redacao.feedback_competencia_4 = correcao.get("feedback_competencia_4", "")
        redacao.feedback_competencia_5 = correcao.get("feedback_competencia_5", "")
        redacao.feedback_geral = correcao.get("feedback_geral", "")
        
        redacao.pontos_fortes = correcao.get("pontos_fortes", [])
        redacao.pontos_melhoria = correcao.get("pontos_melhoria", [])
        redacao.sugestoes = correcao.get("sugestoes", [])
        redacao.analise_detalhada = correcao
        
        redacao.status = StatusRedacao.CORRIGIDA
        redacao.corrigido_em = datetime.now(timezone.utc)
        
        db.commit()
        
        # Montar resposta
        competencias = []
        nomes_competencias = [
            "Domínio da Norma Culta",
            "Compreensão da Proposta",
            "Argumentação",
            "Coesão Textual",
            "Proposta de Intervenção"
        ]
        descricoes = [
            "Demonstrar domínio da modalidade escrita formal da língua portuguesa",
            "Compreender a proposta e aplicar conceitos das várias áreas de conhecimento",
            "Selecionar, relacionar, organizar e interpretar informações e argumentos",
            "Demonstrar conhecimento dos mecanismos linguísticos para construção da argumentação",
            "Elaborar proposta de intervenção para o problema, respeitando os direitos humanos"
        ]
        
        for i in range(1, 6):
            nota = correcao.get(f"nota_competencia_{i}", 0)
            nivel = correcao.get(f"nivel_competencia_{i}", "")
            
            competencias.append(CompetenciaENEM(
                numero=i,
                nome=nomes_competencias[i-1],
                descricao=descricoes[i-1],
                nota=nota,
                nivel=nivel,
                feedback=correcao.get(f"feedback_competencia_{i}", ""),
                descritor_nivel=correcao.get(f"descritor_competencia_{i}"),
                criterios=correcao.get(f"criterios_competencia_{i}")
            ))
        
        return CorrecaoENEMResponse(
            redacao_id=redacao.id,
            nota_final=correcao.get("nota_final", 0),
            competencias=competencias,
            feedback_geral=correcao.get("feedback_geral", ""),
            pontos_fortes=correcao.get("pontos_fortes", []),
            pontos_melhoria=correcao.get("pontos_melhoria", []),
            sugestoes=correcao.get("sugestoes", []),
            nivel_geral=correcao.get("nivel_geral", "")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERRO] Erro ao submeter redação: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao corrigir redação. Tente novamente."
        )
    finally:
        db.close()


@router.get("/aluno/{aluno_id}/historico", response_model=HistoricoRedacoesResponse)
def obter_historico_aluno(
    aluno_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📊 Obter histórico completo de redações do aluno
    """
    # SEGURANCA: valida acesso ao aluno (evita IDOR)
    verificar_acesso_aluno(db, aluno_id, current_user)
    redacoes = db.query(RedacaoAluno).filter(
        RedacaoAluno.aluno_id == aluno_id
    ).order_by(RedacaoAluno.iniciado_em.desc()).all()
    
    total = len(redacoes)
    corrigidas = [r for r in redacoes if r.status == StatusRedacao.CORRIGIDA]
    total_corrigidas = len(corrigidas)
    
    # Calcular médias
    media_geral = None
    melhor_nota = None
    pior_nota = None
    media_por_competencia = {}
    
    if corrigidas:
        notas = [r.nota_final for r in corrigidas if r.nota_final]
        if notas:
            media_geral = sum(notas) / len(notas)
            melhor_nota = max(notas)
            pior_nota = min(notas)
        
        # Média por competência
        for i in range(1, 6):
            notas_comp = [getattr(r, f"nota_competencia_{i}") for r in corrigidas if getattr(r, f"nota_competencia_{i}")]
            if notas_comp:
                media_por_competencia[f"competencia_{i}"] = sum(notas_comp) / len(notas_comp)
    
    # Evolução (últimas 10 redações corrigidas)
    evolucao = []
    for r in corrigidas[:10]:
        evolucao.append({
            "data": r.corrigido_em.isoformat() if r.corrigido_em else None,
            "nota_final": r.nota_final,
            "tema": r.tema.titulo if r.tema else "Sem título"
        })
    
    # Lista resumida
    lista_redacoes = []
    for r in redacoes:
        lista_redacoes.append({
            "id": r.id,
            "tema_titulo": r.tema.titulo if r.tema else "Sem título",
            "status": r.status,
            "nota_final": r.nota_final,
            "submetido_em": r.submetido_em,
            "corrigido_em": r.corrigido_em
        })
    
    return HistoricoRedacoesResponse(
        total_redacoes=total,
        total_corrigidas=total_corrigidas,
        media_geral=media_geral,
        melhor_nota=melhor_nota,
        pior_nota=pior_nota,
        media_por_competencia=media_por_competencia,
        evolucao=evolucao,
        redacoes=lista_redacoes
    )


@router.get("/rubrica")
def obter_rubrica_enem(
    current_user: User = Depends(get_current_user)
):
    """📚 Rubrica detalhada das 5 competências do ENEM (critérios e descritores por nível)."""
    return redacao_ai_service.get_rubrica()


@router.get("/aluno/{aluno_id}/evolucao")
def obter_evolucao_aluno(
    aluno_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📈 Evolução do aluno ao longo das redações corrigidas.

    Série cronológica (ascendente) de notas (final e por competência) + comparação
    entre a primeira e a última redação corrigida (variação e tendência).
    """
    # SEGURANCA: garante acesso ao aluno (evita IDOR)
    verificar_acesso_aluno(db, aluno_id, current_user)

    corrigidas = db.query(RedacaoAluno).filter(
        RedacaoAluno.aluno_id == aluno_id,
        RedacaoAluno.status == StatusRedacao.CORRIGIDA
    ).order_by(RedacaoAluno.corrigido_em.asc()).all()

    serie = []
    for r in corrigidas:
        serie.append({
            "redacao_id": r.id,
            "data": r.corrigido_em.isoformat() if r.corrigido_em else None,
            "tema": r.tema.titulo if r.tema else "Sem título",
            "nota_final": r.nota_final,
            "competencias": {
                f"competencia_{i}": getattr(r, f"nota_competencia_{i}")
                for i in range(1, 6)
            },
        })

    comparacao = None
    por_competencia = {}
    if len(corrigidas) >= 2:
        primeira, ultima = corrigidas[0], corrigidas[-1]
        n0 = primeira.nota_final or 0
        n1 = ultima.nota_final or 0
        variacao = n1 - n0
        if variacao > 20:
            tendencia = "melhorando"
        elif variacao < -20:
            tendencia = "piorando"
        else:
            tendencia = "estavel"
        comparacao = {
            "primeira_nota": n0,
            "ultima_nota": n1,
            "variacao": variacao,
            "variacao_percentual": round((variacao / n0) * 100, 1) if n0 else None,
            "tendencia": tendencia,
        }
        for i in range(1, 6):
            c0 = getattr(primeira, f"nota_competencia_{i}") or 0
            c1 = getattr(ultima, f"nota_competencia_{i}") or 0
            por_competencia[f"competencia_{i}"] = {
                "primeira": c0, "ultima": c1, "variacao": c1 - c0,
            }

    return {
        "aluno_id": aluno_id,
        "total_corrigidas": len(corrigidas),
        "serie": serie,
        "comparacao": comparacao,
        "por_competencia": por_competencia,
    }


@router.get("/aluno/{aluno_id}/redacao/{redacao_id}/resultado", response_model=RedacaoAlunoResponse)
def obter_resultado_redacao(
    aluno_id: int,
    redacao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    📝 Obter resultado detalhado de uma redação corrigida
    """
    # SEGURANCA: valida acesso ao aluno (evita IDOR)
    verificar_acesso_aluno(db, aluno_id, current_user)
    redacao = db.query(RedacaoAluno).filter(
        RedacaoAluno.id == redacao_id,
        RedacaoAluno.aluno_id == aluno_id
    ).first()
    
    if not redacao:
        raise HTTPException(status_code=404, detail="Redação não encontrada")
    
    # Converter para response
    resultado = {
        "id": redacao.id,
        "tema_id": redacao.tema_id,
        "aluno_id": redacao.aluno_id,
        "titulo_redacao": redacao.titulo_redacao,
        "texto": redacao.texto,
        "quantidade_linhas": redacao.quantidade_linhas,
        "quantidade_palavras": redacao.quantidade_palavras,
        "status": redacao.status,
        "iniciado_em": redacao.iniciado_em,
        "submetido_em": redacao.submetido_em,
        "corrigido_em": redacao.corrigido_em,
        "nota_competencia_1": redacao.nota_competencia_1,
        "nota_competencia_2": redacao.nota_competencia_2,
        "nota_competencia_3": redacao.nota_competencia_3,
        "nota_competencia_4": redacao.nota_competencia_4,
        "nota_competencia_5": redacao.nota_competencia_5,
        "nota_final": redacao.nota_final,
        "feedback_competencia_1": redacao.feedback_competencia_1,
        "feedback_competencia_2": redacao.feedback_competencia_2,
        "feedback_competencia_3": redacao.feedback_competencia_3,
        "feedback_competencia_4": redacao.feedback_competencia_4,
        "feedback_competencia_5": redacao.feedback_competencia_5,
        "feedback_geral": redacao.feedback_geral,
        "pontos_fortes": redacao.pontos_fortes,
        "pontos_melhoria": redacao.pontos_melhoria,
        "sugestoes": redacao.sugestoes,
        "tema": redacao.tema,
        "aluno_nome": redacao.aluno.name if redacao.aluno else None
    }
    
    return resultado
