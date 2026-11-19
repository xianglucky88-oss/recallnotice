"""Multi-source adjudication for recall notices.

The same product recall is republished through multiple channels, so the
number of times a channel repeats a notice must not decide the final
announcement.  Adjudication here is a pure, deterministic function of the
distinct announcements seen so far:

* identical re-publications collapse into a single announcement;
* the earliest published value stays incumbent -- a louder channel or a
  late revision cannot overturn already published history;
* every verdict keeps the candidate announcements and the basis for the
  decision, so each conclusion can be reviewed later.

Verdict states:
    pending       尚待确认 -- a single announcement, awaiting corroboration
    published     已发布   -- independent announcements agree
    conflict      相互矛盾 -- announcements disagree; incumbent value kept
    insufficient  资料不足 -- no usable value on record
"""
import hashlib
import json
from collections import defaultdict

PENDING = "pending"
PUBLISHED = "published"
CONFLICT = "conflict"
INSUFFICIENT = "insufficient"
STATES = (PENDING, PUBLISHED, CONFLICT, INSUFFICIENT)

# Keys produced by adjudication itself; never part of a record's identity.
DERIVED_KEYS = frozenset({
    "merge_key", "src_value", "reconciled_value", "consensus_hits",
    "conflict", "verdict", "verdict_basis",
})


def _ident(rec):
    nm = str(rec.get("name", ""))
    return nm.split()[0].lower() if nm else str(rec.get("id"))


def process(record):
    return {"merge_key": _ident(record), "src_value": record.get("value")}


def strip_derived(record):
    """Return the source content of *record* without adjudication fields."""
    return {k: v for k, v in record.items() if k not in DERIVED_KEYS}


def _identity(rec):
    blob = json.dumps(strip_derived(rec), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _usable(value):
    return value is not None and str(value).strip() != ""


def _first_ts(rows):
    vals = [str(r.get("ts", "")) for r in rows]
    vals = [v for v in vals if v]
    return min(vals) if vals else ""


def _candidate(value, rows):
    return {
        "value": value,
        "hits": len(rows),
        "first_ts": _first_ts(rows),
        "ids": sorted(str(r.get("id", "")) for r in rows),
    }


def _decide(key, rows):
    by_value = defaultdict(list)
    incomplete = 0
    for r in rows:
        v = r.get("value")
        if _usable(v):
            by_value[str(v).strip() if isinstance(v, str) else str(v)].append(r)
        else:
            incomplete += 1
    candidates = [_candidate(v, rs) for v, rs in by_value.items()]
    # Deterministic order: earliest publication first, then value.  Arrival
    # order and repetition counts never influence the ranking.
    candidates.sort(key=lambda c: (c["first_ts"], c["value"]))
    total = len(rows)
    if not candidates:
        return {
            "merge_key": key, "state": INSUFFICIENT,
            "chosen_value": None, "chosen_hits": 0,
            "support": 0, "incomplete": incomplete,
            "candidates": [],
            "basis": ("no usable value in %d announcement(s); required field "
                      "'value' is missing" % total),
        }
    top = candidates[0]
    if len(candidates) > 1:
        state = CONFLICT
        basis = ("%d distinct values across %d announcement(s); keeping "
                 "earliest published value %r (first_ts=%s) -- later or more "
                 "frequent revisions do not overturn published history"
                 % (len(candidates), total, top["value"], top["first_ts"]))
    elif top["hits"] >= 2:
        state = PUBLISHED
        basis = ("%d independent announcements agree on %r; no dissenting "
                 "value" % (top["hits"], top["value"]))
    else:
        state = PENDING
        basis = "single announcement only; awaiting corroboration"
    return {
        "merge_key": key, "state": state,
        "chosen_value": top["value"], "chosen_hits": top["hits"],
        "support": total - incomplete, "incomplete": incomplete,
        "candidates": candidates, "basis": basis,
    }


def adjudicate(records):
    """Adjudicate *records* into one verdict per merge key.

    The result is a deterministic function of the distinct announcements:
    arrival order, duplicate re-publications and re-runs never change the
    outcome.
    """
    seen = set()
    groups = defaultdict(list)
    for r in records:
        h = _identity(r)
        if h in seen:
            continue
        seen.add(h)
        groups[_ident(r)].append(r)
    return [_decide(key, groups[key]) for key in sorted(groups)]


def reduce(records):
    """Annotate each record with the verdict for its merge key.

    Kept for backward compatibility: ``reconciled_value``, ``consensus_hits``
    and ``conflict`` retain their old meaning, while ``verdict`` and
    ``verdict_basis`` expose the full adjudication outcome.
    """
    verdicts = {v["merge_key"]: v for v in adjudicate(records)}
    for r in records:
        v = verdicts[_ident(r)]
        r["merge_key"] = _ident(r)
        r["src_value"] = r.get("value")
        r["reconciled_value"] = v["chosen_value"]
        r["consensus_hits"] = v["chosen_hits"]
        r["conflict"] = int(v["state"] == CONFLICT)
        r["verdict"] = v["state"]
        r["verdict_basis"] = v["basis"]
    return records
