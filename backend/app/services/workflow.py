"""Governance workflow: a state machine with approval gates.

    draft -> submitted -> qualified -> approved -> in_build -> in_production -> retired
                 \\__________\\___________\\_______________________> rejected
    rejected -> draft (rework), any pre-production state -> draft (rework)
"""
from __future__ import annotations

from ..models import Role, Status

# (from, to): minimum role allowed to perform the transition
TRANSITIONS: dict[tuple[str, str], str] = {
    (Status.draft.value, Status.submitted.value): Role.requester.value,
    (Status.submitted.value, Status.qualified.value): Role.reviewer.value,   # gate 1: qualification
    (Status.submitted.value, Status.rejected.value): Role.reviewer.value,
    (Status.submitted.value, Status.draft.value): Role.requester.value,      # withdraw
    (Status.qualified.value, Status.approved.value): Role.reviewer.value,    # gate 2: investment decision
    (Status.qualified.value, Status.rejected.value): Role.reviewer.value,
    (Status.qualified.value, Status.draft.value): Role.reviewer.value,       # send back for rework
    (Status.approved.value, Status.in_build.value): Role.reviewer.value,
    (Status.approved.value, Status.rejected.value): Role.admin.value,
    (Status.in_build.value, Status.in_production.value): Role.reviewer.value,  # gate 3: go-live
    (Status.in_build.value, Status.approved.value): Role.reviewer.value,       # paused
    (Status.in_production.value, Status.retired.value): Role.reviewer.value,
    (Status.rejected.value, Status.draft.value): Role.requester.value,         # rework
}

GATES = {
    (Status.submitted.value, Status.qualified.value): "Qualification gate: problem, users, data and value are clear.",
    (Status.qualified.value, Status.approved.value): "Investment gate: risk tier validated, dependencies known, sponsor committed.",
    (Status.in_build.value, Status.in_production.value): "Go-live gate: evaluation results, monitoring and mitigations in place.",
}

ORDER = [s.value for s in Status]
ROLE_RANK = {Role.requester.value: 0, Role.reviewer.value: 1, Role.admin.value: 2}


def allowed_transitions(current: str, role: str) -> list[dict]:
    out = []
    for (frm, to), min_role in TRANSITIONS.items():
        if frm == current and ROLE_RANK.get(role, 0) >= ROLE_RANK[min_role]:
            out.append({"to": to, "gate": GATES.get((frm, to)), "min_role": min_role})
    return out


def can_transition(current: str, to: str, role: str) -> tuple[bool, str]:
    min_role = TRANSITIONS.get((current, to))
    if min_role is None:
        return False, f"Transition {current} -> {to} is not allowed"
    if ROLE_RANK.get(role, 0) < ROLE_RANK[min_role]:
        return False, f"Transition {current} -> {to} requires role {min_role}"
    return True, ""
