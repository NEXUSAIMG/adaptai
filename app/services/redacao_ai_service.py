"""
📝 AdaptAI - Servico de IA para Redacoes ENEM
Geracao de temas atuais e correcao por competencias.

Usa cliente Anthropic centralizado (core/anthropic_client.py).
"""
import os
import json
import re
from app.core.logging_config import get_logger
from datetime import datetime
from typing import Dict, List, Any, Optional
from app.core.anthropic_client import get_anthropic_client, get_default_model, sistema_cacheado

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm

logger = get_logger(__name__)
from app.core.features import F

# NOTA: antes este modulo instanciava Anthropic() em module-level, o que causava
# erro na importacao se ANTHROPIC_API_KEY nao estivesse setada ainda.
# Agora usamos o singleton lazy via get_anthropic_client().


class RedacaoAIService:
    """
    Serviço de IA para redações no estilo ENEM
    """
    
    COMPETENCIAS_ENEM = {
        1: {
            "nome": "Domínio da Norma Culta",
            "descricao": "Demonstrar domínio da modalidade escrita formal da língua portuguesa",
            "criterios": [
                "Ortografia e acentuação",
                "Concordância verbal e nominal",
                "Regência verbal e nominal",
                "Pontuação",
                "Colocação pronominal",
                "Uso de crase"
            ]
        },
        2: {
            "nome": "Compreensão da Proposta",
            "descricao": "Compreender a proposta de redação e aplicar conceitos das várias áreas de conhecimento para desenvolver o tema, dentro dos limites estruturais do texto dissertativo-argumentativo em prosa",
            "criterios": [
                "Atendimento ao tema proposto",
                "Estrutura dissertativo-argumentativa",
                "Uso de repertório sociocultural",
                "Autoria e originalidade"
            ]
        },
        3: {
            "nome": "Argumentação",
            "descricao": "Selecionar, relacionar, organizar e interpretar informações, fatos, opiniões e argumentos em defesa de um ponto de vista",
            "criterios": [
                "Seleção de argumentos",
                "Consistência argumentativa",
                "Organização das ideias",
                "Progressão temática"
            ]
        },
        4: {
            "nome": "Coesão Textual",
            "descricao": "Demonstrar conhecimento dos mecanismos linguísticos necessários para a construção da argumentação",
            "criterios": [
                "Uso de conectivos",
                "Referenciação",
                "Articulação entre parágrafos",
                "Sequenciação lógica"
            ]
        },
        5: {
            "nome": "Proposta de Intervenção",
            "descricao": "Elaborar proposta de intervenção para o problema abordado, respeitando os direitos humanos",
            "criterios": [
                "Ação (O que fazer)",
                "Agente (Quem vai fazer)",
                "Modo/Meio (Como fazer)",
                "Efeito/Finalidade (Para que)",
                "Detalhamento de um dos elementos",
                "Respeito aos direitos humanos"
            ]
        }
    }
    
    NIVEIS_NOTA = [
        (200, "Excelente"),
        (160, "Bom"),
        (120, "Mediano"),
        (80, "Insuficiente"),
        (40, "Precário"),
        (0, "Zero")
    ]

    # Descritores (resumidos) de cada nivel (0-200) por competencia, conforme a
    # matriz de referencia do ENEM. Usados para montar a rubrica detalhada.
    RUBRICA_NIVEIS = {
        1: {
            200: "Excelente dominio da norma culta, com no maximo desvios eventuais.",
            160: "Bom dominio da norma culta, com poucos desvios gramaticais e de convencoes.",
            120: "Dominio mediano da norma culta, com alguns desvios.",
            80: "Dominio insuficiente da norma culta, com muitos desvios.",
            40: "Dominio precario da norma culta, com desvios sistematicos e frequentes.",
            0: "Desconhecimento da norma culta.",
        },
        2: {
            200: "Desenvolve o tema com repertorio produtivo e estrutura dissertativo-argumentativa completa.",
            160: "Desenvolve o tema com repertorio pertinente e estrutura completa.",
            120: "Desenvolve o tema de forma mediana, com repertorio baseado nos textos motivadores.",
            80: "Desenvolve o tema de forma tangencial ou com dominio insuficiente do tipo textual.",
            40: "Tangencia o tema ou apresenta tracos de outros tipos textuais.",
            0: "Fuga ao tema ou nao atendimento a estrutura dissertativo-argumentativa.",
        },
        3: {
            200: "Argumentos consistentes e bem organizados, com autoria clara na defesa do ponto de vista.",
            160: "Argumentos consistentes, com organizacao adequada das ideias.",
            120: "Argumentacao previsivel, com organizacao mediana.",
            80: "Argumentacao fragil, com ideias pouco desenvolvidas.",
            40: "Informacoes e argumentos pouco relacionados ao tema.",
            0: "Ausencia de defesa de um ponto de vista.",
        },
        4: {
            200: "Articula bem as partes do texto, com repertorio diversificado de conectivos.",
            160: "Articula as partes do texto, com poucas inadequacoes de coesao.",
            120: "Articula as partes de forma mediana, com algumas inadequacoes.",
            80: "Articula as partes de forma insuficiente, com muitas inadequacoes.",
            40: "Articulacao precaria entre as partes do texto.",
            0: "Ausencia de articulacao entre as partes.",
        },
        5: {
            200: "Proposta completa (acao, agente, modo, efeito e detalhamento), respeitando os direitos humanos.",
            160: "Proposta com quatro dos cinco elementos, respeitando os direitos humanos.",
            120: "Proposta com tres dos cinco elementos.",
            80: "Proposta com dois dos cinco elementos.",
            40: "Proposta vaga ou tangencial (apenas um elemento).",
            0: "Ausencia de proposta ou desrespeito aos direitos humanos.",
        },
    }

    def _descritor_nivel(self, competencia: int, nota: int) -> str:
        """Descritor do nivel da rubrica para uma competencia, dada a nota (0-200)."""
        nota = nota or 0
        nivel_pontos = max(0, min(200, round(nota / 40) * 40))
        return self.RUBRICA_NIVEIS.get(competencia, {}).get(nivel_pontos, "")

    def get_rubrica(self) -> Dict[str, Any]:
        """Retorna a rubrica detalhada das 5 competencias (criterios + descritores de nivel)."""
        rubrica = []
        for num in range(1, 6):
            comp = self.COMPETENCIAS_ENEM[num]
            rubrica.append({
                "numero": num,
                "nome": comp["nome"],
                "descricao": comp["descricao"],
                "criterios": comp["criterios"],
                "niveis": [
                    {"pontos": pts, "descritor": desc}
                    for pts, desc in sorted(self.RUBRICA_NIVEIS[num].items(), reverse=True)
                ],
            })
        return {"competencias": rubrica, "nota_maxima": 1000, "pontos_por_competencia": 200}

    def _classificar_nivel(self, nota: int) -> str:
        """Classifica o nível baseado na nota"""
        for limite, nivel in self.NIVEIS_NOTA:
            if nota >= limite:
                return nivel
        return "Zero"
    
    def _classificar_nivel_geral(self, nota_final: int) -> str:
        """Classifica nível geral (0-1000)"""
        if nota_final >= 900:
            return "Excelente"
        elif nota_final >= 700:
            return "Muito Bom"
        elif nota_final >= 500:
            return "Bom"
        elif nota_final >= 300:
            return "Regular"
        else:
            return "Insuficiente"

    @tm.feature(F.REDACAO_TEMA)
    async def gerar_tema_atual(
        self,
        area_tematica: Optional[str] = None,
        nivel_dificuldade: str = "medio"
    ) -> Dict[str, Any]:
        """
        Gera um tema de redação ATUAL usando IA
        A IA escolhe um tema relevante e contemporâneo
        """
        
        areas = area_tematica or "qualquer área relevante (tecnologia, meio ambiente, sociedade, saúde, educação, cultura)"
        
        prompt = f"""Você é um especialista em elaboração de provas do ENEM. Crie um tema de redação ATUAL e RELEVANTE para o Brasil em {datetime.now().year}.

ÁREA TEMÁTICA: {areas}
NÍVEL DE DIFICULDADE: {nivel_dificuldade}

O tema deve:
1. Ser ATUAL e relevante para a sociedade brasileira
2. Permitir argumentação de diferentes perspectivas
3. Exigir proposta de intervenção social
4. Estar no formato do ENEM

Responda APENAS com um JSON válido no seguinte formato:
{{
    "titulo": "Título do tema em formato de frase (ex: 'Os desafios da educação digital no Brasil')",
    "tema": "Descrição detalhada do tema e seu contexto social",
    "proposta": "A partir da leitura dos textos motivadores e com base nos conhecimentos construídos ao longo de sua formação, redija texto dissertativo-argumentativo em modalidade escrita formal da língua portuguesa sobre o tema [TEMA], apresentando proposta de intervenção que respeite os direitos humanos. Selecione, organize e relacione, de forma coerente e coesa, argumentos e fatos para defesa de seu ponto de vista.",
    "texto_motivador_1": "Primeiro texto motivador (citação, dado estatístico ou trecho de reportagem REAL e ATUAL)",
    "texto_motivador_2": "Segundo texto motivador (perspectiva diferente sobre o tema)",
    "texto_motivador_3": "Terceiro texto motivador (pode ser um dado numérico ou infográfico descrito)",
    "texto_motivador_4": "Quarto texto motivador (opcional, pode ser null)",
    "area_tematica": "Nome da área (ex: 'Tecnologia e Sociedade')",
    "palavras_chave": ["palavra1", "palavra2", "palavra3"]
}}

IMPORTANTE: 
- Use dados e referências REAIS e ATUAIS
- O tema deve ser algo que está em discussão na sociedade brasileira AGORA
- Os textos motivadores devem parecer autênticos (com fontes citadas)
"""

        try:
            response = get_anthropic_client().messages.create(
                model=get_default_model(),
                # TC-055 subiu de 2000 -> 3000 reconhecendo o risco de truncamento,
                # mas SEM checar stop_reason. Um tema ENEM completo tem titulo,
                # tema, proposta e ATE QUATRO textos motivadores densos: em pt-BR
                # isso passa de 3000 com folga. O JSON vinha cortado, o regex
                # `\{[\s\S]*\}` nao encontrava o fecha-chaves e a geracao morria
                # em "Resposta da IA nao contem JSON valido". (2026-08-11)
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Truncamento e a causa mais comum de falha aqui — detectar antes de
            # tentar parsear evita uma mensagem generica que nao ajuda ninguem.
            if getattr(response, "stop_reason", None) == "max_tokens":
                logger.error(
                    "Geracao de tema de redacao truncada no limite de tokens",
                    extra={"area_tematica": area_tematica},
                )
                raise ValueError(
                    "A geração do tema foi cortada por exceder o limite de tamanho. "
                    "Tente novamente; se persistir, gere com menos textos motivadores."
                )

            content = response.content[0].text.strip()
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            # Extrair JSON da resposta
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                tema_data = json.loads(json_match.group())
                tema_data["nivel_dificuldade"] = nivel_dificuldade
                return tema_data
            else:
                logger.error(
                    "Tema de redacao sem JSON reconhecivel",
                    extra={"preview": content[:300]},
                )
                raise ValueError(
                    "A IA respondeu em um formato inesperado ao gerar o tema. "
                    "Tente novamente."
                )
                
        except Exception as e:
            print(f"[ERRO] Erro ao gerar tema: {e}")
            raise

    @tm.feature(F.REDACAO_CORRECAO)
    async def corrigir_redacao_enem(
        self,
        texto_redacao: str,
        tema: Dict[str, Any],
        aluno_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Corrige redação no padrão ENEM com as 5 competências
        Retorna notas de 0-200 por competência e feedback detalhado
        """
        
        # Contar linhas e palavras
        linhas = texto_redacao.strip().split('\n')
        quantidade_linhas = len([l for l in linhas if l.strip()])
        quantidade_palavras = len(texto_redacao.split())
        
        # Verificar se está muito curta.
        # TC-144: quem barra o texto curto e POST /redacoes/corrigir, ANTES de
        # persistir e de gastar tokens, com um 400 explicando quantas palavras
        # faltam. Este piso continua aqui como ultima linha de defesa para
        # chamadas diretas ao servico - se cair aqui, ja e caso de anulacao.
        if quantidade_palavras < 50:
            return self._redacao_anulada("Texto muito curto (menos de 50 palavras)")
        
        # Preparar contexto do aluno
        contexto_aluno = ""
        if aluno_info:
            contexto_aluno = f"""
INFORMAÇÕES DO ALUNO:
- Nome: {aluno_info.get('nome', 'Não informado')}
- Série: {aluno_info.get('serie', 'Não informada')}
- Diagnóstico: {aluno_info.get('diagnostico', 'Nenhum')}

Considere o nível escolar e possíveis necessidades especiais ao dar feedback (seja encorajador mas honesto).
"""

        # PROMPT CACHING: a rubrica + instrucoes de correcao sao IDENTICAS em toda
        # correcao -> viram o system estatico (cacheado). So o variavel (tema +
        # redacao do aluno) vai na mensagem. Corrigir uma turma em sequencia le a
        # rubrica do cache a ~10% do custo. Ver core/anthropic_client.sistema_cacheado.
        system_prompt = """Você é um corretor oficial do ENEM com vasta experiência. Corrija a redação seguindo RIGOROSAMENTE os critérios do ENEM.

=== INSTRUÇÕES DE CORREÇÃO ===
Analise a redação nas 5 COMPETÊNCIAS do ENEM. Cada competência vale de 0 a 200 pontos (em múltiplos de 40: 0, 40, 80, 120, 160, 200).

COMPETÊNCIA 1 - Domínio da norma culta:
- Ortografia, acentuação, concordância, regência, pontuação

COMPETÊNCIA 2 - Compreensão da proposta:
- Atendimento ao tema, estrutura dissertativo-argumentativa, repertório sociocultural

COMPETÊNCIA 3 - Argumentação:
- Seleção de argumentos, consistência, organização, progressão

COMPETÊNCIA 4 - Coesão textual:
- Conectivos, referenciação, articulação entre parágrafos

COMPETÊNCIA 5 - Proposta de intervenção:
- Deve ter: AÇÃO + AGENTE + MODO + EFEITO + DETALHAMENTO
- Deve respeitar direitos humanos

Responda APENAS com um JSON válido:
{
    "nota_competencia_1": <0-200>,
    "feedback_competencia_1": "Feedback detalhado da competência 1",
    "nota_competencia_2": <0-200>,
    "feedback_competencia_2": "Feedback detalhado da competência 2",
    "nota_competencia_3": <0-200>,
    "feedback_competencia_3": "Feedback detalhado da competência 3",
    "nota_competencia_4": <0-200>,
    "feedback_competencia_4": "Feedback detalhado da competência 4",
    "nota_competencia_5": <0-200>,
    "feedback_competencia_5": "Feedback detalhado da competência 5",
    "feedback_geral": "Análise geral da redação em 2-3 parágrafos",
    "pontos_fortes": ["ponto forte 1", "ponto forte 2", "ponto forte 3"],
    "pontos_melhoria": ["ponto a melhorar 1", "ponto a melhorar 2", "ponto a melhorar 3"],
    "sugestoes": ["sugestão de estudo 1", "sugestão de estudo 2"]
}

IMPORTANTE:
- Seja JUSTO mas RIGOROSO como um corretor real do ENEM
- O feedback deve ser EDUCATIVO e CONSTRUTIVO
- Aponte erros específicos com exemplos do texto
- Dê sugestões práticas de melhoria"""

        user_content = f"""{contexto_aluno}

=== TEMA DA REDAÇÃO ===
TÍTULO: {tema.get('titulo', '')}
TEMA: {tema.get('tema', '')}
PROPOSTA: {tema.get('proposta', '')}

TEXTOS MOTIVADORES:
1. {tema.get('texto_motivador_1', 'Não fornecido')}
2. {tema.get('texto_motivador_2', 'Não fornecido')}
3. {tema.get('texto_motivador_3', 'Não fornecido')}

=== REDAÇÃO DO ALUNO ===
{texto_redacao}

Corrija esta redação seguindo as instruções e responda APENAS com o JSON."""

        try:
            response = get_anthropic_client().messages.create(
                model=get_default_model(),
                # TC-055/TC-144/TC-145: 3000 tokens era insuficiente para o JSON completo
                # (5 competencias com feedback detalhado + feedback_geral + listas), causando
                # resposta truncada -> JSON invalido -> "Erro ao corrigir redacao. Tente novamente."
                # 2026-08-11: 6000 ainda truncava em redacoes longas — sao 5
                # competencias com feedback detalhado + pontos fortes + pontos de
                # melhoria + sugestoes + feedback geral.
                max_tokens=10000,
                system=sistema_cacheado(system_prompt),
                messages=[{"role": "user", "content": user_content}]
            )

            if getattr(response, "stop_reason", None) == "max_tokens":
                logger.error("Correcao de redacao truncada no limite de tokens")
                raise ValueError(
                    "A correção foi cortada por exceder o limite de tamanho. "
                    "Tente enviar novamente."
                )

            content = response.content[0].text.strip()
            # Remove cercas de markdown (```json ... ```), como feito nos demais servicos de IA
            # (ai_materiais_service, prova_ai_service etc) - o modelo as vezes ignora o pedido
            # de "sem markdown" e isso quebrava o parsing aqui.
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()

            # Extrair JSON
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                correcao = json.loads(json_match.group())
                
                # Calcular nota final
                nota_final = (
                    correcao.get("nota_competencia_1", 0) +
                    correcao.get("nota_competencia_2", 0) +
                    correcao.get("nota_competencia_3", 0) +
                    correcao.get("nota_competencia_4", 0) +
                    correcao.get("nota_competencia_5", 0)
                )
                
                correcao["nota_final"] = nota_final
                correcao["quantidade_linhas"] = quantidade_linhas
                correcao["quantidade_palavras"] = quantidade_palavras
                correcao["nivel_geral"] = self._classificar_nivel_geral(nota_final)
                
                # Adicionar niveis, descritores e criterios por competencia (rubrica detalhada)
                for i in range(1, 6):
                    nota = correcao.get(f"nota_competencia_{i}", 0)
                    correcao[f"nivel_competencia_{i}"] = self._classificar_nivel(nota)
                    correcao[f"descritor_competencia_{i}"] = self._descritor_nivel(i, nota)
                    if not correcao.get(f"criterios_competencia_{i}"):
                        correcao[f"criterios_competencia_{i}"] = [
                            {"criterio": c, "situacao": None}
                            for c in self.COMPETENCIAS_ENEM[i]["criterios"]
                        ]
                
                return correcao
            else:
                raise ValueError("Resposta da IA não contém JSON válido")
                
        except Exception as e:
            print(f"[ERRO] Erro ao corrigir redação: {e}")
            raise

    def _redacao_anulada(self, motivo: str) -> Dict[str, Any]:
        """Retorna estrutura de redação anulada"""
        return {
            "nota_competencia_1": 0,
            "nota_competencia_2": 0,
            "nota_competencia_3": 0,
            "nota_competencia_4": 0,
            "nota_competencia_5": 0,
            "nota_final": 0,
            "feedback_competencia_1": motivo,
            "feedback_competencia_2": motivo,
            "feedback_competencia_3": motivo,
            "feedback_competencia_4": motivo,
            "feedback_competencia_5": motivo,
            "feedback_geral": f"Redação anulada: {motivo}",
            "pontos_fortes": [],
            "pontos_melhoria": [motivo],
            "sugestoes": ["Reescreva a redação com atenção aos requisitos mínimos"],
            "nivel_geral": "Anulada",
            "anulada": True,
            "motivo_anulacao": motivo
        }


# Instância global do serviço
redacao_ai_service = RedacaoAIService()
