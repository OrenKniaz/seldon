"""Lossless packet-view source checks; no reviewer/provider is invoked."""

import copy
import hashlib
import json

import pytest
from tests.python.core.test_roundtable import store as roundtable_store

from codex_harness.core import acceptance, roundtable

store = roundtable_store


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def repeated_scenarios(*, prefix=None):
    prefix = prefix or '"{python}" -I -S -B -c "' + "encoded-program-Î»" * 400
    return [
        {
            "id": f"S{index}",
            "title": f"Synthetic scenario {index}",
            "proofArtifact": {"source": "UNCHANGED ASSERTION BODY"},
            "steps": [
                {"command": prefix + f'" "{{repo}}" {index}', "expect": {"exit": 0}},
            ],
        }
        for index in range(5)
    ]


def test_prefix_projection_exact_reverse_utf8_and_record_immutability():
    original = repeated_scenarios()
    before = copy.deepcopy(original)
    projected, prefixes = acceptance._project_scenario_commands(original)
    assert prefixes and projected != original
    assert original == before
    assert acceptance._restore_scenario_commands(projected, prefixes) == original
    for old, new in zip(original, projected, strict=True):
        assert old["proofArtifact"] == new["proofArtifact"]
        ref = new["steps"][0]["plannedCommandReference"]
        assert prefixes[ref["prefixRef"]] + ref["suffix"] == old["steps"][0]["command"]
        assert ref["commandSha256"] == digest(old["steps"][0]["command"])
        assert new["steps"][0]["expect"] == old["steps"][0]["expect"]
    assert len(json.dumps(projected)) + len(json.dumps(prefixes)) + 1024 < len(json.dumps(original))


@pytest.mark.parametrize("case", ["short", "unique", "duplicate-id", "collision", "string-step"])
def test_unsupported_or_nonbeneficial_input_falls_back_unchanged(case):
    scenarios = repeated_scenarios()
    if case == "short":
        scenarios = repeated_scenarios(prefix="small")
    elif case == "unique":
        scenarios = scenarios[:1]
    elif case == "duplicate-id":
        scenarios[1]["id"] = scenarios[0]["id"]
    elif case == "collision":
        scenarios[0]["steps"][0]["plannedCommandReference"] = {"existing": "retain"}
    else:
        scenarios = [{"id": "S", "steps": [scenarios[0]["steps"][0]["command"]]}]
    before = copy.deepcopy(scenarios)
    projected, prefixes = acceptance._project_scenario_commands(scenarios)
    assert projected == scenarios == before
    assert not prefixes


@pytest.mark.parametrize("tamper", ["prefix", "suffix", "hash", "unknown", "extra-key"])
def test_restore_refuses_tampering_without_mutating_input(tamper):
    projected, prefixes = acceptance._project_scenario_commands(repeated_scenarios())
    ref = projected[0]["steps"][0]["plannedCommandReference"]
    if tamper == "prefix":
        prefixes[ref["prefixRef"]] += "altered"
    elif tamper == "suffix":
        ref["suffix"] += "altered"
    elif tamper == "hash":
        ref["commandSha256"] = "0" * 64
    elif tamper == "unknown":
        ref["prefixRef"] = "UNKNOWN"
    else:
        ref["extra"] = "ambiguous"
    before = copy.deepcopy((projected, prefixes))
    with pytest.raises(ValueError):
        acceptance._restore_scenario_commands(projected, prefixes)
    assert (projected, prefixes) == before


def test_deterministic_separate_bindings_and_recorded_command_hashes():
    scenarios = repeated_scenarios()
    # A second group gets its own ID, independent of recorded B1 bindings.
    other = repeated_scenarios(prefix='"{python}" -c "' + "OTHER-PROGRAM" * 500)
    for row in other:
        row["id"] = "other-" + row["id"]
    scenarios += other
    view, prefixes = acceptance._project_scenario_commands(scenarios)
    assert len(prefixes) == 2
    assert acceptance._project_scenario_commands(scenarios) == (view, prefixes)
    run = {
        "repo": "C:/repo/Î»",
        "results": [
            {
                "id": row["id"],
                "steps": [
                    {
                        "command": row["steps"][0]["command"]
                        .replace("{repo}", "C:/repo/Î»")
                        .replace("{python}", "C:/python.exe"),
                        "stdout": "EXACT OBSERVED OUTPUT",
                        "stderr": "EXACT OBSERVED STDERR",
                        "exit": 0,
                    }
                ],
            }
            for row in scenarios
        ],
    }
    before = copy.deepcopy(run)
    recorded, bindings = acceptance._project_run_commands(run, scenarios)
    restored = {row["id"]: row for row in acceptance._restore_scenario_commands(view, prefixes)}
    for result in recorded["results"]:
        step = result["steps"][0]
        ref = step["commandReference"]
        template = restored[ref["scenarioId"]]["steps"][ref["step"] - 1]["command"]
        assert digest(template) == ref["templateSha256"]
        for name in ("repo", "home", "data", "python"):
            if name in bindings[ref["bindingRef"]]:
                template = template.replace("{" + name + "}", bindings[ref["bindingRef"]][name])
        assert digest(template) == ref["expandedSha256"]
        assert step["stdout"] == "EXACT OBSERVED OUTPUT"
        assert step["stderr"] == "EXACT OBSERVED STDERR"
    assert run == before


def test_synthetic_29_scenarios_78_steps_fit_default_full_packet(store):
    """Portable SOURCE-ONLY representative structure, no historical packet bytes."""
    prefix = '"{python}" -c "' + "SYNTHETIC PROGRAM λ" * 280
    scenarios = []
    for index in range(29):
        scenarios.append(
            {
                "id": f"synthetic-{index}",
                "steps": [
                    {"command": prefix + f'" {index}', "expect": {"exit": 0}},
                    *[{"command": "short unique " + str(index), "expect": {"exit": 0}}]
                    * (2 if index < 20 else 1),
                ],
                "proofArtifact": {"source": "SYNTHETIC ASSERTIONS RETAINED"},
            }
        )
    assert len(scenarios) == 29 and sum(len(row["steps"]) for row in scenarios) == 78
    original = copy.deepcopy(scenarios)
    projected, prefixes = acceptance._project_scenario_commands(scenarios)
    assert acceptance._restore_scenario_commands(projected, prefixes) == scenarios == original
    protected = roundtable._test_bodies(store)
    # Synthetic context/observed output is retained byte for byte; all complete
    # protected bodies and source remain present, not counted as provider proof.
    context = "SYNTHETIC CONTEXT AND OBSERVED OUTPUT " * 8000
    header = json.dumps(prefixes, ensure_ascii=False) + "\n"
    old_packet = header + context + roundtable._scenario_section(scenarios) + protected
    section = roundtable._scenario_section(projected)
    packet = header + context + section + protected
    assert roundtable.estimated_tokens(old_packet) > 150000
    assert roundtable.estimated_tokens(packet) < 150000
    for provider in ("codex", "claude"):
        shards = roundtable.packet_shards(
            store, provider, packet=packet, included_header=header, scenario_section=section
        )
        assert len(shards) == 1 and not shards[0]["truncatedNodes"]
        assert shards[0]["packet"] == packet
        assert protected in packet and context in packet and section in packet


def test_scenario_override_survives_shared_shard_path_and_default_unchanged(store, monkeypatch):
    scenarios = repeated_scenarios()
    monkeypatch.setattr(store, "acceptance_scenarios", lambda: scenarios)
    projected, prefixes = acceptance._project_scenario_commands(scenarios)
    original = roundtable._scenario_section(scenarios)
    section = roundtable._scenario_section(projected)
    header = json.dumps(prefixes, ensure_ascii=False) + "\n"
    # Force the shared path; do not execute any generated command.
    shards = roundtable.packet_shards(
        store,
        "claude",
        packet="x" * 500000,
        included_header=header,
        scenario_section=section,
    )
    assert all(section in row["packet"] and header in row["packet"] for row in shards)
    assert all(original not in row["packet"] for row in shards)
    normal = roundtable.packet_shards(store, "claude")
    assert original in normal[0]["packet"]
    assert roundtable.packet_shards(store, "claude", packet="small")[0]["packet"] == "small"
    with pytest.raises(ValueError, match="scenario section"):
        roundtable.packet_shards(store, "claude", scenario_section={})
