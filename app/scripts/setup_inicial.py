# ============================================
# SCRIPT DE SETUP INICIAL - AdaptAI
# ============================================
"""
Este script configura o sistema inicial:
1. Cria tabelas no banco de dados
2. Cria Super Admin do sistema
3. Cria escola de demonstração
4. Cria usuários (admin, coordenador, professores)
5. Associa alunos existentes aos professores corretos

EXECUTAR: python -m app.scripts.setup_inicial
"""

from app.core.config import settings
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models.user import User, UserRole
from app.models.student import Student
from app.models.escola import Escola, ConfiguracaoEscola
from app.core.security import get_password_hash
from datetime import datetime, timezone

        

def get_or_create_user(db: Session, email: str, name: str, password: str, role: UserRole, escola_id: int = None) -> User:
    """
    Obtém um usuário existente ou cria um novo.
    Se o usuário existir, retorna ele sem modificar.
    """
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        print(f"   ✅ Usuário já existe: {email}")
        return existing
    
    user = User(
        name=name,
        email=email,
        hashed_password=get_password_hash(password),
        role=role,
        escola_id=escola_id,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"   ✅ Criado: {email}")
    return user


def criar_super_admin(db: Session) -> User:
    """Cria o Super Admin do sistema"""
    print("\n🔐 Verificando Super Admin...")
    return get_or_create_user(
        db=db,
        email="admin@adaptai.com.br",
        name="Administrador AdaptAI",
        password="Admin@2024",
        role=UserRole.SUPER_ADMIN,
        escola_id=None
    )


def criar_escola_demo(db: Session) -> Escola:
    """Cria escola de demonstração"""
    print("\n🏫 Verificando Escola de Demonstração...")
    
    existing = db.query(Escola).filter(Escola.email == "contato@escolainclusiva.com.br").first()
    if existing:
        print("   ✅ Escola já existe: Escola Inclusiva Modelo")
        return existing
    
    escola = Escola(
        nome="Escola Inclusiva Modelo",
        cnpj="12.345.678/0001-90",
        email="contato@escolainclusiva.com.br",
        telefone="(91) 3333-4444",
        logradouro="Av. Educação, 1000",       # CORRIGIDO
        cidade="Belém",
        estado="PA",
        cep="66000-000",
        tipo="ESCOLA",                         # CORRIGIDO
        segmento="Educação Especial",          # CORRIGIDO
        site="https://escolainclusiva.com.br", # CORRIGIDO
        ativa=True                             # CORRIGIDO
    )
    db.add(escola)
    db.commit()
    db.refresh(escola)
    print(f"   ✅ Criada: {escola.nome}")
    
    # Criar configuração da escola
    config = ConfiguracaoEscola(
        escola_id=escola.id,
        modelo_ia_preferido="claude-3-5-sonnet-20241022", # CORRIGIDO
        pei_automatico_ativo=True,                        # CORRIGIDO
        materiais_adaptativos_ativo=True                  # CORRIGIDO
    )
    db.add(config)
    db.commit()
    print("   ✅ Configuração da escola criada")
    
    return escola


def criar_usuarios_escola(db: Session, escola: Escola) -> dict:
    """Cria usuários da escola (admin, coordenador, professores)"""
    print("\n👥 Verificando Usuários da Escola...")
    
    usuarios = {}
    
    # Admin da escola
    usuarios['admin'] = get_or_create_user(
        db=db,
        email="admin@escolainclusiva.com.br",
        name="Admin Escola Inclusiva",
        password="Admin@2024",
        role=UserRole.ADMIN,
        escola_id=escola.id
    )
    
    # Coordenador
    usuarios['coordenador'] = get_or_create_user(
        db=db,
        email="coordenador@escolainclusiva.com.br",
        name="Coordenador Pedagógico",
        password="Coord@2024",
        role=UserRole.COORDINATOR,
        escola_id=escola.id
    )
    
    # Professor Márcio Góes
    usuarios['prof_marcio'] = get_or_create_user(
        db=db,
        email="marcio.goes@adaptai.com.br",
        name="Márcio Góes do Nascimento",
        password="Prof@2024",
        role=UserRole.TEACHER,
        escola_id=escola.id
    )
    
    # Professor Fábio Azevedo
    usuarios['prof_fabio'] = get_or_create_user(
        db=db,
        email="fabio.azevedo@adaptai.com.br",
        name="Fábio Roberto Albuquerque Azevedo",
        password="Prof@2024",
        role=UserRole.TEACHER,
        escola_id=escola.id
    )
    
    # Professor Ad-Hoc (sem escola)
    usuarios['prof_adhoc'] = get_or_create_user(
        db=db,
        email="professor.adhoc@adaptai.com.br",
        name="Professor Independente",
        password="Prof@2024",
        role=UserRole.TEACHER,
        escola_id=None  # Sem escola
    )
    
    return usuarios


def associar_alunos(db: Session, usuarios: dict, escola: Escola):
    """Associa alunos existentes aos professores corretos"""
    print("\n🎓 Verificando Associação de Alunos...")
    
    # Mapeamento de alunos para professores
    # Baseado nos nomes reais dos alunos no sistema
    mapeamento = {
        'prof_marcio': [
            'MARCIO GOES DO NASCIMENTO JUNIOR',
            'CASSIO ANTONIO FERREIRA BASTOS GOES DO NASCIMENTO',
            'Márcio Jr',
            'Cássio'
        ],
        'prof_fabio': [
            'ENRICO MELO MOTA AZEVEDO',
            'Enrico'
        ]
    }
    
    for prof_key, nomes_alunos in mapeamento.items():
        professor = usuarios.get(prof_key)
        if not professor:
            continue
            
        for nome in nomes_alunos:
            # Buscar aluno por nome (case insensitive)
            aluno = db.query(Student).filter(
                Student.name.ilike(f"%{nome}%")
            ).first()
            
            if aluno:
                # Atualizar associação
                aluno.created_by_user_id = professor.id
                aluno.escola_id = escola.id
                db.commit()
                print(f"   ✅ {aluno.name} → Prof. {professor.name}")


def criar_alunos_demo(db: Session, usuarios: dict, escola: Escola):
    """Cria alunos de demonstração se não existirem"""
    print("\n👨‍🎓 Verificando Alunos de Demonstração...")
    
    alunos_demo = [
        {
            'name': 'Márcio Góes do Nascimento Junior',
            'email': 'marcio.junior@aluno.adaptai.com.br',
            'grade_level': '5º ano',
            'professor': usuarios['prof_marcio']
        },
        {
            'name': 'Cássio Antônio Ferreira Bastos Góes do Nascimento',
            'email': 'cassio.goes@aluno.adaptai.com.br',
            'grade_level': '3º ano',
            'professor': usuarios['prof_marcio']
        },
        {
            'name': 'Enrico Melo Mota Azevedo',
            'email': 'enrico.azevedo@aluno.adaptai.com.br',
            'grade_level': '4º ano',
            'professor': usuarios['prof_fabio']
        }
    ]
    
    for aluno_data in alunos_demo:
        # Verificar se já existe
        existing = db.query(Student).filter(Student.email == aluno_data['email']).first()
        if existing:
            print(f"   ✅ Aluno já existe: {aluno_data['name']}")
            continue
        
        aluno = Student(
            name=aluno_data['name'],
            email=aluno_data['email'],
            hashed_password=get_password_hash("Aluno@2024"),
            grade_level=aluno_data['grade_level'],
            is_active=True,
            created_by_user_id=aluno_data['professor'].id,
            escola_id=escola.id
        )
        db.add(aluno)
        db.commit()
        print(f"   ✅ Criado: {aluno_data['name']}")


def main():
    """Executa o setup inicial"""
    print("=" * 60)
    print("🚀 SETUP INICIAL - AdaptAI")
    print("=" * 60)
    
    print(f"Esse script vai alterar o seguinte banco de dados -> {settings.db_url}")
    
    input("Verifique a URL acima. Pressione ENTER se for o banco local, ou CTRL+C para sair!");

    print("Criando tabelas no banco de dados...")
    Base.metadata.create_all(bind=engine)
    print("✅ Tabelas criadas ou já existentes.")

    db = SessionLocal()
    
    try:
        # 1. Super Admin
        super_admin = criar_super_admin(db)
        
        # 2. Escola de demonstração
        escola = criar_escola_demo(db)
        
        # 3. Usuários da escola
        usuarios = criar_usuarios_escola(db, escola)
        
        # 4. Associar alunos existentes
        associar_alunos(db, usuarios, escola)
        
        # 5. Criar alunos demo se não existirem
        criar_alunos_demo(db, usuarios, escola)
        
        print("\n" + "=" * 60)
        print("✅ SETUP CONCLUÍDO COM SUCESSO!")
        print("=" * 60)
        
        print("\n📋 CREDENCIAIS DE ACESSO:")
        print("-" * 60)
        print(f"🔐 Super Admin:   admin@adaptai.com.br / Admin@2024")
        print(f"🏛️  Admin Escola:  admin@escolainclusiva.com.br / Admin@2024")
        print(f"👔 Coordenador:   coordenador@escolainclusiva.com.br / Coord@2024")
        print(f"👨‍🏫 Prof. Márcio:  marcio.goes@adaptai.com.br / Prof@2024")
        print(f"👨‍🏫 Prof. Fábio:   fabio.azevedo@adaptai.com.br / Prof@2024")
        print(f"👨‍🏫 Prof. Ad-Hoc:  professor.adhoc@adaptai.com.br / Prof@2024")
        print("-" * 60)
        print(f"👨‍🎓 Alunos:        [email]@aluno.adaptai.com.br / Aluno@2024")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
