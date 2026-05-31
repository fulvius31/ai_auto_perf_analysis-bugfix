"""Prompt classes for the Phase 0-5 bug-fix porting pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from auto_bug_fix.bug_fix_config import BugFixConfig, ClaudeConfig


def create_context_str(claude_config: ClaudeConfig, config: BugFixConfig) -> str:
    return (
        "<context>\n"
        f"<repo_path>{config.repo_path}</repo_path>\n"
        f"<build_dir>{config.build_dir}</build_dir>\n"
        f"<source_branch>{config.source_branch}</source_branch>\n"
        f"<target_branch>{config.target_branch}</target_branch>\n"
        f"<source_fix_commit>{config.source_fix_commit}</source_fix_commit>\n"
        f"<bug_description>{config.bug_description}</bug_description>\n"
        f"<issue_id>{config.issue_id}</issue_id>\n"
        f"<build_command>{config.build_command}</build_command>\n"
        f"<test_command>{config.test_command}</test_command>\n"
        f"<cwd>{claude_config.cwd}</cwd>\n"
        "</context>\n"
    )


@dataclass
class TriageAgentPrompt:
    context: str
    seed_files: list[str]
    allowed_modules: list[str]
    output_file: str

    prompt_template: ClassVar[str] = """\
{context}

You are the Triage Agent (Phase 0). Your goal is to analyze the bug fix and determine:
1. Whether the target branch is affected by the bug
2. Whether the fix has already been applied under a different SHA
3. What the bug is and how the fix addresses it

The upstream fix touches these files:
{seed_files}

The resolved allowlist (target-branch paths) is:
{allowed_modules}

## PART 1: Understand the Bug and Fix

First, analyze the fix itself:

1. **Get the fix diff**: `git show <source_fix_commit>`
2. **Analyze what changed**:
   - Which functions/code paths were modified?
   - What incorrect behavior was removed? (e.g., unbounded loop, missing check, buffer overflow, memory leak, logic error)
   - What correct behavior was added? (e.g., bounds check, input validation, size limit, cleanup code, error handling)
3. **Extract bug details from commit message**:
   - Bug category (e.g., CWE if security-related, or general categories: memory leak, race condition, logic error, performance issue)
   - Impact (crashes, incorrect results, security vulnerability, performance degradation, etc.)
   - Root cause (what coding mistake caused this?)

Write a "Bug Analysis" section (or "Vulnerability Analysis" if it's a CVE) explaining:
- **What the bug is** (in 1-2 sentences)
- **How it manifests** (crash scenario, incorrect behavior, security exploit, performance issue, etc.)
- **How the fix prevents it** (what code change corrects the bug)
- **Bug category** (CWE if security-related, or general category)

## PART 2: Check if Target is Affected

Now determine if <target_branch> has the buggy code:

1. **Symbol check**: Use `git grep` on <target_branch> to find the functions/variables from the fix
   - If symbols don't exist → STOP (not affected)
   - If symbols exist → continue analysis

2. **Code comparison**: Check if buggy code exists on target:
   - Extract the REMOVED lines from the fix diff (the buggy code)
   - Search for similar patterns on <target_branch>
   - Use `git show <target_branch>:<file>` to inspect current state

3. **Blame analysis**: If buggy code found, identify when it was introduced:
   - `git blame -L <start>,<end> <file>` on the buggy lines
   - Report the commit SHA and author who introduced it
   - Estimate how long the bug has existed

Write an "Impact Assessment" section:
- **Target has the bug**: YES/NO
- **Bug introduced in**: commit SHA and date
- **Impact**: describe how target users could be affected (crashes, incorrect results, security risk, performance issues, etc.)

## PART 3: Deterministic Gates

Execute these git checks:

1. **Forward patch-id check**:
   - Compute: `git show <source_fix_commit> | git patch-id --stable`
   - Search recent commits on <target_branch> for matching patch-id
   - If match found → STOP (fix already present)

2. **Ancestry check**:
   - `git merge-base --is-ancestor <vuln_introducing_commit> <target_branch>`
   - EXIT CODE: 0=ancestor (affected), 1=not ancestor (may not be affected)

For each check, report:
- Exact command
- Raw output/exit code
- Interpretation

## Report Structure

Write a structured triage report to <cwd>/{output_file} with these sections:

### 1. Bug Analysis (or "Vulnerability Analysis" if it's a CVE)
[What the bug is, how it manifests, how fix corrects it]

### 2. Impact Assessment
[Whether target is affected, where buggy code lives, since when]

### 3. Gate Decisions
[Forward patch-id, ancestry, symbol checks with raw git outputs]

### 4. Recommendation
- **STOP (already fixed)**: Fix present under different SHA
- **STOP (not affected)**: Buggy code doesn't exist on target
- **ESCALATE (symbols missing)**: Cannot map fix to target
- **PROCEED**: Target is affected, fix is needed

Include all raw tool outputs and cite every claim with a git command.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            seed_files="\n".join(f"  - {f}" for f in self.seed_files),
            allowed_modules="\n".join(f"  - {f}" for f in self.allowed_modules),
            output_file=self.output_file,
        )


TRIAGE_REPORT_FILE = "triage_report.txt"


@dataclass
class TestPortAgentPrompt:
    context: str
    source_fix_commit: str
    target_branch: str
    output_manifest_file: str

    prompt_template: ClassVar[str] = """\
{context}

You are the Test Port Agent (Phase 1, step 3). Your goal is to port test files from \
<source_fix_commit> to <target_branch>.

1. Run `git show {source_fix_commit}` and identify all test files added or modified.
2. For each test file, try `git checkout {source_fix_commit} -- <test_file>` first.
3. If the checkout succeeds, verify the test compiles and is recognized by the test harness \
on <target_branch>. If it needs adaptation (different harness, renamed includes, build system \
registration), make the minimum changes needed.
4. If the checkout fails (file doesn't exist at that path on target), adapt the test to target's \
test infrastructure layout.
5. Do NOT change test logic — only adapt harness compatibility.

Write each ported test file to its correct location on <target_branch>.
Record the absolute path of every ported test file in <cwd>/{output_manifest_file}, one per line.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            source_fix_commit=self.source_fix_commit,
            target_branch=self.target_branch,
            output_manifest_file=self.output_manifest_file,
        )


TEST_PORT_MANIFEST_FILE = "test_port_manifest.txt"


@dataclass
class CherryPickAgentPrompt:
    context: str
    fix_commit: str
    strategies_log_file: str

    prompt_template: ClassVar[str] = """\
{context}

You are the Cherry-Pick Agent (Phase 2). Your goal is to apply the upstream fix via cherry-pick, \
trying multiple strategies in order.

Try these strategies in order, aborting between attempts:

1. `git cherry-pick -x {fix_commit}` — default 3-way merge
2. `git cherry-pick -x --strategy-option=patience {fix_commit}` — patience diff
3. `git cherry-pick -x --strategy=ort {fix_commit}` — modern merge strategy

For each strategy:
- Run the command
- Check exit code and `git status --porcelain`
- If clean (exit 0, no conflicts): report success and stop
- If conflicts (UU markers): log the conflicted files, run `git cherry-pick --abort`, try next
- If unmappable (renames, missing files): log the issue, run `git cherry-pick --abort`, try next

Log each attempt to <cwd>/{strategies_log_file} in this format:
```
Strategy: <name>
Outcome (deterministic): <clean|conflict|unmappable> [details]
Commentary (LLM): <your analysis of why this outcome occurred>
```

IMPORTANT: the "Outcome" line must be the raw mechanical result. Your analysis goes in \
"Commentary" — these are logged separately for audit purposes.

Report the final result:
- CLEAN: which strategy succeeded
- CONFLICT: list conflicted files from the last attempt (for Phase 3a)
- UNMAPPABLE: describe the structural issue (for Phase 3b)
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            fix_commit=self.fix_commit,
            strategies_log_file=self.strategies_log_file,
        )


STRATEGIES_LOG_FILE = "cherry_pick_strategies.txt"


@dataclass
class NarrowResolutionAgentPrompt:
    context: str
    fix_diff: str
    conflicted_files: list[str]
    allowed_modules: list[str]

    prompt_template: ClassVar[str] = """\
{context}

You are the Narrow Resolution Agent (Phase 3a). Cherry-pick produced conflicts in these files:
{conflicted_files}

The upstream fix diff for reference:
<fix_diff>
{fix_diff}
</fix_diff>

Your task is to resolve ONLY the conflict hunks (marked with <<<<<<<, =======, >>>>>>>). \
Strict constraints:

1. Resolve each conflict hunk to produce the intended fix behavior on the target branch.
2. Do NOT re-derive intent — the fix diff tells you exactly what should change.
3. Do NOT rewrite surrounding code or modify files outside the cherry-pick's scope.
4. Do NOT modify any file not in this allowlist:
{allowed_modules}

After resolving all conflicts:
- Run `git diff --name-only HEAD` and verify every changed file is in the allowlist.
- If any file is outside the allowlist, undo that change and re-resolve.
- Stage and commit with the cherry-pick message plus structured trailers.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            fix_diff=self.fix_diff,
            conflicted_files="\n".join(f"  - {f}" for f in self.conflicted_files),
            allowed_modules="\n".join(f"  - {f}" for f in self.allowed_modules),
        )


@dataclass
class FixAgentPrompt:
    context: str
    build_output: str
    test_output: str
    allowed_modules: list[str]
    allowed_seed: list[str]
    iteration: int
    max_retries: int

    prompt_template: ClassVar[str] = """\
{context}

You are the Fix Agent (Phase 4, iteration {iteration}/{max_retries}). The patched target branch \
failed to build or test. Your goal is to diagnose and fix the issue.

Build output:
<build_output>
{build_output}
</build_output>

Test output:
<test_output>
{test_output}
</test_output>

Constraints:
1. You may ONLY modify files in this allowlist:
{allowed_modules}

2. You may NOT modify test files unless they are in the original seed:
{allowed_seed}

3. After applying your fix, run `git diff --name-only HEAD` and verify compliance.
4. If you cannot fix the issue within these constraints, report that explicitly.

Diagnose the root cause, apply a targeted fix, and report what you changed and why.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            build_output=self.build_output,
            test_output=self.test_output,
            allowed_modules="\n".join(f"  - {f}" for f in self.allowed_modules),
            allowed_seed="\n".join(f"  - {f}" for f in self.allowed_seed),
            iteration=self.iteration,
            max_retries=self.max_retries,
        )


@dataclass
class SemanticEquivalencePrompt:
    range_diff_output: str
    upstream_patch: str
    ported_patch: str

    prompt_template: ClassVar[str] = """\
You are assessing semantic equivalence between an upstream CVE fix and its ported version.

git range-diff output:
<range_diff>
{range_diff_output}
</range_diff>

Upstream patch:
<upstream>
{upstream_patch}
</upstream>

Ported patch:
<ported>
{ported_patch}
</ported>

Assess whether the ported patch is semantically equivalent to the upstream patch.

Respond with a structured assessment:
1. Verdict: "equivalent", "equivalent with modifications", or "not equivalent"
2. If modifications exist, list each difference and whether it preserves the fix's intent
3. If you cannot determine equivalence, respond with "Vote: ABSTAIN" and explain why

Be precise. The deterministic range-diff output carries most of the signal — your role is to \
narrate what it shows, not to override it.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            range_diff_output=self.range_diff_output,
            upstream_patch=self.upstream_patch,
            ported_patch=self.ported_patch,
        )


@dataclass
class AllowlistExpansionPrompt:
    context: str
    seed_files: list[str]
    target_branch: str
    unresolved_files: list[str]

    prompt_template: ClassVar[str] = """\
{context}

You are helping resolve the allowlist for a CVE backport. The upstream fix touches these files, \
but some could not be mapped to <target_branch> via deterministic rename detection:

Resolved (already mapped):
{seed_files}

Unresolved (need your help):
{unresolved_files}

For each unresolved file:
1. Use `git grep` to search for key symbols from that file on {target_branch}
2. Use `git log --all -- <path>` to check if the file ever existed
3. Determine where the code landed — renamed, split, or moved to a different module

For each file, report:
- The original upstream path
- The target-branch path(s) where the code now lives
- How you determined this (which git commands, which symbols matched)

If you cannot determine where a file's code landed, say so explicitly — do not guess.
"""

    def prompt(self) -> str:
        return self.prompt_template.format(
            context=self.context,
            seed_files="\n".join(f"  - {f}" for f in self.seed_files),
            unresolved_files="\n".join(f"  - {f}" for f in self.unresolved_files),
            target_branch=self.target_branch,
        )
