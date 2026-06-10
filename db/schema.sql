-- =============================================================================
-- schema.sql — Estrutura da base de dados no Supabase
--
-- COMO USAR:
--   1. No dashboard do Supabase, vai a: SQL Editor → New query
--   2. Cola este ficheiro completo e clica em "Run"
--   3. Deves ver "Success. No rows returned" para cada statement
--
-- Este ficheiro é idempotente: podes correr várias vezes sem erros
-- (todos os CREATE usam IF NOT EXISTS / OR REPLACE).
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Tabela principal: feiras
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feiras (
    -- Identificador estável gerado pelo scraper (hash MD5 de nome+data+fonte).
    -- Porquê TEXT e não UUID? Porque este ID é calculado pelo scraper, não
    -- gerado pela BD. Isso permite fazer upserts idempotentes: o mesmo evento
    -- scrapeado duas vezes tem sempre o mesmo ID → INSERT ON CONFLICT UPDATE.
    id              TEXT PRIMARY KEY,

    -- Dados do evento
    nome            TEXT        NOT NULL,
    data_inicio     DATE,
    data_fim        DATE,

    -- Localização textual
    localidade      TEXT,
    municipio       TEXT,
    distrito        TEXT,

    -- Coordenadas GPS (para o mapa)
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,

    -- Conteúdo
    descricao       TEXT,
    imagem_url      TEXT,

    -- Origem dos dados
    fonte_url       TEXT        NOT NULL,
    fonte           TEXT        NOT NULL    CHECK (fonte IN ('feirasmedievais', 'aondevamos')),

    -- Auditoria
    criado_em       TIMESTAMPTZ NOT NULL    DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL    DEFAULT NOW()
);

COMMENT ON TABLE feiras IS 'Feiras medievais e recriações históricas em Portugal, scrapeadas de feirasmedievais.pt e aondevamos.pt.';
COMMENT ON COLUMN feiras.id IS 'Hash MD5(nome|data_inicio|fonte) — estável entre runs do scraper.';
COMMENT ON COLUMN feiras.fonte IS 'Site de origem: feirasmedievais | aondevamos';


-- ---------------------------------------------------------------------------
-- Índices
-- Os índices aceleram as queries mais comuns da app:
-- - listar por data (ordenar cronologicamente)
-- - filtrar por distrito ou município (pesquisa regional)
-- - filtrar por fonte (debug / admin)
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_feiras_data_inicio  ON feiras (data_inicio);
CREATE INDEX IF NOT EXISTS idx_feiras_data_fim      ON feiras (data_fim);
CREATE INDEX IF NOT EXISTS idx_feiras_distrito      ON feiras (distrito);
CREATE INDEX IF NOT EXISTS idx_feiras_municipio     ON feiras (municipio);
CREATE INDEX IF NOT EXISTS idx_feiras_fonte         ON feiras (fonte);

-- Índice composto para a query mais frequente: "feiras futuras, ordenadas por data"
CREATE INDEX IF NOT EXISTS idx_feiras_data_inicio_nome
    ON feiras (data_inicio, nome);


-- ---------------------------------------------------------------------------
-- Função para atualizar automaticamente o campo atualizado_em
--
-- Porquê um trigger e não atualizar no script Python?
-- Porque garante consistência mesmo que alguém edite a tabela manualmente
-- via dashboard do Supabase. A BD mantém-se sempre coerente.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_atualizado_em()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.atualizado_em = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_feiras_atualizado_em ON feiras;
CREATE TRIGGER trg_feiras_atualizado_em
    BEFORE UPDATE ON feiras
    FOR EACH ROW
    EXECUTE FUNCTION set_atualizado_em();


-- ---------------------------------------------------------------------------
-- Row Level Security (RLS)
--
-- RLS controla quem pode ler e escrever na tabela.
-- Para esta app pessoal, a configuração é simples:
--   - Leitura pública (anon key): qualquer pessoa pode ler → a app frontend usa isto
--   - Escrita apenas com service_role key → só o scraper/import script escreve
--
-- A service_role key bypassa o RLS por definição no Supabase,
-- por isso não precisamos de policy de escrita explícita.
-- ---------------------------------------------------------------------------

ALTER TABLE feiras ENABLE ROW LEVEL SECURITY;

-- Apagar policy anterior se existir (para este script ser re-runnable)
DROP POLICY IF EXISTS "leitura_publica" ON feiras;

CREATE POLICY "leitura_publica" ON feiras
    FOR SELECT
    USING (true);  -- qualquer pedido pode ler, sem autenticação


-- ---------------------------------------------------------------------------
-- View útil: feiras futuras ordenadas por data
--
-- Esta view pode ser usada diretamente pela app como endpoint:
--   GET /rest/v1/feiras_futuras
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW feiras_futuras AS
SELECT *
FROM   feiras
WHERE  data_inicio >= CURRENT_DATE
ORDER  BY data_inicio, nome;

COMMENT ON VIEW feiras_futuras IS 'Feiras com data de início a partir de hoje, ordenadas cronologicamente.';
