"""Run the ported test against fix_commit^ to confirm it exercises the vulnerability."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from auto_bug_fix.git_tools import (
    git_rev_parse,
    git_worktree_add,
    git_worktree_remove,
)


def run_positive_control(
    repo_path: str,
    fix_commit: str,
    fixture_cache: str,
    test_dir: str,
    build_cmd: str,
    test_cmd: str,
    ported_test: str,
    manifest_patch_cmd: str | None = None,
) -> tuple[int, str]:
    """Run the ported test against fix_commit^ to confirm it catches the vulnerability."""
    parent_sha = git_rev_parse(repo_path, f"{fix_commit}^")
    worktree_path = tempfile.mkdtemp(prefix="auto_bug_fix_posctrl_")
    try:
        git_worktree_add(repo_path, worktree_path, parent_sha)
        dest = os.path.join(worktree_path, test_dir)
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(fixture_cache):
            shutil.copy2(
                os.path.join(fixture_cache, name), os.path.join(dest, name),
            )
        if manifest_patch_cmd:
            subprocess.run(
                manifest_patch_cmd, shell=True, cwd=worktree_path, check=False,
            )
        subprocess.run(build_cmd, shell=True, cwd=worktree_path, check=False)
        result = subprocess.run(
            f"{test_cmd} --only {ported_test}",
            shell=True,
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        return (result.returncode, result.stdout + result.stderr)
    finally:
        git_worktree_remove(repo_path, worktree_path)
