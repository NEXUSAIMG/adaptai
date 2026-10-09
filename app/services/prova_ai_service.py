"""
🤖 AdaptAI - Serviço de Geração de Questões com IA
Integração com Claude API da Anthropic
"""
import anthropic  # tokenmeter: allow  (só para tipos de exceção; client vem do factory)
import json
import re
import asyncio
from typing import List, Dict, Any
from app.core.config import settings
from app.core.anthropic_client import get_anthropic_client
from app.core.logging_config import get_logger
from app.models.prova import TipoQuestao, DificuldadeQuestao

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F

logger = get_logger(__name__)


class ProvaIAError(Exception):
    """Erro ao gerar ou interpretar questoes da IA (apos esgotar as tentativas)."""
    pass


class ProvaAIService:
    """Serviço para gerar questões usando Claude AI"""
    
    def __init__(self):
        self._client = None
        self.model = settings.LLM_MODEL
        self.max_retries = 3          # tentativas para gerar questoes
        self.timeout_seconds = 120.0  # timeout por chamada a IA
    
    @property
    def client(self):
        """Lazy initialization do cliente Anthropic (com timeout e retries de rede)"""
        if self._client is None:
            self._client = get_anthropic_client(
                timeout=self.timeout_seconds,
                max_retries=2,
            )
        return self._client
    
    @tm.feature(F.PROVA_GERACAO)
    async def gerar_questoes(
        self,
        conteudo_prompt: str,
        materia: str,
        serie_nivel: str,
        quantidade: int,
        tipo_questao: TipoQuestao,
        dificuldade: DificuldadeQuestao
    ) -> List[Dict[str, Any]]:
        """
        Gera questões usando Claude AI, com retry + backoff e timeout.

        Tenta ate self.max_retries vezes. Erros transitorios da API e respostas
        com JSON invalido disparam nova tentativa (com espera crescente). Apos
        esgotar as tentativas, levanta ProvaIAError com mensagem amigavel.
        """
        
        # Monta o prompt para Claude
        prompt = self._criar_prompt_geracao(
            conteudo_prompt=conteudo_prompt,
            materia=materia,
            serie_nivel=serie_nivel,
            quantidade=quantidade,
            tipo_questao=tipo_questao,
            dificuldade=dificuldade
        )
        
        # ------------------------------------------------------------------
        # CORRECAO 2026-08-11 — "Erro ao criar prova" em provas dissertativas
        # ------------------------------------------------------------------
        # O teto era `max_tokens=4000` FIXO, independente do tipo e da
        # quantidade. Uma questao de multipla escolha ocupa ~150 tokens; uma
        # dissertativa carrega enunciado + resposta esperada + criterios de
        # avaliacao e passa de 350. Dez dissertativas nao cabiam em 4000: o
        # JSON vinha cortado, o parse falhava, e o retry disparava mais DUAS
        # chamadas com o MESMO teto — falha garantida, credito gasto 3x, e o
        # professor via so "Erro ao criar prova. Tente novamente."
        max_tokens = self._calcular_max_tokens(quantidade, tipo_questao)

        ultimo_erro = None
        for tentativa in range(1, self.max_retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    timeout=self.timeout_seconds,
                    messages=[{"role": "user", "content": prompt}],
                )

                # Truncamento nao se resolve tentando de novo com o mesmo teto.
                # Detectamos, subimos o teto e so entao reenviamos.
                if getattr(message, "stop_reason", None) == "max_tokens":
                    logger.error(
                        "Geracao de questoes truncada no limite de tokens",
                        extra={
                            "max_tokens": max_tokens,
                            "quantidade": quantidade,
                            "tipo": getattr(tipo_questao, "value", str(tipo_questao)),
                            "tentativa": tentativa,
                        },
                    )
                    if tentativa < self.max_retries and max_tokens < self.MAX_TOKENS_TETO:
                        max_tokens = min(max_tokens * 2, self.MAX_TOKENS_TETO)
                        continue
                    raise ProvaIAError(
                        f"A resposta da IA foi cortada: {quantidade} questões deste "
                        f"tipo são longas demais para uma única geração. "
                        f"Reduza a quantidade de questões e tente novamente."
                    )

                resposta = message.content[0].text
                questoes = self._parse_questoes_json(resposta)

                if not isinstance(questoes, list) or not questoes:
                    raise ProvaIAError("A IA nao retornou uma lista de questoes valida")

                return questoes

            except (anthropic.APIError, ProvaIAError) as e:
                ultimo_erro = e
                logger.warning(
                    "Falha ao gerar questoes (tentativa %s/%s): %s",
                    tentativa, self.max_retries, e
                )
                if tentativa < self.max_retries:
                    # Backoff exponencial: 2s, 4s, 8s...
                    await asyncio.sleep(min(2 ** tentativa, 8))
                    continue
            except Exception as e:
                ultimo_erro = e
                logger.exception("Erro inesperado ao gerar questoes com IA")
                break

        # Preserva a mensagem util quando ja temos uma (ex.: truncamento).
        # Antes, qualquer causa virava o mesmo texto generico e o professor
        # nao tinha o que fazer com a informacao.
        if isinstance(ultimo_erro, ProvaIAError) and str(ultimo_erro):
            raise ultimo_erro

        raise ProvaIAError(
            "Não foi possível gerar as questões com a IA após várias tentativas. "
            "Tente novamente em instantes."
        ) from ultimo_erro

    # Teto absoluto por chamada — acima disso, o caminho certo e reduzir a
    # quantidade de questoes, nao insistir numa resposta gigante.
    MAX_TOKENS_TETO = 16000

    @staticmethod
    def _calcular_max_tokens(quantidade: int, tipo_questao: TipoQuestao) -> int:
        """
        Orcamento de tokens proporcional ao tipo e a quantidade de questoes.

        Valores medidos sobre as respostas reais do modelo, com folga de ~40%
        para variacao de tamanho de enunciado:

          multipla_escolha : ~150 tokens/questao (enunciado + 4 alternativas)
          verdadeiro_falso : ~110 tokens/questao
          lacunas          : ~140 tokens/questao
          dissertativa     : ~380 tokens/questao (enunciado + resposta
                             esperada + criterios de avaliacao)
        """
        por_questao = {
            TipoQuestao.MULTIPLA_ESCOLHA: 220,
            TipoQuestao.VERDADEIRO_FALSO: 170,
            TipoQuestao.LACUNAS: 200,
            TipoQuestao.DISSERTATIVA: 550,
        }.get(tipo_questao, 300)

        # 800 de base cobre o preambulo do JSON e a margem de seguranca.
        estimado = 800 + (por_questao * max(1, quantidade))
        return max(4000, min(estimado, ProvaAIService.MAX_TOKENS_TETO))
    
    def _criar_prompt_geracao(
        self,
        conteudo_prompt: str,
        materia: str,
        serie_nivel: str,
        quantidade: int,
        tipo_questao: TipoQuestao,
        dificuldade: DificuldadeQuestao
    ) -> str:
        """Cria o prompt para Claude gerar as questões"""
        
        tipo_descricao = {
            TipoQuestao.MULTIPLA_ESCOLHA: "múltipla escolha com 4 alternativas (A, B, C, D)",
            TipoQuestao.VERDADEIRO_FALSO: "verdadeiro ou falso",
            TipoQuestao.DISSERTATIVA: "dissertativa (resposta aberta)",
            TipoQuestao.LACUNAS: "completar lacunas"
        }
        
        dificuldade_descricao = {
            DificuldadeQuestao.FACIL: "fácil - conceitos básicos",
            DificuldadeQuestao.MEDIO: "médio - aplicação de conceitos",
            DificuldadeQuestao.DIFICIL: "difícil - análise e síntese",
            DificuldadeQuestao.MUITO_DIFICIL: "muito difícil - pensamento crítico avançado"
        }
        
        prompt = f"""Você é um especialista em educação e criação de avaliações pedagógicas.

**TAREFA:** Criar {quantidade} questões de {materia} para {serie_nivel or 'estudantes'}.

**CONTEXTO DO CONTEÚDO:**
{conteudo_prompt}

**ESPECIFICAÇÕES:**
- Tipo: {tipo_descricao.get(tipo_questao, 'múltipla escolha')}
- Dificuldade: {dificuldade_descricao.get(dificuldade, 'médio')}
- Quantidade: {quantidade} questões
- Matéria: {materia}
- Nível: {serie_nivel or 'Não especificado'}

**FORMATO DE SAÍDA:**
Retorne APENAS um JSON válido (sem markdown, sem ```json) com a seguinte estrutura:

{{
  "questoes": [
    {{
      "numero": 1,
      "enunciado": "Texto da questão...",
      "tipo": "{tipo_questao.value}",
      "dificuldade": "{dificuldade.value}",
      "opcoes": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "resposta_correta": "A",
      "explicacao": "Explicação detalhada da resposta correta...",
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

**INSTRUÇÕES IMPORTANTES:**
1. Cada questão deve ser clara, objetiva e pedagogicamente adequada
2. Para múltipla escolha: sempre 4 alternativas (A, B, C, D)
3. Para verdadeiro/falso: use opcoes: ["Verdadeiro", "Falso"]
4. Para dissertativa: deixe opcoes como null e forneça criterios_avaliacao
5. A resposta_correta deve ser apenas a letra (ex: "A") ou o texto exato da opção correta
6. Inclua explicações detalhadas para cada resposta
7. Use tags relevantes para categorizar o conteúdo
8. Varie os assuntos dentro do tema proposto
9. As questões devem estar adequadas ao nível de {serie_nivel or 'escolar'}
10. RETORNE APENAS O JSON, sem texto adicional antes ou depois

Gere as {quantidade} questões agora:"""
        
        return prompt
    
    def _parse_questoes_json(self, resposta: str):
        """
        Parse robusto da resposta da IA. Tenta, em ordem:
        1) bloco cercado por ```json ... ``` (ou ``` ... ```),
        2) o texto inteiro sem cercas,
        3) o recorte do primeiro '{' ao ultimo '}' (descarta preambulo/postambulo).
        Em cada candidato tenta tambem reparar virgulas finais.
        Levanta ProvaIAError se nada for interpretavel.

        Retorna a lista em data["questoes"] quando existir; senao o proprio objeto
        (reaproveitado por analises que retornam um dict).
        """
        bruto = (resposta or "").strip()
        if not bruto:
            raise ProvaIAError("Resposta vazia da IA")

        candidatos = []

        # 1) Bloco cercado por ```json ... ``` ou ``` ... ```
        m = re.search(r"```(?:json)?\s*(.+?)```", bruto, re.DOTALL | re.IGNORECASE)
        if m:
            candidatos.append(m.group(1).strip())

        # 2) Texto inteiro, removendo cercas de inicio/fim se houver
        sem_cercas = bruto
        if sem_cercas.startswith("```json"):
            sem_cercas = sem_cercas[7:]
        elif sem_cercas.startswith("```"):
            sem_cercas = sem_cercas[3:]
        if sem_cercas.endswith("```"):
            sem_cercas = sem_cercas[:-3]
        candidatos.append(sem_cercas.strip())

        # 3) Recorte do primeiro '{' ao ultimo '}'
        ini, fim = bruto.find("{"), bruto.rfind("}")
        if ini != -1 and fim != -1 and fim > ini:
            candidatos.append(bruto[ini:fim + 1])

        for cand in candidatos:
            data = self._tentar_json(cand)
            if data is None:
                continue
            if isinstance(data, dict) and "questoes" in data:
                return data["questoes"]
            return data

        logger.error("JSON invalido da IA. Inicio da resposta: %s", bruto[:400])
        raise ProvaIAError("Formato de resposta da IA invalido (JSON nao reconhecido)")

    @staticmethod
    def _tentar_json(texto: str):
        """json.loads; se falhar, remove virgulas finais e tenta de novo. None se falhar."""
        if not texto:
            return None
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            limpo = re.sub(r",(\s*[}\]])", r"\1", texto)
            try:
                return json.loads(limpo)
            except json.JSONDecodeError:
                return None
    
    @tm.feature(F.DESEMPENHO_ANALISE)
    async def analisar_desempenho(
        self,
        questoes: List[Dict],
        respostas: List[Dict],
        aluno_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analisa o desempenho do aluno usando IA
        
        Args:
            questoes: Lista de questões da prova
            respostas: Lista de respostas do aluno
            aluno_info: Informações do aluno
            
        Returns:
            Análise detalhada do desempenho
        """
        
        prompt = f"""Você é um especialista em análise pedagógica e educação inclusiva.

**TAREFA:** Analisar o desempenho de um aluno em uma prova.

**INFORMAÇÕES DO ALUNO:**
Nome: {aluno_info.get('nome', 'Não informado')}
Série: {aluno_info.get('serie', 'Não informado')}
Diagnósticos: {aluno_info.get('diagnosticos', 'Nenhum')}

**QUESTÕES E RESPOSTAS:**
{json.dumps({'questoes': questoes, 'respostas': respostas}, ensure_ascii=False, indent=2)}

**INSTRUÇÕES:**
Forneça uma análise detalhada em JSON com:

1. **pontos_fortes**: Lista de áreas onde o aluno foi bem
2. **pontos_melhoria**: Lista de áreas que precisam de atenção
3. **conceitos_dominados**: Conceitos que o aluno demonstrou dominar
4. **conceitos_revisar**: Conceitos que precisam ser revisados
5. **recomendacoes**: Recomendações pedagógicas específicas
6. **adaptacoes_sugeridas**: Sugestões de adaptações para próximas atividades
7. **nivel_compreensao**: Nível geral de compreensão (0-100)

**FORMATO DE SAÍDA:**
Retorne APENAS um JSON válido (sem markdown):

{{
  "pontos_fortes": ["...", "..."],
  "pontos_melhoria": ["...", "..."],
  "conceitos_dominados": ["...", "..."],
  "conceitos_revisar": ["...", "..."],
  "recomendacoes": ["...", "..."],
  "adaptacoes_sugeridas": ["...", "..."],
  "nivel_compreensao": 75
}}

Gere a análise agora:"""
        
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                timeout=self.timeout_seconds,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            
            resposta = message.content[0].text
            analise = self._parse_questoes_json(resposta)
            
            return analise
            
        except Exception as e:
            print(f"❌ Erro ao analisar desempenho: {e}")
            return {
                "pontos_fortes": [],
                "pontos_melhoria": [],
                "conceitos_dominados": [],
                "conceitos_revisar": [],
                "recomendacoes": ["Análise detalhada não disponível"],
                "adaptacoes_sugeridas": [],
                "nivel_compreensao": 0
            }
    
    @tm.feature(F.PROVA_FEEDBACK)
    async def gerar_feedback_personalizado(
        self,
        questoes: List[Dict],
        respostas: List[Dict],
        analise: Dict[str, Any],
        aluno_info: Dict[str, Any]
    ) -> str:
        """
        Gera feedback personalizado para o aluno
        
        Args:
            questoes: Questões da prova
            respostas: Respostas do aluno
            analise: Análise de desempenho
            aluno_info: Informações do aluno
            
        Returns:
            Texto de feedback personalizado
        """
        
        prompt = f"""Você é um educador empático e motivador.

**TAREFA:** Escrever um feedback personalizado e encorajador para o aluno.

**ALUNO:**
{json.dumps(aluno_info, ensure_ascii=False, indent=2)}

**ANÁLISE DE DESEMPENHO:**
{json.dumps(analise, ensure_ascii=False, indent=2)}

**INSTRUÇÕES:**
1. Seja positivo e encorajador
2. Destaque os pontos fortes primeiro
3. Sugira melhorias de forma construtiva
4. Use linguagem adequada à idade/série do aluno
5. Considere os diagnósticos para personalizar o feedback
6. Seja específico sobre o que fazer para melhorar
7. Termine com uma mensagem motivadora

Escreva o feedback (máximo 300 palavras):"""
        
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                timeout=self.timeout_seconds,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
            
            feedback = message.content[0].text.strip()
            return feedback
            
        except Exception as e:
            print(f"❌ Erro ao gerar feedback: {e}")
            return "Parabéns por completar a prova! Continue estudando e se esforçando."


# Instância global do serviço
prova_ai_service = ProvaAIService()
