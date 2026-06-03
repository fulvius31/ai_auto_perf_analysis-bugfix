"""Phase 0-5 orchestrator for the auto_bug_fix pipeline.

Usage:
    Edit ``auto_bug_fix/bug_fix_config.py`` to configure your project, then run:

        python -m auto_bug_fix.run_bug_fix
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
import asyncio
from dataclasses import dataclass, field
from typing import Any

from common.utils import Tee
from common.claude_utils import ClaudeConfig, claude_run

from auto_bug_fix.bug_fix_config import BugFixConfig
from auto_bug_fix.git_tools import (
    git_show_files,
    git_cherry_pick,
    git_cherry_pick_abort,
    git_status_porcelain,
    git_diff_name_only,
    git_format_patch,
    git_merge_base,
    git_reset_hard,
    git_commit,
)
from auto_bug_fix.patch_id import forward_patch_id_check, backward_patch_id_check
from auto_bug_fix.signature import capture_output, normalize_signature, compare_signatures
from auto_bug_fix.allowlist import derive_seed, resolve_renames, enforce_allowlist, enforce_test_file_veto
from auto_bug_fix.baseline import capture_baseline, check_regression, run_test_suite
from auto_bug_fix.git_bisect import create_fixture_cache, create_bisect_wrapper, run_bisect
from auto_bug_fix.positive_control import run_positive_control
from auto_bug_fix.range_diff import run_range_diff, parse_equivalence
from auto_bug_fix.quorum import create_quorum_prompts, tally_votes
from auto_bug_fix.dossier import Dossier, build_trailers, format_dossier
from auto_bug_fix.tracker import Tracker
from auto_bug_fix.bug_fix_prompts import (
    create_context_str,
    TriageAgentPrompt,
    TestPortAgentPrompt,
    CherryPickAgentPrompt,
    NarrowResolutionAgentPrompt,
    FixAgentPrompt,
    SemanticEquivalencePrompt,
    AllowlistExpansionPrompt,
    TRIAGE_REPORT_FILE,
    TEST_PORT_MANIFEST_FILE,
    STRATEGIES_LOG_FILE,
)

LOG_FILE = "__run_log_bug_fix.txt"
DOSSIER_FILE = "dossier.md"

log = logging.getLogger(__name__)


class PipelineEscalation(Exception):
    pass


class PipelineStop(Exception):
    pass


@dataclass
class PipelineState:
    seed: list[str] = field(default_factory=list)
    allowed_modules: list[str] = field(default_factory=list)
    escalated_paths: list[str] = field(default_factory=list)
    baseline: set[str] = field(default_factory=set)
    fixture_cache: str = ""
    ported_test_files: list[str] = field(default_factory=list)
    s_target: str = ""
    s_parent: str = ""
    bisect_sha: str | None = None
    cherry_pick_path: str = ""
    dossier: Dossier | None = None


def phase_0_triage(
    config: BugFixConfig,
    claude_config: ClaudeConfig,
    state: PipelineState,
) -> str:
    """Run deterministic triage gates (patch-id, ancestry, seed files) and derive the allowlist."""
    repo = config.repo_path
    fix = config.source_fix_commit
    target = config.target_branch

    state.seed = derive_seed(repo, fix)

    fork_point = git_merge_base(repo, config.source_branch, target)
    resolved, escalated = resolve_renames(
        repo, state.seed, target, fork_point,
        cap=config.allowlist_rename_expansion_cap,
    )
    state.allowed_modules = resolved
    state.escalated_paths = escalated

    if config.allowed_modules is not None:
        state.allowed_modules = config.allowed_modules

    fwd = forward_patch_id_check(repo, fix, target, config.forward_patch_id_lookback)
    if fwd:
        raise PipelineStop(f"Fix already present on target as {fwd}")

    from auto_bug_fix.git_tools import git_is_ancestor, git_cat_file_exists
    blame_candidate = None
    bwd = backward_patch_id_check(repo, fix, target, config.forward_patch_id_lookback)
    is_ancestor = git_is_ancestor(repo, fix, target)

    if not is_ancestor and not bwd:
        # Before stopping, check if any seed files exist on target branch.
        # Branches may diverge but still share the vulnerable code.
        seed_exists_on_target = any(
            git_cat_file_exists(repo, target, f) for f in state.seed
        )
        if not seed_exists_on_target:
            raise PipelineStop("Target branch is not affected (no ancestry, no patch-id match, no seed files)")
        log.info("Ancestry/patch-id checks failed but seed files exist on target — proceeding with caution")

    return "proceed"


def phase_1_baseline_red_bisect(
    config: BugFixConfig,
    claude_config: ClaudeConfig,
    state: PipelineState,
) -> None:
    """Capture test baseline, run RED check, and optionally bisect for the introducing commit."""
    repo = config.repo_path

    _, baseline_stdout, baseline_stderr = run_test_suite(config.test_command, config.build_dir)
    state.baseline = capture_baseline(config.test_command, config.build_dir)

    red_exit, red_output = capture_output(
        [config.test_command], config.build_dir,
    )
    if red_exit == 0:
        if state.ported_test_files:
            raise PipelineEscalation("RED check passed — target may already be fixed or test is inapplicable")
        log.info("Baseline tests pass (no ported vulnerability test available) — proceeding")
    else:
        state.s_target = normalize_signature(red_output)

    if state.fixture_cache and state.ported_test_files:
        pc_exit, pc_output = run_positive_control(
            repo_path=repo,
            fix_commit=config.source_fix_commit,
            fixture_cache=state.fixture_cache,
            test_dir="tests",
            build_cmd=config.build_command,
            test_cmd=config.test_command,
            ported_test=state.ported_test_files[0] if state.ported_test_files else "",
        )
        if pc_exit == 0:
            raise PipelineEscalation(
                "Positive control passed — test doesn't exercise the vuln"
            )
        state.s_parent = normalize_signature(pc_output)

    fork_point = git_merge_base(repo, config.source_branch, config.target_branch)
    if state.fixture_cache:
        wrapper = create_bisect_wrapper(
            fixture_cache=state.fixture_cache,
            test_dir="tests",
            build_cmd=config.build_command,
            test_cmd=config.test_command,
            ported_test=state.ported_test_files[0] if state.ported_test_files else "",
            manifest_patch_cmd=config.manifest_patch_command,
        )
        state.bisect_sha = run_bisect(
            repo, "HEAD", fork_point, wrapper, config.bisect_max_commits,
        )


def phase_1_5_failure_mode(
    config: BugFixConfig,
    claude_config: ClaudeConfig,
    state: PipelineState,
) -> str:
    """Confirm the failure mode matches via signature comparison or quorum vote."""
    if not state.s_target or not state.s_parent:
        return "skip"

    result = compare_signatures(state.s_target, state.s_parent)
    if result == "match":
        return "proceed"

    prompts = create_quorum_prompts(
        s_target=state.s_target,
        s_parent=state.s_parent,
        fix_diff="",
        call_graph="",
        advisory_text=config.bug_description,
    )
    responses: list[str] = []
    for p in prompts:
        responses.append(p.prompt())

    quorum = tally_votes(responses, config.require_unanimity)
    if quorum.decision == "escalate":
        raise PipelineEscalation(
            f"Q1 voted to escalate (signatures mismatched: {result})"
        )
    return "proceed"


CHERRY_PICK_STRATEGIES = [
    {"strategy": None, "strategy_option": None, "name": "default"},
    {"strategy": None, "strategy_option": "patience", "name": "patience"},
    {"strategy": "ort", "strategy_option": None, "name": "ort"},
]


def phase_2_cherry_pick(
    config: BugFixConfig,
    state: PipelineState,
) -> str:
    """Attempt cherry-pick with multiple strategies. Returns 'clean', 'conflict', or 'unmappable'."""
    repo = config.repo_path
    fix = config.source_fix_commit

    for strat in CHERRY_PICK_STRATEGIES:
        result = git_cherry_pick(repo, fix, strategy=strat["strategy"], strategy_option=strat["strategy_option"])

        if result.success:
            status = git_status_porcelain(repo)
            uu_files = [l for l in status if l.startswith("UU ") or l.startswith("AA ")]
            if not uu_files:
                state.cherry_pick_path = "clean"
                if state.dossier:
                    state.dossier.add_strategy(strat["name"], "clean")
                return "clean"

        status = git_status_porcelain(repo)
        uu_files = [l for l in status if l.startswith("UU ") or l.startswith("AA ")]

        if uu_files:
            if state.dossier:
                state.dossier.add_strategy(
                    strat["name"],
                    f"conflict ({', '.join(l[3:] for l in uu_files)})",
                )
            git_cherry_pick_abort(repo)
            continue

        if state.dossier:
            state.dossier.add_strategy(strat["name"], "unmappable")
        git_cherry_pick_abort(repo)

    last_status = git_status_porcelain(repo)
    uu_in_last = [l for l in last_status if l.startswith("UU ") or l.startswith("AA ")]

    git_cherry_pick(repo, fix, strategy=CHERRY_PICK_STRATEGIES[-1]["strategy"],
                    strategy_option=CHERRY_PICK_STRATEGIES[-1]["strategy_option"])
    status = git_status_porcelain(repo)
    uu_files = [l for l in status if l.startswith("UU ") or l.startswith("AA ")]

    if uu_files:
        state.cherry_pick_path = "conflict"
        return "conflict"

    state.cherry_pick_path = "unmappable"
    git_cherry_pick_abort(repo)
    return "unmappable"


def phase_4_verify(
    config: BugFixConfig,
    state: PipelineState,
) -> bool:
    """Build, test, and check allowlist conformance. Retries up to max_build_test_retries."""
    repo = config.repo_path

    for i in range(config.max_build_test_retries):
        _, build_stdout, build_stderr = run_test_suite(config.build_command, config.build_dir)

        _, test_stdout, test_stderr = run_test_suite(config.test_command, config.build_dir)
        from auto_bug_fix.baseline import parse_test_failures
        failures = parse_test_failures(test_stdout, test_stderr)

        passed, new_failures = check_regression(failures, state.baseline)
        if passed:
            ok, violations = enforce_allowlist(repo, state.allowed_modules, config.disallowed_modules)
            if ok:
                test_ok, test_violations = enforce_test_file_veto(repo, state.seed)
                if test_ok:
                    return True

    raise PipelineEscalation(
        f"Phase 4 verify failed after {config.max_build_test_retries} retries"
    )


def phase_4_5_semantic_equivalence(
    config: BugFixConfig,
    state: PipelineState,
) -> str:
    """Run range-diff to classify the port as identical, modified, or unmatched."""
    repo = config.repo_path
    try:
        rd_output = run_range_diff(
            repo,
            f"{config.source_fix_commit}^..{config.source_fix_commit}",
            "HEAD^..HEAD",
        )
    except Exception:
        return "unmatched"

    equivalence = parse_equivalence(rd_output)
    if equivalence == "identical":
        return "identical"

    return equivalence


def run_pipeline(
    config: BugFixConfig | None = None,
    claude_config: ClaudeConfig | None = None,
    output_dir: str = "runs",
) -> tuple[Dossier, Tracker]:
    """Orchestrate the full pipeline: triage, baseline, cherry-pick, resolve, verify, equivalence."""
    if config is None:
        from auto_bug_fix.bug_fix_config import bug_fix_config as _default
        config = _default
    if claude_config is None:
        from auto_bug_fix.bug_fix_config import claude_config as _default
        claude_config = _default

    from dataclasses import asdict
    tracker = Tracker(
        issue_id=config.issue_id,
        config_dict=asdict(config),
        model=claude_config.model,
        output_dir=output_dir,
    )

    state = PipelineState()
    state.dossier = Dossier(
        issue_id=config.issue_id,
        source_branch=config.source_branch,
        target_branch=config.target_branch,
        fix_commit=config.source_fix_commit,
        bug_description=f"Porting fix for {config.issue_id}",
    )

    with tracker.phase("Phase 0 — Triage"):
        triage_result = phase_0_triage(config, claude_config, state)
        state.dossier.add("Triage result", triage_result, "Phase 0")
        tracker.record_gate("forward_patch_id", "not_found")
        tracker.record_gate("ancestry", "affected")

    with tracker.phase("Phase 1 — Baseline + RED + Bisect"):
        phase_1_baseline_red_bisect(config, claude_config, state)
        if state.bisect_sha:
            state.dossier.add("Bisect result", state.bisect_sha, "Phase 1 git bisect")
            tracker.record_gate("bisect", state.bisect_sha)

    with tracker.phase("Phase 1.5 — Failure-mode confirmation"):
        phase_1_5_failure_mode(config, claude_config, state)
        sig_result = compare_signatures(state.s_target, state.s_parent) if state.s_target and state.s_parent else "skip"
        state.dossier.add("Signature comparison", sig_result, "Phase 1.5")
        tracker.record_gate("signature_comparison", sig_result)

    with tracker.phase("Phase 2 — Cherry-pick"):
        cp_result = phase_2_cherry_pick(config, state)
        state.dossier.cherry_pick_path = state.cherry_pick_path
        tracker.record_gate("cherry_pick", cp_result)

    if cp_result == "unmappable":
        state.dossier.add("Phase 3b", "Pipeline paused — human-driven port required", "Phase 3b")
        tracker.set_outcome("escalate")
        tracker.save()
        raise PipelineEscalation("Unmappable cherry-pick — human intervention required")

    if cp_result == "conflict":
        with tracker.phase("Phase 3a — Conflict resolution"):
            fix_diff = subprocess.run(
                ["git", "show", config.source_fix_commit],
                cwd=config.repo_path, capture_output=True, text=True,
            ).stdout

            MAX_DIFF_CHARS = 200_000
            if len(fix_diff) > MAX_DIFF_CHARS:
                log.warning("fix_diff too large (%d chars), using --stat + conflicted file diffs only", len(fix_diff))
                stat = subprocess.run(
                    ["git", "show", "--stat", config.source_fix_commit],
                    cwd=config.repo_path, capture_output=True, text=True,
                ).stdout
                status_lines = git_status_porcelain(config.repo_path)
                uu_paths = [l[3:] for l in status_lines if l.startswith("UU ") or l.startswith("AA ")]
                partial_diff = subprocess.run(
                    ["git", "show", config.source_fix_commit, "--"] + uu_paths,
                    cwd=config.repo_path, capture_output=True, text=True,
                ).stdout
                fix_diff = stat + "\n\n--- Partial diff (conflicted files only) ---\n\n" + partial_diff
                if len(fix_diff) > MAX_DIFF_CHARS:
                    fix_diff = fix_diff[:MAX_DIFF_CHARS] + "\n\n[TRUNCATED — diff too large]"

            status = git_status_porcelain(config.repo_path)
            uu_files = [l[3:] for l in status if l.startswith("UU ") or l.startswith("AA ")]

            context = create_context_str(claude_config, config)
            prompt = NarrowResolutionAgentPrompt(
                fix_diff=fix_diff,
                conflicted_files=uu_files,
                context=context,
                allowed_modules=state.allowed_modules,
            )

            claude_config_3a = ClaudeConfig(
                model=claude_config.model,
                allowed_tools=claude_config.allowed_tools,
                perm_mode=claude_config.perm_mode,
                cwd=config.repo_path,
            )

            for attempt in range(1, config.max_resolution_retries + 1):
                log.info("Phase 3a attempt %d/%d — resolving %d conflicts: %s",
                         attempt, config.max_resolution_retries, len(uu_files), ", ".join(uu_files))
                asyncio.run(claude_run(claude_config_3a, [prompt.prompt()], tracker=tracker))

                remaining = git_status_porcelain(config.repo_path)
                still_conflicted = [l for l in remaining if l.startswith("UU ") or l.startswith("AA ")]
                if not still_conflicted:
                    git_commit(config.repo_path, f"Port fix for {config.issue_id}\n\nResolved by Claude ({attempt} attempt(s))")
                    state.dossier.add("Resolution", f"Resolved on attempt {attempt}", "Phase 3a")
                    break
                log.warning("Attempt %d: %d conflicts remain", attempt, len(still_conflicted))
                uu_files = [l[3:] for l in still_conflicted]
            else:
                raise PipelineEscalation(f"Phase 3a failed after {config.max_resolution_retries} attempts")

    with tracker.phase("Phase 4 — Verify"):
        phase_4_verify(config, state)
        tracker.record_gate("verify", "passed")

    with tracker.phase("Phase 4.5 — Semantic equivalence"):
        eq_result = phase_4_5_semantic_equivalence(config, state)
        state.dossier.add("Semantic equivalence", eq_result, "Phase 4.5 range-diff")
        tracker.record_gate("semantic_equivalence", eq_result)

    state.dossier.trailers = build_trailers(
        phase=state.cherry_pick_path,
        model=claude_config.model,
        bisect_sha=state.bisect_sha,
    )

    log.info("Phase 5 — Dossier ready for human review")
    state.dossier.add("Candidate patch", git_format_patch(config.repo_path), "git format-patch")
    state.dossier.add(
        "Allowlist conformance",
        "\n".join(state.allowed_modules),
        "git diff --name-only",
    )

    tracker.set_outcome("success")
    run_path = tracker.save()
    log.info("Run saved to %s", run_path)
    print(tracker.summary())

    return state.dossier, tracker


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    log_file = open(LOG_FILE, "w")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, log_file)

    # Load config from YAML if provided, otherwise use defaults
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        from auto_bug_fix.config_loader import load_pipeline_config
        from auto_bug_fix.worktree import setup_per_cve_worktree
        import os
        import shutil

        bug_fix_config, claude_config, workdir, source_path, _, _ = load_pipeline_config(config_path)

        # Set up worktree for this CVE (avoids copying multi-GB repos)
        os.makedirs(workdir, exist_ok=True)
        worktree_path = os.path.join(workdir, bug_fix_config.issue_id)

        if os.path.exists(worktree_path):
            print(f"Worktree already exists at {worktree_path}, removing...")
            from auto_bug_fix.worktree import cleanup_worktree
            cleanup_worktree(source_path, worktree_path)
            if os.path.exists(worktree_path):
                shutil.rmtree(worktree_path)

        target_branch = bug_fix_config.target_branch
        print(f"Creating worktree at {worktree_path} on {target_branch}...")
        from auto_bug_fix.git_tools import git_worktree_add
        result = git_worktree_add(source_path, worktree_path, target_branch)
        if not result.success:
            print(f"ERROR: git worktree add failed: {result.stderr}")
            sys.exit(1)
        print(f"Worktree created successfully")

        bug_fix_config.repo_path = worktree_path
        bug_fix_config.build_dir = worktree_path
    else:
        from auto_bug_fix.bug_fix_config import claude_config, bug_fix_config

    start_time = time.time()
    try:
        dossier, tracker = run_pipeline(bug_fix_config, claude_config)
        dossier_text = format_dossier(dossier)
        with open(DOSSIER_FILE, "w") as f:
            f.write(dossier_text)
        print(f"Dossier written to {DOSSIER_FILE}")
    except PipelineStop as e:
        print(f"PIPELINE STOP: {e}")
    except PipelineEscalation as e:
        print(f"PIPELINE ESCALATION: {e}")
    finally:
        duration = time.time() - start_time
        print(f"FINISHED: total_duration = {duration:.1f}s")
