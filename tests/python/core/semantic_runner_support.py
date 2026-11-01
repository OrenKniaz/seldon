import json

from codex_harness.core.semantic_runner import run

WEB_TARGET = {"page": {"kind": "web", "entry": "index.html"}}


WEB_PAGE = """<!doctype html><html><meta charset="utf-8"><title>Fixture</title>
<style>body{margin:16px;color:#111;background:white;font:16px Arial}
a,button{color:#111;background:white} :focus{outline:2px solid black}

@keyframes fade{from{opacity:.95}to{opacity:1}}</style>
<h1>Welcome</h1><p>Useful content</p><a href="#details">Details</a>
<button
onclick="document.querySelector('#extra').hidden=!document.querySelector('#extra').hidden">Reveal</button>
<section id="details"><h2>Details section</h2><p id="extra" hidden>Revealed content</p></section>
</html>"""


def web_document(*, split=False):
    from codex_harness.core.semantic_schema import WEB

    actions = [
        {
            "id": f"web-{width}-{motion}",
            "web": {
                "target": "page",
                "operation": "visit",
                "viewport": {"width": width, "height": height},
                "reducedMotion": motion,
                "steps": [
                    {"op": "open"},
                    {"op": "assertVisible", "locator": {"text": "Welcome"}},
                    {"op": "assertHidden", "locator": {"selector": "#extra"}},
                    {"op": "key", "key": "Tab"},
                    {"op": "assertFocus", "locator": {"role": "link", "name": "Details"}},
                    {"op": "key", "key": "Enter"},
                    {"op": "assertFragment", "fragment": "#details"},
                    {"op": "click", "locator": {"selector": "button"}},
                    {"op": "assertVisible", "locator": {"text": "Revealed content"}},
                    {"op": "focus", "locator": {"selector": "button"}},
                    {"op": "key", "key": "Space"},
                    {"op": "assertHidden", "locator": {"selector": "#extra"}},
                    {"op": "key", "key": "Escape"},
                ],
            },
        }
        for width, height in [(375, 812), (1440, 900)]
        for motion in ["no-preference", "reduce"]
    ]
    result = {
        "schema": "seldon/scenarios-v1",
        "scenarios": [
            {
                "id": "web",
                "requirementRefs": ["R1@1"],
                "contractRefs": ["page@1"],
                "setup": {},
                "actions": actions,
                "observe": [
                    {
                        "action": a["id"],
                        **{
                            k: 0 if k in ("consoleErrors", "externalRequests") else True
                            for k in WEB
                        },
                    }
                    for a in actions
                ],
                "verdict": "all",
            }
        ],
    }

    if split:
        base = result["scenarios"][0]
        result["scenarios"] = [
            dict(base, id=a["id"], actions=[a], observe=[o])
            for a, o in zip(base["actions"], base["observe"], strict=True)
        ]
    return result


def browser_readiness(tmp_path_factory):
    """Retain real readiness through the production launcher; startup failure is an error."""
    root = tmp_path_factory.mktemp("web-browser-readiness")
    (root / "index.html").write_text(WEB_PAGE, encoding="utf-8")
    result = run(web_document(), root, WEB_TARGET, "validation", evidence_dir=root.parent / "pngs")[
        0
    ]
    receipt = root.parent / "web-browser-readiness.json"
    receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["state"] == "PASS", result
    return result
