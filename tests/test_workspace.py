"""Real Git contract tests. All commits/worktrees live in temporary repositories."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agentautomation.workspace import (
    COAUTHOR, GitError, commit_workspace, create_workspace, describe_changes,
    fingerprint, inspect_repository, integrate_workspace,
)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "user repo"
        self.root.mkdir()
        self.git(self.root, "init", "-b", "main")
        self.git(self.root, "config", "user.name", "Workspace Test")
        self.git(self.root, "config", "user.email", "test@example.invalid")
        # Test-only configuration makes temporary commits independent of the
        # developer's signing setup; production code never changes signing.
        self.git(self.root, "config", "commit.gpgsign", "false")
        self.git(self.root, "config", "core.autocrlf", "false")
        self.git(self.root, "config", "core.hooksPath", str(self.parent / "no-hooks"))
        (self.root / "tracked.txt").write_text("base\n", encoding="utf-8")
        self.git(self.root, "add", "--all")
        self.git(self.root, "commit", "-m", "Initial baseline")
        self.repository = inspect_repository(self.root, "main")
        self.workspace = None

    def git(self, path, *args, allowed=(0,)):
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(path), *args],
            capture_output=True, timeout=30, shell=False,
            env={key: value for key, value in os.environ.items()
                 if not key.upper().startswith("GIT_")},
        )
        self.assertIn(result.returncode, allowed,
                      result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("utf-8").strip()

    def task(self):
        if self.workspace is None:
            self.workspace = create_workspace(
                self.repository, self.parent / "task workspace", "task/work"
            )
        return Path(self.workspace["path"])

    def commit_task(self, name="new file.txt", content="task bytes\n"):
        path = self.task()
        (path / name).write_text(content, encoding="utf-8")
        return commit_workspace(path, "Task implementation", fingerprint(path))

    def integrate(self, checks=None, callback=None, target=None):
        return integrate_workspace(
            self.repository, self.workspace,
            expected_target=target or self.repository["head"],
            check_commands=checks or [["test-command", "literal argument"]],
            run_check=callback or (lambda command, cwd: {"returncode": 0}),
        )

    def assert_target_unchanged(self, head=None):
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"),
                         head or self.repository["head"])

    def test_inspect_requires_exact_nonbare_root_and_named_local_branch(self):
        self.assertEqual(inspect_repository(str(self.root)), self.repository)
        (self.root / "subdir").mkdir()
        with self.assertRaises(GitError):
            inspect_repository(self.root / "subdir")
        with self.assertRaises(GitError):
            inspect_repository(self.root, "missing")
        self.git(self.root, "branch", "other")
        with self.assertRaises(GitError):
            inspect_repository(self.root, "other")
        self.git(self.root, "checkout", "--detach")
        with self.assertRaises(GitError):
            inspect_repository(self.root)
        bare = self.parent / "bare"
        self.git(self.root, "init", "--bare", str(bare))
        with self.assertRaises(GitError):
            inspect_repository(bare)

    def test_inspect_rejects_dirty_untracked_and_incomplete_operation(self):
        (self.root / "untracked").write_text("untracked")
        with self.assertRaises(GitError):
            inspect_repository(self.root)
        (self.root / "untracked").unlink()
        (self.root / ".git" / "CHERRY_PICK_HEAD").write_text(self.repository["head"])
        with self.assertRaises(GitError):
            inspect_repository(self.root)

    def test_create_workspace_uses_baseline_and_leaves_user_checkout_clean(self):
        path = self.task()
        self.assertEqual(self.workspace["base_sha"], self.repository["head"])
        self.assertEqual(inspect_repository(path)["branch"], "task/work")
        self.assertEqual(inspect_repository(self.root), self.repository)
        self.assertTrue((path / "tracked.txt").is_file())
        with self.assertRaises(GitError):
            create_workspace(self.repository, self.parent / "other", "task/work")
        with self.assertRaises(GitError):
            create_workspace(self.repository, self.root / "nested", "task/nested")

    def test_create_rejects_stale_baseline(self):
        (self.root / "target.txt").write_text("target")
        self.git(self.root, "add", "--all")
        self.git(self.root, "commit", "-m", "Advance target")
        with self.assertRaises(GitError):
            self.task()

    def test_fingerprint_changes_for_untracked_content_rename_and_mode(self):
        path = self.task()
        initial = fingerprint(path)
        file = path / "untracked.txt"
        file.write_text("one")
        one = fingerprint(path)
        file.write_text("two")  # Same length; mtime/size alone are insufficient.
        two = fingerprint(path)
        file.rename(path / "renamed.txt")
        renamed = fingerprint(path)
        self.assertEqual(len({initial, one, two, renamed}), 4)
        tracked = path / "tracked.txt"
        mode = tracked.stat().st_mode
        try:
            tracked.chmod(0o444)
            self.assertNotEqual(fingerprint(path), renamed)
        finally:
            tracked.chmod(mode)

    def test_fingerprint_changes_for_head_index_and_tracked_bytes(self):
        path = self.task()
        original = fingerprint(path)
        (path / "tracked.txt").write_text("changed\n")
        edited = fingerprint(path)
        self.git(path, "add", "--all")
        staged = fingerprint(path)
        self.assertEqual(len({original, edited, staged}), 3)
        sha = commit_workspace(path, "Commit staged work", staged)
        self.assertEqual(sha, self.git(path, "rev-parse", "HEAD"))
        self.assertNotEqual(staged, fingerprint(path))

    def test_describe_contains_untracked_text_binary_and_deleted_files(self):
        path = self.task()
        (path / "tracked.txt").unlink()
        (path / "new file.txt").write_text("complete new content\n", encoding="utf-8")
        (path / "binary.bin").write_bytes(b"\0\x01\xff")
        state = describe_changes(path)
        self.assertEqual(state["files"], ["binary.bin", "new file.txt", "tracked.txt"])
        self.assertIn("complete new content", state["diff"])
        self.assertIn("GIT binary patch", state["diff"])
        self.assertIn("deleted file", state["diff"])
        self.assertEqual(state["head"], self.repository["head"])
        self.assertEqual(len(state["tree_hash"]), 64)

    def test_describe_fails_instead_of_truncating(self):
        path = self.task()
        (path / "new").write_text("x" * 1000)
        with patch("agentautomation.workspace.MAX_DIFF_BYTES", 80):
            with self.assertRaises(GitError):
                describe_changes(path)
        with patch("agentautomation.workspace.MAX_FILE_BYTES", 80):
            with self.assertRaises(GitError):
                fingerprint(path)

    def test_stale_candidate_cannot_commit(self):
        path = self.task()
        (path / "new.txt").write_text("reviewed")
        approved = fingerprint(path)
        (path / "new.txt").write_text("changed!")
        with self.assertRaisesRegex(GitError, "Stale"):
            commit_workspace(path, "Stale candidate", approved)
        self.assertEqual(self.git(path, "rev-parse", "HEAD"), self.repository["head"])
        self.assertEqual(self.git(path, "diff", "--cached", "--name-only"), "")

    def test_empty_commit_rejected_and_real_commit_has_attribution(self):
        path = self.task()
        with self.assertRaises(GitError):
            commit_workspace(path, "Empty", fingerprint(path))
        sha = self.commit_task()
        self.assertEqual(inspect_repository(path)["head"], sha)
        self.assertIn(COAUTHOR, self.git(path, "log", "-1", "--format=%B"))
        self.assertEqual((path / "new file.txt").read_text(), "task bytes\n")
        self.assertEqual(inspect_repository(self.root), self.repository)

    def test_commit_runs_hooks_without_bypass(self):
        path = self.task()
        hooks = self.parent / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git(path, "config", "core.hooksPath", str(hooks))
        (path / "new").write_text("change")
        with self.assertRaises(GitError):
            commit_workspace(path, "Rejected by hook", fingerprint(path))
        self.assertEqual(self.git(path, "rev-parse", "HEAD"), self.repository["head"])

    def test_rejects_hidden_index_entries_and_gitlinks(self):
        path = self.task()
        self.git(path, "update-index", "--assume-unchanged", "tracked.txt")
        with self.assertRaises(GitError):
            fingerprint(path)
        self.git(path, "update-index", "--no-assume-unchanged", "tracked.txt")
        self.git(path, "update-index", "--add", "--cacheinfo",
                 f"160000,{self.repository['head']},submodule")
        with self.assertRaises(GitError):
            fingerprint(path)

    def test_rejects_symlink_escape_and_nested_repo(self):
        path = self.task()
        outside = self.parent / "secret"
        outside.write_text("external")
        try:
            (path / "link").symlink_to(outside)
        except OSError:
            pass  # Windows may require developer mode or symlink privilege.
        else:
            with self.assertRaises(GitError):
                fingerprint(path)
            (path / "link").unlink()
        nested = path / "nested"
        nested.mkdir()
        self.git(nested, "init")
        (nested / "file").write_text("nested file")
        with self.assertRaises(GitError):
            fingerprint(path)

    def test_dirty_target_blocks_update_before_any_checks(self):
        self.commit_task()
        (self.root / "user work").write_text("keep me")
        calls = []
        with self.assertRaises(GitError):
            self.integrate(callback=lambda command, cwd: calls.append(cwd))
        self.assertEqual(calls, [])
        self.assert_target_unchanged()
        self.assertEqual((self.root / "user work").read_text(), "keep me")

    def test_conflict_does_not_update_target_and_retains_integration(self):
        self.commit_task("tracked.txt", "task version\n")
        (self.root / "tracked.txt").write_text("target version\n")
        self.git(self.root, "add", "--all")
        self.git(self.root, "commit", "-m", "Target diverged")
        target = self.git(self.root, "rev-parse", "HEAD")
        result = self.integrate(target=target)
        self.assertEqual(result["status"], "conflict")
        self.assert_target_unchanged(target)
        integration = Path(result["integration_path"])
        self.assertTrue(integration.is_dir())
        self.assertIn("<<<<<<<", (integration / "tracked.txt").read_text())
        self.assertEqual((self.root / "tracked.txt").read_text(), "target version\n")
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    def test_failed_check_does_not_update_target(self):
        self.commit_task()
        result = self.integrate(callback=lambda command, cwd: {"returncode": 1})
        self.assertEqual(result["status"], "check_failed")
        self.assert_target_unchanged()
        self.assertTrue((Path(result["integration_path"]) / "new file.txt").is_file())
        self.assertFalse((self.root / "new file.txt").exists())

    def test_check_exception_retains_integration(self):
        self.commit_task()
        def failed(command, cwd):
            raise RuntimeError("test runner failed")
        result = self.integrate(callback=failed)
        self.assertEqual(result["status"], "check_failed")
        self.assertIn("test runner failed", result["error"])
        self.assert_target_unchanged()
        self.assertTrue(Path(result["integration_path"]).is_dir())

    def test_target_dirtied_during_checks_is_preserved_and_blocks_update(self):
        self.commit_task()
        def dirty(command, cwd):
            (self.root / "user work").write_text("concurrent edit")
            return {"returncode": 0}
        with self.assertRaises(GitError) as raised:
            self.integrate(callback=dirty)
        self.assertTrue(Path(raised.exception.integration_path).is_dir())
        self.assert_target_unchanged()
        self.assertEqual((self.root / "user work").read_text(), "concurrent edit")

    def test_target_moved_during_checks_blocks_update(self):
        self.commit_task()
        target = []
        def advance(command, cwd):
            (self.root / "concurrent").write_text("other change")
            self.git(self.root, "add", "--all")
            self.git(self.root, "commit", "-m", "Concurrent target change")
            target.append(self.git(self.root, "rev-parse", "HEAD"))
            return {"exit_code": 0}
        with self.assertRaisesRegex(GitError, "Target HEAD changed"):
            self.integrate(callback=advance)
        self.assert_target_unchanged(target[0])
        self.assertFalse((self.root / "new file.txt").exists())

    def test_check_mutation_cannot_change_verified_candidate(self):
        self.commit_task()
        def mutate(command, cwd):
            (cwd / "new file.txt").write_text("changed by check")
            return {"returncode": 0}
        with self.assertRaises(GitError):
            self.integrate(callback=mutate)
        self.assert_target_unchanged()

    def test_successful_integration_checks_isolated_files_and_ff_only_target(self):
        task_sha = self.commit_task()
        calls = []
        def check(command, cwd):
            self.assertNotEqual(cwd, self.root)
            self.assertNotEqual(cwd, Path(self.workspace["path"]))
            self.assertEqual((cwd / "new file.txt").read_text(), "task bytes\n")
            self.assertFalse((self.root / "new file.txt").exists())
            self.assert_target_unchanged()
            calls.append((command, cwd))
            return {"returncode": 0, "stdout": "checked real file"}
        result = self.integrate(callback=check)
        self.assertEqual(result["status"], "integrated")
        self.assertEqual(result["task_sha"], task_sha)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["target_sha"], self.git(self.root, "rev-parse", "HEAD"))
        self.assertEqual((self.root / "new file.txt").read_text(), "task bytes\n")
        self.assertEqual(inspect_repository(self.root)["head"], result["target_sha"])
        self.assertIn(task_sha, self.git(self.root, "rev-list", "HEAD").splitlines())
        self.assertEqual(self.git(self.root, "branch", "--show-current"), "main")

    def test_clean_diverged_target_integrates_both_actual_files(self):
        self.commit_task()
        (self.root / "target-only.txt").write_text("target change")
        self.git(self.root, "add", "--all")
        self.git(self.root, "commit", "-m", "Target independent change")
        current = self.git(self.root, "rev-parse", "HEAD")
        result = self.integrate(target=current)
        self.assertEqual(result["status"], "integrated")
        self.assertEqual((self.root / "target-only.txt").read_text(), "target change")
        self.assertEqual((self.root / "new file.txt").read_text(), "task bytes\n")

    def test_ignored_test_cache_does_not_change_evidence(self):
        path = self.task()
        (path / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
        before = fingerprint(path)
        cache = path / "__pycache__"
        cache.mkdir()
        (cache / "test.pyc").write_bytes(b"test cache")
        self.assertEqual(fingerprint(path), before)
        self.assertNotIn("__pycache__/test.pyc", describe_changes(path)["files"])

    def test_cancel_during_check_blocks_target_update(self):
        self.commit_task()
        cancelled = []
        def check(command, cwd):
            cancelled.append(True)
            return {"returncode": 0}
        with self.assertRaisesRegex(GitError, "cancelled") as raised:
            integrate_workspace(
                self.repository, self.workspace,
                expected_target=self.repository["head"],
                check_commands=[["check"]], run_check=check,
                cancelled=lambda: bool(cancelled),
            )
        self.assert_target_unchanged()
        self.assertTrue(Path(raised.exception.integration_path).is_dir())

    def test_hook_mutation_reports_created_commit_as_failure(self):
        path = self.task()
        hooks = self.parent / "mutating-hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf 'hook content' > new.txt\ngit add new.txt\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        self.git(path, "config", "core.hooksPath", str(hooks))
        (path / "new.txt").write_text("reviewed content")
        with self.assertRaisesRegex(GitError, "was created but verification failed"):
            commit_workspace(path, "Candidate", fingerprint(path))
        self.assertNotEqual(self.git(path, "rev-parse", "HEAD"), self.repository["head"])
        self.assertEqual((path / "new.txt").read_text(), "hook content")
        self.assert_target_unchanged()

    def test_integration_hook_cannot_rewrite_approved_task_contents(self):
        self.commit_task()
        hooks = self.parent / "integration-hooks"
        hooks.mkdir()
        hook = hooks / "pre-merge-commit"
        hook.write_text("#!/bin/sh\nprintf 'unapproved' > injected.txt\ngit add injected.txt\n",
                        encoding="utf-8")
        hook.chmod(0o755)
        self.git(self.root, "config", "core.hooksPath", str(hooks))
        with self.assertRaises(GitError) as raised:
            self.integrate()
        self.assert_target_unchanged()
        self.assertTrue((Path(raised.exception.integration_path) / "injected.txt").exists())
        self.assertFalse((self.root / "injected.txt").exists())

    def test_precancelled_commit_does_not_stage_or_commit(self):
        path = self.task()
        (path / "new.txt").write_text("reviewed")
        candidate = fingerprint(path)
        with self.assertRaisesRegex(GitError, "cancelled"):
            commit_workspace(path, "Cancelled", candidate, cancelled=lambda: True)
        self.assertEqual(self.git(path, "rev-parse", "HEAD"), self.repository["head"])
        self.assertEqual(self.git(path, "diff", "--cached", "--name-only"), "")

    def test_deleted_file_can_be_committed_with_stable_content_evidence(self):
        path = self.task()
        (path / "tracked.txt").unlink()
        sha = commit_workspace(path, "Delete tracked file", fingerprint(path))
        self.assertEqual(inspect_repository(path)["head"], sha)
        self.assertEqual(self.git(path, "ls-files"), "")

    def test_subprocess_timeout_is_reported_as_giterror(self):
        with patch("agentautomation.workspace.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["git"], 120)):
            with self.assertRaises(GitError):
                fingerprint(self.root)


if __name__ == "__main__":
    unittest.main()
