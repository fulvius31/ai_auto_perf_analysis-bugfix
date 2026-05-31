"""Create test-fixture caches, generate bisect wrapper scripts, and drive git bisect run."""
from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile

from auto_bug_fix.git_tools import (
    git_bisect_reset,
    git_bisect_run,
    git_bisect_start,
    git_log_count,
)


def create_fixture_cache(test_files: list[str]) -> str:
    cache_dir = tempfile.mkdtemp(prefix="auto_bug_fix_fixtures_")
    for path in test_files:
        shutil.copy2(path, os.path.join(cache_dir, os.path.basename(path)))
    return cache_dir


def create_bisect_wrapper(
    fixture_cache: str,
    test_dir: str,
    build_cmd: str,
    test_cmd: str,
    ported_test: str,
    manifest_patch_cmd: str | None = None,
) -> str:
    script_dir = os.path.dirname(fixture_cache)
    script_path = os.path.join(script_dir, "bisect_wrapper.sh")
    content = (
        "#!/bin/bash\n"
        f'for f in "{fixture_cache}"/*; do\n'
        f'    cp "$f" "{test_dir}/$(basename "$f")"\n'
        "done\n"
        f"{manifest_patch_cmd or 'true'}\n"
        f"{build_cmd} || exit 125\n"
        f"{test_cmd} --only {ported_test}\n"
    )
    with open(script_path, "w") as fh:
        fh.write(content)
    os.chmod(script_path, 0o755)
    return script_path


def run_bisect(
    repo_path: str,
    bad_ref: str,
    good_ref: str,
    wrapper_path: str,
    max_commits: int = 200,
) -> str | None:
    count = git_log_count(repo_path, f"{good_ref}..{bad_ref}")
    if count > max_commits:
        return None
    try:
        git_bisect_start(repo_path, bad_ref, good_ref)
        result = git_bisect_run(repo_path, wrapper_path)
        match = re.search(r"([0-9a-f]{40}).*is the first bad commit", result.stdout)
        if match:
            return match.group(1)
        return None
    finally:
        git_bisect_reset(repo_path)
