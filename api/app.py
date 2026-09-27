"""FastAPI service over the orchestrator.

    uv run python -m api                     # binds 127.0.0.1:8000 by default
    uv run python -m api.export_openapi      # rewrite docs/schemas/openapi.json

Auth: POST /auth/challenge with a document number starts a login; the one-time code goes to the mock channel
(in demo mode, readable at GET /demo/outbox/{challenge_id}); POST /auth/verify returns a session token for
'Authorization: Bearer'. Conversation: POST /conversations/turn; when a turn carries `recognition`, POST
/conversations/{id}/recognize with the customer's answer; when it carries `confirmation`, POST
/conversations/{id}/confirm with the confirmation id. The human-agent console reads /console/* with 'X-Console-Key';
every console route is a read.

Rate limits key on the client address: the socket peer, or the address the frontend proxy names in
X-Cautela-Client when the request also carries CAUTELA_PROXY_KEY in X-Cautela-Proxy-Key. Without that key the header
is ignored, so no caller picks its own bucket.

Errors always have the shape {"error": {"code", "message", "trace_id", "fields"}} and never include stack traces,
SQL, file paths or the request body.
"""

# No `from __future__ import annotations` here: FastAPI resolves the local Depends aliases at runtime.
import ipaddress
import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api import views
from api.models import (CaseStatusResponse, ChallengeRequest, ChallengeResponse, ConfirmRequest, ConversationAudit,
                        ConversationSummary, ConversationView, DemoIdentity, ErrorResponse, HandoffQueueItem, HealthResponse,
                        OutboxResponse, RecognizeRequest, SessionResponse, TraceView, TranslateRequest,
                        TranslationResponse, TurnRequest, TurnResponse, VerifyRequest)
from api.ratelimit import RateLimiter
from api.runtime import Runtime
from api.settings import ApiSettings

log = logging.getLogger("cautela.api")
ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 500)}
AUTH_CODES = {"session_invalid", "session_expired", "session_revoked"}
MESSAGES = {"session_invalid": "Session is not valid.", "session_expired": "Session expired; log in again.",
            "session_revoked": "Session was closed.", "otp_invalid": "The code is not valid.",
            "otp_expired": "The code expired; start again.", "otp_locked": "Too many attempts; start again.",
            "conversation_not_found": "Conversation not found.", "no_pending_confirmation":
            "There is no pending confirmation with that id.", "no_pending_recognition":
            "There is no pending recognition question with that id.", "not_found": "Not found.",
            "rate_limited": "Too many requests; try again later.", "console_forbidden": "Console key required.",
            "demo_disabled": "Not available.", "validation_error": "Invalid request.",
            "translation_unavailable": "Translation is not available right now."}


PROXY_KEY_HEADER = "x-cautela-proxy-key"
PROXY_CLIENT_HEADER = "x-cautela-client"


def client_address(request: Request, proxy_key: str | None) -> str:
    """The address rate limits key on. The proxy's claim about the visitor counts only with the proxy's secret."""
    peer = request.client.host if request.client else "unknown"
    claimed = request.headers.get(PROXY_CLIENT_HEADER)
    offered = request.headers.get(PROXY_KEY_HEADER)
    if not proxy_key or not claimed or not offered or not secrets.compare_digest(proxy_key, offered):
        return peer
    try:
        return str(ipaddress.ip_address(claimed.strip()))
    except ValueError:
        return peer


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, trace_id: str | None = None) -> None:
        super().__init__(status, MESSAGES.get(code, code))
        self.code, self.trace_id = code, trace_id


def _error(status: int, code: str, message: str, trace_id: str | None = None,
           fields: list[str] | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "trace_id": trace_id, "fields": fields or []}}
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def create_app(runtime: Runtime | None = None, settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else ApiSettings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "runtime", None) is None:
            app.state.runtime = Runtime(settings)
        yield
        app.state.runtime.close()

    app = FastAPI(title="Cautela API", version="0.1.0", lifespan=lifespan,
                  description="Transaction-dispute intake: session gate, conversation, confirmation, handoff queue "
                              "and audit trace. Synthetic data only.")
    app.state.runtime = runtime
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Console-Key"])
    limits = {"auth": RateLimiter(*settings.auth_rate), "turn": RateLimiter(*settings.turn_rate),
              "console": RateLimiter(*settings.console_rate)}
    _handlers(app)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
        return response

    def rt(request: Request) -> Runtime:
        return request.app.state.runtime

    def limited(group: str) -> Callable[[Request], None]:
        def check(request: Request) -> None:
            host = client_address(request, rt(request).settings.proxy_key)
            if not limits[group].allow(f"{group}:{host}"):
                raise ApiError(429, "rate_limited")
        return check

    bearer = HTTPBearer(auto_error=False)

    def token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
        if credentials is None or not credentials.credentials:
            raise ApiError(401, "session_invalid")
        return credentials.credentials

    def console(request: Request, x_console_key: Annotated[str | None, Header()] = None) -> None:
        key = rt(request).console_key
        if not key or not x_console_key or not secrets.compare_digest(key, x_console_key):
            raise ApiError(403, "console_forbidden")

    def demo_only(request: Request) -> None:
        if not rt(request).settings.demo_mode:
            raise ApiError(404, "demo_disabled")

    Auth = Annotated[str, Depends(token)]

    # ---- health and demo ---------------------------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health(request: Request) -> Any:
        r = rt(request)
        return {"status": "ok", "llm_provider": r.llm_choice.provider, "llm_model": r.llm_choice.model,
                "disposition_model": r.orchestrator.disposition.name, "demo_mode": r.settings.demo_mode,
                "service_clock": r.clock()}

    @app.get("/demo/identities", response_model=list[DemoIdentity], tags=["demo"],
             dependencies=[Depends(demo_only)], responses=ERRORS)
    def identities(request: Request) -> Any:
        return rt(request).identities

    @app.get("/demo/outbox/{challenge_id}", response_model=OutboxResponse, tags=["demo"], responses=ERRORS,
             dependencies=[Depends(demo_only), Depends(limited("auth"))])
    def outbox(challenge_id: str, request: Request) -> Any:
        with rt(request).lock:
            code = rt(request).demo_code(challenge_id)
        if code is None:
            raise ApiError(404, "not_found")
        return {"code": code}

    # ---- auth -------------------------------------------------------------------------------------------------
    @app.post("/auth/challenge", response_model=ChallengeResponse, tags=["auth"], responses=ERRORS,
              dependencies=[Depends(limited("auth"))])
    def challenge(body: ChallengeRequest, request: Request) -> Any:
        with rt(request).lock:
            c = rt(request).start_login(body.document_number)
        return {"challenge_id": c.challenge_id, "channel_hint": c.channel_hint, "expires_at": c.expires_at}

    @app.post("/auth/verify", response_model=SessionResponse, tags=["auth"], responses=ERRORS,
              dependencies=[Depends(limited("auth"))])
    def verify(body: VerifyRequest, request: Request) -> Any:
        return views.verify(rt(request), body, ApiError)

    @app.post("/auth/logout", status_code=204, tags=["auth"], responses=ERRORS)
    def logout(request: Request, session: Auth) -> Response:
        views.logout(rt(request), session, ApiError)
        return Response(status_code=204)

    # ---- conversation ------------------------------------------------------------------------------------------
    @app.post("/conversations/turn", response_model=TurnResponse, tags=["conversation"], responses=ERRORS,
              dependencies=[Depends(limited("turn"))])
    def turn(body: TurnRequest, request: Request, session: Auth) -> Any:
        r = rt(request)
        with r.lock:
            result = r.orchestrator.turn(session, body.message, body.conversation_id, body.language)
        return views.turn_response(result, ApiError)

    @app.post("/conversations/{conversation_id}/confirm", response_model=TurnResponse, tags=["conversation"],
              responses=ERRORS, dependencies=[Depends(limited("turn"))])
    def confirm(conversation_id: str, body: ConfirmRequest, request: Request, session: Auth) -> Any:
        r = rt(request)
        with r.lock:
            result = r.orchestrator.confirm(session, conversation_id, body.confirmation_id, body.accept)
        return views.turn_response(result, ApiError)

    @app.post("/conversations/{conversation_id}/recognize", response_model=TurnResponse, tags=["conversation"],
              responses=ERRORS, dependencies=[Depends(limited("turn"))])
    def recognize(conversation_id: str, body: RecognizeRequest, request: Request, session: Auth) -> Any:
        r = rt(request)
        with r.lock:
            result = r.orchestrator.recognize(session, conversation_id, body.recognition_id, body.recognized)
        return views.turn_response(result, ApiError)

    @app.get("/conversations/{conversation_id}", response_model=ConversationView, tags=["conversation"],
             responses=ERRORS, dependencies=[Depends(limited("turn"))])
    def conversation(conversation_id: str, request: Request, session: Auth) -> Any:
        return views.conversation(rt(request), session, conversation_id, ApiError)

    @app.post("/conversations/{conversation_id}/translate", response_model=TranslationResponse,
              tags=["conversation"], responses={**ERRORS, 503: {"model": ErrorResponse}},
              dependencies=[Depends(limited("turn"))])
    def translate(conversation_id: str, body: TranslateRequest, request: Request, session: Auth) -> Any:
        """English machine translation of one message of this conversation, for reviewers who read English."""
        return views.translate(rt(request), session, conversation_id, body, ApiError)

    @app.get("/cases/{case_id}", response_model=CaseStatusResponse, tags=["conversation"], responses=ERRORS,
             dependencies=[Depends(limited("turn"))])
    def case_status(case_id: str, request: Request, session: Auth) -> Any:
        return views.case_status(rt(request), session, case_id, ApiError)

    # ---- human-agent console (read-only) --------------------------------------------------------------------------
    Console = [Depends(limited("console")), Depends(console)]

    @app.get("/console/handoffs", response_model=list[HandoffQueueItem], tags=["console"], responses=ERRORS,
             dependencies=Console)
    def handoffs(request: Request) -> Any:
        with rt(request).lock:
            return [views.queue_item(i) for i in reversed(rt(request).queue)]

    @app.get("/console/handoffs/{handoff_id}", response_model=HandoffQueueItem, tags=["console"],
             responses=ERRORS, dependencies=Console)
    def handoff(handoff_id: str, request: Request) -> Any:
        item = rt(request).handoff(handoff_id)
        if item is None:
            raise ApiError(404, "not_found")
        return views.queue_item(item)

    @app.get("/console/conversations", response_model=list[ConversationSummary], tags=["console"],
             responses=ERRORS, dependencies=Console)
    def conversation_index(request: Request) -> Any:
        """Every conversation of this process, newest first: resolved, handed off or still open."""
        return views.conversations(rt(request))

    @app.get("/console/conversations/{conversation_id}/audit", response_model=ConversationAudit,
             tags=["console"], responses=ERRORS, dependencies=Console)
    def conversation_audit(conversation_id: str, request: Request) -> Any:
        return views.conversation_audit(rt(request), conversation_id, ApiError)

    @app.get("/console/traces/{trace_id}", response_model=TraceView, tags=["console"], responses=ERRORS,
             dependencies=Console)
    def trace(trace_id: str, request: Request) -> Any:
        return views.trace(rt(request), trace_id, ApiError)

    return app


def _handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, str(exc.detail), exc.trace_id)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, MESSAGES.get(code, "Request not allowed."))

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        return _error(422, "validation_error", MESSAGES["validation_error"], fields=fields)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error: %s", type(exc).__name__, exc_info=exc)
        return _error(500, "internal_error", "Internal error.")
