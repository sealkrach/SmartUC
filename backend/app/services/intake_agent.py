"""Conversational intake agent (LangGraph).

One graph invocation = one assistant turn. The human is always in the loop: the graph ends after
asking a question (or after declaring the draft ready) and resumes on the next user message with the
state persisted in IntakeSession.

    extract -> assess -> ask        (interview continues)
                      -> wrap_up    (enough info or the user asked to finish)
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from . import llm
from .templates import completeness, field_map, is_empty, iter_fields, normalize_fields, schema_for_llm

READY_COMPLETENESS = 0.6
QUESTIONS_PER_TURN = 3
FINISH_WORDS = ("that's all", "thats all", "finish", "done", "c'est tout", "terminer", "générer", "generate", "wrap up", "submit")


class IntakeState(TypedDict, total=False):
    definition: dict
    messages: list[dict]          # [{role: user|assistant, content}]
    draft: dict
    assumptions: list[str]
    missing_required: list[str]
    completeness: float
    user_wants_to_finish: bool
    next_fields: list[str]
    reply: str
    phase: str                    # interview | ready


EXTRACT_SYSTEM = """You are the intake assistant of an AI use case registry. You read the conversation so far and
update the structured draft of the use case according to the template.

Rules:
- Update ONLY fields the user has given information for (explicitly or very clearly implied). Keep existing values
  unless the user corrected them. Never invent names, numbers or systems.
- Rewrite free-text fields in clear professional English (translate if the user writes in another language), preserving meaning.
- list / entity_list fields are arrays of short strings. enum values must match the allowed options exactly.
- If the user gave a value for an enum that does not fit any option, pick the closest option and note it in assumptions.
- Output JSON: {"fields": {<field_id>: <value>}, "assumptions": [<string>], "user_wants_to_finish": <bool>}
  "user_wants_to_finish" is true when the user says they are done / wants to generate or submit the use case."""


ASK_SYSTEM = """You are a friendly, sharp intake assistant helping someone describe an AI use case for a company registry.
You will be told which fields are still missing. Write the next assistant message:
- First, in one short sentence, acknowledge or reflect back what you understood (reformulate briefly, it helps the user see gaps).
- Then ask about at most {n} of the missing fields, grouped naturally in ONE message. Prefer the fields in the given order.
- Make questions concrete: give 1-2 short examples or options in parentheses so a non-expert can answer fast.
- If the user seems unsure, offer a plausible suggestion they can confirm with one word.
- Keep it under 120 words. Plain text, no markdown headers, no JSON. Use the same language as the user's last message.
Output JSON: {"message": <string>, "fields_asked": [<field_id>]}"""


WRAP_SYSTEM = """You are the intake assistant. The draft use case is now complete enough.
Write a short closing message (max 120 words, same language as the user): summarize the use case in 2-3 sentences,
list any assumptions you made that the user should confirm, and tell them they can now review the canvas on the right,
edit any field, and click "Create use case". Plain text. Output JSON: {"message": <string>}"""


def _transcript(messages: list[dict], limit: int = 30) -> str:
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages[-limit:])


def _missing_by_priority(definition: dict, draft: dict) -> list[str]:
    fields = iter_fields(definition)
    missing = [f for f in fields if is_empty(draft.get(f["id"]))]
    # required first, then ask_priority, then weight (desc)
    missing.sort(key=lambda f: (0 if f.get("required") else 1, f.get("ask_priority", 99), -float(f.get("weight", 1))))
    return [f["id"] for f in missing]


async def node_extract(state: IntakeState) -> dict[str, Any]:
    definition = state["definition"]
    last_user = next((m for m in reversed(state["messages"]) if m["role"] == "user"), None)
    if last_user is None:
        return {}
    user = (
        f"TEMPLATE FIELDS:\n{schema_for_llm(definition)}\n\n"
        f"CURRENT DRAFT:\n{json.dumps(state.get('draft') or {}, ensure_ascii=False)}\n\n"
        f"CONVERSATION:\n{_transcript(state['messages'])}"
    )
    result = await llm.complete_json(EXTRACT_SYSTEM, user)
    merged = normalize_fields(definition, {**(state.get("draft") or {}), **(result.get("fields") or {})})
    wants_finish = bool(result.get("user_wants_to_finish")) or any(
        w in last_user["content"].lower() for w in FINISH_WORDS
    )
    assumptions = list(dict.fromkeys((state.get("assumptions") or []) + [str(a) for a in result.get("assumptions") or []]))
    return {"draft": merged, "assumptions": assumptions, "user_wants_to_finish": wants_finish}


async def node_assess(state: IntakeState) -> dict[str, Any]:
    definition = state["definition"]
    score, missing_required = completeness(definition, state.get("draft") or {})
    ordered = _missing_by_priority(definition, state.get("draft") or {})
    ready = (not missing_required and score >= READY_COMPLETENESS) or (
        state.get("user_wants_to_finish") and not missing_required
    )
    return {
        "completeness": score,
        "missing_required": missing_required,
        "next_fields": ordered[:QUESTIONS_PER_TURN],
        "phase": "ready" if ready else "interview",
    }


def route_after_assess(state: IntakeState) -> str:
    return "wrap_up" if state.get("phase") == "ready" else "ask"


async def node_ask(state: IntakeState) -> dict[str, Any]:
    definition = state["definition"]
    fm = field_map(definition)
    targets = state.get("next_fields") or []
    descr = "\n".join(
        f"- {fid}: {fm[fid].get('label')}" + (f" — {fm[fid]['ai_hint']}" if fm[fid].get("ai_hint") else "")
        + (f" (options: {', '.join(map(str, fm[fid]['options']))})" if fm[fid].get("options") else "")
        for fid in targets if fid in fm
    )
    finish_note = ""
    if state.get("user_wants_to_finish") and state.get("missing_required"):
        finish_note = (
            "\nThe user wants to finish but these REQUIRED fields are still missing: "
            + ", ".join(state["missing_required"])
            + ". Explain kindly that you need them before the use case can be created, and ask for them."
        )
    user = (
        f"DRAFT SO FAR:\n{json.dumps(state.get('draft') or {}, ensure_ascii=False)}\n\n"
        f"MISSING FIELDS TO ASK NOW (in order):\n{descr}\n{finish_note}\n\n"
        f"CONVERSATION:\n{_transcript(state['messages'], 12)}"
    )
    result = await llm.complete_json(ASK_SYSTEM.replace("{n}", str(QUESTIONS_PER_TURN)), user, fast=True)
    return {"reply": str(result.get("message") or "Could you tell me more about the use case?")}


async def node_wrap_up(state: IntakeState) -> dict[str, Any]:
    user = (
        f"DRAFT:\n{json.dumps(state.get('draft') or {}, ensure_ascii=False)}\n\n"
        f"ASSUMPTIONS:\n{json.dumps(state.get('assumptions') or [], ensure_ascii=False)}\n\n"
        f"LAST USER MESSAGE: {state['messages'][-1]['content'] if state['messages'] else ''}"
    )
    result = await llm.complete_json(WRAP_SYSTEM, user, fast=True)
    return {"reply": str(result.get("message") or "The draft looks complete — review it and create the use case.")}


def build_graph():
    g = StateGraph(IntakeState)
    g.add_node("extract", node_extract)
    g.add_node("assess", node_assess)
    g.add_node("ask", node_ask)
    g.add_node("wrap_up", node_wrap_up)
    g.set_entry_point("extract")
    g.add_edge("extract", "assess")
    g.add_conditional_edges("assess", route_after_assess, {"ask": "ask", "wrap_up": "wrap_up"})
    g.add_edge("ask", END)
    g.add_edge("wrap_up", END)
    return g.compile()


_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


async def run_turn(definition: dict, messages: list[dict], draft: dict, assumptions: list[str]) -> IntakeState:
    state: IntakeState = {
        "definition": definition,
        "messages": messages,
        "draft": draft or {},
        "assumptions": assumptions or [],
    }
    return await graph().ainvoke(state)


def opening_message(definition: dict) -> str:
    return (
        f"Hi! I'll help you describe your AI use case for the registry ({definition.get('name')}). "
        "Tell me in your own words what you'd like to do: the problem, who it's for, and what you imagine "
        "the AI doing. Don't worry about structure — I'll organise it into the canvas on the right and ask "
        "about what's missing. You can paste an existing brief or email too."
    )
