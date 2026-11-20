"""Envoi des courriels transactionnels (vérification, réinitialisation, invitations, avis de sécurité).

- « smtp » : Mailpit en local (aucun envoi réel, aucun service payant) ; un serveur SMTP réel
  ne sera utilisé qu'avec une configuration explicite (ADR-0008 : région du fournisseur à documenter).
- « memory » : courriels conservés en mémoire, pour les tests.
- « disabled » : rien n'est envoyé.
Un échec d'envoi est journalisé sans le contenu (qui peut porter un lien secret) et n'est jamais
renvoyé au client : la réponse reste identique, quel que soit l'état du compte.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Literal

from app.core.config import Settings

log = logging.getLogger("nurselearn.mail")

Template = Literal[
    "verify_email", "account_exists", "password_reset", "password_changed", "invitation", "new_login_locked"
]


@dataclass
class Mail:
    to: str
    subject: str
    body: str


_TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    "fr": {
        "verify_email": (
            "Confirmez votre adresse — NurseLearn AI",
            "Bonjour,\n\nPour activer votre compte NurseLearn AI, ouvrez ce lien puis confirmez :\n{link}\n\n"
            "Ce lien est valable {ttl} et ne peut servir qu'une fois.\n"
            "Si vous n'êtes pas à l'origine de cette inscription, ignorez ce message.",
        ),
        "account_exists": (
            "Tentative d'inscription — NurseLearn AI",
            "Bonjour,\n\nUne inscription a été tentée avec votre adresse, mais un compte existe déjà.\n"
            "Pour vous connecter : {link}\nMot de passe oublié ? Utilisez « Mot de passe oublié » sur la page de "
            "connexion.\nSi vous n'êtes pas à l'origine de cette demande, ignorez ce message.",
        ),
        "password_reset": (
            "Réinitialisation du mot de passe — NurseLearn AI",
            "Bonjour,\n\nPour choisir un nouveau mot de passe, ouvrez ce lien :\n{link}\n\n"
            "Ce lien est valable {ttl} et ne peut servir qu'une fois. Toutes vos sessions seront fermées.\n"
            "Si vous n'avez rien demandé, ignorez ce message : votre mot de passe reste inchangé.",
        ),
        "password_changed": (
            "Votre mot de passe a été modifié — NurseLearn AI",
            "Bonjour,\n\nLe mot de passe de votre compte vient d'être modifié et vos autres sessions ont été fermées.\n"
            "Si vous n'êtes pas à l'origine de ce changement, réinitialisez-le immédiatement : {link}",
        ),
        "invitation": (
            "Invitation à rejoindre NurseLearn AI",
            "Bonjour,\n\nVous êtes invité(e) à rejoindre NurseLearn AI avec le rôle « {role} »{org}.\n"
            "Pour accepter, connectez-vous (ou créez un compte avec cette adresse), puis ouvrez :\n{link}\n\n"
            "Cette invitation est valable {ttl} et ne peut servir qu'une fois.",
        ),
        "new_login_locked": (
            "Connexions bloquées temporairement — NurseLearn AI",
            "Bonjour,\n\nPlusieurs tentatives de connexion ont échoué sur votre compte ; les connexions sont "
            "bloquées pendant quelques minutes.\nSi ce n'était pas vous, nous vous conseillons de réinitialiser "
            "votre mot de passe : {link}",
        ),
    },
    "en": {
        "verify_email": (
            "Confirm your address — NurseLearn AI",
            "Hello,\n\nTo activate your NurseLearn AI account, open this link and confirm:\n{link}\n\n"
            "This link is valid for {ttl} and can only be used once.\n"
            "If you did not sign up, please ignore this message.",
        ),
        "account_exists": (
            "Sign-up attempt — NurseLearn AI",
            "Hello,\n\nSomeone tried to sign up with your address, but an account already exists.\n"
            "To sign in: {link}\nForgot your password? Use “Forgot password” on the sign-in page.\n"
            "If this was not you, please ignore this message.",
        ),
        "password_reset": (
            "Password reset — NurseLearn AI",
            "Hello,\n\nTo choose a new password, open this link:\n{link}\n\n"
            "This link is valid for {ttl} and can only be used once. All your sessions will be closed.\n"
            "If you did not ask for this, ignore this message: your password is unchanged.",
        ),
        "password_changed": (
            "Your password was changed — NurseLearn AI",
            "Hello,\n\nYour account password was just changed and your other sessions were closed.\n"
            "If this was not you, reset it immediately: {link}",
        ),
        "invitation": (
            "Invitation to join NurseLearn AI",
            "Hello,\n\nYou are invited to join NurseLearn AI with the “{role}” role{org}.\n"
            "To accept, sign in (or create an account with this address), then open:\n{link}\n\n"
            "This invitation is valid for {ttl} and can only be used once.",
        ),
        "new_login_locked": (
            "Sign-ins temporarily blocked — NurseLearn AI",
            "Hello,\n\nSeveral sign-in attempts failed on your account; sign-ins are blocked for a few minutes.\n"
            "If this was not you, we recommend resetting your password: {link}",
        ),
    },
}


def render(template: Template, locale: str, to: str, **values: str) -> Mail:
    subject, body = _TEXTS.get(locale, _TEXTS["fr"])[template]
    defaults = {"link": "", "ttl": "", "role": "", "org": ""}
    defaults.update(values)
    return Mail(to=to, subject=subject, body=body.format(**defaults))


@dataclass
class Mailer:
    settings: Settings
    outbox: list[Mail] = field(default_factory=list)  # backend « memory » (tests)

    async def send(self, mail: Mail) -> None:
        backend = self.settings.mail_backend
        if backend == "disabled":
            return
        if backend == "memory":
            self.outbox.append(mail)
            return
        try:
            await asyncio.to_thread(self._send_smtp, mail)
        except Exception:  # l'échec est journalisé sans le contenu (lien secret)
            log.warning("courriel_non_envoye", extra={"subject": mail.subject})

    def _send_smtp(self, mail: Mail) -> None:
        msg = EmailMessage()
        msg["From"] = self.settings.mail_from
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        msg.set_content(mail.body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as smtp:
            if self.settings.smtp_starttls:
                smtp.starttls()
            if self.settings.smtp_username and self.settings.smtp_password:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(msg)
