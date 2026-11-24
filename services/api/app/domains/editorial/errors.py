"""Traduction des erreurs PostgreSQL en problèmes `application/problem+json` stables.

Les règles de la migration 0006 portent un code `HINT = 'nl:<code>'`. Les règles de 0005 non
redéfinies (workflow des versions de leçon, journal) n'en ont pas : elles sont reconnues par une
liste FERMÉE de fragments de message (`LEGACY_MESSAGES`), dont la couverture est vérifiée par un test.
Le texte SQL n'est jamais renvoyé au client.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import DBAPIError

from app.core.errors import ProblemError

log = logging.getLogger("nurselearn.editorial")
BASE = "https://nurselearn.ai/problems/"

# Code stable → (statut HTTP, type de problème, message destiné à l'utilisateur).
CODES: dict[str, tuple[int, str, str]] = {
    "transition_forbidden": (409, "editorial-state-conflict", "Transition éditoriale non autorisée depuis cet état"),
    "not_draft": (409, "editorial-state-conflict", "Seul un brouillon peut être modifié ; créez une nouvelle version"),
    "immutable": (409, "editorial-state-conflict", "Élément figé : il ne peut plus être modifié"),
    "in_use": (409, "editorial-state-conflict", "Version utilisée par une édition publiée"),
    "history": (409, "editorial-state-conflict", "Élément conservé pour l'historique éditorial"),
    "legacy_structure": (409, "editorial-state-conflict", "Structure d'édition historique non modifiable"),
    "publish_via_release": (409, "editorial-state-conflict", "La publication passe uniquement par une édition"),
    "born_draft": (409, "editorial-state-conflict", "Un contenu naît en brouillon"),
    "version_sequence": (409, "conflict", "Numérotation des versions concurrente ; réessayez"),
    "forged_review": (422, "invalid-input", "Champs de relecture ou de publication non modifiables"),
    "self_approval": (403, "independence-required", "L'auteur ne peut pas relire son propre contenu"),
    "reviewer_not_independent": (
        403,
        "independence-required",
        "Le relecteur est l'auteur d'un élément inclus : relecture indépendante requise",
    ),
    "reviewer_required": (403, "forbidden", "Action réservée à un relecteur habilité"),
    "actor_missing": (403, "forbidden", "Action non autorisée"),
    "forbidden": (403, "forbidden", "Action non autorisée dans cette portée"),
    "comment_required": (422, "invalid-input", "Un motif est obligatoire"),
    "review_date_required": (422, "invalid-input", "Une date de prochaine révision future est obligatoire"),
    "missing_source": (
        422,
        "publication-requirements",
        "Programme médical : chaque leçon doit citer une source active et vérifiée",
    ),
    "source_unverified": (422, "publication-requirements", "Source non vérifiée"),
    "invalid_date": (422, "invalid-input", "Date invalide"),
    "unapproved_component": (422, "publication-requirements", "Un élément inclus n'est pas approuvé"),
    "unsubmitted_component": (422, "publication-requirements", "Un élément inclus n'a pas été soumis"),
    "tampered": (422, "publication-requirements", "Un élément a changé depuis son approbation"),
    "incoherent_reference": (422, "publication-requirements", "Référence incohérente entre versions"),
    "empty_release": (422, "publication-requirements", "L'édition ne contient aucune version de module"),
    "empty_module_version": (422, "publication-requirements", "La version de module ne contient aucune leçon"),
    "empty_content": (422, "publication-requirements", "La leçon n'a pas de contenu"),
    # 0007 (2.2.3-a/b) : médias protégés.
    "quota_exceeded": (413, "quota-exceeded", "Quota de stockage dépassé"),
    "quota_below_usage": (409, "editorial-state-conflict", "Quota inférieur à l'occupation actuelle"),
    "invalid_reservation": (409, "reservation-invalid", "Réservation de téléversement invalide ou expirée"),
    "born_pending": (422, "invalid-input", "Un média naît en attente de téléversement"),
    "legal_hold": (409, "editorial-state-conflict", "Média sous conservation légale"),
    "reported": (409, "editorial-state-conflict", "Média lié à un signalement de contenu"),
    "untraceable_verdict": (409, "editorial-state-conflict", "Verdict antivirus non traçable"),
    # 0007 (2.2.3-a) : une version de leçon ne quitte pas le brouillon avec un média non validé.
    "media_not_clean": (422, "publication-requirements", "Média non validé par l'antivirus rattaché à la version"),
    "missing_captions": (422, "publication-requirements", "Vidéo sans sous-titres validés (WCAG 1.2.2)"),
    # 0010 (2.2.3-e) : rattachement contrôlé et conservation légale.
    "not_found": (404, "not-found", "Élément introuvable"),
    "reason_required": (422, "invalid-input", "Justification obligatoire (5 à 1000 caractères)"),
    "no_change": (409, "editorial-state-conflict", "Aucun changement : état déjà en place"),
    "purged": (409, "editorial-state-conflict", "Média purgé"),
    "missing_author": (422, "publication-requirements", "Auteur non renseigné"),
    "missing_base_locale": (422, "publication-requirements", "Chaque leçon exige une version française"),
    "prerequisite_cycle": (422, "publication-requirements", "Prérequis circulaires"),
    "organization_scope": (422, "publication-requirements", "Module privé hors de l'établissement du programme"),
    "frozen_program": (409, "editorial-state-conflict", "Nature, établissement et métier du programme sont figés"),
    "inactive_enrollment": (409, "editorial-state-conflict", "Inscription inactive"),
}

# Règles de 0005 (et antérieures) sans HINT : fragment de message → code. Liste fermée.
LEGACY_MESSAGES: list[tuple[str, str]] = [
    ("sans acteur déclaré", "actor_missing"),
    ("Auto-approbation interdite", "self_approval"),
    ("Indépendance", "reviewer_not_independent"),
    ("ne détient pas content:", "forbidden"),
    ("n'est ni relecteur ni éditeur", "forbidden"),
    ("Transition interdite", "transition_forbidden"),
    ("modification directe interdite", "not_draft"),
    ("Une transition ne modifie pas", "immutable"),
    ("Identité d'une version de leçon non modifiable", "immutable"),
    ("Identité d'une édition non modifiable", "immutable"),
    ("Sources d'une version", "not_draft"),
    ("non supprimable", "history"),
    ("citée par une édition", "in_use"),
    ("naît en brouillon", "born_draft"),
    ("Numéro de version", "version_sequence"),
    ("Numéro d'édition", "version_sequence"),
    ("trace de relecture", "forged_review"),
    ("Champs de relecture", "forged_review"),
    ("Champs de publication", "forged_review"),
    ("Date de soumission non modifiable", "forged_review"),
    ("motif obligatoire", "comment_required"),
    ("prochaine révision", "review_date_required"),
    ("auteur non renseigné", "missing_author"),
    ("leçon sans contenu", "empty_content"),
    ("a changé depuis", "tampered"),
    ("modifiée depuis son approbation", "tampered"),
    ("non approuvée", "unapproved_component"),
    ("encore en", "unsubmitted_component"),
    ("aucune source active", "missing_source"),
    ("Édition incohérente", "incoherent_reference"),
    ("Édition vide", "empty_release"),
    ("Édition sans version", "empty_release"),
    ("Module privé", "organization_scope"),
    ("publication uniquement par une édition", "publish_via_release"),
    ("figés dès la première édition", "frozen_program"),
    ("non habilité", "forbidden"),
    ("alimenté uniquement par les transitions", "forbidden"),
    ("version remplacée est encore suivie", "in_use"),
]

SQLSTATE_FALLBACK = {
    "23503": (422, "invalid-reference", "Référence inexistante ou incohérente"),
    "23505": (409, "conflict", "Conflit : l'élément existe déjà"),
    "23514": (422, "invalid-input", "Données refusées par une règle d'intégrité"),
    "23502": (422, "invalid-input", "Champ obligatoire manquant"),
    "22001": (422, "invalid-input", "Valeur trop longue"),
    "22P02": (422, "invalid-input", "Valeur invalide"),
    "40001": (409, "conflict", "Opération concurrente ; réessayez"),
    "40P01": (409, "conflict", "Opération concurrente ; réessayez"),
}


def problem(code: str, extra: dict[str, Any] | None = None, detail: str | None = None) -> ProblemError:
    status, type_, message = CODES[code]
    return ProblemError(status, detail or message, type_=BASE + type_, extra={"code": code, **(extra or {})})


def _pg_details(exc: DBAPIError) -> tuple[str | None, str | None, str]:
    orig: Any = exc.orig
    cause: Any = getattr(orig, "__cause__", None) or orig
    sqlstate = getattr(cause, "sqlstate", None) or getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    hint = getattr(cause, "hint", None)
    message = getattr(cause, "message", None) or str(orig)
    return sqlstate, hint, message


def code_for(sqlstate: str | None, hint: str | None, message: str) -> str | None:
    if hint and hint.startswith("nl:"):
        return hint[3:]
    if sqlstate in ("23514", "42501", "P0001"):
        for fragment, code in LEGACY_MESSAGES:
            if fragment in message:
                return code
    if sqlstate == "42501":
        return "forbidden"
    return None


def translate(exc: DBAPIError) -> ProblemError:
    sqlstate, hint, message = _pg_details(exc)
    code = code_for(sqlstate, hint, message)
    if code and code in CODES:
        return problem(code)
    if sqlstate in SQLSTATE_FALLBACK:
        status, type_, text_ = SQLSTATE_FALLBACK[sqlstate]
        return ProblemError(status, text_, type_=BASE + type_, extra={"code": sqlstate})
    log.error("erreur_base_non_repertoriee", extra={"sqlstate": sqlstate})
    return ProblemError(500, "Erreur interne", type_=BASE + "internal")
