# ADR-0005 — Tuteur IA limité aux sources approuvées

**Statut** : proposé · 2026-11-18

**Décision** — Recherche hybride (pgvector + plein texte) restreinte aux `ai_knowledge_sources` au statut `approved` ; citations obligatoires et vérifiées après génération ; refus des demandes de conseil clinique personnel ; prompts versionnés en base ; fournisseur LLM derrière une interface et désactivé par défaut ; chaque message journalisé avec modèle, version de prompt et jetons.

**Conséquences** — Réponses moins « créatives » mais traçables ; qualité mesurée par un jeu d'évaluation rejoué en CI. Le choix du fournisseur reste ouvert (dépend de l'hébergement des données).
