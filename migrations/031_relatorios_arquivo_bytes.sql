-- ============================================================================
--  031 — relatorios.arquivo_bytes: laudo para de depender de disco efemero
--
--  Contexto: o laudo (PDF/imagem) enviado pelo professor e salvo em
--  backend/storage/relatorios/{nome}.pdf, com o banco guardando so o nome do
--  arquivo em arquivo_path (app/api/routes/relatorios.py:605-608,760-765). O
--  servico web do Railway roda em disco EFEMERO, sem volume persistente
--  montado. A cada redeploy o arquivo some enquanto a linha em `relatorios`
--  continua com arquivo_path preenchido, e GET /relatorios/{id}/arquivo passa
--  a devolver 404 "Arquivo nao encontrado no disco" (relatorios.py:864-865)
--  pra laudo que o sistema jura estar disponivel.
--
--  E O MESMO defeito que a migration 011 corrigiu em `ilustracoes`
--  (imagem_bytes, 2026-08-17) e a 012 corrigiu em `materiais`
--  (conteudo_gerado) — nunca tinha chegado em `relatorios`.
--
--  Correcao: o conteudo passa a morar NA PROPRIA LINHA (arquivo_bytes,
--  MEDIUMBLOB - ate 16MB). O upload ja rejeita arquivo acima de 10MB
--  (relatorios.py:568,742) - sobra margem folgada. arquivo_path FICA
--  (expand/migrate/contract, ver docs/ARQUITETURA-CONTEUDOS.md secao 4) e
--  continua sendo escrito como cache local; a leitura so cai nele quando
--  arquivo_bytes estiver vazio, que e o caso das linhas antigas e das que
--  ja perderam o arquivo no disco (essas ficam irrecuperaveis - o 404 que ja
--  acontece hoje continua acontecendo pra elas).
--
--  So ADD COLUMN aqui. A troca de leitura/escrita em
--  app/models/relatorio.py e app/api/routes/relatorios.py (upload, download,
--  exclusao) e passo separado, ainda nao feito - sem isso a coluna existe no
--  banco mas nao e usada. Quando for cablear, seguir o padrao de
--  `Material.conteudo_gerado` (deferred, fora do SELECT de listagem, mesmo
--  cuidado do 1038 Out of sort memory que motivou a migration 013).
--
--  ATENCAO A ORDEM: rodar esta migration ANTES do deploy do codigo que
--  passar a escrever em arquivo_bytes.
--
--  Risco de aplicar: baixo. So ADD COLUMN, nenhum SELECT/INSERT existente
--  muda de forma ou de comportamento.
--
--  Desfazer:
--    ALTER TABLE relatorios DROP COLUMN arquivo_bytes;
-- ============================================================================

-- Sem "IF NOT EXISTS": clausulado so existe a partir do MySQL 8.0.29. Rodar
-- 1x; rodar de novo por engano da erro de coluna duplicada (seguro, so nao
-- reaplica).
ALTER TABLE relatorios
  ADD COLUMN arquivo_bytes MEDIUMBLOB NULL AFTER arquivo_path;

-- conferencia — esperado: arquivo_bytes listada, tipo mediumblob, nullable
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
  FROM INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_NAME = 'relatorios'
 ORDER BY ORDINAL_POSITION;
