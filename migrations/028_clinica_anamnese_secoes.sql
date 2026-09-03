-- ============================================================================
--  028 — anamnese: secoes estruturadas adicionais
--
--  Acrescenta blocos que faltavam na ficha de anamnese (uma por paciente):
--  sono, alimentacao (seletividade), perfil_sensorial (hiper/hipo), autonomia
--  em AVDs, uso de telas, contexto social/familiar e objetivos/expectativas da
--  familia. Todos TEXT NULL — aditivo, nenhum dado existente muda.
--
--  Espelha app/models/clinica_anamnese.py::Anamnese e o _CAMPOS da rota
--  app/api/routes/clinica_anamnese.py.
--
--  ATENCAO: MySQL 8 NAO suporta ADD COLUMN IF NOT EXISTS — rode esta migration
--  UMA vez. Se reexecutar, o banco acusa coluna duplicada (seguro ignorar).
--
--  Desfazer:
--    ALTER TABLE anamneses
--      DROP COLUMN sono, DROP COLUMN alimentacao, DROP COLUMN perfil_sensorial,
--      DROP COLUMN autonomia_avds, DROP COLUMN uso_telas,
--      DROP COLUMN contexto_social_familiar, DROP COLUMN objetivos_familia;
-- ============================================================================

ALTER TABLE anamneses
  ADD COLUMN sono                     TEXT NULL,
  ADD COLUMN alimentacao              TEXT NULL,
  ADD COLUMN perfil_sensorial         TEXT NULL,
  ADD COLUMN autonomia_avds           TEXT NULL,
  ADD COLUMN uso_telas                TEXT NULL,
  ADD COLUMN contexto_social_familiar TEXT NULL,
  ADD COLUMN objetivos_familia        TEXT NULL;

-- conferencia — esperado: as 7 colunas novas presentes
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
  FROM INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_NAME = 'anamneses'
 ORDER BY ORDINAL_POSITION;
