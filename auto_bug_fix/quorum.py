"""Quorum 1 voting for failure-mode confirmation (fallback when signatures mismatch)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


class Vote(Enum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


@dataclass
class QuorumResult:
    votes: list[Vote]
    raw_responses: list[str]
    require_unanimity: bool

    @property
    def decision(self) -> str:
        non_abstain = [v for v in self.votes if v != Vote.ABSTAIN]
        if not non_abstain:
            return "escalate"
        if self.require_unanimity:
            return "proceed" if all(v == Vote.YES for v in non_abstain) else "escalate"
        yes_count = sum(1 for v in non_abstain if v == Vote.YES)
        return "proceed" if yes_count > len(non_abstain) / 2 else "escalate"


def parse_vote(response: str) -> Vote:
    lower = response.strip().lower()
    for line in lower.splitlines():
        line = line.strip()
        if line.startswith("vote:"):
            token = line.split(":", 1)[1].strip()
            if token in ("yes", "proceed"):
                return Vote.YES
            if token in ("no", "reject", "escalate"):
                return Vote.NO
            if token in ("abstain", "insufficient information", "unknown"):
                return Vote.ABSTAIN
    if "vote: yes" in lower or "\nyes\n" in f"\n{lower}\n":
        return Vote.YES
    if "vote: no" in lower or "\nno\n" in f"\n{lower}\n":
        return Vote.NO
    return Vote.ABSTAIN


@dataclass
class QuorumVoterPrompt:
    variant: str
    context: str
    s_target: str
    s_parent: str
    fix_diff: str = ""
    call_graph: str = ""
    advisory_text: str = ""

    DIFF_TEMPLATE: ClassVar[str] = """You are a security engineer reviewing whether a test failure on a target branch
is caused by the same vulnerability as a test failure on the source branch.

The upstream fix diff:
<fix_diff>
{fix_diff}
</fix_diff>

Failure signature on target (unpatched):
<s_target>
{s_target}
</s_target>

Failure signature on source-parent (pre-fix):
<s_parent>
{s_parent}
</s_parent>

The failure signatures did NOT match after normalization.
Analyze the diff and determine: are both failures caused by the same underlying vulnerability?

Respond with exactly one line: "Vote: YES", "Vote: NO", or "Vote: ABSTAIN"
followed by a brief explanation."""

    CALL_GRAPH_TEMPLATE: ClassVar[str] = """You are a security engineer reviewing whether a test failure on a target branch
is caused by the same vulnerability as a test failure on the source branch.

Call graph of the touched function on the target branch:
<call_graph>
{call_graph}
</call_graph>

Failure signature on target (unpatched):
<s_target>
{s_target}
</s_target>

Failure signature on source-parent (pre-fix):
<s_parent>
{s_parent}
</s_parent>

The failure signatures did NOT match after normalization.
Analyze the call graph and determine: are both failures caused by the same underlying vulnerability?

Respond with exactly one line: "Vote: YES", "Vote: NO", or "Vote: ABSTAIN"
followed by a brief explanation."""

    ADVISORY_TEMPLATE: ClassVar[str] = """You are a security engineer reviewing whether a test failure on a target branch
is caused by the same vulnerability as a test failure on the source branch.

CVE advisory text:
<advisory>
{advisory_text}
</advisory>

Failure signature on target (unpatched):
<s_target>
{s_target}
</s_target>

Failure signature on source-parent (pre-fix):
<s_parent>
{s_parent}
</s_parent>

The failure signatures did NOT match after normalization.
Based on the advisory description, determine: are both failures caused by the same underlying vulnerability?

Respond with exactly one line: "Vote: YES", "Vote: NO", or "Vote: ABSTAIN"
followed by a brief explanation."""

    def prompt(self) -> str:
        if self.variant == "diff":
            return self.DIFF_TEMPLATE.format(
                fix_diff=self.fix_diff,
                s_target=self.s_target,
                s_parent=self.s_parent,
            )
        if self.variant == "call_graph":
            return self.CALL_GRAPH_TEMPLATE.format(
                call_graph=self.call_graph,
                s_target=self.s_target,
                s_parent=self.s_parent,
            )
        return self.ADVISORY_TEMPLATE.format(
            advisory_text=self.advisory_text,
            s_target=self.s_target,
            s_parent=self.s_parent,
        )


def create_quorum_prompts(
    s_target: str,
    s_parent: str,
    fix_diff: str,
    call_graph: str,
    advisory_text: str,
    context: str = "",
) -> list[QuorumVoterPrompt]:
    return [
        QuorumVoterPrompt(
            variant="diff",
            context=context,
            s_target=s_target,
            s_parent=s_parent,
            fix_diff=fix_diff,
        ),
        QuorumVoterPrompt(
            variant="call_graph",
            context=context,
            s_target=s_target,
            s_parent=s_parent,
            call_graph=call_graph,
        ),
        QuorumVoterPrompt(
            variant="advisory",
            context=context,
            s_target=s_target,
            s_parent=s_parent,
            advisory_text=advisory_text,
        ),
    ]


def tally_votes(
    responses: list[str],
    require_unanimity: bool = True,
) -> QuorumResult:
    votes = [parse_vote(r) for r in responses]
    return QuorumResult(
        votes=votes,
        raw_responses=responses,
        require_unanimity=require_unanimity,
    )
