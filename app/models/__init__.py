# ============================================
# MODELOS - AdaptAI Multi-tenant
# ============================================

# Multi-tenant (Escolas e Assinaturas)
from app.models.escola import Escola, ConfiguracaoEscola
from app.models.plano import Plano
from app.models.assinatura import Assinatura, Fatura, StatusAssinatura, StatusFatura

# Usuários e Alunos
from app.models.user import User, UserRole
from app.models.student import Student

# Questões e Provas
from app.models.question import QuestionSet, Question, DifficultyLevel
from app.models.application import Application, StudentAnswer, ApplicationStatus
from app.models.prova import (
    Prova, 
    QuestaoGerada, 
    ProvaAluno, 
    RespostaAluno,
    StatusProva,
    StatusProvaAluno,
    DificuldadeQuestao,
    TipoQuestao
)

# Análises e Relatórios
from app.models.performance import PerformanceAnalysis
from app.models.relatorio import Relatorio
from app.models.analise_qualitativa import AnaliseQualitativa

# Materiais
from app.models.material import Material, MaterialAluno, StatusMaterial
from app.models.material_adaptado_gerado import MaterialAdaptadoGerado

# Currículo e BNCC
from app.models.curriculo import (
    CurriculoNacional,
    MapeamentoPrerequisitos,
    CurriculoEscola,
    DificuldadeCurriculo
)

# PEI - Plano Educacional Individualizado
from app.models.pei import (
    PEI,
    PEIObjetivo,
    PEIProgressLog,
    PEIAjuste,
    StatusPEI,
    TipoPeriodo,
    AreaPEI,
    StatusObjetivo,
    OrigemObjetivo
)

# Atividades do PEI (Calendário)
from app.models.atividade_pei import (
    AtividadePEI,
    SequenciaObjetivo,
    TipoAtividade,
    StatusAtividade
)

# Diário de Aprendizagem
from app.models.diario_aprendizagem import (
    DiarioAprendizagem,
    ConteudoExtraido,
    ResumoSemanalAprendizagem,
    HumorEstudo,
    NivelCompreensao
)

# Agenda do Professor
from app.models.agenda import (
    AgendaProfessor,
    LembreteAgenda,
    TipoEvento,
    StatusEvento,
    Recorrencia
)

# Registro Diário de Aulas
from app.models.registro_diario import (
    RegistroDiario,
    AulaRegistrada
)

# Redações ENEM
from app.models.redacao import (
    TemaRedacao,
    RedacaoAluno,
    StatusRedacao
)

# Jobs de Planejamento
from app.models.planejamento_job import (
    PlanejamentoJob,
    PlanejamentoJobLog,
    JobStatus
)

# Background Tasks (E2 - persistidos no DB)
from app.models.background_task import (
    BackgroundTask,
    BackgroundTaskStatus
)

# Cache de respostas de IA (E3 - economia de creditos Anthropic)
from app.models.ai_cache import AICache

# Log de consumo de tokens da IA (contador por feature/aluno/usuario)
from app.models.ai_usage_log import AIUsageLog

# Ilustracoes (apoio visual: pictogramas ARASAAC + ilustracao IA)
from app.models.ilustracao import (
    Ilustracao,
    ContextoIlustracao,
    FonteIlustracao,
    StatusIlustracao
)


__all__ = [
    # Multi-tenant
    "Escola",
    "ConfiguracaoEscola",
    "Plano",
    "Assinatura",
    "Fatura",
    "StatusAssinatura",
    "StatusFatura",
    
    # Usuários
    "User",
    "UserRole",
    "Student",
    
    # Questões e Provas
    "QuestionSet",
    "Question",
    "DifficultyLevel",
    "Application",
    "StudentAnswer",
    "ApplicationStatus",
    "Prova",
    "QuestaoGerada",
    "ProvaAluno",
    "RespostaAluno",
    "StatusProva",
    "StatusProvaAluno",
    "DificuldadeQuestao",
    "TipoQuestao",
    
    # Análises
    "PerformanceAnalysis",
    "Relatorio",
    "AnaliseQualitativa",
    
    # Materiais
    "Material",
    "MaterialAluno",
    "StatusMaterial",
    "MaterialAdaptadoGerado",
    
    # Currículo e BNCC
    "CurriculoNacional",
    "MapeamentoPrerequisitos",
    "CurriculoEscola",
    "DificuldadeCurriculo",
    
    # PEI
    "PEI",
    "PEIObjetivo",
    "PEIProgressLog",
    "PEIAjuste",
    "StatusPEI",
    "TipoPeriodo",
    "AreaPEI",
    "StatusObjetivo",
    "OrigemObjetivo",
    
    # Atividades PEI (Calendário)
    "AtividadePEI",
    "SequenciaObjetivo",
    "TipoAtividade",
    "StatusAtividade",
    
    # Diário de Aprendizagem
    "DiarioAprendizagem",
    "ConteudoExtraido",
    "ResumoSemanalAprendizagem",
    "HumorEstudo",
    "NivelCompreensao",
    
    # Agenda do Professor
    "AgendaProfessor",
    "LembreteAgenda",
    "TipoEvento",
    "StatusEvento",
    "Recorrencia",
    
    # Registro Diário de Aulas
    "RegistroDiario",
    "AulaRegistrada",
    
    # Redações ENEM
    "TemaRedacao",
    "RedacaoAluno",
    "StatusRedacao",
    
    # Jobs de Planejamento
    "PlanejamentoJob",
    "PlanejamentoJobLog",
    "JobStatus",
    
    # Background Tasks
    "BackgroundTask",
    "BackgroundTaskStatus",
    
    # Cache de IA
    "AICache",

    # Log de consumo de tokens da IA
    "AIUsageLog",

    # Ilustracoes
    "Ilustracao",
    "ContextoIlustracao",
    "FonteIlustracao",
    "StatusIlustracao",
]


# ============================================
# Clinica (vertical CLINICA - Fase 0 + Modulo 1)
# ============================================
from app.models.clinica_core import (
    EscolaModulo, Profissional, Paciente, EquipeCaso, Consentimento,
    VinculoAlunoPaciente, AuditoriaAcesso,
    ModuloEscola, Especialidade, Conselho, PapelProfissional, PapelNoCaso,
    StatusPaciente, TipoConsentimento, AcaoAuditoria,
)
from app.models.clinica_terapia import (
    PlanoTerapeutico, ObjetivoTerapeutico, Sessao, RegistroTentativa, Evolucao,
    StatusPlanoTerapeutico, StatusObjetivoTerapeutico, Presenca, NivelAjuda,
)

__all__ += [
    "EscolaModulo", "Profissional", "Paciente", "EquipeCaso", "Consentimento",
    "VinculoAlunoPaciente", "AuditoriaAcesso",
    "ModuloEscola", "Especialidade", "Conselho", "PapelProfissional", "PapelNoCaso",
    "StatusPaciente", "TipoConsentimento", "AcaoAuditoria",
    "PlanoTerapeutico", "ObjetivoTerapeutico", "Sessao", "RegistroTentativa", "Evolucao",
    "StatusPlanoTerapeutico", "StatusObjetivoTerapeutico", "Presenca", "NivelAjuda",
]


# Clinica - CAA (pranchas de comunicacao)
from app.models.clinica_caa import Prancha, PranchaItem, TipoPrancha
__all__ += ["Prancha", "PranchaItem", "TipoPrancha"]

# Clinica - Agenda
from app.models.clinica_agenda import Agendamento, StatusAgendamento
__all__ += ["Agendamento", "StatusAgendamento"]

# Clinica - Programa de casa
from app.models.clinica_casa import TarefaCasa, TarefaCasaCheck
__all__ += ["TarefaCasa", "TarefaCasaCheck"]

# Clinica - Mensagens familia
from app.models.clinica_mensagens import MensagemFamilia, OrigemMensagem
__all__ += ["MensagemFamilia", "OrigemMensagem"]

# Clinica - Comportamento (ABC)
from app.models.clinica_comportamento import RegistroComportamento, Intensidade
__all__ += ["RegistroComportamento", "Intensidade"]

# Clinica - Instrumentos padronizados
from app.models.clinica_instrumentos import AplicacaoInstrumento
__all__ += ["AplicacaoInstrumento"]

# Clinica - Faturamento/convenios
from app.models.clinica_faturamento import (
    Convenio, Faturamento, TipoConvenio, StatusFaturamento, PrecoEspecialidade,
)
__all__ += ["Convenio", "Faturamento", "TipoConvenio", "StatusFaturamento", "PrecoEspecialidade"]

from app.models.clinica_anamnese import Anamnese
__all__ += ["Anamnese"]

from app.models.clinica_anexo import AnexoProntuario
__all__ += ["AnexoProntuario"]

from app.models.clinica_repasse import Repasse
__all__ += ["Repasse"]

from app.models.sintese_jornada import SinteseJornada  # Sintese da Jornada Terapeutica
__all__ += ["SinteseJornada"]

from app.models.clinica_supervisao import FidelidadeAplicacao, IOARegistro
__all__ += ["FidelidadeAplicacao", "IOARegistro"]
