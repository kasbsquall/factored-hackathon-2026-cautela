"""Audit log: conversation ids on every record, the stored-file check, restarts, retention wiring, concurrency."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import UTC, datetime, timedelta

import pytest

from agent.clock import FrozenClock
from agent.security.audit import AUDIT_RETENTION_DAYS, GENESIS_HASH, AuditLog, AuditRecord

DAY = "2026-06-01"


@pytest.fixture()
def clock():
    return FrozenClock(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))


def _log(tmp_path, clock) -> AuditLog:
    return AuditLog(secrets.token_bytes(48), tmp_path, clock)


def test_records_of_a_bound_trace_carry_the_conversation_id(tmp_path, clock):
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_login", step="auth.start_login", outcome="challenge_issued")
    log.bind("tr_1", "cv_a")
    log.bind("tr_2", "cv_a")
    log.record(trace_id="tr_1", step="orchestrator.gate", outcome="session_valid")
    log.record(trace_id="tr_1", step="tool", tool="get_customer_profile", outcome="ok")
    log.record(trace_id="tr_2", step="llm.reply", outcome="ok")
    assert [r.conversation_id for r in log.records()] == [None, "cv_a", "cv_a", "cv_a"]
    assert [r.trace_id for r in log.records(conversation_id="cv_a")] == ["tr_1", "tr_1", "tr_2"]
    stored = AuditLog.load(tmp_path / f"audit-{DAY}.jsonl")
    assert [r.conversation_id for r in stored] == [None, "cv_a", "cv_a", "cv_a"]
    assert log.verify_chain() and log.verify_chain(stored)
    edited = [stored[0], stored[1].model_copy(update={"conversation_id": "cv_b"}), *stored[2:]]
    assert not log.verify_chain(edited), "the conversation id is part of the hashed body"


def test_records_written_before_conversation_ids_existed_still_verify(tmp_path, clock):
    """A line without the field hashes as it did when it was written."""
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    line = json.loads((tmp_path / f"audit-{DAY}.jsonl").read_text(encoding="utf-8"))
    assert "conversation_id" not in line
    assert log.verify_chain([AuditRecord(**line)])


def test_the_stored_check_reads_the_files_and_catches_an_edit_that_memory_cannot(tmp_path, clock):
    log = _log(tmp_path, clock)
    for i in range(4):
        log.record(trace_id="tr_1", step=f"s{i}", outcome="ok")
    check = log.check_stored()
    assert check.intact and check.source == "stored_files" and check.records_checked == 4
    path = tmp_path / f"audit-{DAY}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace('"outcome":"ok"', '"outcome":"denied"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain(), "the in-memory copy is untouched"
    broken = log.check_stored()
    assert not broken.intact and broken.first_bad_seq == 2


def test_a_deleted_or_unreadable_line_breaks_the_stored_chain(tmp_path, clock):
    log = _log(tmp_path, clock)
    for i in range(3):
        log.record(trace_id="tr_1", step=f"s{i}", outcome="ok")
    path = tmp_path / f"audit-{DAY}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert log.check_stored().first_bad_seq == 2
    path.write_text("\n".join([lines[0], "{not json", lines[2]]) + "\n", encoding="utf-8")
    assert not log.check_stored().intact


def test_an_unchanged_log_is_not_verified_again(tmp_path, clock):
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    first = log.check_stored()
    assert log.check_stored() is first, "same files, same sizes: the cached result answers"
    log.record(trace_id="tr_1", step="s2", outcome="ok")
    assert log.check_stored().records_checked == 2


def test_without_a_directory_the_check_says_it_covers_memory_only(clock):
    log = AuditLog(secrets.token_bytes(48), None, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    check = log.check_stored()
    assert check.intact and check.source == "memory" and check.records_checked == 1


def test_a_restart_continues_the_stored_chain_instead_of_starting_a_second_one(tmp_path, clock):
    first = _log(tmp_path, clock)
    first.record(trace_id="tr_1", step="s", outcome="ok")
    first.record(trace_id="tr_1", step="s", outcome="ok")
    second = _log(tmp_path, clock)  # a new process on the same directory
    rec = second.record(trace_id="tr_2", step="s", outcome="ok")
    assert rec.seq == 2 and rec.prev_hash == first.records()[-1].record_hash
    assert second.verify_chain(), "the in-memory copy starts where the stored chain ended"
    check = second.check_stored()
    assert check.intact and check.records_checked == 3


def test_the_chain_spans_day_files(tmp_path):
    clock = FrozenClock(datetime(2026, 6, 1, 23, 59, tzinfo=UTC))
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    clock.advance(minutes=2)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["audit-2026-06-01.jsonl", "audit-2026-06-02.jsonl"]
    check = log.check_stored()
    assert check.intact and check.records_checked == 2 and check.files_checked == 2


def test_expired_day_files_are_purged_when_the_log_starts(tmp_path, clock):
    old = tmp_path / "audit-2010-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    kept = tmp_path / f"audit-{DAY}.jsonl"
    _log(tmp_path, clock).record(trace_id="tr_1", step="s", outcome="ok")
    assert not old.exists() and kept.exists()


def test_expired_day_files_are_purged_when_the_day_changes(tmp_path):
    clock = FrozenClock(datetime(2026, 6, 1, 23, 59, tzinfo=UTC))
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    expiring = tmp_path / f"audit-{(clock() - timedelta(days=AUDIT_RETENTION_DAYS)).date().isoformat()}.jsonl"
    expiring.write_text("", encoding="utf-8")  # inside the window today, outside it tomorrow
    log.record(trace_id="tr_1", step="s", outcome="ok")
    assert expiring.exists()
    clock.advance(minutes=2)
    log.record(trace_id="tr_2", step="s", outcome="ok")
    assert not expiring.exists()


def test_after_a_purge_the_check_starts_at_the_oldest_kept_record(tmp_path):
    clock = FrozenClock(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
    log = _log(tmp_path, clock)
    log.record(trace_id="tr_1", step="s", outcome="ok")
    clock.advance(days=1)
    log.record(trace_id="tr_2", step="s", outcome="ok")
    (tmp_path / f"audit-{DAY}.jsonl").unlink()  # what a retention purge does to the oldest day
    check = log.check_stored()
    assert check.intact and check.records_checked == 1


def test_concurrent_writers_keep_one_chain(tmp_path, clock):
    log = _log(tmp_path, clock)

    def write(n: int) -> None:
        for i in range(50):
            log.record(trace_id=f"tr_{n}", step=f"s{i}", outcome="ok")

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert log.verify_chain() and log.check_stored().intact
    assert [r.seq for r in log.records()] == list(range(200))
    assert log.records()[0].prev_hash == GENESIS_HASH
