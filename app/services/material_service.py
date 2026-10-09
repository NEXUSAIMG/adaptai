"""
Service para geração de materiais com IA
"""
from app.core.anthropic_client import get_anthropic_client, get_default_model
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List
from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.svg_sanitizer import sanitizar_svg

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F

logger = get_logger(__name__)

# Tetos da atividade de geometria (custo + tempo): cada figura e UMA chamada
# extra a IA. 6 exercicios ja e uma lista de casa cheia; acima disso o material
# fica longo pro aluno e caro pra escola.
MAX_EXERCICIOS_GEOMETRIA = 6
_MAX_WORKERS_FIGURA = 3  # I/O-bound (rede); mesmo criterio de _ilustrar_itens


# ----------------------------------------------------------------------------
# 2026-08-18 — TRUNCAMENTO SILENCIOSO
# ----------------------------------------------------------------------------
# Os tres geradores abaixo pegavam `response.content[0].text` e devolviam
# success=True sem olhar `stop_reason`. Quando a resposta batia no teto de
# max_tokens (4000 para um prompt que pede HTML rico com CSS inline — acontecia
# com frequencia em "Material Visual" e "Lista de Atividades"), o material era
# gravado com o HTML CORTADO NO MEIO: o professor abria e via a pagina
# terminando no meio de uma frase, ou com o layout quebrado por uma tag aberta.
# Pior: ficava com status "disponivel", entao nada indicava problema.
#
# `ai_materiais_service._chamar_ia` ja fazia essa checagem desde 11/08 (para os
# materiais adaptados); a Biblioteca tinha ficado de fora.
_ERRO_TRUNCADO = (
    "O conteúdo pedido é longo demais e a resposta da IA foi cortada. "
    "Tente um tema mais específico ou divida em dois materiais."
)


def _extrair_texto(response) -> str:
    """Texto da resposta, sem markdown. Levanta ValueError se veio truncada/vazia."""
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError(_ERRO_TRUNCADO)

    blocos = getattr(response, "content", None) or []
    if not blocos:
        raise ValueError("A IA devolveu uma resposta vazia. Tente novamente.")

    texto = (getattr(blocos[0], "text", "") or "").strip()
    if not texto:
        raise ValueError("A IA devolveu uma resposta vazia. Tente novamente.")

    return texto.replace("```html", "").replace("```json", "").replace("```", "").strip()


class MaterialGeracaoService:
    """Service para gerar materiais educacionais com IA"""
    
    def __init__(self):
        """Inicializa o cliente da Anthropic (lazy)"""
        self._client = None
        # Era "claude-3-5-sonnet-20241022", introduzido no commit c0b0515 (13/01/2026)
        # - um modelo que a Anthropic ja havia aposentado em 28/10/2025. A linha nunca
        # chegou a executar (a tabela `materiais` so tem 3 registros, todos de nov/2025
        # e todos com status=disponivel), entao NAO houve falha em producao: era um bug
        # latente, que dispararia no proximo uso da feature.
        # get_default_model() resolve settings.LLM_MODEL e nao envelhece sozinho.
        self.model = get_default_model()
    
    @property
    def client(self):
        """Lazy initialization do cliente Anthropic"""
        if self._client is None:
            self._client = get_anthropic_client()
        return self._client
    
    @tm.feature(F.MATERIAL_ADAPTADO)
    def gerar_material_visual(self, titulo: str, conteudo: str, materia: str, serie: str, adaptacoes: list = None) -> dict:
        """
        Gera um material visual rico em HTML
        
        Args:
            titulo: Título do material
            conteudo: Descrição do que deve ser criado
            materia: Matéria (ex: Biologia, Matemática)
            serie: Série/nível (ex: 8º ano)
            adaptacoes: Lista de adaptações necessárias (TEA, TDAH, etc)
        
        Returns:
            dict com 'success', 'html' e 'tokens_used'
        """
        adaptacoes_text = ""
        if adaptacoes:
            adaptacoes_text = f"\n\nIMPORTANTE: Este material será usado por alunos com: {', '.join(adaptacoes)}. Adapte a linguagem e formato para ser mais acessível."
        
        prompt = f"""Crie um material educacional VISUAL e ATRATIVO sobre: {titulo}

MATÉRIA: {materia}
SÉRIE/NÍVEL: {serie}

CONTEÚDO SOLICITADO:
{conteudo}{adaptacoes_text}

IMPORTANTE - FORMATO DE SAÍDA:
- Retorne APENAS o conteúdo HTML (SEM as tags <html>, <head>, <body>)
- Use CSS inline para estilização
- Crie um conteúdo VISUALMENTE RICO e ATRATIVO

ESTRUTURA DO CONTEÚDO:
1. TÍTULO PRINCIPAL (grande, colorido, com gradiente)
2. INTRODUÇÃO VISUAL (box com cor de fundo, texto introdutório)
3. SEÇÕES BEM DEFINIDAS:
   - Cada seção com título colorido
   - Background diferente para cada seção
   - Boxes informativos coloridos
   - Exemplos práticos destacados
4. DIAGRAMAS/FLUXOGRAMAS em texto (use caracteres ▶, ►, →, ↓, •, ◆)
5. RESUMO FINAL em cards coloridos

ESTILO CSS INLINE:
- Use cores vibrantes e gradientes (ex: background: linear-gradient(135deg, #667eea 0%, #764ba2 100%))
- Cards com sombras (box-shadow)
- Use emojis e ícones quando apropriado
- Fonte grande e legível (min 16px)
- Espaçamento generoso (padding, margin)
- Bordas arredondadas (border-radius)
- Tipografia hierárquica (h1, h2, h3 bem definidos)
- Adicione hover effects sutis

EXEMPLO DE ESTRUTURA:
<div style="max-width: 800px; margin: 0 auto; font-family: 'Segoe UI', Arial, sans-serif;">
  <h1 style="font-size: 42px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; text-align: center; margin-bottom: 30px;">
    [TÍTULO]
  </h1>
  
  <div style="background: #f0f4ff; padding: 25px; border-radius: 15px; margin-bottom: 30px; border-left: 5px solid #667eea;">
    <p style="font-size: 18px; line-height: 1.8; color: #333;">
      [INTRODUÇÃO]
    </p>
  </div>
  
  <h2 style="color: #667eea; font-size: 32px; margin-top: 40px; margin-bottom: 20px;">
    📚 [SEÇÃO 1]
  </h2>
  
  <div style="background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 25px;">
    [CONTEÚDO DA SEÇÃO]
  </div>
  
  <!-- Mais seções... -->
</div>

NÃO inclua explicações, apenas o HTML puro e bem formatado."""

        try:
            response = self.client.messages.create(
                model=self.model,
                # 8192: o prompt pede HTML rico com CSS inline em varias secoes;
                # com 4000 a resposta era cortada com frequencia (ver
                # _extrair_texto).
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}]
            )
            
            html_content = _extrair_texto(response)
            
            return {
                "success": True,
                "html": html_content,
                "tokens_used": response.usage.input_tokens + response.usage.output_tokens
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @tm.feature(F.MATERIAL_ADAPTADO)
    def gerar_mapa_mental(self, titulo: str, conteudo: str, materia: str, serie: str, adaptacoes: list = None) -> dict:
        """
        Gera um mapa mental estruturado em JSON
        
        Args:
            titulo: Título do mapa mental
            conteudo: Descrição do que deve ser criado
            materia: Matéria
            serie: Série/nível
            adaptacoes: Lista de adaptações
        
        Returns:
            dict com 'success', 'json' e 'tokens_used'
        """
        adaptacoes_text = ""
        if adaptacoes:
            adaptacoes_text = f"\n\nADAPTAÇÕES: Simplifique para alunos com: {', '.join(adaptacoes)}"
        
        prompt = f"""Crie um mapa mental sobre: {titulo}

MATÉRIA: {materia}
SÉRIE: {serie}

CONTEÚDO:
{conteudo}{adaptacoes_text}

RETORNE APENAS UM JSON com esta estrutura exata:
{{
  "titulo": "Conceito Central",
  "cor_central": "#6366F1",
  "nos": [
    {{
      "id": "no1",
      "texto": "Conceito Principal 1",
      "cor": "#EF4444",
      "nivel": 1,
      "filhos": [
        {{
          "id": "no1_1",
          "texto": "Subconceito 1.1",
          "cor": "#FCA5A5",
          "nivel": 2,
          "filhos": []
        }}
      ]
    }}
  ]
}}

REGRAS IMPORTANTES:
1. O conceito central vai no "titulo"
2. Crie 3-5 nós principais (nivel: 1)
3. Cada nó principal pode ter 2-4 subnós (nivel: 2)
4. Máximo 20-25 nós no total
5. Use textos CURTOS (4-5 palavras máximo)
6. Use emojis apropriados nos textos
7. IDs únicos para cada nó (ex: no1, no1_1, no1_2)
8. Cores sugeridas:
   - Central: #6366F1 (azul/roxo)
   - Nível 1: #EF4444 (vermelho), #10B981 (verde), #F59E0B (laranja), #8B5CF6 (roxo), #EC4899 (rosa)
   - Nível 2: versões mais claras (#FCA5A5, #86EFAC, #FCD34D, #C4B5FD, #F9A8D4)

RETORNE APENAS O JSON, sem explicações ou markdown."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )
            
            json_text = _extrair_texto(response)
            
            # Parse JSON
            try:
                mapa_json = json.loads(json_text)
            except json.JSONDecodeError as je:
                return {
                    "success": False,
                    "error": f"JSON inválido retornado pela IA: {str(je)}",
                    "raw_response": json_text
                }
            
            return {
                "success": True,
                "json": mapa_json,
                "tokens_used": response.usage.input_tokens + response.usage.output_tokens
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    # tags_from={"formato": "formato"}: sem isso, resumo/roteiro/atividades
    # caem todos juntos em feature=material_adaptado no tokenmeter — foi por
    # essa falta de granularidade que o caso do material 23 (2026-09-09) exigiu
    # cruzar tabelas na mao pra achar o evento real (ver docs do incidente).
    @tm.feature(F.MATERIAL_ADAPTADO, tags_from={"formato": "formato"})
    def gerar_material_texto(self, formato: str, titulo: str, conteudo: str, materia: str, serie: str, adaptacoes: list = None) -> dict:
        """
        Gera materiais textuais em HTML para os formatos de adaptacao:
        resumo, texto_simplificado, roteiro_estudo, atividades.

        Returns:
            dict com 'success', 'html' e 'tokens_used' (ou 'error').
        """
        formatos = {
            "resumo": {
                "nome": "Resumo de estudo",
                "objetivo": "Resuma o conteudo de forma clara e organizada, destacando os pontos principais.",
                "estrutura": "Use topicos curtos, destaque palavras-chave e finalize com um quadro de 'pontos essenciais'.",
            },
            "texto_simplificado": {
                "nome": "Texto simplificado",
                "objetivo": "Reescreva o conteudo em linguagem simples e acessivel, com frases curtas e diretas.",
                "estrutura": "Paragrafos curtos, vocabulario simples; explique termos dificeis entre parenteses. Evite metaforas e ironia.",
            },
            "roteiro_estudo": {
                "nome": "Roteiro de estudo",
                "objetivo": "Crie um roteiro de estudo passo a passo sobre o conteudo.",
                "estrutura": "Etapas numeradas com o que estudar em cada uma, tempo sugerido e uma checklist final.",
            },
            "atividades": {
                "nome": "Lista de atividades",
                "objetivo": "Crie atividades/exercicios sobre o conteudo, com gabarito ao final.",
                "estrutura": "5 a 8 questoes variadas (objetivas e abertas), seguidas de uma secao 'Gabarito comentado'.",
            },
        }
        cfg = formatos.get(formato, formatos["resumo"])

        adaptacoes_text = ""
        if adaptacoes:
            adaptacoes_text = (
                f"\n\nIMPORTANTE: material para alunos com: {', '.join(adaptacoes)}. "
                "Adapte a linguagem e o formato para maior acessibilidade (frases curtas, sem ambiguidade)."
            )

        prompt = f"""Crie um material do tipo "{cfg['nome']}" sobre: {titulo}

MATERIA: {materia}
SERIE/NIVEL: {serie}

CONTEUDO BASE:
{conteudo}{adaptacoes_text}

OBJETIVO: {cfg['objetivo']}
ESTRUTURA: {cfg['estrutura']}

FORMATO DE SAIDA:
- Retorne APENAS HTML (sem as tags <html>, <head>, <body>), com CSS inline.
- Visual limpo e legivel: fonte minima 16px, bom espacamento, titulos hierarquicos e boxes com bordas arredondadas.
- Use cores suaves e, quando ajudar a compreensao, emojis discretos.
- NAO inclua explicacoes fora do HTML."""

        try:
            response = self.client.messages.create(
                model=self.model,
                # 2026-09-09: 8192 (motivo original: "atividades" com gabarito
                # comentado nao cabia em 4000) ainda truncou um caso real em
                # producao (tm_usage_event: 8192/8192 tokens, stop_reason
                # max_tokens). Subiu pro mesmo teto que planejamento_bncc_service
                # ja roda em prod com o mesmo modelo — nao e novidade no repo.
                max_tokens=16384,
                messages=[{"role": "user", "content": prompt}]
            )
            html_content = _extrair_texto(response)
            return {
                "success": True,
                "html": html_content,
                "tokens_used": response.usage.input_tokens + response.usage.output_tokens
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    # ==========================================================================
    # ATIVIDADE DE GEOMETRIA — 2026-08-17
    # ==========================================================================
    # POR QUE A FIGURA E SVG FEITO PELO CLAUDE, E NAO IMAGEM DO FLUX/fal.ai
    #
    # O repo ja gera imagem por difusao (image_providers -> Flux). Para uma
    # cena ilustrativa ("uma crianca regando uma planta") isso e otimo. Para
    # geometria e a ferramenta errada: modelo de difusao nao respeita medida.
    # Pedir "triangulo retangulo com catetos 3 e 4" devolve um triangulo
    # plausivel, com angulos errados e rotulos ilegiveis — e o exercicio inteiro
    # passa a ensinar coisa errada. Alem disso o modelo de imagem escreve texto
    # mal (por isso o _ESTILO de ilustracao_service proibe texto), e figura
    # geometrica sem rotulo ("A", "B", "5 cm", "60°") nao serve.
    #
    # SVG escrito pelo Claude resolve os tres pontos: coordenada e exata, o
    # rotulo e texto de verdade (selecionavel, legivel por leitor de tela,
    # nitido na impressao) e o arquivo tem alguns KB em vez de ~400KB por
    # imagem em base64 — o que importa muito aqui, porque foi exatamente o
    # peso do base64 que derrubou o salvamento de hq_tirinha/album_figurinhas
    # (ver o comentario de 2026-08-17 em routes/materiais_adaptados.py).
    #
    # SEGURANCA (duas camadas): o SVG passa por svg_sanitizer.sanitizar_svg()
    # ANTES de ser gravado — allowlist de tags e atributos, sem
    # script/style/foreignObject/href/on*. Ver o docstring de la. No frontend,
    # GeometriaViewer.jsx ainda o renderiza dentro de um <img src="data:...">,
    # onde o navegador nao executa script nem que o sanitizador falhasse.
    #
    # DUAS FASES (uma chamada de estrutura + uma chamada POR FIGURA):
    # pedir tudo numa tacada so faz o modelo economizar atencao no SVG (que e
    # a parte dificil) e ainda arrisca truncar no limite de tokens. Cada figura
    # isolada tambem falha isolada: sem a figura, o exercicio ainda vale, e o
    # viewer mostra a descricao textual no lugar.
    # ==========================================================================

    @tm.feature(F.MATERIAL_ADAPTADO)
    def _gerar_figura_svg(self, exercicio: Dict[str, Any], serie: str) -> None:
        """Gera o SVG de UM exercicio e o grava em `exercicio['figura_svg']`.

        Muta o dict in-place (mesmo padrao de ai_materiais_service._ilustrar_itens).
        Falha aqui nao derruba o material: o exercicio simplesmente fica sem
        figura e o viewer exibe `figura_descricao`.
        """
        descricao = (exercicio.get("figura_descricao") or "").strip()
        if not descricao:
            return

        prompt = f"""Desenhe em SVG a figura geometrica descrita abaixo, para uma atividade escolar de {serie}.

FIGURA A DESENHAR:
{descricao}

REGRAS OBRIGATORIAS:
1. Responda APENAS com o codigo SVG. Sem markdown, sem crases, sem explicacao.
2. Comece com <svg viewBox="0 0 400 300" xmlns="http://www.w3.org/2000/svg"> e ajuste o viewBox se a figura pedir outra proporcao. NAO use os atributos width/height.
3. PRECISAO IMPORTA: calcule as coordenadas de verdade. Se o enunciado diz que dois lados sao iguais, eles PRECISAM ter o mesmo comprimento no desenho; se diz 90°, o angulo tem que ser reto. O aluno vai medir com regua e transferidor.
4. Rotule com <text>: vertices (A, B, C), medidas ("5 cm") e angulos ("60°") que o enunciado citar. Fonte legivel (font-size 14 a 18), posicionada FORA da figura para nao cobrir os tracos.
5. Estilo: fundo transparente, tracos escuros (stroke="#1f2937") com stroke-width entre 2 e 3, preenchimento suave (fill="#dbeafe" com fill-opacity="0.6") ou fill="none". Marque o angulo reto com o quadradinho tradicional quando houver.
6. Deixe margem de pelo menos 30 unidades entre a figura e a borda do viewBox, para os rotulos caberem.
7. NAO use <script>, <style>, <foreignObject>, <image>, <use> nem atributos href/on*. Somente formas, linhas, textos e transform."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            bruto = response.content[0].text if response.content else ""
            limpo = sanitizar_svg(bruto)
            if limpo:
                exercicio["figura_svg"] = limpo
            else:
                logger.warning(
                    "Figura de geometria descartada na sanitizacao ('%s...')",
                    descricao[:60],
                )
        except Exception:
            logger.exception("Falha ao gerar figura de geometria ('%s...')", descricao[:60])

    @tm.feature(F.MATERIAL_ADAPTADO)
    def gerar_atividade_geometria(
        self,
        titulo: str,
        conteudo: str,
        serie: str,
        adaptacoes: list = None,
    ) -> dict:
        """Gera uma atividade de geometria: exercicios + figuras em SVG.

        Returns:
            dict com 'success' e 'json' (ou 'error'). O 'json' e o conteudo
            estruturado que o frontend renderiza em GeometriaViewer.jsx.
        """
        adaptacoes_text = ""
        if adaptacoes:
            adaptacoes_text = (
                f"\n\nADAPTACOES: a turma inclui alunos com {', '.join(adaptacoes)}. "
                "Use enunciados curtos e diretos, um comando por frase, sem pegadinha "
                "de interpretacao — a dificuldade deve estar na geometria, nao no texto. "
                "Para discalculia, prefira numeros inteiros e pequenos."
            )

        prompt = f"""Crie uma ATIVIDADE DE GEOMETRIA sobre: {titulo}

SERIE/NIVEL: {serie}

O QUE O PROFESSOR PEDIU:
{conteudo}{adaptacoes_text}

Cada exercicio deve ter uma FIGURA para o aluno observar. Voce nao desenha a
figura agora — descreve em `figura_descricao` o que ela mostra, com TODAS as
medidas, rotulos de vertice e marcacoes de angulo necessarias, de forma que
outra pessoa consiga desenha-la sem ler o enunciado.

RETORNE APENAS UM JSON com esta estrutura exata:
{{
  "titulo": "Titulo da atividade",
  "introducao": "1 ou 2 frases situando o aluno no assunto",
  "conceitos": [
    {{"nome": "Teorema de Pitagoras", "definicao": "Explicacao curta em linguagem de {serie}", "formula": "a² = b² + c²"}}
  ],
  "exercicios": [
    {{
      "numero": 1,
      "enunciado": "Pergunta completa, citando os rotulos que aparecem na figura",
      "figura_descricao": "Triangulo retangulo ABC com angulo reto em B, cateto AB = 3 cm (vertical), cateto BC = 4 cm (horizontal), hipotenusa AC. Marcar o angulo reto em B e rotular os tres vertices e as duas medidas.",
      "dica": "Empurrao inicial, sem entregar a resposta",
      "resposta": "Resposta final, curta",
      "resolucao": "Passo a passo do calculo, com as contas explicitas"
    }}
  ]
}}

REGRAS:
1. Entre 4 e {MAX_EXERCICIOS_GEOMETRIA} exercicios, em dificuldade crescente.
2. 2 a 4 conceitos, so os que a atividade realmente cobra.
3. TODA medida citada no enunciado precisa aparecer tambem em `figura_descricao` — enunciado e figura nao podem se contradizer.
4. Use numeros que fecham em resultado limpo sempre que possivel (3-4-5, 5-12-13, 6-8-10).
5. Unidades sempre explicitas (cm, m, cm²).
6. `resolucao` mostra a conta, nao so o resultado.
7. RETORNE APENAS O JSON, sem markdown e sem explicacao."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )

            # Truncamento silencioso foi a causa raiz de "erro na geracao" sem
            # pista nenhuma em Materiais Adaptados (ver ai_materiais_service).
            # Aqui a checagem vem de fabrica.
            if getattr(response, "stop_reason", None) == "max_tokens":
                return {
                    "success": False,
                    "error": (
                        "A resposta da IA foi cortada no limite de tokens. "
                        "Peca um conteudo mais especifico (ex: so triangulos, "
                        "so area de quadrilateros)."
                    ),
                }

            texto = response.content[0].text.strip()
            texto = texto.replace("```json", "").replace("```", "").strip()

            try:
                atividade = json.loads(texto)
            except json.JSONDecodeError as je:
                return {
                    "success": False,
                    "error": f"JSON invalido retornado pela IA: {je}",
                }

            exercicios: List[Dict[str, Any]] = atividade.get("exercicios") or []
            if not isinstance(exercicios, list) or not exercicios:
                return {"success": False, "error": "A IA nao devolveu nenhum exercicio."}

            # Teto de custo: a IA pode devolver mais do que o prompt pediu, e
            # cada exercicio a mais e mais uma chamada paga de figura.
            exercicios = exercicios[:MAX_EXERCICIOS_GEOMETRIA]
            atividade["exercicios"] = exercicios

            with ThreadPoolExecutor(max_workers=_MAX_WORKERS_FIGURA) as executor:
                list(executor.map(lambda ex: self._gerar_figura_svg(ex, serie), exercicios))

            # O viewer usa isto para avisar o professor quando alguma figura
            # nao saiu — antes de ele imprimir e descobrir na sala de aula.
            atividade["figuras_geradas"] = sum(1 for ex in exercicios if ex.get("figura_svg"))
            atividade["total_exercicios"] = len(exercicios)

            return {
                "success": True,
                "json": atividade,
                "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}


# Instância global do service
material_service = MaterialGeracaoService()
