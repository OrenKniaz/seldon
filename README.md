# Seldon

Seldon aims to turn a person's dream into a real, working product, using agents to plan,
build and prove it. CHARTER.md section 1 defines the goal; BLUEPRINT.md defines the machine.
Install with `pip install .`, then register the chat front door with either:
```sh
codex mcp add seldon -- seldon mcp
claude mcp add seldon -- seldon mcp
```
then tell the agent your dream.

Run the semantic journey suite from the repository root in PowerShell:
```powershell
New-Item -ItemType Directory -Force C:/seldon/tests/cutover-1
$env:PYTHONPATH = "$PWD/src;$PWD/tests/seldon"
python -B -m pytest tests/seldon -q -p no:cacheprovider --basetemp=C:/seldon/tests/cutover-1/bt
```
