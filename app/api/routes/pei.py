from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from sqlalchemy.orm import Session
from typing import Optional, List
import base64
import json
from pathlib import Path

from app.database import get_db
from app.core.config import settings
from app.core.anthropic_client import get_anthropic_client, get_default_model
from app.core.ai_usage import registrar_uso_ia
from app.core.rate_limit import check_rate_limit
from app.core.logging_config import get_logger
from app.api.dependencies import get_current_active_user
from app.models.user import User
from app.models.relatorio import Relatorio
from app.models.student import Student

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F

logger = get_logger(__name__)

router = APIRouter(prefix="/pei", tags=["PEI - Plano Educacional Individualizado"])

# Diretorio de relatorios
RELATORIOS_DIR = Path(__file__).parent.parent.parent.parent / "storage" / "relatorios"

# Modelo que suporta PDFs e imagens (controlado via settings.LLM_MODEL).
# Antes cravava 'claude-3-5-sonnet-20241022', aposentado em 28/10/2025.
MODELO_VISAO = get_default_model()


@router.post("/analisar-laudo")
@tm.feature(F.PEI_ANALISE_LAUDO)
async def analisar_laudo(
    request: Request,
    arquivo: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Analisa relatórios de terapias e acompanhamento usando IA e extrai informações automaticamente.
    Aceita relatórios de: psicopedagogos, terapeutas ocupacionais, fonoaudiólogos, psicólogos,
    neurologistas, psiquiatras e outros profissionais de saúde.
    Aceita PDF ou imagens (JPG, PNG).
    
    SEGURANCA: rate limited (10/hora) - cada analise custa tokens de visao (caro).
    """
    check_rate_limit(
        request, key="analisar_laudo", max_requests=10, window_seconds=3600,
        error_message="Limite de analises de laudo atingido. Aguarde 1 hora."
    )
    
    client = get_anthropic_client()
    
    # Verificar tipo de arquivo
    content_type = arquivo.content_type
    allowed_types = ["application/pdf", "image/jpeg", "image/png", "image/jpg", "image/webp"]
    
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de arquivo não suportado: {content_type}. Use PDF, JPG, PNG ou WebP."
        )
    
    # Ler conteúdo do arquivo
    file_content = await arquivo.read()
    
    # Verificar tamanho (máximo 10MB)
    if len(file_content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="Arquivo muito grande. Máximo permitido: 10MB"
        )
    
    # Converter para base64
    file_base64 = base64.standard_b64encode(file_content).decode("utf-8")
    
    # Preparar mensagem para o Claude
    prompt = """Analise este relatório de terapia, acompanhamento ou avaliação profissional e extraia as informações em formato JSON.

Este documento pode ser um relatório de:
- Psicopedagogo(a)
- Terapeuta Ocupacional
- Fonoaudiólogo(a)
- Psicólogo(a)
- Neuropsicólogo(a)
- Neurologista
- Psiquiatra
- Pediatra
- Neuropediatra
- Fisioterapeuta
- Assistente Social
- Ou outro profissional de saúde/educação

IMPORTANTE: Retorne APENAS o JSON, sem explicações ou texto adicional.

Estrutura esperada:
{
    "tipo_laudo": "string (ex: Relatório Psicopedagógico, Relatório de Terapia Ocupacional, Avaliação Fonoaudiológica, Laudo Médico Neurológico, Parecer Psicológico, etc)",
    "profissional": {
        "nome": "string (nome completo do profissional)",
        "registro": "string (CRM, CRP, CRFa, CREFITO, etc com número e estado)",
        "especialidade": "string (especialidade ou área de atuação)"
    },
    "paciente": {
        "nome": "string (se disponível)",
        "data_nascimento": "string formato YYYY-MM-DD (se disponível)",
        "idade": "string (se disponível)"
    },
    "datas": {
        "emissao": "string formato YYYY-MM-DD",
        "validade": "string formato YYYY-MM-DD (se mencionada)"
    },
    "diagnosticos": [
        {
            "cid": "string (código CID-10 ou CID-11, se disponível)",
            "descricao": "string (nome do diagnóstico ou hipótese diagnóstica)"
        }
    ],
    "condicoes_identificadas": {
        "tea": false,
        "tea_nivel": null,
        "tdah": false,
        "dislexia": false,
        "discalculia": false,
        "disgrafia": false,
        "deficiencia_visual": false,
        "deficiencia_auditiva": false,
        "deficiencia_intelectual": false,
        "deficiencia_fisica": false,
        "superdotacao": false,
        "atraso_desenvolvimento": false,
        "transtorno_linguagem": false,
        "transtorno_ansiedade": false,
        "outras": []
    },
    "resumo_clinico": "string (resumo das principais conclusões, observações e achados)",
    "recomendacoes": [
        "string (cada recomendação como item da lista)"
    ],
    "adaptacoes_sugeridas": {
        "curriculares": "string (adaptações curriculares sugeridas)",
        "avaliacao": "string (adaptações para avaliação)",
        "ambiente": "string (adaptações de ambiente)",
        "recursos": "string (recursos de apoio sugeridos)"
    },
    "acompanhamentos_indicados": [
        {
            "profissional": "string (tipo de profissional)",
            "frequencia": "string (frequência sugerida)"
        }
    ],
    "objetivos_terapeuticos": [
        "string (objetivos de tratamento/intervenção mencionados)"
    ],
    "evolucao": "string (se for relatório de acompanhamento, descrever a evolução observada)",
    "observacoes": "string (outras observações relevantes para o contexto escolar)"
}

Se algum campo não estiver disponível no documento, use null para valores únicos ou [] para listas.
Para as condições em condicoes_identificadas, marque true apenas se explicitamente mencionado no relatório.
Se o TEA for identificado, tente determinar o nível de suporte (1, 2 ou 3) se mencionado.

Analise o documento com atenção e extraia todas as informações relevantes para o Plano Educacional Individualizado (PEI)."""

    try:
        # Determinar media type
        if content_type == "application/pdf":
            media_type = "application/pdf"
        elif content_type in ["image/jpeg", "image/jpg"]:
            media_type = "image/jpeg"
        elif content_type == "image/png":
            media_type = "image/png"
        elif content_type == "image/webp":
            media_type = "image/webp"
        else:
            media_type = content_type

        # Chamar Claude com visão
        message = client.messages.create(
            model=MODELO_VISAO,  # Modelo que suporta PDF/imagens
            # 2026-08-11: laudos de neuropsicologia passam facil de 4096 tokens
            # de saida estruturada. Truncava em silencio e o PEI herdava o vazio.
            max_tokens=8192,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document" if content_type == "application/pdf" else "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": file_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
        )

        registrar_uso_ia(
            feature="pei_analise_laudo",
            model=MODELO_VISAO,
            usage=message.usage,
            user_id=current_user.id,
            escola_id=getattr(current_user, "escola_id", None),
        )

        # Extrair resposta
        response_text = message.content[0].text
        
        # Tentar fazer parse do JSON
        # Remover possíveis marcadores de código
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # 2026-08-11: mesmo problema do endpoint de gerar PEI — o laudo
        # truncado virava "sucesso" com um objeto vazio, e esse objeto vazio
        # era justamente o que alimentava o PEI depois. Um laudo mal extraido
        # aqui produz um PEI vazio la na frente, sem nenhum aviso no caminho.
        stop_reason = getattr(message, "stop_reason", None)
        parse_ok = True

        if stop_reason == "max_tokens":
            logger.error(
                "Analise de laudo truncada no limite de tokens",
                extra={"arquivo": arquivo.filename, "tamanho": len(response_text)},
            )
            parse_ok = False
            dados_extraidos = {
                "erro_parse": True,
                "texto_bruto": response_text,
                "mensagem": (
                    "O laudo é longo demais e a extração foi cortada. "
                    "Os campos abaixo podem estar incompletos — revise antes de salvar."
                ),
            }
        else:
            try:
                dados_extraidos = json.loads(response_text)
            except json.JSONDecodeError:
                # Aqui o texto bruto AINDA e util: o professor consegue ler e
                # preencher a mao. Por isso nao levantamos erro — mas
                # sinalizamos com success=False para a UI avisar.
                logger.warning(
                    "Laudo nao pode ser estruturado automaticamente",
                    extra={"arquivo": arquivo.filename},
                )
                parse_ok = False
                dados_extraidos = {
                    "erro_parse": True,
                    "texto_bruto": response_text,
                    "mensagem": (
                        "Não foi possível estruturar os dados automaticamente. "
                        "O texto extraído está abaixo para preenchimento manual."
                    ),
                }

        return {
            # success reflete o que REALMENTE aconteceu. O frontend precisa
            # saber que os campos vieram vazios antes de o professor gerar um
            # PEI em cima de nada.
            "success": parse_ok,
            "dados": dados_extraidos,
            "arquivo_nome": arquivo.filename,
            "arquivo_tipo": content_type,
            "arquivo_base64": f"data:{content_type};base64,{file_base64}"
        }
        
    except Exception:
        logger.exception("Falha ao analisar laudo com IA", extra={"arquivo": arquivo.filename})
        raise HTTPException(
            status_code=500,
            detail="Erro ao analisar o relatório. Tente novamente mais tarde."
        )


# Modelo usado para consolidar o PEI a partir de relatorios (apenas texto, sem visao).
# Antes cravava 'claude-sonnet-4-20250514' (Sonnet 4, retirement em 15/06/2026).
MODELO_PEI_TEXTO = get_default_model()


@router.post("/gerar-pei-de-relatorios")
@tm.feature(F.PEI_GERACAO)
async def gerar_pei_de_relatorios(
    data: dict,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Gera um PEI completo automaticamente a partir dos relatorios ja salvos do aluno.

    Recebe uma lista de IDs de relatorios, carrega os dados extraidos de cada um e
    consolida tudo em um PEI estruturado usando IA.

    SEGURANCA: rate limited (20/hora) - cada geracao com IA e cara em tokens.

    Body:
    {
        "student_id": int,
        "relatorio_ids": [1, 2, 3, ...]
    }
    """
    check_rate_limit(
        request, key="gerar_pei_de_relatorios", max_requests=20, window_seconds=3600,
        error_message="Limite de geracoes de PEI atingido. Aguarde 1 hora."
    )

    # NOTA: o limite mensal de PEIs (limite_peis_mes) NAO e aplicado aqui de proposito.
    # O fluxo atual nao grava registros na tabela 'peis': o PEI e salvo como JSON em
    # Student.diagnosis (via PUT /students/{id}), entao nao ha um sinal mensal confiavel
    # para contar aqui. O custo de geracao ja e contido pelo rate limit acima (20/hora).
    # A cota mensal de PEI sera tratada junto com a trilha de pagamento.

    student_id = data.get("student_id")
    relatorio_ids = data.get("relatorio_ids", [])

    if not student_id:
        raise HTTPException(status_code=400, detail="student_id é obrigatório")

    if not relatorio_ids:
        raise HTTPException(
            status_code=400,
            detail="Selecione pelo menos um relatório para gerar o PEI"
        )

    # Verificar se o aluno existe
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    # Carregar relatorios filtrando pelo aluno (evita acesso cruzado entre alunos)
    relatorios = db.query(Relatorio).filter(
        Relatorio.id.in_(relatorio_ids),
        Relatorio.student_id == student_id
    ).all()

    if not relatorios:
        raise HTTPException(
            status_code=404,
            detail="Nenhum relatório encontrado com os IDs fornecidos"
        )

    # Compilar os dados de todos os relatorios selecionados
    relatorios_dados = []
    for rel in relatorios:
        dados = rel.dados_extraidos

        # Se houver ponteiro para um JSON completo no storage, carrega-lo
        if isinstance(dados, dict) and dados.get("json_path"):
            json_file = RELATORIOS_DIR / dados["json_path"]
            if json_file.exists():
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        dados = json.load(f)
                except Exception:
                    logger.warning(
                        "Falha ao ler JSON do relatorio no storage",
                        extra={"relatorio_id": rel.id}
                    )

        relatorios_dados.append({
            "id": rel.id,
            "tipo": rel.tipo,
            "profissional": {
                "nome": rel.profissional_nome,
                "especialidade": rel.profissional_especialidade,
                "registro": rel.profissional_registro
            },
            "data_emissao": rel.data_emissao.isoformat() if rel.data_emissao else None,
            "resumo": rel.resumo,
            "dados_completos": dados
        })

    client = get_anthropic_client()

    prompt = f"""Você é um especialista em educação inclusiva e está criando um Plano Educacional Individualizado (PEI) completo.

INFORMAÇÕES DO ALUNO:
- Nome: {student.name}
- Série/Ano: {student.grade_level or 'Não especificado'}

RELATÓRIOS DE TERAPIAS E ACOMPANHAMENTO ({len(relatorios_dados)} documentos):
{json.dumps(relatorios_dados, ensure_ascii=False, indent=2)}

Com base em TODOS os relatórios acima, gere um PEI COMPLETO e DETALHADO em formato JSON:

{{
    "diagnosticos": {{
        "tea": false,
        "tea_nivel": null,
        "tdah": false,
        "dislexia": false,
        "discalculia": false,
        "disgrafia": false,
        "deficiencia_visual": false,
        "deficiencia_auditiva": false,
        "deficiencia_intelectual": false,
        "deficiencia_fisica": false,
        "superdotacao": false,
        "outro": "",
        "outro_qual": ""
    }},
    "caracteristicas_gerais": "Parágrafo detalhado com características gerais do aluno, consolidando informações de TODOS os relatórios",
    "pontos_fortes": "Parágrafo detalhado com pontos fortes identificados pelos profissionais",
    "dificuldades": "Parágrafo detalhado com principais dificuldades identificadas",
    "adaptacoes_curriculares": "Parágrafo detalhado com adaptações curriculares específicas recomendadas pelos profissionais",
    "adaptacoes_avaliacao": "Parágrafo detalhado com adaptações para avaliações (tempo extra, formato, etc)",
    "adaptacoes_ambiente": "Parágrafo detalhado com adaptações de ambiente físico e social",
    "recursos_apoio": "Parágrafo detalhado com recursos e materiais de apoio necessários",
    "metas_curto_prazo": "Parágrafo detalhado com 3-5 metas concretas para 1-3 meses",
    "metas_medio_prazo": "Parágrafo detalhado com 3-5 metas concretas para 3-6 meses",
    "metas_longo_prazo": "Parágrafo detalhado com 3-5 metas concretas para o ano letivo",
    "estrategias_ensino": "Parágrafo detalhado com estratégias pedagógicas específicas",
    "estrategias_comunicacao": "Parágrafo detalhado com estratégias de comunicação (verbal, visual, etc)",
    "estrategias_comportamento": "Parágrafo detalhado com estratégias de manejo comportamental",
    "profissionais_apoio": "Lista de profissionais que devem acompanhar o aluno (psicólogo, fonoaudiólogo, etc)",
    "frequencia_acompanhamento": "Frequência recomendada para revisão do PEI e acompanhamentos",
    "observacoes": "Observações gerais importantes para a equipe escolar"
}}

INSTRUÇÕES IMPORTANTES:
1. Analise TODOS os relatórios e consolide as informações
2. Priorize recomendações que aparecem em múltiplos relatórios
3. Seja ESPECÍFICO e PRÁTICO - evite generalidades
4. Use linguagem acessível para professores
5. Foque em ações CONCRETAS e IMPLEMENTÁVEIS
6. Considere a realidade escolar brasileira
7. Marque os diagnósticos apenas se explicitamente mencionados
8. Nos parágrafos, seja detalhado (mínimo 3-4 linhas cada)
9. NUNCA use listas com bullet points ou números nos parágrafos - escreva texto corrido
10. Seja encorajador mas realista

Retorne APENAS o JSON, sem explicações adicionais."""

    try:
        logger.info(
            "Gerando PEI consolidado a partir de relatorios",
            extra={"student_id": student_id, "relatorios": len(relatorios_dados)}
        )

        # 2026-08-11: 8000 nao comportava um PEI completo (dados do aluno +
        # diagnosticos + objetivos por area + estrategias + adaptacoes), ainda
        # mais com o texto de um laudo extenso no contexto. O JSON vinha
        # cortado e caia no fallback de parse — que devolvia "sucesso".
        message = client.messages.create(
            model=MODELO_PEI_TEXTO,
            max_tokens=16000,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
        )

        registrar_uso_ia(
            feature="pei_geracao",
            model=MODELO_PEI_TEXTO,
            usage=message.usage,
            student_id=student_id,
            user_id=current_user.id,
            escola_id=getattr(current_user, "escola_id", None),
        )

        response_text = message.content[0].text.strip()

        # Limpar marcadores de bloco de codigo, se vierem
        for marker in ["```json", "```"]:
            response_text = response_text.replace(marker, "")
        response_text = response_text.strip()

        # ------------------------------------------------------------------
        # CORRECAO 2026-08-11 — "PEI parcial, sem preenchimento"
        # ------------------------------------------------------------------
        # O fluxo antigo era:
        #
        #   except json.JSONDecodeError:
        #       pei_gerado = {"erro_parse": True, "texto_bruto": ...}
        #   return {"success": True, "pei": pei_gerado}   # <-- MENTIA
        #
        # Ou seja: quando o JSON vinha truncado, a rota respondia SUCESSO com
        # um objeto que nao tem NENHUM dos campos do PEI. O frontend exibia
        # "PEI gerado com sucesso!" e abria o formulario vazio — exatamente o
        # "PEI parcial sem informacoes" relatado.
        #
        # Agora: truncamento e detectado, e falha e reportada COMO falha.
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.error(
                "PEI truncado no limite de tokens",
                extra={"student_id": student_id, "tamanho": len(response_text)},
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "O PEI ficou grande demais para ser gerado de uma vez — a resposta "
                    "foi cortada. Selecione menos relatórios e gere novamente."
                ),
            )

        try:
            pei_gerado = json.loads(response_text)
        except json.JSONDecodeError:
            logger.warning(
                "PEI gerado nao retornou JSON valido",
                extra={"student_id": student_id, "preview": response_text[:300]},
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "A IA respondeu em um formato inesperado e o PEI não pôde ser "
                    "montado. Tente gerar novamente."
                ),
            )

        # Rede de seguranca: JSON valido, porem sem os campos essenciais.
        # Melhor avisar agora do que abrir um formulario vazio.
        if not isinstance(pei_gerado, dict) or not any(
            pei_gerado.get(campo) for campo in ("objetivos", "areas", "diagnosticos", "resumo")
        ):
            logger.warning(
                "PEI gerado sem campos essenciais",
                extra={"student_id": student_id, "chaves": list(pei_gerado)[:10]
                       if isinstance(pei_gerado, dict) else None},
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "O PEI foi gerado sem as informações esperadas. Verifique se os "
                    "relatórios selecionados já foram analisados e tente novamente."
                ),
            )

        return {
            "success": True,
            "student_name": student.name,
            "relatorios_utilizados": len(relatorios_dados),
            "pei": pei_gerado
        }

    except Exception:
        logger.exception(
            "Falha ao gerar PEI a partir de relatorios",
            extra={"student_id": student_id}
        )
        raise HTTPException(
            status_code=500,
            detail="Erro ao gerar PEI com IA. Tente novamente mais tarde."
        )
