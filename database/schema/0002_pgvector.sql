-- =============================================================================
-- Migration 0002 — Index vectoriel du tuteur IA (pgvector)
-- Prérequis : image PostgreSQL avec l'extension "vector" (ex. pgvector/pgvector:pg16,
-- utilisée par infra/docker-compose.yml).
-- La dimension (1536) correspond au modèle d'embedding par défaut ; elle est
-- configurable via EMBEDDING_DIM et doit alors être changée ici AVANT ingestion.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE ai_knowledge_chunks
  ADD COLUMN embedding vector(1536),
  ADD COLUMN embedding_model text;

-- Recherche par similarité cosinus (HNSW : bon compromis rappel / latence).
CREATE INDEX ix_ai_chunks_embedding_hnsw
  ON ai_knowledge_chunks USING hnsw (embedding vector_cosine_ops);
