"""Tests for auto_bug_fix.bug_fix_prompts — Phase 0-5 prompt class rendering."""
from auto_bug_fix.bug_fix_prompts import (
    TriageAgentPrompt,
    CherryPickAgentPrompt,
    NarrowResolutionAgentPrompt,
    SemanticEquivalencePrompt,
    FixAgentPrompt,
)


def test_triage_prompt_contains_context():
    prompt = TriageAgentPrompt(
        context="<context>test</context>",
        seed_files=["lib/url.c"],
        allowed_modules=["lib/"],
        output_file="triage.txt",
    )
    text = prompt.prompt()
    assert "lib/url.c" in text


def test_cherry_pick_prompt_contains_commit():
    prompt = CherryPickAgentPrompt(
        context="<context>test</context>",
        fix_commit="abc123",
        strategies_log_file="strats.txt",
    )
    text = prompt.prompt()
    assert "abc123" in text


def test_narrow_resolution_prompt_lists_files():
    conflicted = ["lib/url.c", "lib/parser.c"]
    prompt = NarrowResolutionAgentPrompt(
        context="<context>test</context>",
        fix_diff="--- a/lib/url.c\n+++ b/lib/url.c",
        conflicted_files=conflicted,
        allowed_modules=["lib/"],
    )
    text = prompt.prompt()
    for f in conflicted:
        assert f in text, f"{f} should appear in the prompt output"


def test_semantic_equivalence_prompt():
    prompt = SemanticEquivalencePrompt(
        range_diff_output="1:  abc = 1:  def  some change",
        upstream_patch="upstream diff content",
        ported_patch="ported diff content",
    )
    text = prompt.prompt()
    assert "1:  abc = 1:  def  some change" in text


def test_fix_agent_prompt_shows_iteration():
    prompt = FixAgentPrompt(
        context="<context>test</context>",
        build_output="error: undefined reference",
        test_output="FAIL: test_foo",
        allowed_modules=["lib/"],
        allowed_seed=["lib/url.c"],
        iteration=2,
        max_retries=3,
    )
    text = prompt.prompt()
    assert "2/3" in text
