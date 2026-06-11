"""Zombie-job reconciliation: fail_unfinished_jobs + reconcile_stale_jobs.

These guarantee the produce UI never spins on a job left mid-flight by an
uncatchable interrupt (an arq ``job_timeout`` cancels the task with
CancelledError, a crash/OOM/redeploy kills it outright). DB is faked in-memory.
"""

from __future__ import annotations

import contextlib

from module2 import runner


class _Row:
    def __init__(self, id, parent_id, state, stderr=None):
        self.id = id
        self.parent_id = parent_id
        self.state = state
        self.stderr = stderr


# --------------------------------------------------------------------------- #
# fail_unfinished_jobs                                                        #
# --------------------------------------------------------------------------- #
class _FakeJobRepo:
    def __init__(self, children):
        self._children = children

    async def children_for_episode(self, user_id, episode_id):
        return self._children


class _FakeSession:
    async def flush(self):
        return None


async def test_fail_unfinished_jobs_flips_only_nonterminal_of_this_parent(monkeypatch):
    parent = _Row("p", None, "running")
    children = [
        _Row("c1", "p", "running"),   # -> error
        _Row("c2", "p", "done"),      # terminal, untouched
        _Row("c3", "p", "queued"),    # -> error
        _Row("c4", "p", "paused"),    # -> error
        _Row("cx", "other", "running"),  # different parent, untouched
    ]
    repo = _FakeJobRepo(children)

    @contextlib.asynccontextmanager
    async def _scope():
        yield _FakeSession()

    async def _find_parent(r, u, e):
        return parent

    monkeypatch.setattr(runner, "session_scope", _scope)
    monkeypatch.setattr(runner, "GenJobRepo", lambda s: repo)
    monkeypatch.setattr(runner.jobmod, "find_parent_for_episode", _find_parent)

    flipped = await runner.fail_unfinished_jobs("u", "e", "boom")

    assert flipped == 4  # parent + c1 + c3 + c4
    assert parent.state == "error" and parent.stderr == "boom"
    assert children[0].state == "error"
    assert children[1].state == "done"      # terminal untouched
    assert children[4].state == "running"   # other parent untouched
    # An existing stderr is not clobbered.


async def test_fail_unfinished_jobs_no_parent_is_noop(monkeypatch):
    @contextlib.asynccontextmanager
    async def _scope():
        yield _FakeSession()

    async def _no_parent(r, u, e):
        return None

    monkeypatch.setattr(runner, "session_scope", _scope)
    monkeypatch.setattr(runner, "GenJobRepo", lambda s: _FakeJobRepo([]))
    monkeypatch.setattr(runner.jobmod, "find_parent_for_episode", _no_parent)

    assert await runner.fail_unfinished_jobs("u", "e", "boom") == 0


# --------------------------------------------------------------------------- #
# reconcile_stale_jobs (worker-startup sweep)                                 #
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeExecSession:
    """Session whose execute() returns the pre-filtered stale rows (running/paused)."""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *a, **k):
        return _FakeResult(self.rows)

    async def flush(self):
        return None


async def test_reconcile_stale_jobs_flips_running_and_paused(monkeypatch):
    stale = [
        _Row("a", None, "running"),
        _Row("b", "a", "paused"),
        _Row("c", "a", "running", stderr="already had a reason"),
    ]
    session = _FakeExecSession(stale)

    @contextlib.asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(runner, "session_scope", _scope)

    flipped = await runner.reconcile_stale_jobs()

    assert flipped == 3
    assert all(r.state == "error" for r in stale)
    # A pre-existing stderr is preserved (not overwritten by the sweep message).
    assert stale[2].stderr == "already had a reason"
    assert "gián đoạn" in stale[0].stderr  # default message filled in for empty ones
