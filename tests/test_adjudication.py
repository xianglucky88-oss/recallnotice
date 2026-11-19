"""Unit tests for multi-source adjudication in the kernel."""
import itertools

from recallnotice import kernel

T1 = "2030-01-01T00:00:00Z"
T2 = "2030-01-02T00:00:00Z"
T3 = "2030-01-03T00:00:00Z"


def rec(rid, name, value, ts, **extra):
    d = {"id": rid, "name": name, "value": value, "status": "active", "ts": ts}
    d.update(extra)
    return d


def verdict_for(records, key="alpha"):
    return {v["merge_key"]: v for v in kernel.adjudicate(records)}[key]


def test_single_announcement_is_pending():
    v = verdict_for([rec("A-1", "Alpha Widget", 100, T1)])
    assert v["state"] == kernel.PENDING
    assert v["chosen_value"] == "100"
    assert v["candidates"][0]["ids"] == ["A-1"]
    assert v["basis"]


def test_corroborated_announcement_is_published():
    v = verdict_for([rec("A-1", "Alpha Widget", 100, T1),
                     rec("A-2", "Alpha Widget", 100, T2)])
    assert v["state"] == kernel.PUBLISHED
    assert v["chosen_value"] == "100"
    assert v["chosen_hits"] == 2


def test_dissent_is_conflict_and_earliest_value_stays():
    v = verdict_for([rec("A-1", "Alpha Widget", 100, T1),
                     rec("A-2", "Alpha Widget", 200, T2)])
    assert v["state"] == kernel.CONFLICT
    assert v["chosen_value"] == "100"


def test_repetition_does_not_decide():
    # A louder channel (three copies of the later value) must not outvote
    # the single earliest publication.
    records = [rec("A-1", "Alpha Widget", 100, T1),
               rec("A-2", "Alpha Widget", 200, T2),
               rec("A-3", "Alpha Widget", 200, T2),
               rec("A-4", "Alpha Widget", 200, T3)]
    v = verdict_for(records)
    assert v["state"] == kernel.CONFLICT
    assert v["chosen_value"] == "100"


def test_identical_republication_counts_once():
    same = rec("A-1", "Alpha Widget", 100, T1)
    v = verdict_for([same, dict(same), dict(same)])
    assert v["state"] == kernel.PENDING
    assert v["chosen_hits"] == 1


def test_late_revision_does_not_overturn_published():
    base = [rec("A-1", "Alpha Widget", 100, T1),
            rec("A-2", "Alpha Widget", 100, T2)]
    assert verdict_for(base)["state"] == kernel.PUBLISHED
    revised = base + [rec("A-3", "Alpha Widget", 999, T3)]
    v = verdict_for(revised)
    assert v["state"] == kernel.CONFLICT
    assert v["chosen_value"] == "100"  # published history stands


def test_missing_value_is_insufficient():
    v = verdict_for([rec("A-1", "Alpha Widget", "", T1),
                     rec("A-2", "Alpha Widget", None, T2)])
    assert v["state"] == kernel.INSUFFICIENT
    assert v["chosen_value"] is None
    assert v["incomplete"] == 2


def test_arrival_order_never_changes_the_verdict():
    records = [rec("A-1", "Alpha Widget", 100, T1),
               rec("A-2", "Alpha Widget", 200, T2),
               rec("B-1", "Beta Gadget", 7, T1),
               rec("B-2", "Beta Gadget", 7, T3)]
    baseline = kernel.adjudicate(records)
    for perm in itertools.permutations(records):
        assert kernel.adjudicate(list(perm)) == baseline


def test_verdict_traces_back_to_candidates():
    v = verdict_for([rec("A-1", "Alpha Widget", 100, T1),
                     rec("A-2", "Alpha Widget", 200, T2)])
    ids = {rid for c in v["candidates"] for rid in c["ids"]}
    assert ids == {"A-1", "A-2"}
    assert "100" in v["basis"]


def test_reduce_keeps_backward_compatible_fields():
    rows = [rec("A-1", "Alpha Widget", 100, T1),
            rec("A-2", "Alpha Widget", 200, T2)]
    out = kernel.reduce(rows)
    assert out is rows
    for r in out:
        assert r["reconciled_value"] == "100"
        assert r["conflict"] == 1
        assert r["verdict"] == kernel.CONFLICT
        assert isinstance(r["consensus_hits"], int)
        assert r["verdict_basis"]
