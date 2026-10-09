"""
Processador Incremental de Relatorios
Analisa PDFs em etapas e notifica progresso em tempo real
"""
import base64
import json
from pathlib import Path
from datetime import datetime
import hashlib
import asyncio
from typing import Callable, Awaitable, Optional

from app.database import SessionLocal
from app.models.relatorio import Relatorio
from app.services.websocket_manager import manager
from app.core.config import settings
from app.core.ai_usage import registrar_uso_ia

# tokenmeter: atribuicao de consumo de IA (ver app/core/features.py)
import tokenmeter as tm
from app.core.features import F


class RelatorioProcessorIncremental:
    """Processa relatorios em etapas, notificando progresso"""
    
    def __init__(self, anthropic_client, modelo=None):
        self.client = anthropic_client
        # Fallback do modelo: antes 'claude-3-5-sonnet-20241022' (aposentado em
        # 28/10/2025). Em prod usa settings.LLM_MODEL; o fallback agora e atual.
        self.modelo = modelo or settings.LLM_MODEL
    
    async def processar_incremental(
        self,
        relatorio_id: int,
        pdf_path: Path,
        json_path: Path,
        content_type: str,
        user_id: int,
        student_id: int = None,
        escola_id: int = None
    ):
        """
        Processa relatório em etapas, enviando atualizações via WebSocket
        
        Etapas:
        1. Extração básica (20%) - Nome, registro, especialidade
        2. Datas e CID (40%) - Datas e diagnósticos
        3. Resumo (60%) - Resumo clínico
        4. Detalhes (80%) - Recomendações e adaptações
        5. Finalização (100%) - Salvar tudo
        """
        
        # Contexto de atribuicao (student_id/user_id/escola_id) usado por
        # _call_claude para registrar o consumo de tokens de cada uma das 4
        # chamadas abaixo.
        self._student_id = student_id
        self._user_id = user_id
        self._escola_id = escola_id

        try:
            # Ler arquivo
            with open(pdf_path, "rb") as f:
                file_content = f.read()
            
            file_base64 = base64.standard_b64encode(file_content).decode("utf-8")
            media_type = self._get_media_type(content_type)
            
            # ETAPA 1: Dados básicos do profissional (20%)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "extracting_professional", 20
            )
            
            profissional_data = await self._extrair_profissional(
                file_base64, media_type, content_type
            )
            
            await self._atualizar_banco(relatorio_id, profissional_data)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "professional_extracted", 20, profissional_data
            )
            
            # ETAPA 2: Datas e diagnósticos (40%)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "extracting_diagnostics", 40
            )
            
            diagnostico_data = await self._extrair_diagnosticos(
                file_base64, media_type, content_type
            )
            
            await self._atualizar_banco(relatorio_id, diagnostico_data)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "diagnostics_extracted", 40, diagnostico_data
            )
            
            # ETAPA 3: Resumo clínico (60%)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "extracting_summary", 60
            )
            
            resumo_data = await self._extrair_resumo(
                file_base64, media_type, content_type
            )
            
            await self._atualizar_banco(relatorio_id, resumo_data)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "summary_extracted", 60, resumo_data
            )
            
            # ETAPA 4: Recomendações e adaptações (80%)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "extracting_recommendations", 80
            )
            
            recomendacoes_data = await self._extrair_recomendacoes(
                file_base64, media_type, content_type
            )
            
            await self._atualizar_banco(relatorio_id, recomendacoes_data)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "recommendations_extracted", 80, recomendacoes_data
            )
            
            # ETAPA 5: Consolidar dados completos (100%)
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "finalizing", 90
            )
            
            # Juntar todos os dados
            dados_completos = {
                **profissional_data,
                **diagnostico_data,
                **resumo_data,
                **recomendacoes_data
            }
            
            # Salvar JSON completo
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(dados_completos, f, ensure_ascii=False, indent=2)
            
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "completed", 100, {"json_saved": True}
            )
            
            print(f"🎉 [INCREMENTAL] Relatório {relatorio_id} processado!")
            
        except Exception as e:
            print(f"❌ [INCREMENTAL] Erro: {e}")
            await manager.notify_relatorio_progress(
                user_id, relatorio_id, "error", 0, {"error": str(e)}
            )
    
    async def _extrair_profissional(self, file_base64, media_type, content_type):
        """Extrai apenas dados do profissional"""
        prompt = """Extraia APENAS as informações do profissional deste relatório.
        
Retorne JSON com:
{
    "tipo_laudo": "string",
    "profissional": {
        "nome": "string",
        "registro": "string", 
        "especialidade": "string"
    }
}

APENAS JSON, sem explicações."""
        
        response = await asyncio.to_thread(
            self._call_claude, file_base64, media_type, content_type, prompt
        )
        return self._parse_json(response)
    
    async def _extrair_diagnosticos(self, file_base64, media_type, content_type):
        """Extrai datas e diagnósticos"""
        prompt = """Extraia APENAS datas e diagnósticos deste relatório.

Retorne JSON com:
{
    "datas": {
        "emissao": "YYYY-MM-DD",
        "validade": "YYYY-MM-DD"
    },
    "diagnosticos": [{"cid": "string", "descricao": "string"}],
    "condicoes_identificadas": {
        "tea": false,
        "tdah": false,
        "dislexia": false,
        "discalculia": false,
        "deficiencia_intelectual": false
    }
}

APENAS JSON, sem explicações."""
        
        response = await asyncio.to_thread(
            self._call_claude, file_base64, media_type, content_type, prompt
        )
        return self._parse_json(response)
    
    async def _extrair_resumo(self, file_base64, media_type, content_type):
        """Extrai resumo clínico completo"""
        prompt = """Extraia APENAS o resumo clínico deste relatório.

Retorne JSON com:
{
    "resumo_clinico": "string (texto completo, sem truncar)"
}

IMPORTANTE: Inclua TODO o resumo, não corte o texto.
APENAS JSON, sem explicações."""
        
        response = await asyncio.to_thread(
            self._call_claude, file_base64, media_type, content_type, prompt
        )
        return self._parse_json(response)
    
    async def _extrair_recomendacoes(self, file_base64, media_type, content_type):
        """Extrai recomendações e adaptações"""
        prompt = """Extraia APENAS recomendações e adaptações deste relatório.

Retorne JSON com:
{
    "recomendacoes": ["string"],
    "adaptacoes_sugeridas": {
        "curriculares": "string",
        "avaliacao": "string",
        "ambiente": "string",
        "recursos": "string"
    },
    "acompanhamentos_indicados": [
        {"profissional": "string", "frequencia": "string"}
    ]
}

APENAS JSON, sem explicações."""
        
        response = await asyncio.to_thread(
            self._call_claude, file_base64, media_type, content_type, prompt
        )
        return self._parse_json(response)
    
    @tm.feature(F.RELATORIO_UPLOAD)
    def _call_claude(self, file_base64, media_type, content_type, prompt):
        """Chama Claude de forma síncrona (para usar com asyncio.to_thread)"""
        message = self.client.messages.create(
            model=self.modelo,
            max_tokens=2000,  # Menor = mais rápido
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
                        {"type": "text", "text": prompt}
                    ],
                }
            ],
        )

        registrar_uso_ia(
            feature="relatorio_upload",
            model=self.modelo,
            usage=message.usage,
            student_id=getattr(self, "_student_id", None),
            user_id=getattr(self, "_user_id", None),
            escola_id=getattr(self, "_escola_id", None),
        )

        return message.content[0].text.strip()
    
    def _parse_json(self, text):
        """Parse JSON removendo markdown"""
        text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(text)
        except:
            return {}
    
    def _get_media_type(self, content_type):
        """Retorna media type correto"""
        mapping = {
            "application/pdf": "application/pdf",
            "image/jpeg": "image/jpeg",
            "image/jpg": "image/jpeg",
            "image/png": "image/png",
            "image/webp": "image/webp"
        }
        return mapping.get(content_type, content_type)
    
    async def _atualizar_banco(self, relatorio_id, data):
        """Atualiza banco com dados parciais"""
        db = SessionLocal()
        try:
            relatorio = db.query(Relatorio).filter(Relatorio.id == relatorio_id).first()
            if not relatorio:
                return
            
            # Atualizar campos conforme dados disponíveis
            if "tipo_laudo" in data:
                relatorio.tipo = data["tipo_laudo"][:100]
            
            if "profissional" in data:
                prof = data["profissional"]
                relatorio.profissional_nome = prof.get("nome", "")[:200]
                relatorio.profissional_registro = prof.get("registro", "")[:50]
                relatorio.profissional_especialidade = prof.get("especialidade", "")[:100]
            
            if "datas" in data:
                datas = data["datas"]
                if datas.get("emissao"):
                    try:
                        relatorio.data_emissao = datetime.strptime(datas["emissao"], "%Y-%m-%d")
                    except:
                        pass
            
            if "resumo_clinico" in data:
                relatorio.resumo = data["resumo_clinico"][:200]
            
            db.commit()
            
        except Exception as e:
            print(f"[ERRO] Atualizar banco: {e}")
            db.rollback()
        finally:
            db.close()


# ============================================
# FUNCAO STANDALONE - usada por relatorios_v2.py
# ============================================
# API esperada:
#   dados = await processar_relatorio_com_progresso(pdf_path, api_key, progress_callback)
# onde progress_callback e uma corotina assinatura (progress: int, message: str) -> None

async def processar_relatorio_com_progresso(
    pdf_path: Path,
    api_key: str,
    progress_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
) -> dict:
    """
    Processa um PDF de relatorio extraindo informacoes estruturadas com IA.
    Envia progresso via progress_callback async.
    
    Retorna um dict consolidado com:
    - tipo_laudo, profissional (dict)
    - datas (dict com emissao/validade)
    - diagnosticos (list), condicoes_identificadas (dict)
    - resumo_clinico (str)
    - recomendacoes (list), adaptacoes_sugeridas (dict)
    """
    from app.core.anthropic_client import get_anthropic_client
    
    async def _notify(progress: int, message: str):
        if progress_callback:
            try:
                await progress_callback(progress, message)
            except Exception as e:
                print(f"[WARN] Progress callback falhou: {e}")
    
    # Determinar content_type pela extensao
    ext = pdf_path.suffix.lower()
    content_type_map = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    content_type = content_type_map.get(ext, "application/pdf")
    
    await _notify(5, "Carregando arquivo...")
    
    # Ler arquivo
    with open(pdf_path, "rb") as f:
        file_content = f.read()
    file_base64 = base64.standard_b64encode(file_content).decode("utf-8")
    
    await _notify(10, "Inicializando IA...")
    
    # Cliente Anthropic (singleton instrumentado). O parametro `api_key` e mantido
    # por compatibilidade de assinatura, mas a chave usada e a de settings.
    client = get_anthropic_client()
    processor = RelatorioProcessorIncremental(client)
    media_type = processor._get_media_type(content_type)
    
    # Processar em 4 etapas em paralelo (mais rapido que sequencial)
    await _notify(20, "Extraindo dados do profissional...")
    
    # Executar as 4 extracoes em paralelo
    try:
        results = await asyncio.gather(
            processor._extrair_profissional(file_base64, media_type, content_type),
            processor._extrair_diagnosticos(file_base64, media_type, content_type),
            processor._extrair_resumo(file_base64, media_type, content_type),
            processor._extrair_recomendacoes(file_base64, media_type, content_type),
            return_exceptions=True
        )
    except Exception as e:
        await _notify(0, f"Erro na extracao: {e}")
        raise
    
    await _notify(80, "Consolidando resultados...")
    
    # Consolidar (ignorando extracoes que falharam)
    profissional_data, diagnostico_data, resumo_data, recomendacoes_data = results
    dados_completos = {}
    for r in results:
        if isinstance(r, dict):
            dados_completos.update(r)
        elif isinstance(r, Exception):
            print(f"[WARN] Extracao parcial falhou: {r}")
    
    await _notify(100, "Processamento concluido!")
    return dados_completos

