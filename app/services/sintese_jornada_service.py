"""
🧭 Servico da Sintese da Jornada Terapeutica.

Consolida os relatorios/laudos do aluno numa SINTESE compacta e persistida
(model SinteseJornada) que:
  1) serve de perfil visual/historico do aluno;
  2) alimenta como PARAMETRO os geradores de tarefa (materiais, provas,
     atividades do PEI, programa de casa) via contexto_para_prompt().

Regras de ouro:
  - contexto_para_prompt() NUNCA levanta excecao e NUNCA chama IA: le a sintese
    ja gravada (rapido/barato). Se nao houver, devolve "" (os geradores seguem
    normais, sem a jornada). Gerar tarefa jamais pode quebrar por causa disso.
  - A regeneracao (que chama IA) acontece so em obter_ou_gerar() — na tela da
    jornada (auto, se as fontes mudaram) e no botao "atualizar agora".
"""
import hashlib
import json
from typing import Optional

import tokenmeter as tm

from app.core.anthropic_client import get_anthropic_client, get_default_model
from app.core.features import F
from app.core.logging_config import get_logger
from app.models.relatorio import Relatorio
from app.models.student import Student
from app.models.sintese_jornada import SinteseJornada

logger = get_logger(__name__)


def _parse_json(texto: str) -> dict:
    bruto = (texto or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(bruto)
    except Exception:
        ini, fim = bruto.find("{"), bruto.rfind("}")
        if ini != -1 and fim != -1 and fim > ini:
            try:
                return json.loads(bruto[ini:fim + 1])
            except Exception:
                pass
    return {}


def _relatorios_do_aluno(db, student_id: int):
    return (
        db.query(Relatorio)
        .filter(Relatorio.student_id == student_id)
        .order_by(Relatorio.data_emissao.asc())
        .all()
    )


def _hash_fontes(relatorios) -> str:
    """Hash estavel das fontes: muda quando entra/atualiza/sai um relatorio."""
    partes = []
    for r in relatorios:
        atualizado = getattr(r, "updated_at", None) or getattr(r, "created_at", None)
        partes.append("%s:%s" % (r.id, atualizado.isoformat() if atualizado else ""))
    base = "|".join(partes)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _montar_prompt(student_name: str, relatorios) -> str:
    linhas = []
    for r in relatorios:
        linhas.append(
            "- [%s] %s (%s): %s" % (
                (r.data_emissao.date().isoformat() if r.data_emissao else "s/data"),
                r.tipo or "relatorio",
                r.profissional_especialidade or "profissional",
                (r.resumo or "")[:600],
            )
        )
    corpo = "\n".join(linhas) if linhas else "(sem relatorios)"

    return (
        "Voce e um especialista em educacao inclusiva e reabilitacao. A partir dos\n"
        "relatorios/laudos abaixo do aluno %s, escreva uma SINTESE da jornada\n"
        "terapeutica que sera usada para PERSONALIZAR tarefas (materiais, provas,\n"
        "atividades) e para o profissional acompanhar a evolucao.\n\n"
        "RELATORIOS (ordem cronologica):\n%s\n\n"
        "Responda APENAS com JSON valido neste formato:\n"
        "{\n"
        '  "resumo": "texto corrido de ate 150 palavras: quem e o aluno hoje, '
        'pontos fortes, principais dificuldades e o foco recomendado agora. '
        'Linguagem util para orientar quem cria as tarefas.",\n'
        '  "pontos_fortes": ["..."],\n'
        '  "dificuldades": ["..."],\n'
        '  "recomendacoes": ["orientacoes praticas para as tarefas/atividades"],\n'
        '  "linha_do_tempo": [{"data": "YYYY-MM-DD", "marco": "..."}]\n'
        "}\n"
        "Seja concreto e apoiado nos relatorios; nao invente diagnosticos."
    ) % (student_name, corpo)


@tm.feature(F.JORNADA_TERAPEUTICA)
def gerar_sintese(db, student_id: int, gerado_por_id: Optional[int] = None) -> Optional[SinteseJornada]:
    """(Re)gera a sintese chamando a IA e faz upsert. Retorna None se o aluno
    nao tiver relatorios."""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return None
    relatorios = _relatorios_do_aluno(db, student_id)
    if not relatorios:
        return None

    prompt = _montar_prompt(student.name, relatorios)
    client = get_anthropic_client(timeout=90.0, max_retries=2)
    message = client.messages.create(
        model=get_default_model(),
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = message.content[0].text if message.content else ""
    dados = _parse_json(texto)
    resumo = (dados.get("resumo") or "").strip() if isinstance(dados, dict) else ""
    if not resumo:
        # Fallback: nao deixa a sintese vazia se a IA fugir do formato.
        resumo = "Sintese indisponivel no momento (a IA nao retornou o formato esperado)."

    sintese = db.query(SinteseJornada).filter(SinteseJornada.student_id == student_id).first()
    fonte_hash = _hash_fontes(relatorios)
    if sintese:
        sintese.resumo = resumo
        sintese.dados_json = dados if isinstance(dados, dict) else None
        sintese.fonte_hash = fonte_hash
        sintese.n_relatorios = len(relatorios)
        if gerado_por_id:
            sintese.gerado_por_id = gerado_por_id
    else:
        sintese = SinteseJornada(
            student_id=student_id,
            resumo=resumo,
            dados_json=dados if isinstance(dados, dict) else None,
            fonte_hash=fonte_hash,
            n_relatorios=len(relatorios),
            gerado_por_id=gerado_por_id,
        )
        db.add(sintese)
    db.commit()
    db.refresh(sintese)
    return sintese


def obter_ou_gerar(db, student_id: int, forcar: bool = False,
                   gerado_por_id: Optional[int] = None) -> Optional[SinteseJornada]:
    """Devolve a sintese; regenera se forcado, se nao existir, ou se os
    relatorios mudaram desde a ultima geracao (fonte_hash diferente)."""
    sintese = db.query(SinteseJornada).filter(SinteseJornada.student_id == student_id).first()
    relatorios = _relatorios_do_aluno(db, student_id)
    if not relatorios:
        return sintese  # pode ser None; sem fontes nao ha o que gerar
    atual = _hash_fontes(relatorios)
    if forcar or sintese is None or sintese.fonte_hash != atual:
        try:
            return gerar_sintese(db, student_id, gerado_por_id=gerado_por_id)
        except Exception:
            logger.exception("Falha ao gerar sintese da jornada (student_id=%s)", student_id)
            return sintese  # devolve a antiga (se houver) em vez de quebrar
    return sintese


def contexto_para_prompt(db, student_id: Optional[int]) -> str:
    """Bloco curto pronto para injetar nos prompts de geracao de tarefa.

    LE a sintese ja gravada (sem IA). Devolve "" se nao houver aluno/sintese ou
    em qualquer erro — gerar tarefa nunca deve falhar por causa disto.
    """
    if not student_id:
        return ""
    try:
        sintese = db.query(SinteseJornada).filter(SinteseJornada.student_id == student_id).first()
        if not sintese or not (sintese.resumo or "").strip():
            return ""
        return (
            "\n\n=== JORNADA TERAPEUTICA DO ALUNO (use para personalizar) ===\n"
            + sintese.resumo.strip()
            + "\nAdapte dificuldade, exemplos e apoios ao perfil acima.\n"
        )
    except Exception:
        logger.warning("contexto_para_prompt falhou (student_id=%s)", student_id, exc_info=True)
        return ""


def contexto_para_prompt_por_paciente(db, paciente_id: Optional[int]) -> str:
    """Versao para o lado CLINICA: resolve paciente -> aluno pela ponte e
    reusa contexto_para_prompt. "" se nao houver vinculo/sintese."""
    if not paciente_id:
        return ""
    try:
        from app.models.clinica_core import VinculoAlunoPaciente
        vinc = (
            db.query(VinculoAlunoPaciente)
            .filter(VinculoAlunoPaciente.paciente_id == paciente_id)
            .first()
        )
        if not vinc or not getattr(vinc, "aluno_id", None):
            return ""
        return contexto_para_prompt(db, vinc.aluno_id)
    except Exception:
        logger.warning("contexto_por_paciente falhou (paciente_id=%s)", paciente_id, exc_info=True)
        return ""
