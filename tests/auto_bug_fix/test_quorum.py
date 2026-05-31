"""Tests for auto_bug_fix.quorum — vote parsing and tally logic."""
from auto_bug_fix.quorum import Vote, QuorumResult, parse_vote, tally_votes


def test_parse_vote_yes():
    assert parse_vote("Vote: YES\nBecause the signatures match") == Vote.YES


def test_parse_vote_no():
    assert parse_vote("Vote: NO\nBecause the code paths diverge") == Vote.NO


def test_parse_vote_abstain():
    assert parse_vote("Vote: ABSTAIN") == Vote.ABSTAIN


def test_parse_vote_unrecognized():
    assert parse_vote("Some random text") == Vote.ABSTAIN


def test_tally_unanimous_yes():
    responses = [
        "Vote: YES\nAll signatures match.",
        "Vote: YES\nCall graph confirms.",
        "Vote: YES\nAdvisory aligns.",
    ]
    result = tally_votes(responses, require_unanimity=True)
    assert isinstance(result, QuorumResult)
    assert result.decision == "proceed"


def test_tally_one_no():
    responses = [
        "Vote: YES\nLooks good.",
        "Vote: YES\nConfirmed.",
        "Vote: NO\nSignatures diverge.",
    ]
    result = tally_votes(responses, require_unanimity=True)
    assert result.decision == "escalate"


def test_tally_majority_mode():
    responses = [
        "Vote: YES\nLooks good.",
        "Vote: YES\nConfirmed.",
        "Vote: NO\nSignatures diverge.",
    ]
    result = tally_votes(responses, require_unanimity=False)
    assert result.decision == "proceed"
