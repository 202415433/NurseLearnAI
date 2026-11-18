# ADR-0004 — Relecture à quatre yeux et attestations internes verrouillées

**Statut** : proposé · 2026-11-18

**Décision** — (1) Un contenu (leçon, question, simulation) ne peut être `approved`/`published` sans relecteur horodaté, distinct de l'auteur. (2) Une version publiée exige une date de prochaine révision. (3) `certificates.kind` n'accepte que `internal_attestation` et la mention de non-équivalence est obligatoire. (4) Le niveau de compétence `verified_in_practice` exige un vérificateur humain distinct de l'apprenant.

**Conséquences** — Aucune publication possible sans comité de relecture : c'est voulu. Les comptes de relecteurs sont désactivés, jamais supprimés (`RESTRICT`).
