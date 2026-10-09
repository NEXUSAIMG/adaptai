from app.core.anthropic_client import get_anthropic_client, get_default_model
import json
from typing import List, Dict
from app.core.config import settings

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F

class AIService:
    def __init__(self):
        self._client = None
        self.model = get_default_model()
    
    @property
    def client(self):
        """Lazy initialization do cliente Anthropic"""
        if self._client is None:
            self._client = get_anthropic_client()
        return self._client
    
    @tm.feature(F.QUESTAO_GERACAO)
    def generate_questions(
        self,
        content: str,
        subject: str,
        grade_level: str,
        difficulty_level: int,
        quantity: int,
        adaptations: List[str] = []
    ) -> List[Dict]:
        """
        Gera questões de múltipla escolha usando Claude API
        """
        
        # Definir regras por nível
        level_rules = {
            1: """
            - Questões muito simples e diretas
            - Vocabulário acessível e cotidiano
            - Conceitos fundamentais
            - Frases curtas (máximo 15 palavras)
            - Evitar abstrações
            - Respostas óbvias para quem entendeu o conceito básico
            """,
            2: """
            - Complexidade média
            - Requer interpretação básica
            - Conecta conceitos simples
            - Frases de tamanho médio (15-25 palavras)
            - Pode exigir um passo de raciocínio
            """,
            3: """
            - Questões mais complexas
            - Pensamento crítico necessário
            - Análise e comparação de informações
            - Pode usar termos técnicos apropriados
            - Exige compreensão profunda
            """,
            4: """
            - Máxima complexidade
            - Raciocínio avançado e abstrato
            - Aplicação em novos contextos
            - Síntese de múltiplos conceitos
            - Pensamento analítico elevado
            """
        }
        
        # Adaptações especiais
        adaptation_rules = ""
        if "simple_language" in adaptations:
            adaptation_rules += "- Use linguagem muito simples e direta\n"
        if "short_sentences" in adaptations:
            adaptation_rules += "- Frases muito curtas (máximo 10 palavras)\n"
        if "avoid_double_negative" in adaptations:
            adaptation_rules += "- Nunca use dupla negativa\n"
        if "visual_support" in adaptations:
            adaptation_rules += "- Sugira quando seria útil apoio visual\n"
        if "highlight_keywords" in adaptations:
            adaptation_rules += "- Indique palavras-chave importantes\n"
        
        prompt = f"""Você é um especialista em educação inclusiva e criação de avaliações adaptadas para estudantes com TEA, TDAH, dislexia e outras necessidades educacionais especiais.

Sua tarefa é gerar {quantity} questões de múltipla escolha de ALTA QUALIDADE baseadas no conteúdo fornecido.

CONTEÚDO BASE:
{content}

CONFIGURAÇÕES:
- Disciplina: {subject}
- Ano escolar: {grade_level}
- Nível de dificuldade: {difficulty_level}
- Quantidade de questões: {quantity}

REGRAS PARA NÍVEL {difficulty_level}:
{level_rules.get(difficulty_level, "")}

ADAPTAÇÕES ESPECIAIS REQUERIDAS:
{adaptation_rules if adaptation_rules else "Nenhuma adaptação especial"}

REGRAS IMPORTANTES:
1. Todas as 4 alternativas devem ser plausíveis e bem escritas
2. Evite pegadinhas desnecessárias que confundem ao invés de avaliar
3. Use linguagem clara e apropriada ao ano escolar
4. Distribua as respostas corretas uniformemente entre a, b, c, d
5. Certifique-se de que há APENAS UMA resposta inequivocamente correta
6. As alternativas incorretas devem ser erros comuns ou conceitos relacionados
7. Identifique a habilidade cognitiva avaliada (ex: "identificar_conceitos", "interpretar_relacoes", "aplicar_conhecimento", "analisar_informacoes", "comparar_e_contrastar")

FORMATO DE SAÍDA:
Responda APENAS com um JSON válido no seguinte formato, sem nenhum texto adicional antes ou depois:

{{
  "questions": [
    {{
      "question_text": "texto da questão clara e direta",
      "option_a": "primeira alternativa",
      "option_b": "segunda alternativa",
      "option_c": "terceira alternativa",
      "option_d": "quarta alternativa",
      "correct_answer": "a",
      "explanation": "explicação pedagógica de por que esta é a resposta correta e o que o aluno deve entender",
      "skill": "habilidade_avaliada_em_snake_case"
    }}
  ]
}}

IMPORTANTE: 
- Responda APENAS com JSON válido
- Não adicione comentários ou texto fora do JSON
- Não use markdown (sem ```json)
- Gere exatamente {quantity} questões"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Extrair o conteúdo da resposta
            response_text = response.content[0].text
            
            # Limpar possíveis markdown
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            # Parse JSON
            result = json.loads(response_text)
            questions = result.get("questions", [])
            
            # Validar que temos o número correto de questões
            if len(questions) != quantity:
                print(f"Aviso: Esperado {quantity} questões, mas recebeu {len(questions)}")
            
            return questions
            
        except json.JSONDecodeError as e:
            print(f"Erro ao decodificar JSON: {e}")
            print(f"Resposta recebida: {response_text}")
            raise Exception(f"Erro ao processar resposta da IA: resposta inválida")
        except Exception as e:
            print(f"Erro ao gerar questões: {e}")
            raise

    @tm.feature(F.DESEMPENHO_ANALISE)
    def analyze_performance(
        self,
        student_name: str,
        answers_data: List[Dict],
        student_profile: Dict = None
    ) -> Dict:
        """
        Analisa o desempenho do aluno e gera recomendações personalizadas
        """
        
        # Preparar resumo das respostas
        total_questions = len(answers_data)
        correct_count = sum(1 for a in answers_data if a.get("is_correct"))
        
        profile_text = "Não fornecido"
        if student_profile:
            profile_text = json.dumps(student_profile, indent=2, ensure_ascii=False)
        
        prompt = f"""Você é um especialista em análise pedagógica e educação inclusiva, especialmente para estudantes com TEA, TDAH, dislexia e outras necessidades especiais.

Analise o desempenho do aluno e forneça recomendações PERSONALIZADAS e PRÁTICAS.

ALUNO: {student_name}

PERFIL DO ALUNO:
{profile_text}

RESUMO DO DESEMPENHO:
- Total de questões: {total_questions}
- Questões corretas: {correct_count}
- Taxa de acerto: {(correct_count/total_questions*100):.1f}%

QUESTÕES RESPONDIDAS (com detalhes):
{json.dumps(answers_data, indent=2, ensure_ascii=False)}

ANÁLISE SOLICITADA:
1. Faça uma análise geral do desempenho considerando o perfil do aluno
2. Identifique os pontos fortes (habilidades bem desenvolvidas)
3. Identifique os pontos que precisam de mais trabalho
4. Forneça recomendações ESPECÍFICAS e PRÁTICAS para o professor
5. Forneça recomendações para trabalho em casa (se apropriado)
6. Sugira próximos passos concretos

IMPORTANTE:
- Seja encorajador mas honesto
- Considere o perfil do aluno (TEA, TDAH, etc.) nas recomendações
- Sugira estratégias ESPECÍFICAS, não genéricas
- Use linguagem clara e profissional
- Foque em ações práticas

Responda APENAS com JSON válido no seguinte formato:

{{
  "summary": "resumo geral do desempenho em 2-3 frases",
  "strong_points_analysis": "análise detalhada dos pontos fortes",
  "weak_points_analysis": "análise detalhada das dificuldades observadas",
  "recommendations": "recomendações detalhadas e práticas para o professor, considerando o perfil do aluno",
  "home_recommendations": "sugestões para trabalho em casa ou reforço familiar",
  "next_steps": ["passo 1 específico", "passo 2 específico", "passo 3 específico"]
}}"""
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Extrair o conteúdo da resposta
            response_text = response.content[0].text
            
            # Limpar possíveis markdown
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            # Parse JSON
            result = json.loads(response_text)
            return result
            
        except json.JSONDecodeError as e:
            print(f"Erro ao decodificar JSON: {e}")
            print(f"Resposta recebida: {response_text}")
            raise Exception(f"Erro ao processar análise da IA: resposta inválida")
        except Exception as e:
            print(f"Erro ao analisar desempenho: {e}")
            raise
