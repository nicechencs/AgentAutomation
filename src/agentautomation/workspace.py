"""Synchronous Git operations with content evidence and isolated integration.

Failed worktrees are deliberately retained for inspection. No operation resets,
cleans, removes a worktree, disables hooks, or overrides commit signing.
"""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from typing import Callable
import uuid

GIT_TIMEOUT = 120
MAX_GIT_OUTPUT = 16 * 1024 * 1024
MAX_DIFF_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
COAUTHOR = "Co-Authored-By: Claude Code <noreply@anthropic.com>"
_PENDING = (
    "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
    "rebase-apply", "rebase-merge", "sequencer", "BISECT_LOG",
    "AUTO_MERGE", "MERGE_AUTOSTASH", "index.lock", "HEAD.lock",
)


class GitError(Exception):
    """Git validation/operation failure; integration_path identifies retained work."""

    def __init__(self, message: str, *, integration_path: str | None = None):
        super().__init__(message)
        self.message = message
        self.integration_path = integration_path


def _git(path: Path, *args: str, allowed: tuple[int, ...] = (0,),
         limit: int = MAX_GIT_OUTPUT) -> bytes:
    # Repository/index/config injection through the runner's environment must not
    # redirect an operation into a different checkout. Normal Git config remains.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    argv = ["git", "--no-pager", "--literal-pathspecs", "-C", str(path), *args]
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(argv, stdout=stdout, stderr=stderr, env=env,
                                    stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT,
                                    shell=False, check=False)
            stdout.seek(0)
            output = stdout.read(limit + 1)
            stderr.seek(0)
            errors = stderr.read(limit + 1)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Git {args[0] if args else 'command'} failed: {exc}") from exc
    if len(output) > limit or len(errors) > limit:
        raise GitError("Git output exceeds the evidence limit; output was not truncated")
    if result.returncode not in allowed:
        detail = (errors or output).decode("utf-8", errors="replace").strip()
        raise GitError(f"Git {args[0]} failed ({result.returncode}): {detail}")
    return output


def _text(path: Path, *args: str) -> str:
    try:
        return _git(path, *args).decode("utf-8").strip()
    except UnicodeError as exc:
        raise GitError("Git metadata is not valid UTF-8") from exc


def _branch_name(path: Path, name: str) -> None:
    if not isinstance(name, str) or not name or name.startswith("-"):
        raise GitError("A local named branch is required")
    _git(path, "check-ref-format", f"refs/heads/{name}")


def _head(path: Path) -> str:
    return _text(path, "rev-parse", "--verify", "HEAD^{commit}")


def _git_path(path: Path, name: str) -> Path:
    found = Path(_text(path, "rev-parse", "--git-path", name))
    return found if found.is_absolute() else path / found


def _no_pending(path: Path) -> None:
    git_dir = Path(_text(path, "rev-parse", "--absolute-git-dir"))
    for marker in _PENDING:
        if (git_dir / marker).exists():
            raise GitError(f"Unfinished Git operation or lock: {marker}")
    if _git(path, "ls-files", "--unmerged", "-z"):
        raise GitError("Unresolved Git conflicts")
    if _git(path, "for-each-ref", "--format=%(refname)", "refs/replace/"):
        raise GitError("Git replacement objects cannot be safely verified")
    if _git_path(path, "info/grafts").exists():
        raise GitError("Git grafts cannot be safely verified")


def _root(path: str | Path, *, named: bool = True) -> dict:
    try:
        root = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GitError(f"Repository path does not exist: {path}") from exc
    if not root.is_dir() or _text(root, "rev-parse", "--is-bare-repository") != "false":
        raise GitError("A non-bare Git worktree root is required")
    actual = Path(_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise GitError("Path must be the Git worktree root, not a subdirectory")
    ref = _git(root, "symbolic-ref", "--quiet", "HEAD", allowed=(0, 1)).decode().strip()
    branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else None
    if named and branch is None:
        raise GitError("Detached HEAD is not a local named branch")
    if branch is not None:
        _branch_name(root, branch)
        _git(root, "show-ref", "--verify", f"refs/heads/{branch}")
    _no_pending(root)
    return {"root": str(root), "branch": branch, "head": _head(root)}


def _clean(path: Path) -> None:
    if _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignore-submodules=none"):
        raise GitError("Git worktree must be clean, including untracked files")


def _records(data: bytes) -> list[str]:
    try:
        return [item.decode("utf-8") for item in data.split(b"\0") if item]
    except UnicodeError as exc:
        raise GitError("Non-UTF-8 file paths cannot be safely verified") from exc


def _safe_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or "\\" in name
            or any(part in {".", ".."} or part.casefold() == ".git" or ":" in part
                   for part in relative.parts)):
        raise GitError(f"Unsafe Git file path: {name!r}")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            # A deleted tracked path (or a deleted parent) is valid evidence.
            continue
        except OSError as exc:
            raise GitError(f"Cannot inspect {name!r}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise GitError(f"Symlinks/reparse points cannot be safely verified: {name}")
        if current != root and current.is_dir() and (current / ".git").exists():
            raise GitError(f"Nested Git repository cannot be safely verified: {name}")
    try:
        current.resolve().relative_to(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise GitError(f"File escapes the worktree: {name}") from exc
    return current


def _pack(digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _snapshot(path: str | Path, *, named: bool = True) -> dict:
    repository = _root(path, named=named)
    root = Path(repository["root"])
    index = _git(root, "ls-files", "--stage", "-z")
    names = set()
    for record in _records(index):
        metadata, name = record.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0" or mode not in {"100644", "100755"}:
            raise GitError(f"Submodule, symlink or conflicted index entry: {name}")
        names.add(name)
    for record in _records(_git(root, "ls-tree", "-r", "-z", "HEAD")):
        metadata, name = record.split("\t", 1)
        if metadata.split()[0] not in {"100644", "100755"}:
            raise GitError(f"Submodules and symlinks cannot be safely verified: {name}")
        names.add(name)
    for record in _records(_git(root, "ls-files", "-v", "-z")):
        if not record.startswith("H "):
            raise GitError("Skip-worktree/assume-unchanged index entries are unsupported")
    untracked = _records(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    names.update(untracked)
    digest = hashlib.sha256()
    total = 0
    for name in sorted(names):
        file = _safe_file(root, name)
        try:
            before = file.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GitError(f"Cannot stat {name}: {exc}") from exc
        if not stat.S_ISREG(before.st_mode):
            raise GitError(f"Only regular files can be verified: {name}")
        _pack(digest, name.encode("utf-8"))
        if before.st_size > MAX_FILE_BYTES or total + before.st_size > MAX_SNAPSHOT_BYTES:
            raise GitError("File content exceeds the evidence limit")
        try:
            with file.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
                opened = os.fstat(stream.fileno())
            after = file.lstat()
        except OSError as exc:
            raise GitError(f"Cannot read {name}: {exc}") from exc
        # Windows Python reports creation time for path st_ctime but change time
        # for handle st_ctime; compare that field only between path observations.
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_mode)
        if (identity(before) != identity(opened) or identity(before) != identity(after)
                or before.st_ctime_ns != after.st_ctime_ns
                or len(data) != before.st_size):
            raise GitError(f"File changed while collecting evidence: {name}")
        total += len(data)
        if total > MAX_SNAPSHOT_BYTES:
            raise GitError("File content exceeds the evidence limit")
        _pack(digest, str(stat.S_IMODE(before.st_mode)).encode("ascii"))
        _pack(digest, data)
    tree_hash = digest.hexdigest()
    evidence = hashlib.sha256()
    for value in (repository["head"].encode("ascii"), tree_hash.encode("ascii"), index):
        _pack(evidence, value)
    # Catch concurrent additions, deletions, staging, or branch movement too.
    if (_git(root, "ls-files", "--stage", "-z") != index
            or _records(_git(root, "ls-files", "--others", "--exclude-standard", "-z")) != untracked
            or _head(root) != repository["head"]):
        raise GitError("Git candidate changed while collecting evidence")
    return {**repository, "tree_hash": tree_hash,
            "fingerprint": evidence.hexdigest(), "untracked": untracked}


def inspect_repository(path: str | Path, target_branch: str | None = None) -> dict:
    """Require an existing, clean, named local checkout at exactly its root."""
    state = _snapshot(path)
    root = Path(state["root"])
    if target_branch is not None:
        _branch_name(root, target_branch)
        _git(root, "show-ref", "--verify", f"refs/heads/{target_branch}")
        if state["branch"] != target_branch:
            raise GitError("Target branch must be currently checked out in the repository")
    _clean(root)
    return {key: state[key] for key in ("root", "branch", "head")}


def create_workspace(repository: dict, path: Path, branch: str) -> dict:
    """Create a new task branch/worktree at the inspected immutable baseline."""
    current = inspect_repository(repository["root"], repository["branch"])
    if current != repository:
        raise GitError("Repository changed since inspection")
    root = Path(current["root"])
    _branch_name(root, branch)
    destination = Path(path).absolute()
    if destination.exists() or destination.is_symlink():
        raise GitError("Workspace destination already exists")
    try:
        destination.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise GitError("Workspace must be outside the user's repository")
    _git(root, "worktree", "add", "-b", branch, str(destination), current["head"])
    state = inspect_repository(destination, branch)
    if state["head"] != current["head"]:
        raise GitError("New workspace does not match the baseline")
    return {"path": state["root"], "branch": branch, "base_sha": current["head"]}


def fingerprint(path: str | Path) -> str:
    """SHA-256 evidence of HEAD, index, paths, modes and actual file bytes."""
    return _snapshot(path)["fingerprint"]


def describe_changes(path: str | Path) -> dict:
    """Return a bounded binary-capable diff including complete untracked content.

    tree_hash is SHA-256 of actual paths/modes/bytes, not a Git object ID. Every
    limit is fail-closed; callers never receive a silently shortened diff.
    """
    before = _snapshot(path)
    root = Path(before["root"])
    diff = _git(root, "diff", "--binary", "--full-index", "--no-ext-diff",
                "--no-textconv", "HEAD", "--", limit=MAX_DIFF_BYTES)
    files = set(_records(_git(root, "diff", "--name-only", "-z", "HEAD", "--")))
    for name in before["untracked"]:
        addition = _git(root, "diff", "--no-index", "--binary", "--full-index",
                        "--no-ext-diff", "--no-textconv", "--", "/dev/null", name,
                        allowed=(0, 1), limit=MAX_DIFF_BYTES)
        if len(diff) + len(addition) > MAX_DIFF_BYTES:
            raise GitError("Diff exceeds the evidence limit; output was not truncated")
        diff += addition
        files.add(name)
    # Content evidence reads every tracked file even where Git's stat cache or
    # a clean filter would conceal raw byte changes. Never call that 'no diff'.
    if not files and _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise GitError("Git reports changes without a reviewable content diff")
    rendered = diff.decode("utf-8", errors="backslashreplace")
    if len(rendered.encode("utf-8")) > MAX_DIFF_BYTES:
        raise GitError("Rendered diff exceeds the evidence limit")
    if _snapshot(root)["fingerprint"] != before["fingerprint"]:
        raise GitError("Candidate changed while describing changes")
    return {"head": before["head"], "tree_hash": before["tree_hash"],
            "diff": rendered, "files": sorted(files)}


def _message(message: str) -> str:
    if not isinstance(message, str) or not message.strip() or "\0" in message:
        raise GitError("A nonempty commit message is required")
    return message.rstrip() + ("\n" if message.rstrip().endswith(COAUTHOR)
                               else f"\n\n{COAUTHOR}\n")


def commit_workspace(path: str | Path, message: str, expected_fingerprint: str, *,
                     cancelled: Callable[[], bool] | None = None) -> str:
    """Commit only the candidate whose complete evidence was approved."""
    def ensure_active() -> None:
        if cancelled is not None and cancelled():
            raise GitError("Commit cancelled or timed out")

    ensure_active()
    message = _message(message)
    before = _snapshot(path)
    root = Path(before["root"])
    if before["fingerprint"] != expected_fingerprint:
        raise GitError("Stale candidate fingerprint; review/approval must be renewed")
    if not describe_changes(root)["files"]:
        raise GitError("Workspace has no changes to commit")
    if _snapshot(root)["fingerprint"] != expected_fingerprint:
        raise GitError("Candidate changed before staging")
    ensure_active()
    _git(root, "add", "--all")
    staged = _snapshot(root)
    if staged["tree_hash"] != before["tree_hash"] or staged["head"] != before["head"]:
        raise GitError("Candidate changed during staging")
    tree = _text(root, "write-tree")
    if tree == _text(root, "rev-parse", "HEAD^{tree}"):
        raise GitError("Workspace has no nonempty Git changes to commit")
    ensure_active()
    _git(root, "commit", "-m", message)
    committed = _head(root)
    try:
        result = inspect_repository(root, before["branch"])
        if (_text(root, "rev-parse", "HEAD^{tree}") != tree
                or _text(root, "rev-list", "--parents", "-n", "1", "HEAD").split()
                != [result["head"], before["head"]]
                or _snapshot(root)["tree_hash"] != before["tree_hash"]
                or COAUTHOR not in _text(root, "log", "-1", "--format=%B").splitlines()):
            raise GitError("Commit hooks changed the approved candidate or ancestry")
    except GitError as exc:
        raise GitError(f"Commit {committed} was created but verification failed; "
                       f"workspace retained, do not integrate: {exc}") from exc
    return result["head"]


def _same_target(repository: dict, expected_target: str) -> dict:
    current = inspect_repository(repository["root"], repository["branch"])
    if current["head"] != expected_target:
        raise GitError("Target HEAD changed since integration approval")
    return current


def _check_passed(result: dict) -> bool:
    if not isinstance(result, dict) or result.get("timed_out") or result.get("error"):
        return False
    if result.get("status") in {"failed", "cancelled", "timed_out", "timeout", "error"}:
        return False
    if result.get("passed") is False:
        return False
    for field in ("returncode", "exit_code"):
        if field in result:
            return type(result[field]) is int and result[field] == 0
    return result.get("status") == "passed" or result.get("passed") is True


def integrate_workspace(repository: dict, workspace: dict, *, expected_target: str,
                        check_commands: list[list[str]],
                        run_check: Callable[[list[str], Path], dict],
                        cancelled: Callable[[], bool] | None = None,
                        update_lock=None, on_updated: Callable[[dict], None] | None = None) -> dict:
    """After integration approval, validate a merge in a separate worktree.

    expected_target is the full approved target HEAD SHA. Conflict/check failure
    returns a status and retained integration_path. Invalid/stale state raises
    GitError. On success the original checkout advances by ff-only merge.
    """
    def ensure_active() -> None:
        if cancelled is not None and cancelled():
            raise GitError("Integration cancelled or timed out; target update is forbidden")

    ensure_active()
    if not isinstance(check_commands, list) or any(
        not isinstance(command, list) or not command
        or any(not isinstance(arg, str) or "\0" in arg for arg in command)
        for command in check_commands
    ):
        raise GitError("Checks must be nonempty argv lists")
    target = _same_target(repository, expected_target)
    root = Path(target["root"])
    task = inspect_repository(workspace["path"], workspace["branch"])
    if workspace.get("commit_sha") is not None and task["head"] != workspace["commit_sha"]:
        raise GitError("Task HEAD does not match the approved task commit")
    task_root = Path(task["root"])
    common = (root / _text(root, "rev-parse", "--git-common-dir")).resolve()
    task_common = (task_root / _text(task_root, "rev-parse", "--git-common-dir")).resolve()
    if common != task_common:
        raise GitError("Task workspace belongs to a different Git repository")
    if workspace["base_sha"] != repository["head"]:
        raise GitError("Task baseline does not match the inspected repository")
    _git(task_root, "merge-base", "--is-ancestor", workspace["base_sha"], task["head"])
    integration = task_root.with_name(f"{task_root.name}-integration-{uuid.uuid4().hex[:12]}")
    result = {"status": "integrated", "target_sha": target["head"],
              "integration_path": str(integration), "task_sha": task["head"], "checks": []}
    try:
        ensure_active()
        _git(root, "worktree", "add", "--detach", str(integration), target["head"])
        try:
            ensure_active()
            # Compute the Git merge tree independently of merge/commit hooks.
            # A hook may alter the index and still exit successfully.
            merged_tree = _git(integration, "merge-tree", "--write-tree",
                               target["head"], task["head"], allowed=(0, 1)).decode(
                                   "utf-8", errors="replace").splitlines()[0]
            ensure_active()
            _git(integration, "-c", "merge.autostash=false", "merge", "--no-ff",
                 "--no-edit", "-m", _message("Integrate approved task workspace"), task["head"])
            if _text(integration, "rev-parse", "HEAD^{tree}") != merged_tree:
                raise GitError("Integration hooks changed the expected merge tree")
        except GitError as exc:
            if _git(integration, "ls-files", "--unmerged", "-z"):
                return {**result, "status": "conflict", "error": str(exc)}
            raise
        candidate = _snapshot(integration, named=False)
        _clean(integration)
        for command in check_commands:
            ensure_active()
            try:
                check = run_check(list(command), integration)
            except Exception as exc:
                return {**result, "status": "check_failed", "error": str(exc)}
            result["checks"].append(check)
            if not _check_passed(check):
                return {**result, "status": "check_failed", "error": "Integration check failed"}
        after = _snapshot(integration, named=False)
        _clean(integration)
        if after["fingerprint"] != candidate["fingerprint"]:
            raise GitError("Checks changed the integration candidate")
        # Revalidate the task as well: approval cannot silently follow new work.
        if inspect_repository(task_root, workspace["branch"])["head"] != task["head"]:
            raise GitError("Task HEAD changed during integration checks")
        with update_lock if update_lock is not None else nullcontext():
            _same_target(repository, expected_target)
            ensure_active()
            _git(root, "-c", "merge.autostash=false", "merge", "--ff-only", "--no-edit",
                 candidate["head"])
            final = inspect_repository(root, repository["branch"])
            if final["head"] != candidate["head"]:
                raise GitError("Target did not advance to the verified integration commit")
            completed = {**result, "target_sha": final["head"], "integration_sha": candidate["head"]}
            if on_updated is not None:
                on_updated(completed)
            return completed
    except GitError as exc:
        exc.integration_path = str(integration)
        raise
