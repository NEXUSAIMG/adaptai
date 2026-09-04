-- ============================================================================
--  029 — sintese da jornada terapeutica (perfil vivo por aluno)
--
--  Persiste a leitura consolidada dos relatorios do aluno numa sintese
--  reutilizavel, que passa a alimentar os geradores de tarefa (materiais,
--  provas, atividades do PEI, programa de casa) como parametro.
--
--  Espelha app/models/sintese_jornada.py::SinteseJornada. Uma linha por aluno
--  (student_id UNIQUE). `fonte_hash` detecta quando os relatorios mudaram
--  (sintese desatualizada -> regenera). Tabela NOVA; nada existente muda.
--
--  Desfazer: DROP TABLE IF EXISTS sinteses_jornada;
-- ============================================================================

CREATE TABLE IF NOT EXISTS sinteses_jornada (
  id            INT          NOT NULL AUTO_INCREMENT,
  student_id    INT          NOT NULL,
  resumo        TEXT                          DEFAULT NULL,
  dados_json    JSON                          DEFAULT NULL,
  fonte_hash    VARCHAR(64)                   DEFAULT NULL,
  n_relatorios  INT                           DEFAULT 0,
  gerado_por_id INT                           DEFAULT NULL,
  criado_em     DATETIME                      DEFAULT NULL,
  atualizado_em DATETIME                      DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sinteses_jornada_student (student_id),
  KEY ix_sinteses_jornada_id (id),
  CONSTRAINT sinteses_jornada_ibfk_1
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
  CONSTRAINT sinteses_jornada_ibfk_2
    FOREIGN KEY (gerado_por_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
  FROM INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_NAME = 'sinteses_jornada'
 ORDER BY ORDINAL_POSITION;
