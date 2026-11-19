"""End-to-end consistency: re-runs, out-of-order arrival, source outages
and resumed execution must converge to the same records and verdicts."""
import json
import sqlite3

import pytest

import mockapp.main as mock_app
from recallnotice import framework
from recallnotice.mockdata import build_rows, extra_row
from recallnotice.store import Store


@pytest.fixture
def source():
    """Restore the fixture source data after each test."""
    old_all, old_extra = mock_app._ALL, list(mock_app._extra)
    try:
        yield mock_app
    finally:
        mock_app._ALL = old_all
        mock_app._extra[:] = old_extra


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    Store(c)
    try:
        yield c
    finally:
        c.close()


def run(con, mode="full"):
    return framework.run(mode=mode, con=con)


def verdicts(con):
    return Store(con).verdicts()


def fresh_con():
    c = sqlite3.connect(":memory:")
    Store(c)
    return c


def test_rerun_is_idempotent(source, con):
    first = run(con)
    assert sum(first["verdicts"].values()) == 4
    snapshot = sorted(Store.hash(r) for r in Store(con).all_records())
    stored_verdicts = verdicts(con)

    again = run(con)
    assert again["ingested"] == 0
    assert again["count"] == first["count"]
    assert again["watermark"] == first["watermark"]
    assert again["verdicts"] == first["verdicts"]
    assert verdicts(con) == stored_verdicts
    assert sorted(Store.hash(r) for r in Store(con).all_records()) == snapshot


def test_out_of_order_arrival_converges(source, con):
    baseline = run(con)
    base_verdicts = verdicts(con)

    source._ALL = list(reversed(source._ALL))
    other = fresh_con()
    try:
        shuffled = run(other)
        assert shuffled["count"] == baseline["count"]
        assert verdicts(other) == base_verdicts
    finally:
        other.close()


def test_outage_preserves_state_and_resume_converges(source, con, monkeypatch):
    baseline = run(con)
    base_verdicts = verdicts(con)

    real = framework.Transport.request
    state = {"down": True}

    def fake(self, method, path, params=None, json_body=None, headers=None):
        if state["down"]:
            return 503, {}, ""
        return real(self, method, path, params=params,
                    json_body=json_body, headers=headers)

    monkeypatch.setattr(framework.Transport, "request", fake)
    with pytest.raises(RuntimeError):
        run(con)
    st = Store(con)
    assert st.count() == baseline["count"]
    assert st.watermark() == baseline["watermark"]
    assert verdicts(con) == base_verdicts

    state["down"] = False
    resumed = run(con)
    assert resumed["ingested"] == 0
    assert resumed["count"] == baseline["count"]
    assert verdicts(con) == base_verdicts


def test_partial_outage_persists_nothing(source, con, monkeypatch):
    real = framework.Transport.request
    state = {"down": True}

    def fake(self, method, path, params=None, json_body=None, headers=None):
        if state["down"] and (params or {}).get("page", 1) >= 2:
            return 503, {}, ""
        return real(self, method, path, params=params,
                    json_body=json_body, headers=headers)

    monkeypatch.setattr(framework.Transport, "request", fake)
    with pytest.raises(RuntimeError):
        run(con)
    st = Store(con)
    assert st.count() == 0
    assert st.watermark() is None
    assert verdicts(con) == []

    state["down"] = False
    resumed = run(con)
    control = fresh_con()
    try:
        expected = run(control)
        assert resumed["count"] == expected["count"]
        assert verdicts(con) == verdicts(control)
    finally:
        control.close()


def test_incremental_catches_up_with_full(source, con):
    run(con)
    source._extra.append(extra_row(1))  # a new announcement appears later
    inc = run(con, mode="incremental")
    assert inc["ingested"] == 1

    control = fresh_con()
    try:
        full = run(control)
        assert inc["count"] == full["count"]
        assert verdicts(con) == verdicts(control)
    finally:
        control.close()


def test_backfilled_revision_is_not_dropped_by_incremental(source, con):
    run(con)
    original = build_rows()[1]
    revision = dict(original, id=original["id"] + "-R1", value=777,
                    ts=original["ts"])  # old timestamp, arrives late
    source._extra.append(revision)
    inc = run(con, mode="incremental")
    assert inc["ingested"] == 1

    control = fresh_con()
    try:
        run(control)
        assert verdicts(con) == verdicts(control)
    finally:
        control.close()


def test_late_revision_keeps_published_history(source, con):
    run(con)
    before = {v["merge_key"]: v for v in verdicts(con)}

    original = build_rows()[0]
    revision = dict(original, id=original["id"] + "-R9", value=424242,
                    ts="2030-02-15T00:00:00Z")
    source._extra.append(revision)
    run(con, mode="incremental")
    after = {v["merge_key"]: v for v in verdicts(con)}

    key = before and [k for k in before if k == "alpha"][0]
    assert after[key]["state"] == "conflict"
    assert after[key]["chosen_value"] == before[key]["chosen_value"]
    ids = {rid for c in after[key]["candidates"] for rid in c["ids"]}
    assert revision["id"] in ids
    for other in before:
        if other != key:
            assert after[other] == before[other]

    control = fresh_con()
    try:
        run(control)
        assert verdicts(control) == verdicts(con)
    finally:
        control.close()


def test_historical_annotated_rows_survive_migration(source):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE records(content_hash TEXT PRIMARY KEY, id TEXT,"
                " ts TEXT, payload TEXT NOT NULL)")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    legacy = {"id": "LEG-1", "name": "Legacy Widget", "value": 5,
              "status": "active", "ts": "2030-01-01T00:00:00Z",
              "product": "widget", "merge_key": "legacy", "src_value": 5,
              "reconciled_value": "5", "consensus_hits": 4, "conflict": 1}
    variant = dict(legacy, consensus_hits=9, reconciled_value="8")
    for row in (legacy, variant):
        con.execute("INSERT INTO records VALUES(?,?,?,?)",
                    (Store.hash(row), row["id"], row["ts"], json.dumps(row)))
    con.commit()
    Store(con)  # adds the verdicts table to the old database
    try:
        run(con)
        st = Store(con)
        assert st.schema_version() == 2
        matches = [r for r in st.all_records() if r.get("id") == "LEG-1"]
        assert len(matches) == 1  # annotated duplicates collapsed
        assert matches[0]["name"] == "Legacy Widget"
        assert matches[0]["value"] == 5
        assert "reconciled_value" not in matches[0]
        keys = {v["merge_key"] for v in st.verdicts()}
        assert "legacy" in keys
    finally:
        con.close()


def test_unchanged_records_remain_readable(source, con):
    run(con)
    run(con, mode="incremental")
    st = Store(con)
    rows = {r["id"]: r for r in st.all_records()}
    src = {r["id"]: r for r in build_rows()}
    assert set(rows) == set(src)
    for rid, row in rows.items():
        for field in ("name", "value", "status", "ts", "product"):
            assert row[field] == src[rid][field]
        assert "reconciled_value" not in row
        assert "verdict" not in row
        assert st.get(Store.hash(row)) == row
