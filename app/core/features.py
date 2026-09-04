"""
Nomes canonicos das features que consomem IA.

MOTIVACAO: ate aqui os nomes de feature eram string literal repetida em cada
chamada a registrar_uso_ia(), documentados num .md sem nenhum enforcement. Com
isso, "prova_reforco", "Prova_Reforco" e "prova_de_reforco" viram tres features
distintas no relatorio e ninguem percebe - o custo se fragmenta em silencio.

Aqui e a fonte unica. O tokenmeter recebe esta lista em `known_features` e emite
warning (nunca excecao) quando ve um nome que nao esta nela.

Uso:

    from app.core.features import F
    import tokenmeter as tm

    with tm.context(feature=F.PEI_GERACAO, tags={"entity_id": pei_id,
                                                 "entity_type": "pei"}):
        ...

REGRA PARA AS TAGS (importante, o dominio lida com dado de saude):
valores de tag sao IDENTIFICADORES OPACOS ou enums de sistema. Nunca atributo
clinico, nunca texto livre, nunca nome de pessoa.

    OK   entity_id="8842", tenant_id="7", entity_type="laudo"
    NAO  diagnostico="F84.0", aluno="Maria S.", observacao="paciente relata..."

O tokenmeter avisa quando um valor "parece conteudo", mas a heuristica so pega o
caso acidental - a regra e de quem escreve.
"""


class F:
    """Constantes de feature. Use SEMPRE estas, nunca string literal."""

    # --- ja existiam em ai_usage_log (mantidos identicos para o historico
    #     continuar comparavel com o que ja foi gravado) -----------------------
    PEI_ANALISE_LAUDO = "pei_analise_laudo"
    PEI_GERACAO = "pei_geracao"
    JORNADA_TERAPEUTICA = "jornada_terapeutica"
    PLANEJAMENTO_ANUAL = "planejamento_anual"
    PLANEJAMENTO_ANUAL_COMPLETO = "planejamento_anual_completo"
    PLANEJAMENTO_TRIMESTRE = "planejamento_trimestre"
    ANALISE_QUALITATIVA = "analise_qualitativa"
    PROVA_REFORCO = "prova_reforco"
    RELATORIO_UPLOAD = "relatorio_upload"

    # --- novos: cobrem os 21 pontos que hoje chamam a IA sem registrar nada ---
    MATERIAL_ADAPTADO = "material_adaptado"        # ai_materiais_service, material_service
    PROVA_GERACAO = "prova_geracao"                # gerador_provas, prova_ai_service
    CALENDARIO_ATIVIDADES = "calendario_atividades"
    DIARIO_APRENDIZAGEM = "diario_aprendizagem"
    REDACAO_CORRECAO = "redacao_correcao"
    REDACAO_FEEDBACK = "redacao_feedback"
    RELATORIO_EXTRACAO = "relatorio_extracao"
    COMUNICACAO_FAMILIA = "comunicacao_familia"
    QUESTAO_GERACAO = "questao_geracao"            # ai_service.generate_questions
    DESEMPENHO_ANALISE = "desempenho_analise"      # ai_service, prova_ai_service
    PROVA_FEEDBACK = "prova_feedback"              # prova_ai_service
    REDACAO_TEMA = "redacao_tema"                  # redacao_ai_service.gerar_tema_atual
    DIARIO_RESUMO_SEMANAL = "diario_resumo_semanal"
    PLANO_AULA = "plano_aula"                      # plano_aula_ai_service
    PROVA_CORRECAO_PAPEL = "prova_correcao_papel"  # prova_folha_service (modo papel)
    REDACAO_PAPEL = "redacao_papel"                # redacao_folha_service (modo papel)
    ILUSTRACAO_IA = "ilustracao_ia"                # ilustracao_service (prompt de imagem)
    EVOLUCAO_RASCUNHO = "evolucao_rascunho"        # evolucao_service (rascunho de nota clinica)
    SESSAO_FOLHA_LEITURA = "sessao_folha_leitura"  # sessao_folha_service (modo papel clinico)
    RELATORIO_EVOLUCAO_CLINICO = "relatorio_evolucao_clinico"  # relatorio_evolucao_service
    PTI_RASCUNHO = "pti_rascunho"                  # pti_service (sugestao de metas)
    HISTORIA_SOCIAL = "historia_social"            # historia_social_service (CAA)
    CLINICA_COPILOTO = "clinica_copiloto"          # copiloto_service (proxima acao no PTI)
    CLINICA_GENERALIZACAO = "clinica_generalizacao"  # generalizacao_service (sintese 3 ambientes)
    EVOLUCAO_FAMILIA = "evolucao_familia"          # tradutor_familia_service (resumo p/ familia)
    CLINICA_ANALISE_FUNCIONAL = "clinica_analise_funcional"  # analise_funcional_service (ABC->funcao)
    EVOLUCAO_DITADO = "evolucao_ditado"            # evolucao_service.estruturar_ditado (voz->nota)

    @classmethod
    def all(cls) -> list[str]:
        return sorted(
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        )


FEATURES = F.all()

# NOTA sobre app/services/ai_cache_service.py: `cached_completion` e um helper
# generico e NAO deve declarar feature propria - ele herda a do contexto de quem
# chamou. E o caso que melhor justifica contextvars: sem isso, so restaria passar
# a feature por parametro em toda chamada (que e como se chega em 32% de
# cobertura) ou deixar sem tracking (que e o estado atual).
