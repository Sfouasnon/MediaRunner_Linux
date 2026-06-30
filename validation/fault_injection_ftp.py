#!/usr/bin/env python3
"""Fault-injection FTP server for MediaRunner field-stress validation.

A local pyftpdlib server that misbehaves on purpose so the retry / resume /
reconnect engine can be proven automatically instead of by yanking cables:

  - DROP:        kill the data connection partway through the first RETR of
                 each file (client must retry and REST-resume the .part)
  - STALL:       hang mid-transfer longer than the client timeout on the
                 first attempt (client must time out, reconnect, resume)
  - REJECT_REST: refuse the REST command (client must fall back to a clean
                 full restart and still verify)
  - NONE:        behave perfectly (control runs)

Faults trigger once per file by default, so a correct client always converges.

Requires: pip3 install pyftpdlib   (dev/validation dependency only)
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer

DROP = "drop"
STALL = "stall"
REJECT_REST = "reject_rest"
NONE = "none"


class FaultPlan:
    """Shared, thread-safe record of which faults fired for which files."""

    def __init__(self, mode: str = NONE, *, fail_after_bytes: int = 256 * 1024,
                 stall_seconds: float = 6.0, faults_per_file: int = 1):
        self.mode = mode
        self.fail_after_bytes = int(fail_after_bytes)
        self.stall_seconds = float(stall_seconds)
        self.faults_per_file = int(faults_per_file)
        self._lock = threading.Lock()
        self._fired: dict[str, int] = {}

    def should_fault(self, path: str) -> bool:
        if self.mode in (NONE, REJECT_REST):
            return False
        with self._lock:
            count = self._fired.get(path, 0)
            if count >= self.faults_per_file:
                return False
            self._fired[path] = count + 1
            return True

    def fault_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._fired)

    @property
    def total_faults_fired(self) -> int:
        with self._lock:
            return sum(self._fired.values())


class _FaultyReader:
    """File wrapper that injects the planned fault during read()."""

    def __init__(self, handle, path: str, plan: FaultPlan, fault_this_attempt: bool):
        self._handle = handle
        self._path = path
        self._plan = plan
        self._fault = fault_this_attempt
        self._sent = 0
        self.name = getattr(handle, "name", path)
        self.closed = False

    def read(self, size=-1):
        if self._fault and self._sent >= self._plan.fail_after_bytes:
            if self._plan.mode == STALL:
                time.sleep(self._plan.stall_seconds)
                # After the stall the connection is torn down anyway so the
                # client's timeout, not luck, decides the outcome.
            raise ConnectionResetError(
                f"fault-injection: {self._plan.mode} after {self._sent} bytes of {self._path}"
            )
        data = self._handle.read(size)
        self._sent += len(data)
        return data

    def seek(self, *args, **kwargs):
        return self._handle.seek(*args, **kwargs)

    def tell(self):
        return self._handle.tell()

    def close(self):
        self.closed = True
        return self._handle.close()

    def fileno(self):
        return self._handle.fileno()


class FaultyFS(AbstractedFS):
    plan: FaultPlan = FaultPlan()  # replaced per server instance

    def open(self, filename, mode):
        handle = super().open(filename, mode)
        if "r" in mode and "b" in mode:
            path = str(filename)
            return _FaultyReader(handle, path, self.plan, self.plan.should_fault(path))
        return handle


class FaultyHandler(FTPHandler):
    plan: FaultPlan = FaultPlan()

    def ftp_REST(self, line):
        if self.plan.mode == REJECT_REST:
            self.respond("502 REST not implemented (fault injection).")
            return
        return super().ftp_REST(line)


class FaultInjectionFTPServer:
    """Run a misbehaving FTP server on 127.0.0.1 for the duration of a test."""

    def __init__(self, root: Path, *, mode: str = NONE, port: int = 0,
                 user: str = "ftp1", password: str = "12345678", **plan_kwargs):
        self.root = Path(root)
        self.plan = FaultPlan(mode, **plan_kwargs)
        authorizer = DummyAuthorizer()
        authorizer.add_user(user, password, str(self.root), perm="elr")

        handler = type("BoundFaultyHandler", (FaultyHandler,), {"plan": self.plan})
        fs = type("BoundFaultyFS", (FaultyFS,), {"plan": self.plan})
        handler.authorizer = authorizer
        handler.abstracted_fs = fs
        handler.banner = "MediaRunner fault-injection FTP ready."

        self.server = ThreadedFTPServer(("127.0.0.1", int(port)), handler)
        self.port = self.server.address[1]
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"blocking": True},
            daemon=True,
            name="fault-ftp",
        )
        self._thread.start()
        time.sleep(0.2)  # accept-loop warm-up
        return self

    def __exit__(self, *exc):
        try:
            self.server.close_all()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        return False
