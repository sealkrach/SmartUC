"""MCP (Model Context Protocol) interface to UseCase Hub.

Mounted at ``/mcp`` (Streamable HTTP). Any MCP client — Claude Desktop, Claude Code, Cursor, a LangGraph
agent — can read the registry, create / update / move use cases, drive a conversational intake, review
AI-suggested relations and trigger portfolio analysis.

Auth: ``Authorization: Bearer <personal API key>`` (create one in the UI under "MCP & API", or
``POST /api/users/me/api-keys``). A session JWT is accepted too. Role checks are the same as in the REST API.

Tool implementations live in :mod:`app.mcp_tools` as plain async functions taking the acting user, so they
are testable without an MCP client; this module only wires them to the protocol.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import mcp_tools as T
from .auth import check_ai_quota, resolve_token
from .db import SessionLocal
from .models import User
from .services import audit

log = logging.getLogger(__name__)

INSTRUCTIONS = """UseCase Hub is an AI use case intake, registry and portfolio-intelligence application.
Use cases are identified by a ref like UC-0042 (ids also work). Typical flows:
- "Do we already have something like X?" -> search_similar(text) then get_use_case(ref).
- Register a new idea from free text -> normalize_text(text) to preview the canvas, then create_use_case(fields).
  For a guided interview use start_intake / continue_intake / convert_intake.
- Governance -> transition_use_case(ref, to, comment); gates require reviewer role and a validated EU AI Act tier
  before approval (suggest_risk + validate_risk).
- Portfolio intelligence -> analyze_use_case / analyze_portfolio, then list_relations(status="suggested") and
  review_relation to confirm or dismiss duplicates, overlaps and dependencies.
Field ids and allowed enum values come from get_template(). Dates are ISO 8601."""


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=1)


AI_TOOLS = {"normalize_text", "improve_field", "suggest_risk", "start_intake", "continue_intake",
            "analyze_use_case", "analyze_portfolio"}


async def _user(ctx: Context, tool: str = "") -> User:
    headers = ctx.headers or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
    async with SessionLocal() as session:
        user = await resolve_token(session, token) if token else None
        if not user or user.is_demo_guest:
            raise T.ToolError("401: Unauthorized — send 'Authorization: Bearer <API key>'")
        audit.set_request_context(request_id=str(ctx.request_id)[:32], client_ip=headers.get("x-forwarded-for", "")[:64],
                                  channel="mcp")
        if tool:
            await audit.record(session, user, f"mcp.{tool}", channel="mcp")
            await session.commit()
    if tool in AI_TOOLS:
        try:
            check_ai_quota(user)
        except Exception as e:  # HTTPException -> readable tool error
            raise T.ToolError(f"429: {getattr(e, 'detail', e)}") from e
    return user


def build_server() -> MCPServer:
    mcp = MCPServer("UseCase Hub", instructions=INSTRUCTIONS, version="0.1.0")

    # ------------------------------------------------------------------ registry
    @mcp.tool(description="List use cases with optional filters. Returns compact rows (ref, title, status, scores).")
    async def list_use_cases(ctx: Context, q: str | None = None, status: list[str] | None = None,
                             business_unit: str | None = None, risk_tier: str | None = None, limit: int = 50) -> str:
        return _dump(await T.list_use_cases(await _user(ctx, "list_use_cases"), q=q, status=status, business_unit=business_unit,
                                            risk_tier=risk_tier, limit=limit))

    @mcp.tool(description="Get the full record of a use case (fields, scores, completeness, allowed transitions, AI suggestions) by ref (UC-0001) or id.")
    async def get_use_case(ctx: Context, ref: str) -> str:
        return _dump(await T.get_use_case(await _user(ctx, "get_use_case"), ref))

    @mcp.tool(description="Semantic search: find existing use cases similar to a free-text description. Use before creating a new one to avoid duplicates.")
    async def search_similar(ctx: Context, text: str, limit: int = 5) -> str:
        return _dump(await T.search_similar(await _user(ctx, "search_similar"), text, limit=limit))

    @mcp.tool(description="Create a use case from template fields (see get_template for field ids). submit=true submits it for qualification if required fields are complete. Analysis (embeddings, relations) runs afterwards.")
    async def create_use_case(ctx: Context, fields: dict[str, Any], submit: bool = False,
                              template_key: str | None = None) -> str:
        return _dump(await T.create_use_case(await _user(ctx, "create_use_case"), fields, submit=submit, template_key=template_key))

    @mcp.tool(description="Update fields of a use case (partial update: only the given fields change). Records a new version.")
    async def update_use_case(ctx: Context, ref: str, fields: dict[str, Any], note: str = "Updated via MCP") -> str:
        return _dump(await T.update_use_case(await _user(ctx, "update_use_case"), ref, fields, note=note))

    @mcp.tool(description="Move a use case through the governance workflow (draft, submitted, qualified, approved, in_build, in_production, retired, rejected). Gates need reviewer/admin role.")
    async def transition_use_case(ctx: Context, ref: str, to: str, comment: str = "") -> str:
        return _dump(await T.transition_use_case(await _user(ctx, "transition_use_case"), ref, to, comment))

    @mcp.tool(description="Decision log and version history of a use case.")
    async def get_history(ctx: Context, ref: str) -> str:
        return _dump(await T.get_history(await _user(ctx, "get_history"), ref))

    # ------------------------------------------------------------------ AI assistance
    @mcp.tool(description="Normalize free text (email, brief, notes) into template fields without creating anything. Returns fields, assumptions, open questions and completeness.")
    async def normalize_text(ctx: Context, text: str, template_key: str | None = None) -> str:
        return _dump(await T.normalize_text(await _user(ctx, "normalize_text"), text, template_key=template_key))

    @mcp.tool(description="Rewrite one field of a use case more precisely with AI (does not save; call update_use_case to persist).")
    async def improve_field(ctx: Context, ref: str, field_id: str, instruction: str = "") -> str:
        return _dump(await T.improve_field(await _user(ctx, "improve_field"), ref, field_id, instruction))

    @mcp.tool(description="Ask the AI for an EU AI Act risk tier suggestion (tier, rationale, risk categories, mitigations).")
    async def suggest_risk(ctx: Context, ref: str) -> str:
        return _dump(await T.suggest_risk(await _user(ctx, "suggest_risk"), ref))

    @mcp.tool(description="Validate the EU AI Act risk tier of a use case (reviewer/admin). Required before approval.")
    async def validate_risk(ctx: Context, ref: str, risk_tier: str, rationale: str = "") -> str:
        return _dump(await T.validate_risk(await _user(ctx, "validate_risk"), ref, risk_tier, rationale))

    # ------------------------------------------------------------------ conversational intake
    @mcp.tool(description="Start a guided intake interview. Optionally pass the user's first description; returns the intake id, the assistant's reply and the live draft.")
    async def start_intake(ctx: Context, initial_text: str | None = None, template_key: str | None = None) -> str:
        return _dump(await T.start_intake(await _user(ctx, "start_intake"), initial_text, template_key=template_key))

    @mcp.tool(description="Send the user's next answer to an intake interview; returns the assistant reply, draft, completeness and missing required fields.")
    async def continue_intake(ctx: Context, intake_id: str, message: str) -> str:
        return _dump(await T.continue_intake(await _user(ctx, "continue_intake"), intake_id, message))

    @mcp.tool(description="Turn an intake draft into a registered use case (submit=true to also submit for qualification).")
    async def convert_intake(ctx: Context, intake_id: str, submit: bool = False) -> str:
        return _dump(await T.convert_intake(await _user(ctx, "convert_intake"), intake_id, submit))

    # ------------------------------------------------------------------ portfolio intelligence
    @mcp.tool(description="List relations (duplicate, overlap, complementary, depends_on, shares_entity). Filter by use case ref and/or status (suggested, confirmed, dismissed, all).")
    async def list_relations(ctx: Context, ref: str | None = None, status: str = "suggested",
                             include_structural: bool = False, limit: int = 100) -> str:
        return _dump(await T.list_relations(await _user(ctx, "list_relations"), ref=ref, status=status,
                                            include_structural=include_structural, limit=limit))

    @mcp.tool(description="Confirm or dismiss an AI-suggested relation, optionally correcting its type.")
    async def review_relation(ctx: Context, relation_id: str, status: str, type: str | None = None) -> str:
        return _dump(await T.review_relation(await _user(ctx, "review_relation"), relation_id, status, type))

    @mcp.tool(description="Declare a relation between two use cases (duplicate, overlap, complementary, depends_on, enables).")
    async def add_relation(ctx: Context, source_ref: str, target_ref: str, type: str, explanation: str = "") -> str:
        return _dump(await T.add_relation(await _user(ctx, "add_relation"), source_ref, target_ref, type, explanation))

    @mcp.tool(description="Re-run analysis for one use case: embedding, entity extraction, structural and AI-judged relations.")
    async def analyze_use_case(ctx: Context, ref: str, use_llm: bool = True) -> str:
        return _dump(await T.analyze_use_case(await _user(ctx, "analyze_use_case"), ref, use_llm))

    @mcp.tool(description="Run the overlap / dependency analysis on the whole portfolio (reviewer/admin). Synchronous; may take a while with many use cases.")
    async def analyze_portfolio(ctx: Context, use_llm: bool = True) -> str:
        return _dump(await T.analyze_portfolio(await _user(ctx, "analyze_portfolio"), use_llm))

    @mcp.tool(description="Portfolio dashboard: counts by status / risk tier / business unit / capability, top priorities, most shared entities.")
    async def portfolio_dashboard(ctx: Context) -> str:
        return _dump(await T.dashboard(await _user(ctx, "portfolio_dashboard")))

    @mcp.tool(description="Shared entities (systems, data sources, teams, models) and how many use cases use each.")
    async def list_entities(ctx: Context, kind: str | None = None) -> str:
        return _dump(await T.list_entities(await _user(ctx, "list_entities"), kind))

    # ------------------------------------------------------------------ templates
    @mcp.tool(description="List available use case templates (key, version, name, default).")
    async def list_templates(ctx: Context) -> str:
        return _dump(await T.list_templates(await _user(ctx, "list_templates")))

    @mcp.tool(description="Get a template's field schema (ids, types, allowed values, required flags, hints). Omit key for the default template.")
    async def get_template(ctx: Context, key: str | None = None) -> str:
        return _dump(await T.get_template(await _user(ctx, "get_template"), key))

    # ------------------------------------------------------------------ resources & prompts
    @mcp.resource("usecase://{ref}", description="A use case record as JSON", mime_type="application/json")
    async def usecase_resource(ref: str) -> str:
        return _dump(await T.get_use_case(None, ref))

    @mcp.resource("template://{key}", description="A template definition as JSON", mime_type="application/json")
    async def template_resource(key: str) -> str:
        return _dump(await T.get_template(None, key))

    @mcp.prompt(description="Review a submitted use case against the qualification gate and draft a decision.")
    def qualify_use_case(ref: str) -> str:
        return (
            f"You are a reviewer of the AI use case registry. Call get_use_case('{ref}') and list_relations(ref='{ref}', "
            "status='all'). Assess the qualification gate: is the problem clear, are target users and data sources named, "
            "is the value measurable, are there duplicates or dependencies to resolve first? Then propose: (1) a decision "
            "(qualify / send back to draft / reject) with a 3-line rationale, (2) the exact fields the requester should "
            "complete, (3) whether suggest_risk should be run now. Do not call transition_use_case without confirmation."
        )

    @mcp.prompt(description="Capture a new use case from a pasted brief and check for overlaps before registering it.")
    def register_from_brief(brief: str) -> str:
        return (
            "Register this AI use case idea in UseCase Hub. Steps: 1) search_similar with a one-paragraph summary of the brief "
            "and report any existing use case that looks like a duplicate or dependency; 2) normalize_text(brief) and show the "
            "resulting canvas, assumptions and open questions to the user; 3) only after the user confirms, create_use_case "
            f"with the fields. Brief:\n\n{brief}"
        )

    return mcp


class BearerGate:
    """ASGI wrapper: reject MCP requests without a valid bearer token before they reach the session manager."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else ""
        user = None
        if token:
            async with SessionLocal() as session:
                user = await resolve_token(session, token)
        if not user or user.is_demo_guest:
            resp = JSONResponse({"error": "unauthorized", "detail": "Send 'Authorization: Bearer <UseCase Hub API key>'"},
                                status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_asgi_app(mcp: MCPServer, path: str = "/mcp") -> ASGIApp:
    """ASGI app serving the MCP endpoint at ``path`` (registered as a Route, not a Mount, so ``/mcp`` works
    without a trailing-slash redirect). Host checks are disabled: the app sits behind nginx / ingress."""
    inner = mcp.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=False,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
    return BearerGate(inner)
