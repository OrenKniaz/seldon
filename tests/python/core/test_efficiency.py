"""Recorded and synthetic telemetry checks; no provider executes."""

import json
from pathlib import Path

import pytest

from codex_harness.core.efficiency import FIELDS, aggregate, summarize_events


def _events(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    return path


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_application_permission_error_is_not_guard_denial(tmp_path, provider):
    output = "PermissionError: Permission denied; request blocked"
    if provider == "codex":
        events = [
            {
                "type": "item.completed",
                "item": {
                    "id": "c",
                    "type": "command_execution",
                    "command": "python app.py",
                    "exit_code": 1,
                    "aggregated_output": output,
                },
            }
        ]
    else:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "c",
                            "name": "Bash",
                            "input": {"command": "python app.py"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c",
                            "is_error": True,
                            "content": output,
                        }
                    ]
                },
            },
        ]
    summary = summarize_events(_events(tmp_path, events))
    assert summary["deniedCommands"] == 0
    assert summary["failedCommands"] == 1


def test_null_output_preserves_prior_observations(tmp_path):
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": str(i),
                "command": "python -m pytest",
                "exit_code": code,
                "aggregated_output": output,
            },
        }
        for i, (code, output) in enumerate([(0, "OK"), (1, None)])
    ]
    summary = summarize_events(_events(tmp_path, events))
    assert summary["commands"] == 2
    assert summary["failedCommands"] == 1


def test_efficiency_recorded_codex_events(tmp_path):
    # Verbatim fixture embedded below to keep all writes in the assigned files.
    path = tmp_path / "events.jsonl"
    path.write_text(RECORDED_CODEX_EVENTS, encoding="utf-8")
    (tmp_path / "request.json").write_text(json.dumps(RECORDED_REQUEST))
    (tmp_path / "result.json").write_text(json.dumps(RECORDED_RESULT))
    assert summarize_events(path) == {
        "commands": 2,
        "failedCommands": 0,
        "deniedCommands": 0,
        "redTestRuns": 0,
        "errorCommands": 0,
        "explorationCommands": 1,
        "fileChanges": 1,
        "inputTokens": 72790,
        "cachedInputTokens": 68608,
        "outputTokens": 795,
        "turns": 1,
        "durationSeconds": 101.76793,
    }


def test_efficiency_codex_failures_repeated_items_and_partial_output(tmp_path):
    command = {"id": "c1", "type": "command_execution", "command": "rg broken"}
    path = _events(
        tmp_path,
        [
            {"type": "turn.started"},
            {"type": "item.started", "item": {**command, "exit_code": None}},
            {"type": "item.completed", "item": {**command, "exit_code": 1}},
            {"type": "item.completed", "item": {**command, "exit_code": 1}},
            {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 4}},
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 5}},
        ],
    )
    with path.open("a") as stream:
        stream.write('\n{"type":')
    summary = summarize_events(path)
    assert summary["commands"] == 1 and summary["failedCommands"] == 1
    assert summary["explorationCommands"] == 1 and summary["turns"] == 2
    assert summary["inputTokens"] == 50 and summary["outputTokens"] == 9
    assert summary["cachedInputTokens"] is None and summary["durationSeconds"] is None


def test_efficiency_claude_stream(tmp_path):
    path = _events(
        tmp_path,
        [
            {"type": "system", "subtype": "init"},
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "cat src.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Read",
                            "input": {"file_path": "src.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "t3",
                            "name": "Edit",
                            "input": {"file_path": "src.py"},
                        },
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "is_error": True},
                        {"type": "tool_result", "tool_use_id": "t2", "content": "source"},
                    ]
                },
            },
            {
                "type": "result",
                "num_turns": 2,
                "duration_ms": 1500,
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 80,
                    "output_tokens": 15,
                },
            },
        ],
    )
    assert summarize_events(path) == {
        "commands": 2,
        "failedCommands": 1,
        "deniedCommands": 0,
        "redTestRuns": 0,
        "errorCommands": 1,
        "explorationCommands": 2,
        "fileChanges": 1,
        "inputTokens": 100,
        "cachedInputTokens": 80,
        "outputTokens": 15,
        "turns": 2,
        "durationSeconds": 1.5,
    }


def test_efficiency_missing_claude_fields_are_null(tmp_path):
    summary = summarize_events(
        _events(tmp_path, [{"type": "result", "usage": {"output_tokens": 9}}])
    )
    assert summary == {**dict.fromkeys(FIELDS), "outputTokens": 9}
    assert summarize_events(tmp_path / "absent.jsonl") == dict.fromkeys(FIELDS)


def test_efficiency_incomplete_commands_and_aggregate_unknowns(tmp_path):
    path = _events(
        tmp_path,
        [
            {
                "type": "item.started",
                "item": {
                    "id": "c",
                    "type": "command_execution",
                    "command": "python test.py",
                    "exit_code": None,
                },
            }
        ],
    )
    summary = summarize_events(path)
    assert summary["commands"] == 1 and summary["failedCommands"] is None
    assert all(summary[key] is None for key in ("deniedCommands", "redTestRuns", "errorCommands"))
    assert summary["inputTokens"] is None
    total = aggregate([summary, summary])
    assert total["commands"] == 2 and total["failedCommands"] is None
    assert aggregate([]) == dict.fromkeys(FIELDS)


def test_efficiency_claude_error_units(tmp_path):
    cases = [
        ("PowerShell", "python -m unittest", "Compound command requires approval", True),
        ("Bash", "python -c 'script'", "Newline can hide arguments", True),
        ("Bash", "git status", "This command requires approval", True),
        ("Bash", "python -m unittest", "Exit code 1\nFAIL: test_cli\nFAILED (failures=1)", True),
        ("Bash", "python -m unittest wrong_selector", "Exit code 5\nNO TESTS RAN", True),
        ("Bash", "python -m unittest", "OK", False),
    ]
    events = []
    for index, (tool, command, output, error) in enumerate(cases):
        events.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "id": f"m{index}",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": str(index),
                                "name": tool,
                                "input": {"command": command},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "tool_result_meta": [{"id": str(index), "non_execution_kind": "user-rejected"}]
                    if index < 3
                    else [],
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": str(index),
                                "is_error": error,
                                "content": [{"type": "text", "text": output}],
                            }
                        ]
                    },
                },
            ]
        )
    summary = summarize_events(_events(tmp_path, events))
    assert {
        key: summary[key]
        for key in (
            "commands",
            "deniedCommands",
            "redTestRuns",
            "failedCommands",
            "errorCommands",
        )
    } == {
        "commands": 6,
        "deniedCommands": 3,
        "redTestRuns": 1,
        "failedCommands": 1,
        "errorCommands": 5,
    }


@pytest.mark.parametrize("denied_status", ["declined", "denied", "permission_denied", "blocked"])
def test_efficiency_codex_error_units(tmp_path, denied_status):
    cases = [
        ("python -m pytest", 1, "FAILED tests/test_cli.py::test_cli", "failed"),
        ("python build.py", 2, "build failed", "failed"),
        ("python -m pytest", None, "", denied_status),
        ("python -m pytest", 5, "FAILED selector\nNO TESTS RAN", "failed"),
        ("python -m pytest", 5, "FAILED selector", "failed"),
        ("python -m pytest", 0, "1 passed", "completed"),
    ]
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": str(index),
                "type": "command_execution",
                "command": command,
                "exit_code": code,
                "aggregated_output": output,
                "status": status,
            },
        }
        for index, (command, code, output, status) in enumerate(cases)
    ]
    summary = summarize_events(_events(tmp_path, events))
    assert summary["commands"] == 6
    assert summary["deniedCommands"] == 1
    assert summary["redTestRuns"] == 1
    assert summary["failedCommands"] == 3
    assert summary["errorCommands"] == 5


def test_efficiency_real_watercli_d_log():
    path = Path(
        r"C:\Users\oren\claude-worktrees\_runs\watercli-exec\runs"
        r"\dogfood-watercli-20261014T135746114873\evidence\D\attempt\events.jsonl"
    )
    if not path.is_file():
        pytest.skip("Real water-cli D log is not present")
    summary = summarize_events(path)
    assert summary["commands"] == 17
    assert summary["deniedCommands"] == 5
    assert summary["redTestRuns"] == 2
    assert summary["failedCommands"] == 1
    assert summary["errorCommands"] == 8
    assert summary["explorationCommands"] == 7


# Captured 2026-10-14 from demo-retry/runs/dogfood-tipsplit-20261014T111109180482/
# evidence/A/attempt/events.jsonl; no provider executes in these tests.

RECORDED_CODEX_EVENTS = '{"type":"thread.started","thread_id":"01a13a1c-1cc0-7df1-a34c-feff6bdf13b1"}\n{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Under-development features enabled: default_mode_request_user_input. Under-development features are incomplete and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` in C:\\\\Users\\\\oren\\\\.codex\\\\config.toml."}}\n{"type":"turn.started"}\n{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"I’ll read the plan, vision, and existing money code, implement Bead A in `tipsplit/money.py`, then run the requested tests."}}\n{"type":"item.started","item":{"id":"item_2","type":"command_execution","command":"\\"C:\\\\\\\\Program Files\\\\\\\\PowerShell\\\\\\\\7\\\\\\\\pwsh.exe\\" -Command \\"Get-Content PLAN.md; Get-Content VISION.md; Get-Content tipsplit/money.py; Get-Content tests/test_money.py; rg --files -g AGENTS.md -g \'*money*\'\\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}\n{"type":"item.completed","item":{"id":"item_2","type":"command_execution","command":"\\"C:\\\\\\\\Program Files\\\\\\\\PowerShell\\\\\\\\7\\\\\\\\pwsh.exe\\" -Command \\"Get-Content PLAN.md; Get-Content VISION.md; Get-Content tipsplit/money.py; Get-Content tests/test_money.py; rg --files -g AGENTS.md -g \'*money*\'\\"","aggregated_output":"# tip-split: plan\\r\\n\\r\\n**Validation command** (run from this folder; standard library only):\\r\\n\\r\\n```\\r\\npython -m unittest discover -s tests -v\\r\\n```\\r\\n\\r\\n## Bead A: money core\\r\\n- **Owner files:** `tipsplit/money.py` only.\\r\\n- **Requirements:**\\r\\n  - **R1 (D1, D4):** `parse_amount(text) -> int` returns cents. For example `\\"12.34\\"` → 1234, `\\"$12\\"` → 1200 and `\\"12.3\\"` → 1230. It raises `ValueError` for negative amounts, more than two decimals, or garbage.\\r\\n  - **R2 (D2, D3):** `split(total_cents, people, tip_percent=0) -> list[int]`.\\r\\n    - It returns `people` shares that sum to the total plus the tip, where the tip is rounded half up.\\r\\n    - Shares differ by at most 1 cent, and leftover cents go to the first people.\\r\\n  - **R3:** `split` raises `ValueError` when `people < 1` or when `tip_percent` is not between 0 and 100.\\r\\n- **Proof:** `tests/test_money.py` passes.\\r\\n- **Memory fact to record on completion:** \\"tip-split amounts are integer cents everywhere; never floats (D1).\\"\\r\\n\\r\\n## Bead B: command line\\r\\n- **Owner files:** `tipsplit/cli.py` only.\\r\\n- **Depends on:** Bead A\'s public functions `parse_amount` and `split`, at integration time only.\\r\\n- **Requirements:**\\r\\n  - **R4 (D1):**\\r\\n    - `format_cents(cents) -> str` returns `\\"$12.34\\"`-style text.\\r\\n    - `format_shares(shares) -> list[str]` returns lines in the form `\\"Person 1: $38.34\\"`.\\r\\n  - **R5 (D5):**\\r\\n    - `main(argv) -> int` parses `TOTAL PEOPLE [--tip PCT]` and prints one line per person to stdout.\\r\\n    - It returns 0 on success.\\r\\n    - `cli.py` imports the money functions into its own namespace (`from tipsplit.money import parse_amount, split`) and calls them by those names, so Bead B\'s tests can stand in for Bead A.\\r\\n    - On invalid input it prints a one-line error to stderr and returns 2.\\r\\n- **Proof:** `tests/test_cli.py` passes.\\r\\n\\r\\n## Integration\\r\\n- **R6:** `main([\\"100.00\\", \\"3\\", \\"--tip\\", \\"15\\"])` prints exactly these lines:\\r\\n  - `Person 1: $38.34`\\r\\n  - `Person 2: $38.33`\\r\\n  - `Person 3: $38.33`\\r\\n  \\r\\n  They sum to $115.00.\\r\\n- **Proof:** `tests/test_end_to_end.py` passes on the integrated result.\\r\\n# tip-split: vision\\r\\n\\r\\n## One sentence\\r\\nA tiny command-line tool that splits a restaurant bill, plus tip, fairly between people, so nobody argues about the last cent.\\r\\n\\r\\n## First user moment\\r\\n`tipsplit 100.00 3 --tip 15` prints three lines that add up to exactly $115.00, with the extra cent given to the first person.\\r\\n\\r\\n## Accepted decisions\\r\\n- **D1. Money is integer cents everywhere.** No floats are ever used for amounts.\\r\\n- **D2. Fair split.** Shares differ by at most one cent. Leftover cents go to the first people in order.\\r\\n- **D3. Tip rounding.** The tip is a whole-number percentage from 0 to 100, rounded half up to the cent.\\r\\n- **D4. Input forms.** Amounts like `12`, `12.3`, `12.34` and `$12.34` are accepted. Negative amounts, more than two decimals and garbage are rejected.\\r\\n- **D5. Error contract.** Invalid input prints a one-line error to stderr and exits with code 2. Success exits with 0.\\r\\n\\r\\n## Out of scope\\r\\n- Currencies other than dollars.\\r\\n- Uneven or weighted splits.\\r\\n- Saving history.\\r\\n\\"\\"\\"Bead A: money core. See PLAN.md requirements R1-R3.\\"\\"\\"\\r\\n\\r\\nfrom __future__ import annotations\\r\\n\\r\\n\\r\\ndef parse_amount(text: str) -> int:\\r\\n    raise NotImplementedError(\\"Bead A: implement R1\\")\\r\\n\\r\\n\\r\\ndef split(total_cents: int, people: int, tip_percent: int = 0) -> list[int]:\\r\\n    raise NotImplementedError(\\"Bead A: implement R2 and R3\\")\\r\\n\\"\\"\\"Bead A proof: requirements R1-R3 (decisions D1-D4).\\"\\"\\"\\r\\n\\r\\nimport unittest\\r\\n\\r\\nfrom tipsplit.money import parse_amount, split\\r\\n\\r\\n\\r\\nclass ParseAmountR1(unittest.TestCase):\\r\\n    def test_accepted_forms_become_integer_cents(self):\\r\\n        for text, cents in [\\r\\n            (\\"12.34\\", 1234),\\r\\n            (\\"12\\", 1200),\\r\\n            (\\"12.3\\", 1230),\\r\\n            (\\"$12.34\\", 1234),\\r\\n            (\\"$12\\", 1200),  # R1 explicitly includes dollar-prefixed whole amounts.\\r\\n            (\\"0.29\\", 29),\\r\\n            (\\"90071992547409.93\\", 9007199254740993),  # D1: exact integer cents.\\r\\n            (\\" 0.05 \\", 5),\\r\\n            (\\"0\\", 0),\\r\\n        ]:\\r\\n            with self.subTest(text=text):\\r\\n                value = parse_amount(text)\\r\\n                self.assertEqual(value, cents)\\r\\n                self.assertIs(type(value), int)\\r\\n\\r\\n    def test_rejected_forms_raise_value_error(self):\\r\\n        for text in [\\"-1\\", \\"-$1.00\\", \\"12.345\\", \\"abc\\", \\"\\", \\"1.2.3\\", \\"$\\", \\"1e3\\"]:\\r\\n            with self.subTest(text=text), self.assertRaises(ValueError):\\r\\n                parse_amount(text)\\r\\n\\r\\n\\r\\nclass SplitR2(unittest.TestCase):\\r\\n    def test_shares_sum_to_total_plus_rounded_tip(self):\\r\\n        self.assertEqual(sum(split(10000, 3, 15)), 11500)\\r\\n        # 15% of 1001 cents = 150.15 -> 150; 50% of 1 cent = 0.5 -> rounds half up to 1.\\r\\n        self.assertEqual(sum(split(1001, 2, 15)), 1151)\\r\\n        self.assertEqual(sum(split(1, 1, 50)), 2)\\r\\n\\r\\n    def test_fair_split_gives_leftover_cents_to_first_people(self):\\r\\n        self.assertEqual(split(10000, 3, 15), [3834, 3833, 3833])\\r\\n        self.assertEqual(split(100, 3), [34, 33, 33])\\r\\n        self.assertEqual(split(101, 3), [34, 34, 33])\\r\\n        self.assertEqual(split(300, 3), [100, 100, 100])\\r\\n\\r\\n    def test_shares_differ_by_at_most_one_cent_and_are_ints(self):\\r\\n        for total in range(0, 500, 37):\\r\\n            for people in range(1, 8):\\r\\n                shares = split(total, people, 10)\\r\\n                with self.subTest(total=total, people=people):\\r\\n                    self.assertEqual(len(shares), people)\\r\\n                    self.assertEqual(sum(shares), total + (total * 10 + 50) // 100)\\r\\n                    self.assertLessEqual(max(shares) - min(shares), 1)\\r\\n                    self.assertTrue(all(type(s) is int for s in shares))\\r\\n\\r\\n    def test_documented_tip_range_and_order_preserve_every_cent(self):\\r\\n        # R2 / D1-D3: valid endpoints, small bills, ordered leftovers and precision.\\r\\n        for total, people, tip, expected in [\\r\\n            (0, 4, 100, [0, 0, 0, 0]),\\r\\n            (111, 4, 10, [31, 31, 30, 30]),\\r\\n            (99, 5, 1, [20, 20, 20, 20, 20]),\\r\\n            (1, 3, 100, [1, 1, 0]),\\r\\n            (5, 2, 50, [4, 4]),\\r\\n            (9007199254740993, 3, 100, [6004799503160662] * 3),\\r\\n        ]:\\r\\n            with self.subTest(total=total, people=people, tip=tip):\\r\\n                shares = split(total, people, tip)\\r\\n                self.assertEqual(shares, expected)\\r\\n                self.assertTrue(all(type(share) is int for share in shares))\\r\\n\\r\\n\\r\\nclass SplitValidationR3(unittest.TestCase):\\r\\n    def test_invalid_people_or_tip_raise_value_error(self):\\r\\n        for args in [(100, 0), (100, -2), (100, 2, -1), (100, 2, 101)]:\\r\\n            with self.subTest(args=args), self.assertRaises(ValueError):\\r\\n                split(*args)\\r\\n\\r\\n\\r\\nif __name__ == \\"__main__\\":\\r\\n    unittest.main()\\r\\ntipsplit\\\\money.py\\ntests\\\\test_money.py\\n","exit_code":0,"status":"completed"}}\n{"type":"item.started","item":{"id":"item_3","type":"file_change","changes":[{"path":"C:\\\\Users\\\\oren\\\\claude-worktrees\\\\_runs\\\\demo-retry\\\\runs\\\\dogfood-tipsplit-20261014T111109180482\\\\bead-A\\\\tipsplit\\\\money.py","kind":"update"}],"status":"in_progress"}}\n{"type":"item.completed","item":{"id":"item_3","type":"file_change","changes":[{"path":"C:\\\\Users\\\\oren\\\\claude-worktrees\\\\_runs\\\\demo-retry\\\\runs\\\\dogfood-tipsplit-20261014T111109180482\\\\bead-A\\\\tipsplit\\\\money.py","kind":"update"}],"status":"completed"}}\n{"type":"item.started","item":{"id":"item_4","type":"command_execution","command":"\\"C:\\\\\\\\Program Files\\\\\\\\PowerShell\\\\\\\\7\\\\\\\\pwsh.exe\\" -Command \'python -m unittest discover -s tests -p test_money.py; git diff -- tipsplit/money.py; git status --short\'","aggregated_output":"","exit_code":null,"status":"in_progress"}}\n{"type":"item.completed","item":{"id":"item_4","type":"command_execution","command":"\\"C:\\\\\\\\Program Files\\\\\\\\PowerShell\\\\\\\\7\\\\\\\\pwsh.exe\\" -Command \'python -m unittest discover -s tests -p test_money.py; git diff -- tipsplit/money.py; git status --short\'","aggregated_output":".......\\r\\n----------------------------------------------------------------------\\r\\nRan 7 tests in 0.002s\\r\\n\\r\\nOK\\r\\ndiff --git a/tipsplit/money.py b/tipsplit/money.py\\nindex 7d52c57..10fd905 100644\\n--- a/tipsplit/money.py\\n+++ b/tipsplit/money.py\\n@@ -2,10 +2,25 @@\\n \\n from __future__ import annotations\\n \\n+import re\\n+\\n \\n def parse_amount(text: str) -> int:\\n-    raise NotImplementedError(\\"Bead A: implement R1\\")\\n+    \\"\\"\\"Parse a dollar amount into exact integer cents.\\"\\"\\"\\n+    match = re.fullmatch(r\\"\\\\$?([0-9]+)(?:\\\\.([0-9]{1,2}))?\\", text.strip())\\n+    if match is None:\\n+        raise ValueError(\\"Enter a nonnegative dollar amount with at most two decimals.\\")\\n+    dollars, fraction = match.groups()\\n+    return int(dollars) * 100 + int((fraction or \\"0\\").ljust(2, \\"0\\"))\\n \\n \\n def split(total_cents: int, people: int, tip_percent: int = 0) -> list[int]:\\n-    raise NotImplementedError(\\"Bead A: implement R2 and R3\\")\\n+    \\"\\"\\"Split the bill and rounded tip, giving extra cents to the first people.\\"\\"\\"\\n+    if people < 1:\\n+        raise ValueError(\\"People must be at least 1.\\")\\n+    if not 0 <= tip_percent <= 100:\\n+        raise ValueError(\\"Tip percent must be between 0 and 100.\\")\\n+\\n+    tip_cents = (total_cents * tip_percent + 50) // 100\\n+    share, leftover = divmod(total_cents + tip_cents, people)\\n+    return [share + (index < leftover) for index in range(people)]\\n M tipsplit/money.py\\n","exit_code":0,"status":"completed"}}\n{"type":"item.completed","item":{"id":"item_5","type":"agent_message","text":"Implemented Bead A in `tipsplit/money.py` only:\\n\\n- Exact dollar parsing into integer cents.\\n- Half-up tip rounding and fair shares, with extra cents assigned first.\\n- Validation for invalid amounts, people counts, and tip ranges.\\n\\nRan `python -m unittest discover -s tests -p test_money.py`: **7 tests passed**.\\n\\nNo other files changed; nothing installed, committed, or pushed."}}\n{"type":"turn.completed","usage":{"input_tokens":72790,"cached_input_tokens":68608,"cache_write_input_tokens":0,"output_tokens":795,"reasoning_output_tokens":117}}\n'  # noqa: E501
RECORDED_REQUEST = {"startedAt": "2026-10-14T11:11:22.646736+00:00"}
RECORDED_RESULT = {"finishedAt": "2026-10-14T11:13:04.414666+00:00"}


def test_claude_stream_with_plain_text_system_messages(tmp_path) -> None:
    """Real Claude Code streams include system events whose message is a string."""

    events = [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "system", "subtype": "status", "message": "plain text status"},
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"command": "python -B -m unittest"},
                    },
                ],
            },
        },
        {"type": "user", "message": {"content": "plain user text"}},
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "is_error": False},
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "m2",
                "content": [
                    {"type": "text", "text": "HARNESS-RESULT: done"},
                ],
            },
        },
        {
            "type": "result",
            "num_turns": 3,
            "duration_ms": 1500,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    summary = summarize_events(path)

    assert summary["commands"] == 1
    assert summary["failedCommands"] == 0
    assert summary["turns"] == 3


def test_red_test_mentioning_permission_is_not_a_denial(tmp_path):
    from codex_harness.core.efficiency import _command_outcome

    command = {
        "command": "python -B -m unittest tests.test_io",
        "exit_code": 1,
        "aggregated_output": "FAIL: test_permission_denied_path (tests.test_io.IoTests)",
    }
    assert _command_outcome(command) == "redTestRuns"
