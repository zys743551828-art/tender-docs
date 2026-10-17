-- Fresh initialization: accepted conversion order is measured at the actual INSERT.
BEGIN;
ALTER TABLE design.word_render_runs ALTER COLUMN created_at SET DEFAULT clock_timestamp();
COMMIT;
