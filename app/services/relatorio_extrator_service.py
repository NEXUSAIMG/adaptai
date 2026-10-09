# ============================================
# SERVICE - Extração de Relatório Diário com IA
# ============================================
"""
Serviço para extrair informações de relatórios diários
escolares usando Claude AI.
"""
from app.core.anthropic_client import get_anthropic_client
import json
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional
import fitz  # PyMuPDF

from app.core.config import settings

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F


class RelatorioExtratorService:
    """
    Serviço para extrair dados de relatórios diários escolares.
    Usa Claude AI para processar PDFs e extrair informações estruturadas.
    """
    
    def __init__(self):
        self.client = get_anthropic_client()
        self.model = settings.LLM_MODEL
    
    def extrair_texto_pdf(self, pdf_path: str) -> str:
        """Extrai texto de um arquivo PDF"""
        try:
            doc = fitz.open(pdf_path)
            texto = ""
            for page in doc:
                texto += page.get_text()
            doc.close()
            return texto
        except Exception as e:
            print(f"Erro ao extrair texto do PDF: {e}")
            return ""
    
    def pdf_para_base64(self, pdf_path: str) -> str:
        """Converte PDF para base64 para enviar ao Claude"""
        with open(pdf_path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    
    @tm.feature(F.RELATORIO_EXTRACAO)
    async def extrair_dados_relatorio(self, pdf_path: str) -> dict:
        """
        Extrai dados estruturados de um relatório diário escolar.
        
        Args:
            pdf_path: Caminho do arquivo PDF
            
        Returns:
            dict com dados extraídos:
            {
                "data": "2026-01-13",
                "serie_turma": "9º ANO",
                "escola": "Colégio IN",
                "aulas": [
                    {
                        "professor": "Humberto Mota",
                        "disciplina": "Matemática",
                        "conteudo": "Revisão de cálculo algébrico",
                        "atividade_sala": "Aula expositiva",
                        "livro": null,
                        "capitulo": null,
                        "paginas": null,
                        "modulo": null,
                        "tem_dever_casa": false,
                        "tem_atividade_avaliativa": false,
                        "dever_casa_descricao": null
                    },
                    ...
                ]
            }
        """
        
        # Extrair texto do PDF primeiro
        texto_pdf = self.extrair_texto_pdf(pdf_path)
        
        prompt = f"""Você é um assistente especializado em extrair informações de relatórios diários escolares.

Analise o seguinte relatório diário de uma escola e extraia todas as informações em formato JSON estruturado.

TEXTO DO RELATÓRIO:
{texto_pdf}

Extraia os dados no seguinte formato JSON (responda APENAS com o JSON, sem texto adicional):

{{
    "data": "YYYY-MM-DD",
    "serie_turma": "série/ano da turma",
    "escola": "nome da escola se identificável",
    "aulas": [
        {{
            "professor": "nome do professor",
            "disciplina": "nome da disciplina",
            "conteudo": "conteúdo estudado",
            "atividade_sala": "descrição da atividade realizada em sala",
            "livro": "número ou nome do livro se mencionado",
            "capitulo": "capítulo se mencionado",
            "paginas": "páginas se mencionadas",
            "modulo": "módulo se mencionado",
            "tem_dever_casa": true/false,
            "tem_atividade_avaliativa": true/false,
            "dever_casa_descricao": "descrição do dever se houver"
        }}
    ]
}}

REGRAS:
1. Se um campo não estiver presente, use null
2. A data deve estar no formato YYYY-MM-DD
3. tem_dever_casa e tem_atividade_avaliativa devem ser true se houver "X" marcado na coluna correspondente
4. Capture TODAS as aulas/disciplinas listadas
5. Se a atividade de sala mencionar páginas/módulos, extraia para os campos correspondentes

Responda APENAS com o JSON válido, sem markdown ou texto adicional."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Extrair o texto da resposta
            response_text = response.content[0].text.strip()
            
            # Limpar possíveis markdown
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            # Parse JSON
            dados = json.loads(response_text.strip())
            
            return {
                "success": True,
                "dados": dados
            }
            
        except json.JSONDecodeError as e:
            print(f"Erro ao parsear JSON: {e}")
            print(f"Resposta: {response_text}")
            return {
                "success": False,
                "error": f"Erro ao processar resposta da IA: {str(e)}",
                "raw_response": response_text
            }
        except Exception as e:
            print(f"Erro na extração: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    @tm.feature(F.RELATORIO_EXTRACAO)
    async def extrair_com_imagem(self, pdf_path: str) -> dict:
        """
        Versão alternativa que envia o PDF como imagem para o Claude.
        Útil quando o PDF tem formatação complexa ou é um scan.
        """
        try:
            # Converter primeira página do PDF para imagem
            doc = fitz.open(pdf_path)
            page = doc[0]
            
            # Renderizar página como imagem
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom para melhor qualidade
            img_bytes = pix.tobytes("png")
            img_base64 = base64.standard_b64encode(img_bytes).decode("utf-8")
            doc.close()
            
            prompt = """Analise esta imagem de um relatório diário escolar e extraia todas as informações em formato JSON.

Extraia os dados no seguinte formato JSON (responda APENAS com o JSON, sem texto adicional):

{
    "data": "YYYY-MM-DD",
    "serie_turma": "série/ano da turma",
    "escola": "nome da escola se identificável",
    "aulas": [
        {
            "professor": "nome do professor",
            "disciplina": "nome da disciplina",
            "conteudo": "conteúdo estudado",
            "atividade_sala": "descrição da atividade realizada em sala",
            "livro": "número ou nome do livro se mencionado",
            "capitulo": "capítulo se mencionado",
            "paginas": "páginas se mencionadas",
            "modulo": "módulo se mencionado",
            "tem_dever_casa": true/false,
            "tem_atividade_avaliativa": true/false,
            "dever_casa_descricao": "descrição do dever se houver"
        }
    ]
}

REGRAS:
1. Se um campo não estiver presente, use null
2. A data deve estar no formato YYYY-MM-DD
3. tem_dever_casa e tem_atividade_avaliativa devem ser true se houver "X" marcado na coluna correspondente
4. Capture TODAS as aulas/disciplinas listadas na tabela

Responda APENAS com o JSON válido."""

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ]
            )
            
            response_text = response.content[0].text.strip()
            
            # Limpar possíveis markdown
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            dados = json.loads(response_text.strip())
            
            return {
                "success": True,
                "dados": dados
            }
            
        except Exception as e:
            print(f"Erro na extração com imagem: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Instância global do serviço
relatorio_extrator_service = RelatorioExtratorService()
