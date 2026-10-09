# ============================================
# SERVICE - Planejamento Curricular BNCC
# ============================================
# Versão com suporte a processamento em background

import json
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.anthropic_client import get_default_model
from app.core.ai_usage import registrar_uso_ia
from app.core.logging_config import get_logger
from app.models.student import Student
from app.models.curriculo import CurriculoNacional, MapeamentoPrerequisitos
from app.models.pei import PEI, PEIObjetivo
from app.models.relatorio import Relatorio

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F


logger = get_logger(__name__)


# Cliente Anthropic (inicialização lazy)
_client = None
# Modelo (tier capaz). Antes cravava 'claude-sonnet-4-20250514' (Sonnet 4),
# que entra em retirement em 15/06/2026. get_default_model() resolve o modelo
# configurado no ambiente (settings.LLM_MODEL), igual ao completo_service.
MODELO_IA = get_default_model()


def get_anthropic_client():
    """Delega ao singleton central (app/core/anthropic_client.py).

    Mantido como funcao local para nao mexer nos pontos de chamada. Preserva o
    contrato antigo de devolver None em caso de falha, em vez de propagar.
    """
    global _client
    if _client is None:
        try:
            from app.core.anthropic_client import get_anthropic_client as _central

            _client = _central()
        except Exception as e:
            logger.exception(f"[AVISO] Erro ao inicializar Anthropic: {e}")
    return _client


class PlanejamentoBNNCService:
    """
    Serviço para gerar planejamento curricular adaptado baseado na BNCC
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.client = get_anthropic_client()
    
    def buscar_habilidades_bncc(
        self,
        ano_escolar: str,
        componente: Optional[str] = None,
        trimestre: Optional[int] = None,
        limit: int = 100
    ) -> List[CurriculoNacional]:
        """
        Busca habilidades da BNCC por filtros
        """
        query = self.db.query(CurriculoNacional).filter(
            CurriculoNacional.ano_escolar == ano_escolar
        )
        
        if componente:
            query = query.filter(CurriculoNacional.componente == componente)
        
        if trimestre:
            query = query.filter(CurriculoNacional.trimestre_sugerido == trimestre)
        
        return query.limit(limit).all()
    
    def buscar_prerequisitos(self, codigo_bncc: str) -> List[MapeamentoPrerequisitos]:
        """
        Busca os pré-requisitos de uma habilidade
        """
        return self.db.query(MapeamentoPrerequisitos).filter(
            MapeamentoPrerequisitos.habilidade_codigo == codigo_bncc
        ).all()
    
    def obter_perfil_aluno(self, student_id: int) -> Dict[str, Any]:
        """
        Consolida o perfil completo do aluno para o planejamento
        """
        student = self.db.query(Student).filter(Student.id == student_id).first()
        if not student:
            return {}
        
        # Buscar relatórios do aluno
        relatorios = self.db.query(Relatorio).filter(
            Relatorio.student_id == student_id
        ).order_by(Relatorio.created_at.desc()).all()
        
        # Consolidar diagnósticos
        diagnosticos = student.diagnosis or {}
        
        # Extrair informações dos relatórios
        dados_relatorios = []
        adaptacoes_consolidadas = []
        pontos_fortes_consolidados = []
        dificuldades_consolidadas = []
        
        for rel in relatorios:
            if rel.dados_extraidos:
                dados = rel.dados_extraidos
                dados_relatorios.append({
                    "tipo": rel.tipo,
                    "data": rel.created_at.isoformat() if rel.created_at else None,
                    "resumo": dados.get("resumo_clinico", ""),
                    "recomendacoes": dados.get("recomendacoes", [])
                })
                
                # Coletar adaptações
                if "adaptacoes_sugeridas" in dados:
                    for key, value in dados["adaptacoes_sugeridas"].items():
                        if value:
                            adaptacoes_consolidadas.append(value)
                
                # Coletar pontos fortes
                if "condicoes_identificadas" in dados:
                    condicoes = dados["condicoes_identificadas"]
                    # Atualizar diagnósticos
                    if condicoes.get("tea"):
                        diagnosticos["tea"] = {"nivel": condicoes.get("tea_nivel")}
                    if condicoes.get("tdah"):
                        diagnosticos["tdah"] = True
                    if condicoes.get("dislexia"):
                        diagnosticos["dislexia"] = True
        
        return {
            "id": student.id,
            "nome": student.name,
            "ano_escolar": student.grade_level,
            "turma": student.turma,
            "diagnosticos": diagnosticos,
            "profile_data": student.profile_data or {},
            "notes": student.notes,
            "relatorios": dados_relatorios,
            "adaptacoes_recomendadas": adaptacoes_consolidadas,
            "pontos_fortes": pontos_fortes_consolidados,
            "dificuldades": dificuldades_consolidadas
        }
    
    @tm.feature(F.PLANEJAMENTO_ANUAL, entity_type="aluno", entity_from="student_id")
    async def gerar_planejamento_anual(
        self,
        student_id: int,
        ano_letivo: str,
        componentes: List[str],
        user_id: int,
        task_id: str = None,
        task_manager = None
    ) -> Dict[str, Any]:
        """
        Gera planejamento anual completo para um aluno
        Suporta atualização de progresso em tempo real
        """
        def update_progress(progress: int, message: str):
            if task_manager and task_id:
                task_manager.update_task(task_id, progress=progress, message=message)
        
        if not self.client:
            raise Exception("Serviço de IA não disponível. Verifique a configuração da API key.")
        
        update_progress(5, "Carregando perfil do aluno...")
        
        # Obter perfil do aluno
        perfil = self.obter_perfil_aluno(student_id)
        if not perfil:
            raise Exception("Aluno não encontrado")
        
        update_progress(15, f"Buscando habilidades BNCC para {len(componentes)} componentes...")
        
        # Buscar habilidades da BNCC para o ano escolar
        habilidades_bncc = {}
        for i, componente in enumerate(componentes):
            habs = self.buscar_habilidades_bncc(
                ano_escolar=perfil["ano_escolar"],
                componente=componente
            )
            habilidades_bncc[componente] = [
                {
                    "codigo": h.codigo_bncc,
                    "descricao": h.habilidade_descricao,
                    "objeto_conhecimento": h.objeto_conhecimento,
                    "trimestre_sugerido": h.trimestre_sugerido,
                    "dificuldade": h.dificuldade
                }
                for h in habs
            ]
            progress = 15 + int((i + 1) / len(componentes) * 15)
            update_progress(progress, f"Habilidades de {componente} carregadas...")
        
        update_progress(35, "Preparando prompt para IA...")
        
        # Preparar prompt para a IA
        prompt = self._criar_prompt_planejamento(perfil, habilidades_bncc, ano_letivo)
        
        update_progress(40, "Gerando planejamento com IA (isso pode levar até 2 minutos)...")
        
        # Chamar a IA
        try:
            message = self.client.messages.create(
                model=MODELO_IA,
                max_tokens=16384,  # Aumentado para gerar mais objetivos
                messages=[{"role": "user", "content": prompt}]
            )

            registrar_uso_ia(
                feature="planejamento_anual",
                model=MODELO_IA,
                usage=message.usage,
                student_id=student_id,
                user_id=user_id,
            )

            update_progress(85, "Processando resposta da IA...")
            
            response_text = message.content[0].text.strip()
            
            # Limpar e parsear resposta
            response_text = self._limpar_json(response_text)
            
            update_progress(90, "Validando planejamento gerado...")
            
            planejamento = json.loads(response_text)
            
            # Validar estrutura
            if "objetivos" not in planejamento or len(planejamento.get("objetivos", [])) == 0:
                raise Exception("Planejamento gerado sem objetivos")
            
            update_progress(95, "Finalizando...")
            
            return {
                "success": True,
                "perfil_aluno": perfil,
                "planejamento": planejamento
            }
            
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Erro ao processar resposta da IA: {str(e)}",
                "raw_response": response_text[:1000] if response_text else None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @tm.feature(F.PLANEJAMENTO_TRIMESTRE, entity_type="aluno", entity_from="student_id")
    async def gerar_objetivos_pei_por_trimestre(
        self,
        student_id: int,
        componente: str,
        trimestre: int,
        ano_letivo: str,
        task_id: str = None,
        task_manager = None
    ) -> Dict[str, Any]:
        """
        Gera objetivos específicos para um trimestre
        """
        def update_progress(progress: int, message: str):
            if task_manager and task_id:
                task_manager.update_task(task_id, progress=progress, message=message)
        
        if not self.client:
            raise Exception("Serviço de IA não disponível")
        
        update_progress(10, "Carregando perfil do aluno...")
        
        perfil = self.obter_perfil_aluno(student_id)
        if not perfil:
            raise Exception("Aluno não encontrado")
        
        update_progress(30, f"Buscando habilidades de {componente}...")
        
        # Buscar habilidades do trimestre
        habilidades = self.buscar_habilidades_bncc(
            ano_escolar=perfil["ano_escolar"],
            componente=componente,
            trimestre=trimestre
        )
        
        if not habilidades:
            # Se não há habilidades específicas do trimestre, buscar todas
            habilidades = self.buscar_habilidades_bncc(
                ano_escolar=perfil["ano_escolar"],
                componente=componente
            )
        
        habilidades_list = [
            {
                "id": h.id,
                "codigo": h.codigo_bncc,
                "descricao": h.habilidade_descricao,
                "objeto_conhecimento": h.objeto_conhecimento,
                "dificuldade": h.dificuldade
            }
            for h in habilidades
        ]
        
        update_progress(50, "Gerando objetivos com IA...")
        
        prompt = self._criar_prompt_objetivos_trimestre(
            perfil, componente, trimestre, habilidades_list, ano_letivo
        )
        
        try:
            message = self.client.messages.create(
                model=MODELO_IA,
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}]
            )

            registrar_uso_ia(
                feature="planejamento_trimestre",
                model=MODELO_IA,
                usage=message.usage,
                student_id=student_id,
            )

            update_progress(85, "Processando resposta...")
            
            response_text = self._limpar_json(message.content[0].text.strip())
            objetivos = json.loads(response_text)
            
            update_progress(95, "Finalizando...")
            
            return {
                "success": True,
                "componente": componente,
                "trimestre": trimestre,
                "objetivos": objetivos
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def salvar_planejamento_como_pei(
        self,
        student_id: int,
        planejamento: Dict[str, Any],
        user_id: int,
        ano_letivo: str
    ) -> PEI:
        """
        Salva o planejamento gerado como um PEI no banco de dados
        """
        # Calcular datas
        ano = int(ano_letivo)
        data_inicio = date(ano, 2, 1)  # Início do ano letivo
        data_fim = date(ano, 12, 15)   # Fim do ano letivo
        
        # Criar PEI
        pei = PEI(
            student_id=student_id,
            created_by=user_id,
            ano_letivo=ano_letivo,
            tipo_periodo="anual",
            data_inicio=data_inicio,
            data_fim=data_fim,
            data_proxima_revisao=data_inicio + timedelta(days=90),  # Revisão em 3 meses
            ia_sugestoes_originais=planejamento,
            status="rascunho"
        )
        
        self.db.add(pei)
        self.db.flush()  # Para obter o ID
        
        # Criar objetivos
        if "objetivos" in planejamento:
            for obj_data in planejamento["objetivos"]:
                # Buscar currículo nacional correspondente
                curriculo_id = None
                codigo_bncc = obj_data.get("codigo_bncc")
                
                if codigo_bncc:
                    curriculo = self.db.query(CurriculoNacional).filter(
                        CurriculoNacional.codigo_bncc == codigo_bncc
                    ).first()
                    if curriculo:
                        curriculo_id = curriculo.id
                
                # Calcular prazo baseado no trimestre
                trimestre = obj_data.get("trimestre", 1)
                prazo = self._calcular_prazo_trimestre(ano, trimestre)
                
                objetivo = PEIObjetivo(
                    pei_id=pei.id,
                    area=obj_data.get("area", "outro"),
                    curriculo_nacional_id=curriculo_id,
                    codigo_bncc=codigo_bncc,
                    titulo=obj_data.get("titulo", ""),
                    descricao=obj_data.get("descricao", ""),
                    meta_especifica=obj_data.get("meta_especifica", ""),
                    criterio_medicao=obj_data.get("criterio_sucesso", ""),
                    valor_alvo=obj_data.get("valor_alvo", 80),
                    prazo=prazo,
                    trimestre=trimestre,
                    adaptacoes=obj_data.get("adaptacoes"),
                    estrategias=obj_data.get("estrategias_ensino"),
                    materiais_recursos=obj_data.get("materiais_recursos"),
                    criterios_avaliacao=obj_data.get("criterios_avaliacao"),
                    origem="ia_sugestao",
                    ia_sugestao_original=obj_data,
                    justificativa=obj_data.get("justificativa")
                )
                
                self.db.add(objetivo)
        
        self.db.commit()
        self.db.refresh(pei)
        
        return pei
    
    def _criar_prompt_planejamento(
        self,
        perfil: Dict,
        habilidades_bncc: Dict,
        ano_letivo: str
    ) -> str:
        """
        Cria o prompt para geração do planejamento
        """
        return f"""Você é um especialista em educação inclusiva e planejamento curricular adaptado.

Com base no perfil do aluno e nas habilidades da BNCC, gere um planejamento anual personalizado.

## PERFIL DO ALUNO:
{json.dumps(perfil, ensure_ascii=False, indent=2)}

## HABILIDADES DA BNCC DISPONÍVEIS:
{json.dumps(habilidades_bncc, ensure_ascii=False, indent=2)}

## ANO LETIVO: {ano_letivo}

## INSTRUÇÕES:

1. ANALISE o perfil do aluno (diagnósticos, pontos fortes, dificuldades, adaptações recomendadas)
2. SELECIONE as habilidades mais relevantes para trabalhar
3. ADAPTE cada objetivo considerando as necessidades específicas
4. DISTRIBUA os objetivos pelos 4 trimestres de forma progressiva
5. INCLUA estratégias pedagógicas personalizadas
6. DEFINA metas SMART mensuráveis

## FORMATO DE RESPOSTA (JSON):

{{
    "resumo_planejamento": "Visão geral do planejamento adaptado",
    "abordagem_geral": "Estratégia geral de ensino para este aluno",
    "objetivos": [
        {{
            "area": "matematica|portugues|ciencias|historia|geografia|socioemocional|autonomia",
            "codigo_bncc": "EF05MA08",
            "titulo": "Título claro do objetivo",
            "descricao": "Descrição detalhada do objetivo ADAPTADO ao perfil do aluno",
            "trimestre": 1,
            "meta_especifica": "Meta SMART mensurável",
            "criterio_sucesso": "Como medir o sucesso",
            "valor_alvo": 80,
            "adaptacoes": [
                "Adaptação 1 específica para o aluno",
                "Adaptação 2 baseada nos laudos"
            ],
            "estrategias_ensino": [
                "Estratégia pedagógica 1",
                "Estratégia pedagógica 2"
            ],
            "materiais_recursos": [
                "Material ou recurso necessário"
            ],
            "criterios_avaliacao": [
                "Como avaliar o progresso"
            ],
            "justificativa": {{
                "porque_este_conteudo": "Justificativa curricular",
                "porque_esta_abordagem": "Justificativa pedagógica",
                "baseado_em": ["Fonte 1", "Fonte 2"]
            }}
        }}
    ],
    "cronograma_trimestral": {{
        "trimestre_1": {{
            "foco_principal": "Descrição do foco",
            "objetivos_ids": [1, 2, 3]
        }},
        "trimestre_2": {{
            "foco_principal": "Descrição do foco",
            "objetivos_ids": [4, 5]
        }},
        "trimestre_3": {{
            "foco_principal": "Descrição do foco",
            "objetivos_ids": [6, 7]
        }},
        "trimestre_4": {{
            "foco_principal": "Descrição do foco",
            "objetivos_ids": [8, 9]
        }}
    }},
    "orientacoes_professor": [
        "Orientação geral 1 para trabalhar com este aluno",
        "Orientação geral 2"
    ],
    "pontos_atencao": [
        "Ponto de atenção importante"
    ]
}}

IMPORTANTE - REGRAS OBRIGATÓRIAS:
- Gere PELO MENOS 3-4 objetivos por componente curricular (total mínimo: 12-16 objetivos)
- OBRIGATÓRIO: Distribua equilibradamente pelos 4 TRIMESTRES
- OBRIGATÓRIO: O 4º TRIMESTRE DEVE TER objetivos!
- Distribuição esperada: ~3-4 objetivos por trimestre
- Cada objetivo deve ser realmente ADAPTADO ao perfil (não apenas copiar a habilidade BNCC)
- Use linguagem clara e prática
- Considere o estilo de aprendizagem do aluno
- Aproveite os pontos fortes como alavanca
- Retorne APENAS o JSON, sem explicações adicionais
- VERIFICAÇÃO FINAL: Certifique-se que há objetivos com "trimestre": 4 no array de objetivos!"""

    def _criar_prompt_objetivos_trimestre(
        self,
        perfil: Dict,
        componente: str,
        trimestre: int,
        habilidades: List[Dict],
        ano_letivo: str
    ) -> str:
        """
        Cria prompt específico para objetivos de um trimestre
        """
        return f"""Você é um especialista em educação inclusiva.

Gere objetivos adaptados para o {trimestre}º trimestre de {componente}.

## PERFIL DO ALUNO:
{json.dumps(perfil, ensure_ascii=False, indent=2)}

## HABILIDADES BNCC DISPONÍVEIS PARA {componente.upper()}:
{json.dumps(habilidades, ensure_ascii=False, indent=2)}

## ANO LETIVO: {ano_letivo}
## TRIMESTRE: {trimestre}

Gere 2 a 4 objetivos SMART adaptados ao perfil do aluno.

## FORMATO DE RESPOSTA (JSON):

{{
    "objetivos": [
        {{
            "curriculo_id": null,
            "codigo_bncc": "EFXXMAXX",
            "area": "{componente.lower()}",
            "titulo": "Título do objetivo",
            "descricao": "Descrição adaptada",
            "trimestre": {trimestre},
            "meta_especifica": "Meta SMART",
            "criterio_sucesso": "Critério mensurável",
            "valor_alvo": 80,
            "adaptacoes": ["Adaptação 1"],
            "estrategias_ensino": ["Estratégia 1"],
            "materiais_recursos": ["Recurso 1"],
            "criterios_avaliacao": ["Como avaliar"],
            "justificativa": {{
                "porque_este_conteudo": "Razão",
                "porque_esta_abordagem": "Razão pedagógica"
            }}
        }}
    ],
    "orientacoes_trimestre": "Orientações gerais para o trimestre"
}}

Retorne APENAS o JSON."""

    def _limpar_json(self, text: str) -> str:
        """Remove marcadores de código do texto"""
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
    
    def _calcular_prazo_trimestre(self, ano: int, trimestre: int) -> date:
        """Calcula a data limite de um trimestre"""
        if trimestre == 1:
            return date(ano, 4, 30)
        elif trimestre == 2:
            return date(ano, 7, 15)
        elif trimestre == 3:
            return date(ano, 10, 15)
        else:
            return date(ano, 12, 15)
