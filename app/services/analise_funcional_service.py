"""
Analise funcional do comportamento por IA (vertical CLINICA).

A partir dos registros ABC (antecedente, comportamento, consequencia, frequencia,
intensidade), a IA levanta a HIPOTESE de FUNCAO do comportamento-alvo e sugere
estrategias baseadas na funcao. Hipotese clinica, nao diagnostico. Minimizacao:
sem nome de paciente.
"""
import json
import re
from typing import Any, Dict, List, Optional

from app.core.anthropic_client import get_anthropic_client, get_default_model
from app.core.logging_config import get_logger

import tokenmeter as tm
from app.core.features import F

logger = get_logger(__name__)

FUNCOES = ["FUGA", "ATENCAO", "TANGIVEL", "SENSORIAL", "MULTIPLA", "INDETERMINADA"]


def _linhas(registros: List[Dict[str, Any]]) -> str:
    out = []
    for r in registros:
        p = []
        if r.get("antecedente"):
            p.append("A: %s" % r["antecedente"].strip())
        p.append("C: %s" % (r.get("comportamento") or "").strip())
        if r.get("consequencia"):
            p.append("Cq: %s" % r["consequencia"].strip())
        extra = []
        if r.get("frequencia") is not None:
            extra.append("freq %s" % r["frequencia"])
        if r.get("intensidade"):
            extra.append("int. %s" % r["intensidade"])
        if r.get("duracao_seg"):
            extra.append("%ss" % r["duracao_seg"])
        if extra:
            p.append("[" + ", ".join(extra) + "]")
        out.append("- " + " | ".join(p))
    return "\n".join(out) if out else "- (sem registros)"


def _prompt(alvo: Optional[str], registros: List[Dict[str, Any]]) -> str:
    foco = ("Comportamento-alvo: %s\n" % alvo) if alvo else ""
    return (
        "Voce e analista do comportamento (ABA). A partir dos registros ABC abaixo\n"
        "(A=antecedente, C=comportamento, Cq=consequencia), levante a HIPOTESE da\n"
        "FUNCAO do comportamento. Baseie-se SO nos padroes dos dados. Isto e uma\n"
        "hipotese clinica, nao um diagnostico. Nao cite nome de paciente.\n\n"
        + foco +
        "Registros:\n" + _linhas(registros) + "\n\n"
        "Funcoes possiveis (chaves EXATAS):\n"
        "FUGA (escapar de demanda/tarefa), ATENCAO (obter atencao social),\n"
        "TANGIVEL (obter item/atividade), SENSORIAL (automatica/autoestimulacao),\n"
        "MULTIPLA (mais de uma), INDETERMINADA (dados insuficientes).\n\n"
        "Responda SOMENTE com JSON valido, sem markdown:\n"
        '{\"funcao_provavel\": \"<CHAVE>\", \"confianca\": <0.0-1.0>, '
        '\"padrao\": \"<1-2 frases: o que os antecedentes/consequencias sugerem>\", '
        '\"estrategias\": [\"<antecedente: prevenir/modificar o gatilho>\", '
        '\"<ensino: comportamento substituto funcional>\", '
        '\"<consequencia: como responder para nao reforcar a funcao>\"]}'
    )


def _parse(t: str) -> Dict[str, Any]:
    t = (t or "").strip()
    t = re.sub(r"^```(json)?|```$", "", t, flags=re.MULTILINE).strip()
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1 and b > a:
        t = t[a:b + 1]
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        return {}


@tm.feature(F.CLINICA_ANALISE_FUNCIONAL)
def analisar(alvo: Optional[str], registros: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = _parse(_chamar(alvo, registros))
    fx = data.get("funcao_provavel")
    if fx not in FUNCOES:
        fx = "INDETERMINADA"
    conf = data.get("confianca")
    try:
        conf = max(0.0, min(1.0, round(float(conf), 2)))
    except (TypeError, ValueError):
        conf = None
    est = data.get("estrategias")
    if not isinstance(est, list):
        est = []
    return {
        "funcao_provavel": fx,
        "confianca": conf,
        "padrao": (data.get("padrao") or "").strip() or None,
        "estrategias": [str(e).strip() for e in est if str(e).strip()][:5],
    }


def _chamar(alvo, registros) -> str:
    client = get_anthropic_client(timeout=60.0, max_retries=2)
    message = client.messages.create(
        model=get_default_model(),
        max_tokens=600,
        messages=[{"role": "user", "content": _prompt(alvo, registros)}],
    )
    return message.content[0].text if message.content else ""
