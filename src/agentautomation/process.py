"""Bounded subprocess execution with private diagnostics and process-tree cleanup."""

import asyncio
import codecs
import ctypes
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from typing import Callable

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_POLL_SECONDS = 0.05


class ProcessError(Exception):
    def __init__(self, message, kind="failed", *, process=None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.process = process


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _secrets(environment):
    sensitive = re.compile(r"secret|password|passwd|token|api[_-]?key|private[_-]?key|access[_-]?key|credential|authorization|connection[_-]?string", re.I)
    return sorted({value for key, value in environment.items()
                   if sensitive.search(key) and value}, key=len, reverse=True)


def _redact(text, secrets):
    for value in secrets:
        text = text.replace(value, "[REDACTED]")
    return text


def redact_process_output(process: dict, env: dict | None = None) -> dict:
    """Copy process metadata and redact output for presentation or exceptions."""
    secrets = _secrets(os.environ if env is None else env)
    return {**process, **{name: _redact(process[name], secrets)
                         for name in ("stdout", "stderr") if name in process}}


class _WindowsJob:
    """Assign a suspended child before it can spawn, then resume its main thread."""

    def __init__(self):
        from ctypes import wintypes as w

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
            "Thread32Next": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        for name, (args, result) in signatures.items():
            func = getattr(self.api, name)
            func.argtypes, func.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Could not create process job")
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Could not configure process job")

    def assign_and_resume(self, process):
        from ctypes import wintypes as w

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                        ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]

        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Could not contain process tree in a Windows Job Object")
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("Could not locate suspended process thread")
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.api.OpenThread(2, False, entry.th32ThreadID)
                    if not thread:
                        raise OSError("Could not open suspended process thread")
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise OSError("Could not resume process thread")
                    finally:
                        self.api.CloseHandle(thread)
                    return
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Could not locate suspended process thread")
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _run(argv, cwd, timeout, cancelled, log_path, input_text, env, stop, raw_output):
    import json

    started_at = _stamp()
    deadline = time.monotonic() + timeout
    environment = dict(os.environ if env is None else env)
    secrets = _secrets(environment)
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    decoders = {name: codecs.getincrementaldecoder("utf-8")("replace") for name in chunks}
    pending = {name: "" for name in chunks}
    # Retain the potential secret prefix across pipe read boundaries.
    tail = max((len(value) for value in secrets), default=1) - 1
    process = job = None
    failure = None
    workers = []
    messages = queue.Queue(maxsize=16)
    readers_stop = threading.Event()

    def snapshot(*, raw=False):
        def text(name):
            decoded = chunks[name].decode("utf-8", "replace")
            return decoded if raw else _redact(decoded, secrets)

        return {"exit_code": process.returncode if process else None,
                "stdout": text("stdout"), "stderr": text("stderr"),
                "started_at": started_at, "finished_at": _stamp(),
                "pid": process.pid if process else None}

    def terminate():
        if job:
            job.close()
        elif process and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process and process.poll() is None:
            process.kill()
        if process:
            process.wait(timeout=5)

    def send(message):
        while not readers_stop.is_set():
            try:
                messages.put(message, timeout=_POLL_SECONDS)
                return
            except queue.Full:
                pass

    def read_pipe(name, pipe):
        try:
            while data := pipe.read(8192):
                send((name, data))
        except OSError:
            send(("error", None))
        finally:
            send((name, None))

    def write_input():
        try:
            if input_text is not None:
                remaining = memoryview(input_text.encode("utf-8"))
                while remaining:
                    written = process.stdin.write(remaining)
                    if not written:
                        break
                    remaining = remaining[written:]
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Never log argv, stdin or the inherited environment.
        with log_path.open("w", encoding="utf-8") as log:
            def record(event, **fields):
                log.write(json.dumps({"at": _stamp(), "event": event, **fields}, ensure_ascii=False) + "\n")
                log.flush()

            def output(name, data=None, final=False):
                pending[name] += decoders[name].decode(data or b"", final=final)
                # Do not cut through a secret. Replace complete matches before splitting.
                text = _redact(pending[name], secrets)
                cut = len(text) if final else max(0, len(text) - tail)
                # A replacement marker itself must stay intact as well.
                marker = text.rfind("[REDACTED]", 0, cut + len("[REDACTED]"))
                if 0 <= marker < cut < marker + len("[REDACTED]"):
                    cut = marker
                if cut:
                    record(name, text=text[:cut])
                pending[name] = text[cut:]

            try:
                if stop.is_set() or cancelled():
                    raise ProcessError("Process cancelled", "cancelled")
                if time.monotonic() >= deadline:
                    raise ProcessError("Process deadline exceeded", "timed_out")
                options = {"start_new_session": True}
                if os.name == "nt":
                    job = _WindowsJob()
                    options = {"creationflags": 0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP}
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, env=environment, bufsize=0, **options)
                if job:
                    job.assign_and_resume(process)
                record("started", pid=process.pid)
                for name in chunks:
                    worker = threading.Thread(target=read_pipe, args=(name, getattr(process, name)), daemon=True)
                    worker.start()
                    workers.append(worker)
                writer = threading.Thread(target=write_input, daemon=True)
                writer.start()
                workers.append(writer)
                ended = set()
                total = 0
                while len(ended) < 2 or process.poll() is None:
                    if stop.is_set() or cancelled():
                        raise ProcessError("Process cancelled", "cancelled")
                    if time.monotonic() >= deadline:
                        raise ProcessError("Process deadline exceeded", "timed_out")
                    try:
                        name, data = messages.get(timeout=min(_POLL_SECONDS, max(0, deadline - time.monotonic())))
                    except queue.Empty:
                        continue
                    if name == "error":
                        raise ProcessError("Could not read process output")
                    if data is None:
                        ended.add(name)
                        output(name, final=True)
                        continue
                    total += len(data)
                    if total > MAX_OUTPUT_BYTES:
                        raise ProcessError(f"Process output exceeded {MAX_OUTPUT_BYTES} bytes; output is incomplete")
                    chunks[name].extend(data)
                    output(name, data)
                if process.returncode:
                    raise ProcessError(f"Process exited with code {process.returncode}")
            except ProcessError as exc:
                failure = exc
            except Exception:
                # Exceptions may contain credentials in argv/environment, so keep messages generic.
                failure = ProcessError("Process could not be started or monitored; inspect local CLI availability and permissions")
            finally:
                try:
                    terminate()
                except Exception:
                    failure = ProcessError("Process tree cleanup failed")
                readers_stop.set()
                for worker in workers:
                    worker.join(timeout=1)
                # Capture already-read diagnostics on cancellation, without exceeding the cap.
                while not messages.empty():
                    name, data = messages.get_nowait()
                    if name in chunks and data:
                        if sum(map(len, chunks.values())) + len(data) > MAX_OUTPUT_BYTES:
                            if failure is None:
                                failure = ProcessError("Process output limit exceeded; output is incomplete")
                            continue
                        chunks[name].extend(data)
                        output(name, data)
                for name in chunks:
                    if pending[name]:
                        output(name, final=True)
                for name in ("stdin", "stdout", "stderr"):
                    if process:
                        getattr(process, name).close()
                record("finished", exit_code=process.returncode if process else None,
                       kind=failure.kind if failure else "completed",
                       message=failure.message if failure else None)
    except ProcessError:
        raise
    except Exception:
        raise ProcessError("Could not write private process log", process=snapshot()) from None
    if failure:
        failure.process = snapshot()
        raise failure
    return snapshot(raw=raw_output)


async def run_process(argv: list[str], cwd: Path, *, timeout: float,
                      cancelled: Callable[[], bool], log_path: Path,
                      input_text: str | None = None, env: dict | None = None,
                      raw_output: bool = False) -> dict:
    """Run without a shell; return bounded, redacted output, never print it.

    ``env`` replaces the environment when supplied. Logs are JSONL, contain no
    launch arguments or stdin, and redact values of secret-like environment keys.
    Explicit cancellation and asyncio task cancellation both clean up the tree.
    ``raw_output`` is for in-memory protocol parsing only: logs and exceptions
    remain redacted. Callers must sanitize raw output before exposing it.
    """
    if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
        raise ProcessError("argv must contain executable arguments without NUL bytes")
    if not math.isfinite(timeout) or timeout < 0:
        raise ProcessError("timeout must be a finite nonnegative number")
    if os.name == "nt" and Path(argv[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ProcessError("Shell wrappers are unsupported; resolve a native executable or Node entry point")
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run, argv, Path(cwd), timeout, cancelled,
                                               Path(log_path), input_text, env, stop, raw_output))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        try:
            await asyncio.shield(task)
        except ProcessError:
            pass
        raise
