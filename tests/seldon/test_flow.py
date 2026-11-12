"""C9/C14/C15; R2/R4/R9: canonical planning and workers produce candidate-bound flow evidence."""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from seldon import flow_review, gitwork, guide, inference, obligations, plan, proof, questions, wave_verify
from seldon.store import encode

from .test_journey import prepare


def integrated(slot, mode, count, change=None):
    run, provider = prepare(slot, mode, count)
    provider.plan_fault = change
    if mode == "records":
        # Extend the existing scripted worker's screenshot-producing check to this shared-page product.
        native = slot / "native.py"
        native.write_text(native.read_text("utf-8").replace(
            'if mode in ("single1", "scale50", "traced-evidence", "visual-unit"):',
            'if mode in ("single1", "scale50", "traced-evidence", "visual-unit", "records"):'))
    try:
        while run.store.cursor().state != "WAVE-VERIFY":
            assert run.store.cursor().state != "INCOMPLETE", run.store.records("outcome")
            run.step()
        assert run.step() == "FLOW-JUDGE"
        return run
    except BaseException:
        run.close()
        raise


def ask_flow(store, feature, answer=None, *, resolve=None, reasked=False):
    question = flow_review.brief(store, feature, resolve=resolve)

    def scripted(q):
        reply = answer or {"verdict": "satisfied", "gaps": [],
                           "assessments": questions.assessments(q["targets"], final=True),
                           "why": "The value is observed through the shared page agreement"}
        return json.dumps({"answer": reply})

    source, reply = inference.call(store, scripted, question,
        check=lambda body: flow_review.validate(body, question["targets"]))
    return question, flow_review.review(store, feature, source, reply, reasked=reasked)


@pytest.mark.fast
def test_shared_page_flows(tmp_path):
    """A shared-page product must carry contract text and screenshots to each feature's judge.

    Per-requirement DEMO cannot expose missing cross-unit context. A negative assessment with a
    satisfied headline catches trusting that headline instead of ANDing requirements. The same
    integrated product then loses a value: real command failure must return its owner as a check Gap,
    without reworking any unit. Success-only flow judgments cannot expose that deterministic path.
    One bounded re-ask keeps unavailable facts not judged; a successful answer cannot expose false Gaps.
    A requirement shared by two features closes once and ANDs both flows, unlike disjoint features.
    A wave composition check catches lost accumulated checks; a malformed guide catches repair hidden
    inside the verifier, which ordinary commands cannot exercise.
    """
    began = time.monotonic()

    def two_unit_feature(answer):
        req = next(e for e in answer["edits"] if e["targetHandle"] == "new:r002")
        req["typedBody"]["features"] = ["new:f001", "new:f002"]
        placeholder = next(e for e in answer["edits"] if e["kind"] == "obligation")["typedBody"]
        placeholder.update(owner=2, closingRequirement="new:r002")
        answer["edits"].append({"kind": "check", "targetHandle": "new:compose", "typedBody": {
            "purpose": "Read both shared-page values together", "mode": "preserves",
            "command": [sys.executable, "-S", "-B", "-c", "from pathlib import Path; "
                        "assert [Path(f'unit-{i:03}/value.txt').read_text() for i in (1,2)]"
                        " == ['value-001','value-002']; print('observed both values together')"],
            "requiredEnvironment": ["Python"], "expectedEvidence": "Both integrated values",
            "requirements": ["new:r001", "new:r002"]}})

    run = integrated(tmp_path / "shared", "records", 3, two_unit_feature)
    try:
        store, cursor = run.store, run.store.cursor()
        document = store.plan(cursor.revision)
        before = store.units()
        assert obligations.remaining(store)
        assert {store.read(r)["check"] for r in cursor.finalObservations} == \
            set(plan.select(document, "check")) | set(guide.checks(run.project))
        assert run.step() == "CHECK-IN"
        verdicts = store.cursor().demoVerdicts
        for feature in plan.select(document, "feature"):
            question, = [q for _, _, q in store.records("question")
                         if q["kind"] == "FLOW"
                         and q["targets"] == list(flow_review.trace(document, feature)[0])]
            verdict, = [store.read(r) for r in verdicts if store.read(r)["feature"] == feature]
            assert verdict["answer"]["verdict"] == "satisfied"
            assert question["images"] and all(Path(p).read_bytes().startswith(b"\x89PNG")
                                               for p in question["images"])
            assert "Shared page exposes a named content slot." in encode(question["facts"])
            sources = gitwork.blobs(run.project, cursor.candidate, ["unit-*/check.py"])
            assert all(encode(source.decode("utf-8"))[1:-1] not in encode(question["facts"])
                       for source in sources.values())
            assert "Product files at P" not in question["facts"] and "Check sources" not in question["facts"]
            assert "from pathlib import Path" not in encode(question)
        assert len(store.records("question")) - sum(
            b["kind"] != "FLOW" for _, _, b in store.records("question")) == len(verdicts)
        closures = flow_review.requirement_verdicts(store, verdicts)
        assert {store.read(r)["subject"] for r in closures} == set(plan.select(document, "requirement"))
        assert len(closures) == len(plan.select(document, "requirement"))
        store.cursor(replace(store.cursor(), requirementVerdicts=closures))
        assert not obligations.remaining(store)
        feature = next(iter(plan.select(document, "feature")))
        question = flow_review.brief(store, feature)
        negative = questions.assessments(question["targets"], final=True)
        negative[-1].update(meaningful=False, why="The observed page does not expose the stored value")
        _, result = ask_flow(store, feature, {"verdict": "satisfied", "gaps": [],
                          "assessments": negative, "why": "One requirement is missing in the flow"})
        gap, = result["gaps"]
        assert gap == {"subject": feature, "kind": "flow", "commit": cursor.candidate,
                       "units": ["u001", "u002"],
                       "correction": "The observed page does not expose the stored value",
                       "source": result["verdict"]}
        uncertain = {"verdict": "uncertain", "neededFacts": ["Explain the observed page transition"],
                     "why": "The supplied images do not settle this transition"}
        _, first = ask_flow(store, feature, uncertain)
        pending = store.read(first["pending"])
        question, second = ask_flow(store, feature, uncertain, resolve=uncertain["neededFacts"], reasked=True)
        assert len(encode(question).encode("utf-8")) <= 2 * pending["questionBytes"]
        assert not second["gaps"] and second["pending"] is None
        assert store.read(second["verdict"])["answer"]["verdict"] == "not judged"
        closures = flow_review.requirement_verdicts(store, [second["verdict"], *verdicts[1:]])
        related = flow_review.trace(document, feature)[0]
        assert all(store.read(r)["answer"]["verdict"] ==
                   ("not judged" if store.read(r)["subject"] in related else "satisfied") for r in closures)
        store.cursor(replace(store.cursor(), requirementVerdicts=closures))
        assert obligations.remaining(store)
        assert store.units() == before

        # Make an ordinary new candidate whose persisted value breaks its actual check and guide.
        (run.project / "unit-002/value.txt").write_text("lost")
        _, broken, _ = gitwork.snapshots(run.project, cursor.candidate, ["**"], [], cursor.scratch)
        gitwork.checkout(run.project, broken)
        observations = wave_verify.verify(store, broken)
        gaps = proof.gaps(store, broken, observations)
        check = next(r for r, c in plan.select(document, "check").items()
                     if c["command"][-1] == "unit-002/check.py")
        failed, = [g for g in gaps if g["subject"] == check]
        assert failed["kind"] == "check" and failed["units"] == ["u002"]
        assert failed["commit"] == broken and store.read(failed["source"])["exitCode"] != 0
        wave_gap, = [g for g in gaps if store.read(g["source"])["unit"] is None]
        assert wave_gap["units"] == ["u001", "u002"]
        assert all(store.read(r)["commit"] == broken for r in observations)
        assert store.units() == before
        gitwork.unchanged(run.project, broken)
        (run.project / plan.GUIDE_PATH).write_text("Try the product without any linked paths")
        _, malformed, _ = gitwork.snapshots(run.project, broken, [plan.GUIDE_PATH], [], cursor.scratch)
        gitwork.checkout(run.project, malformed)
        guide_gap, = [g for g in proof.gaps(store, malformed, wave_verify.verify(store, malformed))
                      if g["subject"] == "guide:format"]
        assert guide_gap["units"] == ["guide"] and "link" in guide_gap["correction"]
        assert store.units() == before
        elapsed = time.monotonic() - began
        print(f"Shared-page FLOW, local gaps, bounded uncertainty: {elapsed:.2f}s", flush=True)
    finally:
        run.close()
