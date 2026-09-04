"""
Servico de rascunho de EVOLUCAO clinica por IA (vertical CLINICA).

A partir dos dados objetivos da sessao (metas trabalhadas, tentativas/acertos,
% de independencia, nivel de ajuda e observacao do terapeuta), a IA redige um
RASCUNHO de nota de evolucao. Segue a regra do projeto (mesma do Modo Papel):
a IA apenas rascunha; a evolucao so vale apos assinada por profissional
habilitado (endpoint /clinica/evolucoes/{id}/assinar).

Instrumentacao de custo: decorator @tm.feature — o gancho unico do projeto.
Nao passamos nome de paciente para a IA (minimizacao de dado); a nota trabalha
com os dados clinicos da sessao, nao com identificacao.
"""
from typing import Any, Dict, List, Optional

from app.core.anthropic_client import get_anthropic_client, get_default_model
from app.core.logging_config import get_logger

import tokenmeter as tm
from app.core.features import F

logger = get_logger(__name__)


def _linhas_metas(metas: List[Dict[str, Any]]) -> str:
    linhas = []
    for m in metas:
        desc = (m.get("descricao") or "").strip()
        esp = m.get("especialidade") or ""
        tent = m.get("tentativas")
        ac = m.get("acertos")
        pct = m.get("percentual_independencia")
        nivel = m.get("nivel_ajuda") or ""
        partes = ["- Meta: %s" % desc]
        if esp:
            partes.append("(%s)" % esp)
        metr = []
        if tent is not None and ac is not None:
            metr.append("%s/%s acertos" % (ac, tent))
        if pct is not None:
            metr.append("%s%% independencia" % pct)
        if nivel:
            metr.append("ajuda: %s" % nivel)
        if metr:
            partes.append("[" + ", ".join(metr) + "]")
        linhas.append(" ".join(partes))
    return "\n".join(linhas) if linhas else "- (sem registro de metas nesta sessao)"


def _montar_prompt(especialidade: Optional[str], metas: List[Dict[str, Any]],
                   observacao: Optional[str]) -> str:
    esp = ("Especialidade da sessao: %s\n" % especialidade) if especialidade else ""
    obs = ("Observacao do terapeuta: %s\n" % observacao.strip()) if (observacao or "").strip() else ""
    return (
        "Voce e assistente de um terapeuta que atende criancas/adolescentes com TEA.\n"
        "Redija um RASCUNHO curto de NOTA DE EVOLUCAO da sessao, em portugues, tom\n"
        "clinico e objetivo, baseado APENAS nos dados abaixo. NAO invente fatos,\n"
        "NAO cite nome do paciente, NAO faca diagnostico. Se um dado faltar, apenas\n"
        "nao o mencione.\n\n"
        + esp +
        "Metas trabalhadas e desempenho:\n"
        + _linhas_metas(metas) + "\n"
        + obs +
        "\nEstruture em 3 partes curtas: (1) o que foi trabalhado; (2) desempenho e\n"
        "nivel de ajuda; (3) encaminhamento/proximo passo sugerido. Maximo ~120\n"
        "palavras. Responda SOMENTE com o texto da nota (sem titulos em markdown)."
    )


@tm.feature(F.EVOLUCAO_RASCUNHO)
def rascunhar_evolucao(
    metas: List[Dict[str, Any]],
    especialidade: Optional[str] = None,
    observacao: Optional[str] = None,
) -> str:
    """Devolve o TEXTO do rascunho de evolucao. Nunca persiste nem assina."""
    prompt = _montar_prompt(especialidade, metas, observacao)
    client = get_anthropic_client(timeout=60.0, max_retries=2)
    message = client.messages.create(
        model=get_default_model(),
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = message.content[0].text if message.content else ""
    texto = (texto or "").strip()
    if not texto:
        logger.warning("Rascunho de evolucao vazio da IA.")
        texto = "(A IA nao retornou texto. Redija a evolucao manualmente.)"
    return texto


def _prompt_ditado(texto, especialidade):
    esp = ("Especialidade da sessao: %s\n" % especialidade) if especialidade else ""
    return (
        "Voce ajuda um terapeuta a organizar uma nota de evolucao DITADA POR VOZ.\n"
        "Reescreva o ditado abaixo como uma NOTA DE EVOLUCAO clinica, em portugues,\n"
        "clara e objetiva: corrija pontuacao e organize em ate 3 partes curtas (o que\n"
        "foi trabalhado; desempenho e nivel de ajuda; proximo passo). NAO invente\n"
        "fatos, NAO cite nome de paciente, NAO faca diagnostico. Preserve o conteudo\n"
        "do ditado. Responda SOMENTE com o texto da nota (sem titulos em markdown).\n\n"
        + esp + "Ditado:\n" + (texto or "").strip()
    )


@tm.feature(F.EVOLUCAO_DITADO)
def estruturar_ditado(texto, especialidade=None):
    """Transforma um ditado bruto (voz->texto) numa nota de evolucao organizada."""
    client = get_anthropic_client(timeout=60.0, max_retries=2)
    message = client.messages.create(
        model=get_default_model(),
        max_tokens=600,
        messages=[{"role": "user", "content": _prompt_ditado(texto, especialidade)}],
    )
    out = message.content[0].text if message.content else ""
    return (out or "").strip() or (texto or "").strip()
