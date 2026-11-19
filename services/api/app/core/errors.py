"""Erreurs au format RFC 9457 (application/problem+json).

Aucune trace technique n'est renvoyée au client ; le détail est journalisé
avec l'identifiant de requête.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("nurselearn.errors")
PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
        *,
        title: str | None = None,
        type_: str = "about:blank",
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.title = title or HTTPStatus(status).phrase
        self.type = type_
        self.extra = extra or {}


def not_found(what: str) -> ProblemError:
    return ProblemError(404, f"{what} introuvable", type_="https://nurselearn.ai/problems/not-found")


def problem_response(
    request: Request,
    status: int,
    title: str,
    detail: str | None,
    type_: str = "about:blank",
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status, "instance": request.url.path}
    if detail:
        body["detail"] = detail
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    body.update(extra or {})
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(request, exc.status, exc.title, exc.detail, exc.type, exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        response = problem_response(request, exc.status_code, HTTPStatus(exc.status_code).phrase, detail)
        # En-têtes utiles au client (ex. « Allow » pour un 405) ; jamais de détail interne.
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        return problem_response(
            request,
            422,
            "Unprocessable Content",
            "Paramètres invalides",
            "https://nurselearn.ai/problems/validation",
            {"errors": errors},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("erreur_non_geree", extra={"request_id": getattr(request.state, "request_id", None)})
        return problem_response(request, 500, "Internal Server Error", "Erreur interne")
