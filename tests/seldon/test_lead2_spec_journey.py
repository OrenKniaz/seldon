"""Fresh Lead spec journeys through the real run store, without a provider or desktop."""

from contextlib import closing

import pytest

from seldon.lead2 import (
    Conflict,
    SpecStore,
    promise_status,
    render_person,
    render_prompt,
    trace_observation,
    trace_words,
)
from seldon.store import Store

pytestmark = pytest.mark.fast
WORDS = "The door lead and volunteer must agree on tonight's count. Keep yesterday out of it."
COMMIT = "a" * 40
NEXT_COMMIT = "b" * 40


def add(name, kind, **body):
    return dict(op="add", name=name, node=dict(kind=kind, **body))


def amend(ref, **node):
    return dict(op="amend", id=ref, node=node)


def link(source, target, op="link"):
    return dict(op=op, source=source, target=target)


def edit(spec, revision, reason, *edits, mode="substantive"):
    return spec.apply(dict(revision=revision, reason=reason, mode=mode, edits=list(edits)),
                      author="planner")


def reply(store, gate, revision, words, effect="approve"):
    """Stand in for the later controller at its canonical decision record boundary."""
    subject = {"lead2_revision": revision}
    with store.transaction():
        request = store.record("decision_request", dict(gate=gate, subject=subject, origin="chat"))
        raw = store.record("user_words", dict(request=request, origin="chat", words=words))
        return store.record("approval", dict(request=request, subject=subject, userWords=raw,
                                             words=raw, effect=effect, scripted=True))


def grow(store):
    spec = SpecStore(store)
    align = edit(spec, 0, "Grow the story with the owner",
                 link("$shift", "$words"),  # Forward name and reversed edge are normalized.
                 add("words", "dream", words=WORDS),
                 add("shift", "story", story="Hand off the live door count"),
                 add("lead", "person", role="Door lead"),
                 add("vol", "person", role="Volunteer"),
                 add("close", "story", story="Close the night"),
                 link("$lead", "$shift"), link("$vol", "$shift"), link("$words", "$close"),
                 link("$lead", "$close"))
    a = reply(store, "A", align.revision, "Yes, that is the door process.")
    spec.bind_decision("A", align.revision, a)
    names = align.names
    what = edit(spec, align.revision, "Set observable outcomes",
                add("enter", "promise", action="Volunteer admits a guest", result="Tonight increases by one",
                    not_met_if="Yesterday's total changes"),
                add("hand", "promise", action="Lead takes over", result="Lead sees the same count",
                    not_met_if="Lead sees a different total"),
                add("close_total", "promise", action="Lead closes the night", result="Final count is saved",
                    not_met_if="Restart loses the saved count"),
                link(names["$shift"], "$enter"), link(names["$shift"], "$hand"),
                link(names["$close"], "$close_total"))
    names.update(what.names)
    how = edit(spec, what.revision, "Choose how to demonstrate the handoff and the close",
               add("ledger", "design", decision="Keep a dated ledger", reason="Separate each night",
                   words="Keep yesterday out of it."),
               add("board", "interface", contract="Both roles can read the same live count"),
               add("handoff", "check", action="Admit one guest and change roles",
                   expected="Both roles see the live count increase exactly once"),
               add("close_api", "interface", contract="Closing returns the saved final count"),
               add("restart", "walk", scenario="Close the night and reopen as door lead",
                   interruption="Restart before reading the archive"),
               link(names["$enter"], "$ledger"), link(names["$hand"], "$board"),
               link("$ledger", "$handoff"), link("$board", "$handoff"),
               link("$ledger", names["$lead"]), link(names["$close_total"], "$close_api"),
               link("$close_api", "$restart"), link(names["$close"], "$restart"),
               link(names["$lead"], "$restart"))
    names.update(how.names)
    b = reply(store, "B", how.revision, "Build the handoff and the close.")
    spec.bind_decision("B", how.revision, b)
    return spec, names, how.revision, a, b


def observe(spec, revision, names, witness, *promises, outcome="passed", commit=COMMIT):
    return spec.observe(revision, promises=[names[promise] for promise in promises],
                        witness=names[witness], commit=commit, session="door-builder-1",
                        outcome=outcome, result="Door exercise recorded on the named candidate.")


def states(spec, revision, names, commit=COMMIT):
    status = promise_status(spec, revision, commit)
    return tuple(status[names[key]] for key in ("$enter", "$hand", "$close_total"))


def test_door_spec_proof_tracks_the_current_graph_and_candidate(tmp_path):
    """A shared check edit reopens both promises, while another story and historical trace survive.

    This catches invalidation that follows just the first or current link, or reuses a pass on
    another commit. The existing Lead journeys do not exercise a revisioned spec graph.
    """
    with closing(Store(tmp_path)) as store:
        spec, names, revision, a, b = grow(store)
        enter, hand, handoff = (names[key] for key in ("$enter", "$hand", "$handoff"))
        assert states(spec, revision, names) == ("planned", "planned", "planned")
        observe(spec, revision, names, "$handoff", "$enter", outcome="built")
        assert states(spec, revision, names)[0] == "built"
        proof = observe(spec, revision, names, "$handoff", "$enter", "$hand")
        close = observe(spec, revision, names, "$restart", "$close_total")
        assert states(spec, revision, names) == ("proven-current",) * 3
        assert states(spec, revision, names, NEXT_COMMIT) == ("stale",) * 3

        label = spec.snapshot().graph.nodes[handoff]
        wording = edit(spec, revision, "Polish the check's punctuation and spacing",
                       amend(handoff, **{**label, "action": f"  {label['action'].upper()}! "}),
                       mode="wording-only").revision
        assert states(spec, wording, names) == ("proven-current",) * 3
        changed = edit(spec, wording, "Exercise the late-night reset before the handoff",
                       amend(handoff, **{**label, "action": "Reset after midnight, then admit one guest"}),
                       mode="wording-only")
        spec = SpecStore(store)
        assert states(spec, changed.revision, names) == ("stale", "stale", "proven-current")

        view = render_person(spec, commit=COMMIT, since=wording)
        assert "Door lead · Volunteer" in view or "Volunteer · Door lead" in view
        assert "Reset after midnight" in view
        assert view.count("(stale)") == 2
        read_revision, section = spec.read_slice(enter)
        assert read_revision == changed.revision and hand not in section.nodes
        prompt = render_prompt(spec, node=enter, commit=COMMIT)
        assert WORDS in prompt.splitlines()[1] and "[stale]" in prompt and handoff in prompt

        observe(spec, changed.revision, names, "$handoff", "$enter", outcome="failed")
        observe(spec, changed.revision, names, "$handoff", "$hand", outcome="not-verifiable-here")
        assert states(spec, changed.revision, names) == ("failed", "not-verifiable-here", "proven-current")
        observe(spec, changed.revision, names, "$handoff", "$enter", "$hand")
        unlinked = edit(spec, changed.revision, "Replace the volunteer's witness in a later edit",
                        link(names["$ledger"], handoff, "unlink"), mode="wording-only").revision
        assert states(spec, unlinked, names) == ("stale", "proven-current", "proven-current")
        with pytest.raises(ValueError, match="Link"):
            observe(spec, unlinked, names, "$handoff", "$enter")

        trace = trace_observation(spec, proof.id)
        assert (trace.observation.commit, trace.observation.session, trace.observation.revision) == (
            COMMIT, "door-builder-1", revision)
        assert set(trace.observation.promises) == {enter, hand}
        assert (names["$ledger"], names["$lead"]) in trace.graph.links
        assert trace.graph.nodes[names["$ledger"]]["words"] == "Keep yesterday out of it."
        assert trace.graph.nodes[names["$words"]]["words"] == WORDS
        assert trace.graph.nodes[handoff]["action"] == label["action"]
        assert set(trace.approvals.values()) == {a, b}
        down = {item.observation.id for item in trace_words(spec, names["$words"])}
        assert {proof.id, close.id} <= down
        retired = edit(spec, unlinked, "Retire the old handoff check", dict(op="retire", id=handoff))
        assert handoff not in spec.read_slice(hand)[1].nodes
        assert states(spec, retired.revision, names) == ("stale", "stale", "proven-current")


def test_door_spec_keeps_conflicting_and_unapproved_edits_out_of_the_head(tmp_path):
    """Competing planners and a late owner reply cannot publish over a newer accepted snapshot.

    Reopen the run DB to catch in-memory proposal state or partial writes. Existing decision
    journeys have no candidate to bind independently of the spec head. An edit labeled as
    wording-only could previously reverse a promise while its old proof stayed current.
    """
    with closing(Store(tmp_path)) as store:
        spec, names, revision, _, _ = grow(store)
        hand = names["$hand"]
        check = names["$handoff"]
        old_revision, view = spec.read_slice(hand)
        promise = view.nodes[hand]
        observe(spec, revision, names, "$handoff", "$hand")
        changed_promise = {**promise, "result": "Lead sees the count with the last volunteer's name"}
        pending = edit(spec, old_revision, "Add the last volunteer to the handoff",
                       amend(hand, **changed_promise), mode="wording-only")
        assert not pending.accepted
        assert spec.snapshot().revision == revision
        assert spec.read_slice(hand)[1].nodes[hand] == promise
        assert states(spec, revision, names)[1] == "proven-current"
        assert states(spec, pending.revision, names)[1] == "stale"
        assert "Scope proposal" in render_person(spec, revision=pending.revision, since=revision)

        with closing(Store(tmp_path)) as concurrent_store:
            concurrent = SpecStore(concurrent_store)
            newer = edit(concurrent, revision, "Choose a witnessed handoff",
                         amend(check, **{**view.nodes[check], "expected": "Lead watches the admission"}))
        with pytest.raises(Conflict, match="Lead watches the admission") as error:
            edit(spec, old_revision, "Plan another check",
                 add(None, "check", action="Retry", expected="Same"))
        assert error.value.current == newer.revision
        with pytest.raises(ValueError, match="Unknown node"):
            edit(spec, newer.revision, "Try an invalid check link",
                 add("orphan", "check", action="Retry", expected="Same"), link("$orphan", "$missing"))
        assert spec.snapshot().revision == newer.revision
        assert not any(node.get("action") == "Retry" for node in spec.snapshot().graph.nodes.values())

        stale_reply = reply(store, "scope", pending.revision, "Yes, include the name.")
        with pytest.raises(Conflict):
            spec.bind_decision("scope", pending.revision, stale_reply)
        replacement = edit(spec, newer.revision, "Propose the name with the current check",
                           amend(hand, **changed_promise), mode="scope-proposal")

    with closing(Store(tmp_path)) as store:
        reopened = SpecStore(store)
        assert reopened.snapshot().revision == newer.revision
        assert {item.revision for item in reopened.proposals()} == {pending.revision, replacement.revision}
        with pytest.raises(ValueError, match="bound to scope"):
            reopened.bind_decision("scope", replacement.revision, stale_reply)
        decline = reply(store, "scope", replacement.revision, "Keep the handoff anonymous.", effect="decline")
        with pytest.raises(ValueError, match="bound to scope"):
            reopened.bind_decision("scope", replacement.revision, decline)
        assert reopened.snapshot().revision == newer.revision
        approved = reply(store, "scope", replacement.revision, "Yes, include the last volunteer's name.")
        reopened.bind_decision("scope", replacement.revision, approved)
        reopened.bind_decision("scope", replacement.revision, approved)
        assert reopened.snapshot().revision == replacement.revision
        assert reopened.snapshot().parent == newer.revision
        assert reopened.read_slice(hand)[1].nodes[hand] == changed_promise
        assert reopened.read_slice(hand, revision)[1].nodes[hand] == promise
        assert reopened.pins()["B"] == revision
        assert states(reopened, replacement.revision, names) == ("planned", "stale", "planned")
        proof = observe(reopened, replacement.revision, names, "$handoff", "$hand")
        assert states(reopened, replacement.revision, names)[1] == "proven-current"
        assert trace_observation(reopened, proof.id).approvals["scope", replacement.revision] == approved
