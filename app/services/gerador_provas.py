from app.core.anthropic_client import get_anthropic_client
from app.core.config import settings
import json
from typing import List, Dict

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F

class GeradorProvasService:
    """Serviço para gerar provas com IA (Claude)"""
    
    def __init__(self):
        self._client = None
        self.model = settings.LLM_MODEL
    
    @property
    def client(self):
        """Lazy initialization do cliente Anthropic"""
        if self._client is None:
            self._client = get_anthropic_client()
        return self._client
    
    @tm.feature(F.PROVA_GERACAO)
    def gerar_prova(
        self,
        subject: str,
        grade_level: str,
        content_prompt: str,
        num_questions: int,
        difficulty_distribution: Dict[str, int] = None,
        adaptations: List[str] = None
    ) -> List[Dict]:
        """
        Gera uma prova completa usando Claude AI
        
        Args:
            subject: Matéria (Matemática, Português, etc)
            grade_level: Série (1º ano, 2º ano, etc)
            content_prompt: Conteúdo/tema para gerar questões
            num_questions: Número total de questões
            difficulty_distribution: Distribuição por nível (opcional)
            adaptations: Adaptações a aplicar (opcional)
        
        Returns:
            Lista de questões geradas
        """
        
        # Montar prompt para IA
        prompt = self._construir_prompt(
            subject=subject,
            grade_level=grade_level,
            content_prompt=content_prompt,
            num_questions=num_questions,
            difficulty_distribution=difficulty_distribution,
            adaptations=adaptations
        )
        
        # Chamar Claude API
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            # Extrair texto da resposta
            response_text = response.content[0].text
            
            # Parse JSON
            questoes = self._parse_response(response_text)
            
            return questoes
            
        except Exception as e:
            print(f"Erro ao gerar prova com IA: {e}")
            raise Exception(f"Falha ao gerar prova: {str(e)}")
    
    def _construir_prompt(
        self,
        subject: str,
        grade_level: str,
        content_prompt: str,
        num_questions: int,
        difficulty_distribution: Dict[str, int] = None,
        adaptations: List[str] = None
    ) -> str:
        """Constrói o prompt para a IA"""
        
        # Distribuição padrão se não fornecida
        if not difficulty_distribution:
            # Distribuição equilibrada
            easy = int(num_questions * 0.4)
            medium = int(num_questions * 0.35)
            hard = int(num_questions * 0.2)
            challenge = num_questions - easy - medium - hard
            
            difficulty_distribution = {
                "1": easy,
                "2": medium,
                "3": hard,
                "4": challenge
            }
        
        # Adaptações
        adaptacoes_texto = ""
        if adaptations:
            adaptacoes_texto = "\n\nAPLIQUE AS SEGUINTES ADAPTAÇÕES:\n"
            if "visual_support" in adaptations:
                adaptacoes_texto += "- Use linguagem visual e descritiva\n"
            if "simple_language" in adaptations:
                adaptacoes_texto += "- Use linguagem simples e clara\n"
            if "step_by_step" in adaptations:
                adaptacoes_texto += "- Apresente conceitos passo a passo\n"
            if "short_sentences" in adaptations:
                adaptacoes_texto += "- Use frases curtas e diretas\n"
        
        prompt = f"""Você é um especialista em educação criando uma prova de {subject} para alunos do {grade_level}.

TEMA/CONTEÚDO DA PROVA:
{content_prompt}

QUANTIDADE DE QUESTÕES:
Total: {num_questions} questões

DISTRIBUIÇÃO POR NÍVEL DE DIFICULDADE:
- Nível 1 (Básico): {difficulty_distribution.get('1', 0)} questões
- Nível 2 (Intermediário): {difficulty_distribution.get('2', 0)} questões
- Nível 3 (Avançado): {difficulty_distribution.get('3', 0)} questões
- Nível 4 (Desafio): {difficulty_distribution.get('4', 0)} questões
{adaptacoes_texto}

FORMATO DE CADA QUESTÃO:
- Enunciado claro e adequado ao nível
- 4 alternativas (a, b, c, d)
- Apenas 1 alternativa correta
- Explicação da resposta correta
- Habilidade avaliada

IMPORTANTE:
- Respeite EXATAMENTE a distribuição de dificuldade solicitada
- Questões devem ser adequadas ao {grade_level}
- Mantenha um padrão de qualidade
- Evite pegadinhas desnecessárias
- Foque no conteúdo especificado

RESPONDA APENAS COM UM JSON VÁLIDO NO SEGUINTE FORMATO (SEM MARKDOWN, SEM BACKTICKS):

[
  {{
    "difficulty_level": 1,
    "question_text": "Texto da questão aqui",
    "option_a": "Primeira alternativa",
    "option_b": "Segunda alternativa",
    "option_c": "Terceira alternativa",
    "option_d": "Quarta alternativa",
    "correct_answer": "a",
    "explanation": "Explicação da resposta correta",
    "skill": "Habilidade avaliada"
  }}
]

GERE AGORA AS {num_questions} QUESTÕES EM JSON:"""
        
        return prompt
    
    def _parse_response(self, response_text: str) -> List[Dict]:
        """Parse da resposta da IA"""
        try:
            # Remover markdown se houver
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
            
            # Parse JSON
            questoes = json.loads(response_text)
            
            # Validar estrutura
            if not isinstance(questoes, list):
                raise ValueError("Resposta não é uma lista")
            
            # Validar cada questão
            for q in questoes:
                required_fields = [
                    'difficulty_level', 'question_text',
                    'option_a', 'option_b', 'option_c', 'option_d',
                    'correct_answer'
                ]
                for field in required_fields:
                    if field not in q:
                        raise ValueError(f"Campo obrigatório ausente: {field}")
            
            return questoes
            
        except json.JSONDecodeError as e:
            print(f"Erro ao fazer parse do JSON: {e}")
            print(f"Response text: {response_text}")
            raise ValueError("IA retornou formato inválido")
        except Exception as e:
            print(f"Erro ao validar resposta: {e}")
            raise

# Singleton
gerador_provas_service = GeradorProvasService()
