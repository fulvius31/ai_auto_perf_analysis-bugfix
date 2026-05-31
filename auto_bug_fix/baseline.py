"""Test suite baseline capture and regression detection for the verify phase."""
from __future__ import annotations

import re
import subprocess


def run_test_suite(test_command: str, cwd: str) -> tuple[int, str, str]:
    result = subprocess.run(
        test_command, shell=True, cwd=cwd, capture_output=True, text=True,
    )
    return (result.returncode, result.stdout, result.stderr)


def parse_test_failures(stdout: str, stderr: str) -> set[str]:
    failures: set[str] = set()
    combined = stdout + "\n" + stderr
    for line in combined.splitlines():
        # FAIL: test_name or FAILED test_name
        m = re.match(r"^\s*FAIL:\s+(\S+)", line)
        if m:
            failures.add(m.group(1))
            continue
        m = re.match(r"^\s*FAILED\s+(\S+)", line)
        if m:
            failures.add(m.group(1))
            continue
        # Go style: FAIL  test_name (with two or more spaces or a tab)
        m = re.match(r"^\s*FAIL\s{2,}(\S+)", line)
        if m:
            failures.add(m.group(1))
            continue
        m = re.match(r"^\s*FAIL\t(\S+)", line)
        if m:
            failures.add(m.group(1))
            continue
        # TAP format: not ok \d+ - test_name
        m = re.match(r"^\s*not ok\s+\d+\s+-\s+(\S+)", line)
        if m:
            failures.add(m.group(1))
            continue
    return failures


def capture_baseline(test_command: str, cwd: str) -> set[str]:
    _, stdout, stderr = run_test_suite(test_command, cwd)
    return parse_test_failures(stdout, stderr)


def check_regression(
    failures: set[str], baseline: set[str],
) -> tuple[bool, set[str]]:
    new_failures = failures - baseline
    return (len(new_failures) == 0, new_failures)
