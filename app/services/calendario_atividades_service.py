# ============================================
# SERVICE - Geração de Calendário e Atividades PEI
# ============================================

from sqlalchemy import text
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timedelta, timezone
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.student import Student
from app.models.pei import PEI, PEIObjetivo
from app.models.atividade_pei import AtividadePEI, SequenciaObjetivo, TipoAtividade, StatusAtividade
from app.models.material import Material, MaterialAluno, StatusMaterial, TipoMaterial
from app.models.prova import Prova, QuestaoGerada, ProvaAluno, StatusProva, StatusProvaAluno, TipoQuestao, DificuldadeQuestao

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F
from app.core.anthropic_client import get_default_model
from app.services import sintese_jornada_service
from app.services import estrategias_service


# Cliente Anthropic
_client = None
# Era "claude-3-5-sonnet-20241022", que a Anthropic aposentou em 28/10/2025.
# Sem uso registrado neste caminho (nenhum material com tipo_geracao='ia_pei' no
# banco), entao era bug latente, nao falha ativa.
# Resolvido em tempo de import a partir de settings.LLM_MODEL.
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
            print(f"[AVISO] Erro ao inicializar Anthropic: {e}")
    return _client


class CalendarioAtividadesService:
    """
    Serviço para gerar calendário de atividades baseado nos objetivos do PEI.
    Para cada objetivo, gera materiais, exercícios e prova de avaliação.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.client = get_anthropic_client()
    
    def _keep_alive(self):
        """Mantém a conexão viva após operações demoradas"""
        try:
            self.db.execute(text("SELECT 1"))
        except Exception as e:
            print(f"[AVISO] Reconectando ao banco: {e}")
            self.db.rollback()
    
    async def gerar_calendario_completo(
        self,
        pei_id: int,
        user_id: int,
        data_inicio: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Gera o calendário completo de atividades para um PEI.
        Cria materiais, exercícios e provas para cada objetivo.
        """
        
        # Buscar PEI com objetivos
        pei = self.db.query(PEI).filter(PEI.id == pei_id).first()
        if not pei:
            raise Exception("PEI não encontrado")
        
        if not pei.objetivos:
            raise Exception("PEI não possui objetivos")
        
        # Buscar aluno
        student = self.db.query(Student).filter(Student.id == pei.student_id).first()
        if not student:
            raise Exception("Aluno não encontrado")

        # Jornada terapeutica como parametro (perfil vivo). "" se nao houver.
        self._ctx_jornada = sintese_jornada_service.contexto_para_prompt(self.db, pei.student_id)
        # Biblioteca de Estrategias de Adaptacao (KB curada) como parametro. "" se nao houver.
        self._ctx_kb = estrategias_service.diretrizes_para_diagnostico(self.db, student.diagnosis or {}, getattr(student, "escola_id", None))
        
        # Data de início (padrão: próxima segunda-feira)
        if not data_inicio:
            hoje = date.today()
            dias_ate_segunda = (7 - hoje.weekday()) % 7
            if dias_ate_segunda == 0:
                dias_ate_segunda = 7
            data_inicio = hoje + timedelta(days=dias_ate_segunda)
        
        # Agrupar objetivos por trimestre
        objetivos_por_trimestre = {}
        for obj in pei.objetivos:
            tri = obj.trimestre or 1
            if tri not in objetivos_por_trimestre:
                objetivos_por_trimestre[tri] = []
            objetivos_por_trimestre[tri].append(obj)
        
        # Calcular datas dos trimestres
        ano = int(pei.ano_letivo) if pei.ano_letivo else date.today().year
        trimestres_datas = {
            1: {"inicio": date(ano, 2, 1), "fim": date(ano, 4, 30)},
            2: {"inicio": date(ano, 5, 1), "fim": date(ano, 7, 15)},
            3: {"inicio": date(ano, 8, 1), "fim": date(ano, 10, 15)},
            4: {"inicio": date(ano, 10, 16), "fim": date(ano, 12, 15)},
        }
        
        resultado = {
            "pei_id": pei_id,
            "student_id": pei.student_id,
            "student_name": student.name,
            "total_objetivos": len(pei.objetivos),
            "atividades_geradas": 0,
            "materiais_gerados": 0,
            "provas_geradas": 0,
            "calendario": []
        }
        
        # Para cada trimestre e seus objetivos
        for trimestre, objetivos in sorted(objetivos_por_trimestre.items()):
            datas_tri = trimestres_datas.get(trimestre, trimestres_datas[1])
            
            # Distribuir objetivos ao longo do trimestre
            semanas_disponiveis = self._calcular_semanas(datas_tri["inicio"], datas_tri["fim"])
            semanas_por_objetivo = max(2, semanas_disponiveis // len(objetivos))
            
            data_atual = datas_tri["inicio"]
            
            for obj in objetivos:
                # Gerar sequência de atividades para este objetivo
                atividades_obj = await self._gerar_atividades_objetivo(
                    pei=pei,
                    objetivo=obj,
                    student=student,
                    data_inicio=data_atual,
                    semanas=semanas_por_objetivo,
                    user_id=user_id
                )
                
                resultado["atividades_geradas"] += atividades_obj["total_atividades"]
                resultado["materiais_gerados"] += atividades_obj["materiais_criados"]
                resultado["provas_geradas"] += atividades_obj["provas_criadas"]
                resultado["calendario"].extend(atividades_obj["atividades"])
                
                # Avançar data para próximo objetivo
                data_atual = data_atual + timedelta(weeks=semanas_por_objetivo)
        
        self.db.commit()
        
        return resultado
    
    async def _gerar_atividades_objetivo(
        self,
        pei: PEI,
        objetivo: PEIObjetivo,
        student: Student,
        data_inicio: date,
        semanas: int,
        user_id: int
    ) -> Dict[str, Any]:
        """
        Gera todas as atividades para um objetivo específico.
        Inclui: materiais de estudo, exercícios e prova final.
        """
        
        resultado = {
            "objetivo_id": objetivo.id,
            "objetivo_titulo": objetivo.titulo,
            "total_atividades": 0,
            "materiais_criados": 0,
            "provas_criadas": 0,
            "atividades": []
        }
        
        # Planejar distribuição das atividades
        # Semana 1-2: Material 1 + Exercícios
        # Semana 2-3: Material 2 + Exercícios  
        # Semana final: Revisão + Prova
        
        dias_por_semana = 5  # Segunda a sexta
        
        # ========================================
        # MATERIAL 1 - Introdução ao conteúdo
        # ========================================
        data_material1 = data_inicio
        material1 = await self._criar_material_para_objetivo(
            pei=pei,
            objetivo=objetivo,
            student=student,
            numero=1,
            tipo="introducao",
            user_id=user_id
        )
        
        if material1:
            # Reconectar se necessário
            try:
                self.db.execute(text("SELECT 1"))
            except:
                self.db.rollback()
            
            atividade1 = AtividadePEI(
                pei_id=pei.id,
                objetivo_id=objetivo.id,
                student_id=student.id,
                material_id=material1.get("material_id"),
                material_aluno_id=material1.get("material_aluno_id"),
                tipo=TipoAtividade.MATERIAL,
                titulo=f"📚 {material1.get('titulo', 'Material de Introdução')}",
                descricao=f"Estudar o material introdutório sobre: {objetivo.titulo}",
                data_programada=data_material1,
                duracao_estimada_min=30,
                ordem_sequencial=1,
                instrucoes="Leia o material com atenção. Use os recursos visuais para melhor compreensão.",
                adaptacoes=objetivo.adaptacoes,
                created_by=user_id
            )
            self.db.add(atividade1)
            resultado["atividades"].append({
                "data": data_material1.isoformat(),
                "tipo": "material",
                "titulo": atividade1.titulo
            })
            resultado["total_atividades"] += 1
            resultado["materiais_criados"] += 1
        
        # ========================================
        # EXERCÍCIOS 1 - Prática inicial
        # ========================================
        data_exercicio1 = data_material1 + timedelta(days=2)
        atividade_ex1 = AtividadePEI(
            pei_id=pei.id,
            objetivo_id=objetivo.id,
            student_id=student.id,
            tipo=TipoAtividade.EXERCICIO,
            titulo=f"📝 Exercícios: {objetivo.titulo[:50]}...",
            descricao=f"Exercícios práticos para fixação do conteúdo introdutório",
            data_programada=data_exercicio1,
            duracao_estimada_min=20,
            ordem_sequencial=2,
            instrucoes="Complete os exercícios no seu ritmo. Peça ajuda se precisar.",
            adaptacoes=objetivo.adaptacoes,
            created_by=user_id
        )
        self.db.add(atividade_ex1)
        resultado["atividades"].append({
            "data": data_exercicio1.isoformat(),
            "tipo": "exercicio",
            "titulo": atividade_ex1.titulo
        })
        resultado["total_atividades"] += 1
        
        # ========================================
        # MATERIAL 2 - Aprofundamento
        # ========================================
        data_material2 = data_inicio + timedelta(weeks=1)
        material2 = await self._criar_material_para_objetivo(
            pei=pei,
            objetivo=objetivo,
            student=student,
            numero=2,
            tipo="aprofundamento",
            user_id=user_id
        )
        
        if material2:
            atividade2 = AtividadePEI(
                pei_id=pei.id,
                objetivo_id=objetivo.id,
                student_id=student.id,
                material_id=material2.get("material_id"),
                material_aluno_id=material2.get("material_aluno_id"),
                tipo=TipoAtividade.MATERIAL,
                titulo=f"📚 {material2.get('titulo', 'Material de Aprofundamento')}",
                descricao=f"Aprofundar conhecimentos sobre: {objetivo.titulo}",
                data_programada=data_material2,
                duracao_estimada_min=35,
                ordem_sequencial=3,
                instrucoes="Este material aprofunda o conteúdo anterior. Revise se necessário.",
                adaptacoes=objetivo.adaptacoes,
                created_by=user_id
            )
            self.db.add(atividade2)
            resultado["atividades"].append({
                "data": data_material2.isoformat(),
                "tipo": "material",
                "titulo": atividade2.titulo
            })
            resultado["total_atividades"] += 1
            resultado["materiais_criados"] += 1
        
        # ========================================
        # EXERCÍCIOS 2 - Prática avançada
        # ========================================
        data_exercicio2 = data_material2 + timedelta(days=2)
        atividade_ex2 = AtividadePEI(
            pei_id=pei.id,
            objetivo_id=objetivo.id,
            student_id=student.id,
            tipo=TipoAtividade.EXERCICIO,
            titulo=f"📝 Exercícios Avançados: {objetivo.titulo[:40]}...",
            descricao=f"Exercícios de fixação do conteúdo avançado",
            data_programada=data_exercicio2,
            duracao_estimada_min=25,
            ordem_sequencial=4,
            instrucoes="Estes exercícios são um pouco mais desafiadores. Faça com calma.",
            adaptacoes=objetivo.adaptacoes,
            created_by=user_id
        )
        self.db.add(atividade_ex2)
        resultado["atividades"].append({
            "data": data_exercicio2.isoformat(),
            "tipo": "exercicio",
            "titulo": atividade_ex2.titulo
        })
        resultado["total_atividades"] += 1
        
        # ========================================
        # REVISÃO
        # ========================================
        data_revisao = data_inicio + timedelta(weeks=semanas-1)
        atividade_rev = AtividadePEI(
            pei_id=pei.id,
            objetivo_id=objetivo.id,
            student_id=student.id,
            tipo=TipoAtividade.REVISAO,
            titulo=f"🔄 Revisão: {objetivo.titulo[:50]}...",
            descricao=f"Revisão de todo o conteúdo antes da avaliação",
            data_programada=data_revisao,
            duracao_estimada_min=20,
            ordem_sequencial=5,
            instrucoes="Revise os materiais e exercícios anteriores. Tire dúvidas com o professor.",
            adaptacoes=objetivo.adaptacoes,
            created_by=user_id
        )
        self.db.add(atividade_rev)
        resultado["atividades"].append({
            "data": data_revisao.isoformat(),
            "tipo": "revisao",
            "titulo": atividade_rev.titulo
        })
        resultado["total_atividades"] += 1
        
        # ========================================
        # PROVA - Avaliação do objetivo
        # ========================================
        data_prova = data_revisao + timedelta(days=2)
        prova_info = await self._criar_prova_para_objetivo(
            pei=pei,
            objetivo=objetivo,
            student=student,
            user_id=user_id
        )
        
        if prova_info:
            atividade_prova = AtividadePEI(
                pei_id=pei.id,
                objetivo_id=objetivo.id,
                student_id=student.id,
                prova_id=prova_info.get("prova_id"),
                prova_aluno_id=prova_info.get("prova_aluno_id"),
                tipo=TipoAtividade.PROVA,
                titulo=f"✅ Avaliação: {objetivo.titulo[:50]}...",
                descricao=f"Prova para avaliar o domínio do objetivo",
                data_programada=data_prova,
                duracao_estimada_min=30,
                ordem_sequencial=6,
                instrucoes="Faça a prova com calma. Leia cada questão com atenção.",
                adaptacoes=objetivo.adaptacoes,
                created_by=user_id
            )
            self.db.add(atividade_prova)
            resultado["atividades"].append({
                "data": data_prova.isoformat(),
                "tipo": "prova",
                "titulo": atividade_prova.titulo
            })
            resultado["total_atividades"] += 1
            resultado["provas_criadas"] += 1
        
        # Criar registro de sequência
        sequencia = SequenciaObjetivo(
            objetivo_id=objetivo.id,
            total_semanas=semanas,
            total_materiais=2,
            total_exercicios=2,
            incluir_prova=True,
            plano_sequencial={
                "data_inicio": data_inicio.isoformat(),
                "data_fim": data_prova.isoformat(),
                "etapas": [
                    {"semana": 1, "atividades": ["material_1", "exercicio_1"]},
                    {"semana": 2, "atividades": ["material_2", "exercicio_2"]},
                    {"semana": semanas, "atividades": ["revisao", "prova"]}
                ]
            },
            gerado=True,
            data_geracao=datetime.now(timezone.utc)
        )
        self.db.add(sequencia)
        
        return resultado
    
    @tm.feature(F.CALENDARIO_ATIVIDADES, entity_type="pei", entity_from="pei")
    async def _criar_material_para_objetivo(
        self,
        pei: PEI,
        objetivo: PEIObjetivo,
        student: Student,
        numero: int,
        tipo: str,
        user_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Cria um material de estudo para o objetivo usando IA.
        """
        
        if not self.client:
            return None
        
        # Preparar perfil do aluno
        diagnosticos = student.diagnosis or {}
        adaptacoes = objetivo.adaptacoes or []
        
        # Prompt para gerar o material
        if tipo == "introducao":
            tipo_descricao = "introdutório e básico"
            complexidade = "Use linguagem simples e exemplos do cotidiano"
        else:
            tipo_descricao = "de aprofundamento"
            complexidade = "Explore mais detalhes e conexões com outros conceitos"
        
        prompt = f"""Crie um material de estudo {tipo_descricao} para um aluno com necessidades especiais.

## ALUNO:
- Nome: {student.name}
- Ano escolar: {student.grade_level}
- Diagnósticos: {json.dumps(diagnosticos, ensure_ascii=False)}
{getattr(self, "_ctx_jornada", "")}
{getattr(self, "_ctx_kb", "")}

## OBJETIVO DE APRENDIZAGEM:
- Código BNCC: {objetivo.codigo_bncc or 'N/A'}
- Título: {objetivo.titulo}
- Descrição: {objetivo.descricao}

## ADAPTAÇÕES NECESSÁRIAS:
{json.dumps(adaptacoes, ensure_ascii=False) if adaptacoes else 'Adaptar conforme o perfil'}

## INSTRUÇÕES:
{complexidade}
- Divida em seções claras
- Use recursos visuais (emojis, listas)
- Inclua exemplos práticos
- Linguagem adequada para {student.grade_level}

## FORMATO DE RESPOSTA (JSON):
{{
    "titulo": "Título do material",
    "introducao": "Parágrafo de introdução",
    "secoes": [
        {{
            "titulo": "Título da seção",
            "conteudo": "Conteúdo da seção com explicações claras",
            "exemplo": "Exemplo prático"
        }}
    ],
    "resumo": "Resumo dos pontos principais",
    "dicas": ["Dica 1", "Dica 2"]
}}

Retorne APENAS o JSON."""

        try:
            message = self.client.messages.create(
                model=MODELO_IA,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text.strip()
            response_text = self._limpar_json(response_text)
            conteudo = json.loads(response_text)
            
            # Manter conexão viva antes de inserir
            self._keep_alive()
            
            # Criar material no banco
            material = Material(
                titulo=conteudo.get("titulo", f"Material {numero}: {objetivo.titulo[:50]}"),
                descricao=f"Material {tipo} para o objetivo: {objetivo.titulo}",
                conteudo_prompt=f"Objetivo PEI: {objetivo.titulo}",
                tipo=TipoMaterial.VISUAL,
                materia=objetivo.area or "geral",
                serie_nivel=student.grade_level,
                metadados={"conteudo": conteudo, "tipo_geracao": "ia_pei"},
                status=StatusMaterial.DISPONIVEL,
                criado_por_id=user_id
            )
            self.db.add(material)
            self.db.flush()
            
            # Associar ao aluno
            material_aluno = MaterialAluno(
                material_id=material.id,
                aluno_id=student.id
            )
            self.db.add(material_aluno)
            self.db.flush()
            
            return {
                "material_id": material.id,
                "material_aluno_id": material_aluno.id,
                "titulo": conteudo.get("titulo")
            }
            
        except Exception as e:
            print(f"[ERRO] Criando material: {e}")
            return None
    
    @tm.feature(F.CALENDARIO_ATIVIDADES, entity_type="pei", entity_from="pei")
    async def _criar_prova_para_objetivo(
        self,
        pei: PEI,
        objetivo: PEIObjetivo,
        student: Student,
        user_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Cria uma prova de avaliação para o objetivo usando IA.
        """
        
        if not self.client:
            return None
        
        diagnosticos = student.diagnosis or {}
        adaptacoes = objetivo.adaptacoes or []
        
        prompt = f"""Crie uma prova de avaliação adaptada para um aluno com necessidades especiais.

## ALUNO:
- Nome: {student.name}
- Ano escolar: {student.grade_level}
- Diagnósticos: {json.dumps(diagnosticos, ensure_ascii=False)}

## OBJETIVO A AVALIAR:
- Código BNCC: {objetivo.codigo_bncc or 'N/A'}
- Título: {objetivo.titulo}
- Descrição: {objetivo.descricao}
- Meta: {objetivo.meta_especifica}

## ADAPTAÇÕES:
{json.dumps(adaptacoes, ensure_ascii=False) if adaptacoes else 'Adaptar conforme perfil'}

## INSTRUÇÕES:
- Crie 5 questões progressivas (fácil → difícil)
- Use linguagem clara e direta
- Inclua recursos visuais nas questões
- Questões objetivas (múltipla escolha) são preferíveis
- Cada questão deve ter 4 alternativas

## FORMATO DE RESPOSTA (JSON):
{{
    "titulo": "Título da prova",
    "instrucoes": "Instruções para o aluno",
    "questoes": [
        {{
            "numero": 1,
            "enunciado": "Texto da questão",
            "tipo": "multipla_escolha",
            "alternativas": [
                {{"letra": "A", "texto": "Alternativa A"}},
                {{"letra": "B", "texto": "Alternativa B"}},
                {{"letra": "C", "texto": "Alternativa C"}},
                {{"letra": "D", "texto": "Alternativa D"}}
            ],
            "resposta_correta": "A",
            "dificuldade": "facil",
            "habilidade_avaliada": "Descrição do que avalia"
        }}
    ]
}}

Retorne APENAS o JSON."""

        try:
            message = self.client.messages.create(
                model=MODELO_IA,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = message.content[0].text.strip()
            response_text = self._limpar_json(response_text)
            prova_data = json.loads(response_text)
            
            # Manter conexão viva antes de inserir
            self._keep_alive()
            
            # Criar prova no banco
            prova = Prova(
                titulo=prova_data.get("titulo", f"Avaliação: {objetivo.titulo[:50]}"),
                descricao=f"Avaliação do objetivo PEI: {objetivo.titulo}",
                conteudo_prompt=f"Objetivo: {objetivo.titulo}\nDescrição: {objetivo.descricao}",
                materia=objetivo.area or "geral",
                serie_nivel=student.grade_level,
                quantidade_questoes=len(prova_data.get("questoes", [])),
                status=StatusProva.ATIVA,
                criado_por_id=user_id
            )
            self.db.add(prova)
            self.db.flush()
            
            # Criar questões
            for q in prova_data.get("questoes", []):
                questao = QuestaoGerada(
                    prova_id=prova.id,
                    numero=q.get("numero", 1),
                    enunciado=q.get("enunciado", ""),
                    tipo=TipoQuestao.MULTIPLA_ESCOLHA,
                    dificuldade=DificuldadeQuestao.MEDIO,
                    opcoes=[alt.get("texto", alt) for alt in q.get("alternativas", [])],
                    resposta_correta=q.get("resposta_correta", "A"),
                    explicacao=q.get("habilidade_avaliada")
                )
                self.db.add(questao)
            
            # Associar ao aluno
            prova_aluno = ProvaAluno(
                prova_id=prova.id,
                aluno_id=student.id,
                status=StatusProvaAluno.PENDENTE
            )
            self.db.add(prova_aluno)
            self.db.flush()
            
            return {
                "prova_id": prova.id,
                "prova_aluno_id": prova_aluno.id,
                "titulo": prova_data.get("titulo")
            }
            
        except Exception as e:
            print(f"[ERRO] Criando prova: {e}")
            return None
    
    def _calcular_semanas(self, data_inicio: date, data_fim: date) -> int:
        """Calcula o número de semanas entre duas datas"""
        dias = (data_fim - data_inicio).days
        return max(1, dias // 7)
    
    def _limpar_json(self, text: str) -> str:
        """Remove marcadores de código"""
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()
    
    def listar_atividades_aluno(
        self,
        student_id: int,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        status: Optional[StatusAtividade] = None
    ) -> List[AtividadePEI]:
        """
        Lista atividades de um aluno com filtros opcionais.
        """
        query = self.db.query(AtividadePEI).filter(
            AtividadePEI.student_id == student_id
        )
        
        if data_inicio:
            query = query.filter(AtividadePEI.data_programada >= data_inicio)
        
        if data_fim:
            query = query.filter(AtividadePEI.data_programada <= data_fim)
        
        if status:
            query = query.filter(AtividadePEI.status == status)
        
        return query.order_by(AtividadePEI.data_programada).all()
    
    def listar_atividades_semana(
        self,
        student_id: int,
        data_referencia: Optional[date] = None
    ) -> Dict[str, Any]:
        """
        Lista atividades da semana de um aluno.
        """
        if not data_referencia:
            data_referencia = date.today()
        
        # Calcular início e fim da semana
        inicio_semana = data_referencia - timedelta(days=data_referencia.weekday())
        fim_semana = inicio_semana + timedelta(days=6)
        
        atividades = self.listar_atividades_aluno(
            student_id=student_id,
            data_inicio=inicio_semana,
            data_fim=fim_semana
        )
        
        # Agrupar por dia
        por_dia = {}
        for ativ in atividades:
            dia = ativ.data_programada.isoformat()
            if dia not in por_dia:
                por_dia[dia] = []
            por_dia[dia].append({
                "id": ativ.id,
                "tipo": ativ.tipo.value,
                "titulo": ativ.titulo,
                "status": ativ.status.value,
                "duracao_min": ativ.duracao_estimada_min
            })
        
        return {
            "semana_inicio": inicio_semana.isoformat(),
            "semana_fim": fim_semana.isoformat(),
            "total_atividades": len(atividades),
            "pendentes": sum(1 for a in atividades if a.status == StatusAtividade.PENDENTE),
            "concluidas": sum(1 for a in atividades if a.status == StatusAtividade.CONCLUIDA),
            "atividades_por_dia": por_dia
        }
    
    def atualizar_status_atividade(
        self,
        atividade_id: int,
        novo_status: StatusAtividade,
        observacoes: Optional[str] = None
    ) -> AtividadePEI:
        """
        Atualiza o status de uma atividade.
        """
        atividade = self.db.query(AtividadePEI).filter(
            AtividadePEI.id == atividade_id
        ).first()
        
        if not atividade:
            raise Exception("Atividade não encontrada")
        
        atividade.status = novo_status
        
        if novo_status == StatusAtividade.EM_ANDAMENTO and not atividade.data_inicio:
            atividade.data_inicio = datetime.now(timezone.utc)
        
        if novo_status == StatusAtividade.CONCLUIDA:
            atividade.data_conclusao = datetime.now(timezone.utc)
        
        if observacoes:
            atividade.observacoes_professor = observacoes
        
        self.db.commit()
        self.db.refresh(atividade)
        
        return atividade
