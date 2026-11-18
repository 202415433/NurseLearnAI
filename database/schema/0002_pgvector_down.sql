DROP INDEX IF EXISTS ix_ai_chunks_embedding_hnsw;
ALTER TABLE ai_knowledge_chunks DROP COLUMN IF EXISTS embedding, DROP COLUMN IF EXISTS embedding_model;
