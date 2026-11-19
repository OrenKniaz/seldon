"""C12/C17: DETAIL continues a proven writer without reallocating paths."""

import json

import pytest

from seldon import plan
from seldon.decisions import Waiting
from test_journey import finish, unit_ref
from wave_loop_journey import Waves, prepared


@pytest.mark.fast
@pytest.mark.parametrize("spans_owners", [False, True])
def test_detail_continues_the_completed_writer(tmp_path, scalar_align, spans_owners):
    """Catch a split writer or stolen peer scope after wave 1 has run and proved its work.

    The existing wave journey repeats the entire scope, so it cannot catch either failure.
    """
    class DetailSlice(Waves):
        def __call__(self, question):
            result = super().__call__(question)
            if (question["kind"] != "DETAIL"
                    or question["facts"]["Current wave"]["goal"] != "Extend saved values"):
                return result
            envelope = json.loads(result)
            scope = ["unit-001/extension-2.txt"]
            if spans_owners:
                scope.append("unit-002/value.txt")
            envelope["answer"]["units"][0]["scope"] = scope
            return json.dumps(envelope)

    slot = tmp_path / "detail"
    run, _ = prepared(slot, False)
    run.provider = DetailSlice(slot, "normal", 3)
    try:
        store = run.store
        while store.cursor().state != "CHECK-IN":
            run.step()
        first = store.cursor()
        owner, peer = (unit_ref(run, n) for n in (1, 2))
        before = plan.execution(store)
        rows = store.units()
        assert rows[owner].state == rows[peer].state == "DONE"
        evidence = {ref: store.read(ref) for ref in first.finalObservations}
        assert any(o["unit"] == owner and o["commit"] == first.candidate and o["exitCode"] == 0
                   for o in evidence.values())

        while store.cursor().state != "DETAIL":
            run.step()
        if spans_owners:
            with pytest.raises(Waiting):
                run.step()
            corrections = store.records("detail_correction")[-1][2]["corrections"]["area-1"]
            assert all("own the shared files" in c["error"] for c in corrections)
            assert len(corrections) == 2
            assert {owner, peer} <= {ref for c in corrections for ref in c["otherSide"]}
            assert store.cursor().state == "DETAIL"
            assert store.units() == rows
            assert plan.select(plan.execution(store), "unit") == plan.select(before, "unit")
            return

        assert run.step() == "REVIEW"
        assert not store.records("detail_correction")
        after = plan.execution(store)
        earlier = plan.select(before, "unit")[owner]
        continued = plan.select(after, "unit")[owner]
        assert continued["scope"] == earlier["scope"]
        assert plan.select(after, "unit")[peer] == plan.select(before, "unit")[peer]
        assert continued["requirements"][:len(earlier["requirements"])] == earlier["requirements"]
        for ref in earlier["requirements"]:
            assert after["nodes"][ref] == before["nodes"][ref]
            for feature in before["nodes"][ref]["body"]["features"]:
                assert after["nodes"][feature] == before["nodes"][feature]
        assert store.cursor().B == first.B

        last, _, finished = finish(run)
        assert finished[owner].wave == 2 and finished[peer] == rows[peer]
        assert last.candidate != first.candidate
        assert (run.project / "unit-001" / "extension-2.txt").read_text() == "extended"
        assert {ref: store.read(ref) for ref in evidence} == evidence
        old_check, = (o["check"] for o in evidence.values() if o["unit"] == owner)
        assert any(store.read(ref)["check"] == old_check for ref in last.finalObservations)
    finally:
        run.close()
