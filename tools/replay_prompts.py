"""Offline paired prompt measurement (C4/C14, R12/R16). Run with --help for the lead workflow."""

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from seldon import plan, providers, schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PIN = "21b638118f9ad549d542a083ca81b7b441f3bcac"
TREE = "a8c2f54ce2db65dd5409c372239a90205d7b4f68"
RENDERER_TREE = "b3c1b71d7a1dd75a1c5df0ce3ac0f79bcd603d95"
RIG = Path("C:/seldon/tests/m1")
Q = "qbu-dbc5db9-s01"
S = "shelflife-main-6433526-x01"
SPLIT = "shelflife-main-ab2ae1f-s01"
WHOLE = {"9996c71c", "61876576"}
WORK = (*sorted(WHOLE), "620aaddb", "77d99488", "2338f24d")
FOLDED = {"6a5e00ae", "4cf3d2b9"}
RENDER_ONLY = "316c807f"
CASES = {
    **dict.fromkeys(("9996c71c", "0f21e094", "6a5e00ae", "c757d11a", "87e7843d", "0876e396",
                     "60506a4c", "315f0928", "903e66fa", "5819ec02", "1067a45a", "b513bb54",
                     RENDER_ONLY), Q),
    **dict.fromkeys(("61876576", "4cf3d2b9"), S),
    **dict.fromkeys(WORK[2:], SPLIT),
}
AXES = ("codeQuality", "completeness", "fidelity")
GUARDS = ("scopeViolation", "falseApproval", "unsupportedProof")
MODEL = "gpt-6.1-terra"
EFFORT = "medium"
TIMEOUT = 1800
RNG = secrets.SystemRandom()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def read_json(path):
    return json.loads(path.read_bytes())


def put(path, data):
    """Publish a complete immutable artifact. Its parent belongs to this experiment."""
    with path.open("xb") as output:
        output.write(data)


def put_json(path, value):
    put(path, json_bytes(value))


def git(repo, *args, data=None):
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
        input=data, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def hashes(folder):
    return {path.name: digest(path.read_bytes()) for path in sorted(folder.iterdir()) if path.is_file()}


def safe_path(name):
    parts = name.split("/")
    if any(not part or part.rstrip(" .") != part or part in (".", "..")
           or part.lower() in (".git", ".seldon") or part.lower().startswith(".env")
           or any(ch in part for ch in '\\:') for part in parts):
        raise ValueError(f"Unsupported replay path: {name}")
    return Path(*parts)


def base_files(repo, revision):
    """Copy only the exact tree's blobs. No working files, history, filters or later peer refs."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Missing exact as-of-call Git revision")
    tree = git(repo, "rev-parse", f"{revision}^{{tree}}").decode().strip()
    files = {}
    for line in git(repo, "ls-tree", "-rz", revision).split(b"\0"):
        if not line:
            continue
        meta, name = line.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        name = name.decode()
        safe_path(name)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError(f"Replay requires regular Git blobs: {name}")
        files[name] = (mode, git(repo, "cat-file", "blob", oid))
    return tree, files


def question_files(folder, question):
    originals = ("question.json", "schema.json", "prompt.txt", "instructions.txt", "invocation.json")
    files = {name: (folder / name).read_bytes() for name in originals}
    if json.loads(files["schema.json"]) != question["schema"]:
        raise ValueError("Native schema differs from the recorded question")
    for index, _ in enumerate(question.get("images", [])):
        name = f"image-{index}.png"
        files[name] = (folder / name).read_bytes()
    for path, meta in question["facts"].get("Check artifacts", {}).items():
        # The producer names the stored bytes by SHA-256 and supplies recorded image copies.
        sha = Path(path).name
        if not any(digest(data) == sha and len(data) == meta["bytes"]
                   for name, data in files.items() if name.startswith("image-")):
            raise ValueError(f"Missing recorded attachment bytes: {sha}")
    return files


def render(question, arm, images):
    prompt, instructions = providers.render_question(question, "codex", images, prompts2=(arm == "B"))
    return {"prompt.txt": prompt.encode(), "instructions.txt": instructions.encode()}


def setup_snapshot(run):
    setup = read_json(run / "setup.json")
    return {key: setup[key] for key in ("python", "node", "codexCommand", "evidenceDirectory")}


def prepare_case(rig, case, labels, root):
    run = rig / CASES[case]
    folder, = (run / "calls").glob(f"call-{case}*")
    question = read_json(folder / "question.json")
    files = question_files(folder, question)
    invocation = json.loads(files["invocation.json"])
    if invocation["provider"].get("provider", "codex") != "codex":
        raise ValueError("The recorded call did not use Codex")
    result = {"run": str(run), "call": folder.name, "kind": question["kind"], "historical": {
        name: digest(data) for name, data in files.items()}, "state": "prepared"}
    images = sorted(name for name in files if name.startswith("image-"))
    result["evidence"] = {name: result["historical"][name] for name in images}
    result["factsHash"] = digest(json_bytes(question["facts"]))
    result["base"] = {"facts": result["factsHash"]}
    base = root / "base"
    if question["kind"] == "WORK":
        if question.get("execution", {}).get("session"):
            result.update(state="render-only", reason="Pre-turn session history is unavailable")
        else:
            execution = question["facts"]["Execution facts"]
            revision = execution["Base"]
            tree, blobs = base_files(run / "product", revision)
            if set(execution["Paths present"]) - blobs.keys():
                raise ValueError("Recorded files are missing from the base")
            base.mkdir()
            for name, (_, data) in blobs.items():
                path = base / safe_path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                put(path, data)
            result["setup"] = setup_snapshot(run)
            result["base"] = {"commit": revision, "tree": tree,
                              "files": {name: [mode, digest(data)] for name, (mode, data) in blobs.items()}}
    elif question["kind"] in ("JUDGE", "PRODUCT"):
        commits = {meta["commit"] for meta in question["facts"].get("Check artifacts", {}).values()
                   if meta.get("commit")}
        if len(commits) == 1:
            revision, = commits
            tree, _ = base_files(run / "product", revision)
            result["base"].update(commit=revision, tree=tree)
    original = root / "historical"
    original.mkdir()
    for name, data in files.items():
        put(original / name, data)
    result["rendered"] = {}
    for arm, label in labels.items():
        target = root / label
        target.mkdir()
        rendered = render(question, arm, [original / name for name in images])
        for name, data in {**rendered, "schema.json": files["schema.json"],
                           **{name: files[name] for name in images}}.items():
            put(target / name, data)
        result["rendered"][label] = {"hashes": hashes(target), "promptBytes": len(rendered["prompt.txt"])}
        if arm == "A":
            result["historicalDelta"] = {
                name: list(difflib.unified_diff(files[name].decode().splitlines(),
                                               data.decode().splitlines(),
                                               fromfile="recorded", tofile="pinned-control", lineterm=""))
                for name, data in rendered.items()
            }
    return result


def verify_renderer():
    if git(REPO, "rev-parse", "HEAD:src/seldon").decode().strip() != RENDERER_TREE or git(
            REPO, "diff", "HEAD", "--", "src/seldon"):
        raise ValueError(f"Replay renderer must match the pinned {PIN[:7]} product tree")


def prepare(root, rig=RIG, cases=tuple(CASES)):
    verify_renderer()
    if len(set(cases)) != len(cases):
        raise ValueError("Select each case once")
    root.mkdir()
    private = root / "private"
    private.mkdir(mode=0o700)
    key, records = {}, {}
    for case in cases:
        pair = secrets.token_hex(8)
        labels = {arm: secrets.token_hex(8) for arm in "AB"}
        key[pair] = {"case": case, "labels": labels}
        pair_dir = private / pair
        pair_dir.mkdir()
        try:
            records[pair] = prepare_case(rig, case, labels, pair_dir)
        except (OSError, ValueError, KeyError) as error:
            records[pair] = {"state": "ineligible", "reason": str(error), "run": str(rig / CASES[case])}
    put_json(private / "key.sealed.json", key)
    put_json(private / "prepared.json", {"pin": PIN, "tree": TREE, "cases": records})
    print(f"Prepared {sum(row['state'] == 'prepared' for row in records.values())}/{len(records)} pairs")
    for pair, record in records.items():
        if record["state"] != "prepared":
            print(key[pair]["case"], record["state"], record["reason"])


def relocate(question, run, workspace, images):
    """Relocate recorded workspace/attachment references. Other recorded run paths become inert labels."""
    old_cwd = question.get("execution", {}).get("cwd", "")
    replacements = {old: str(images[index]) for index, old in enumerate(question.get("images", []))}
    replacements.update({
        old_cwd: str(workspace),
        str(run / "browser-evidence"): str(workspace.parent / "scratch/evidence"),
        str(run): "<recorded-run>",
    })
    replacements = {old: new for old, new in replacements.items() if old}
    variants = {}
    for old, new in replacements.items():
        for sep in ("\\", "/", "\\\\"):
            variants[re.sub(r"[/\\]+", lambda _, separator=sep: separator, old)] = new.replace("\\", sep)
    pattern = re.compile("|".join(re.escape(old) for old in sorted(variants, key=len, reverse=True)),
                         re.IGNORECASE)
    normalized = {old.lower(): new for old, new in variants.items()}

    def replace(value):
        if isinstance(value, str):
            return pattern.sub(lambda match: normalized[match[0].lower()], value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {replace(key): replace(item) for key, item in value.items()}
        return value

    # Preserve exact schema and user prose. The insertion points for machine paths are execution and facts.
    relocated = copy.copy(question)
    relocated["facts"] = {key: value if key == "User's words" else replace(value)
                          for key, value in question["facts"].items()}
    relocated["execution"] = {"cwd": str(workspace), "session": None}
    relocated["images"] = [str(path) for path in images]
    return relocated


def git_stage(repo, workspace, files):
    """Index known regular files without running worktree filters or creating commits."""
    git(repo, "read-tree", "--empty")
    rows = []
    for name, mode in files.items():
        blob = git(repo, "hash-object", "-w", "--stdin", data=(workspace / name).read_bytes()).strip()
        rows.append(mode.encode() + b" " + blob + b"\t" + name.encode() + b"\0")
    git(repo, "update-index", "-z", "--index-info", data=b"".join(rows))
    return git(repo, "write-tree").decode().strip()


def delivered_files(workspace, base):
    files = {}
    for directory, dirs, names in os.walk(workspace):
        for name in (*dirs, *names):
            path = Path(directory) / name
            relative = path.relative_to(workspace).as_posix()
            safe_path(relative)
            info = path.lstat()
            reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            if reparse or path.is_symlink():
                raise ValueError(f"Non-regular delivery path: {relative}")
            if name in names:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(f"Non-regular delivery file: {relative}")
                files[relative] = base.get(relative, ["100644"])[0]
    return files


def checked_base(pair_dir, record):
    base = record["base"]["files"]
    for name, (_, expected) in base.items():
        if digest((pair_dir / "base" / name).read_bytes()) != expected:
            raise ValueError(f"Prepared base changed: {name}")
    return base


def capture(repo, workspace, base, original_tree, scope):
    files = delivered_files(workspace, base)
    final = git_stage(repo, workspace, files)
    changed = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", original_tree, final)
    outside = [name for name in changed.decode().strip("\0").split("\0") if name and
               not plan.matches(name, scope)]
    diff = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--binary", original_tree, final)
    return diff, outside

def installed_codex():
    """Use the installed npm CLI. Pin version and executable paths once per experiment."""
    node = shutil.which("node")
    shim = next((Path(shim) for name in ("codex.cmd", "codex") if (shim := shutil.which(name))), None)
    if not node or not shim:
        raise ValueError("The replay rig requires Node and the installed Codex npm CLI")
    package = shim.parent / "node_modules/@openai/codex"
    command = [node, str(package / "bin/codex.js")]
    if not Path(command[1]).is_file():
        raise ValueError("Codex npm entry point is missing")
    version = read_json(package / "package.json")["version"]
    browser = []
    if local := os.environ.get("LOCALAPPDATA"):
        links = Path(local) / "ms-playwright/.links"
        if links.is_dir():
            browser.append(str(links))
            for link in sorted(links.iterdir()):
                safe_path(link.name)
                if not link.is_file():
                    continue
                lib = Path(link.read_text(encoding="utf-8").strip())
                if lib.is_absolute() and lib.name in ("playwright", "playwright-core", "package"):
                    manifest = lib / "package.json"
                    if manifest.is_file() and read_json(manifest).get("name") in (
                            "playwright", "playwright-core"):
                        browser.append(str(lib.resolve()))
    return {"codexCommand": command, "version": version, "node": node, "python": sys._base_executable,
            "pythonVersion": sys.version, "model": MODEL, "effort": EFFORT, "timeout": TIMEOUT,
            "browserReadPaths": browser}


FULL_ACCESS = False


def native_options(workspace, inputs, scratch, setup, denied):
    # Use the provider's local permission profile. No full-access fallback or global config changes.
    prefix = "permissions.replay.filesystem"
    entries = {":root": "deny", ":minimal": "read", str(workspace): "write", str(inputs): "read",
               str(scratch): "write", str(Path(setup["node"]).parent): "read",
               str(Path(setup["python"]).parent): "read"}
    entries.update(dict.fromkeys(setup["browserReadPaths"], "read"))
    entries.update(dict.fromkeys(map(str, denied), "deny"))
    filesystem = ", ".join(f"{json.dumps(key)}={json.dumps(value)}" for key, value in entries.items())
    options = ['default_permissions="replay"', f"{prefix}={{{filesystem}}}",
               "permissions.replay.network.enabled=false", 'windows.sandbox="elevated"']
    return [arg for option in options for arg in ("-c", option)]


PROBE = """
from pathlib import Path
import sys
assert Path(sys.argv[1]).is_file()
with open(sys.argv[1], "rb") as allowed:
    allowed.read(1)
for name in sys.argv[2:]:
    try:
        with open(name, "rb") as blocked:
            raise SystemExit("Sandbox permitted a private read")
    except PermissionError:
        pass
canary = Path("sandbox-canary")
canary.write_text("ok")
canary.unlink()
print("replay sandbox verified")
"""


def invoke(setup, input_dir, workspace, result_dir, record, private, worker):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("SELDON_", "GIT_"))}
    scratch = workspace.parent / "scratch"
    temp, evidence = scratch / "tmp", scratch / "evidence"
    temp.mkdir(parents=True)
    evidence.mkdir()
    env.update(SELDON_EVIDENCE_DIR=str(evidence), TEMP=str(temp), TMP=str(temp))
    run = Path(record["run"])
    options = native_options(workspace, input_dir, scratch, setup, (private, run.parent, REPO))
    # Verify native confinement before inference against the actual private paths.
    old_record = run / "calls" / record["call"] / "question.json"
    other_stages = sorted(workspace.parent.parent.glob("*/input/schema.json"))
    denied = [private / "key.sealed.json", old_record, REPO / "CHARTER.md",
              *(path for path in other_stages if path.parent != input_dir)]
    command = [*setup["codexCommand"], *options, "sandbox", "--permission-profile", "replay",
               "--include-managed-config", "--cd", str(workspace), "--", setup["python"],
               "-I", "-B", "-c", PROBE, str(input_dir / "schema.json"), *map(str, denied)]
    probe = None if FULL_ACCESS else providers.execute(command, workspace, 60, env=env, output_dir=result_dir)
    if probe is not None and (probe["exitCode"] != 0 or not probe["cleanupVerified"]
            or b"replay sandbox verified" not in probe["stdout"]):
        put(result_dir / "sandbox.log", probe["stdout"] + b"\n" + probe["stderr"])
        raise RuntimeError("Native sandbox did not verify private reads are denied and work is writable")
    config = {"cwd": workspace, "model": MODEL, "effort": EFFORT}
    images = sorted(input_dir.glob("image-*.png"))
    command = providers.codex_command(setup, config, input_dir, worker, None, images)
    if not FULL_ACCESS:
        command.remove('sandbox_mode="danger-full-access"')
        # Remove that option's flag as well, preserving all other provider defaults.
        approval_option = command.index('approval_policy="never"')
        del command[approval_option + 1]
        command[-1:-1] = ["--ephemeral", *options] if worker else options
    elif worker:
        command[-1:-1] = ["--ephemeral"]
    result = providers.execute(command, workspace, TIMEOUT, input_dir / "prompt.txt", env, result_dir)
    stdout = result.pop("stdout")
    # providers.execute may already have stored the streams in result_dir; keep its copy.
    for name, data in (("stdout.bin", stdout), ("stderr.bin", result.pop("stderr"))):
        if not (result_dir / name).exists():
            put(result_dir / name, data)
    output = stdout.decode(errors="replace")
    usage = [event.get("usage") for line in output.splitlines() if (event := stream_event(line))
             and event.get("type") == "turn.completed"]
    result.update(confinement="full-access (same as Seldon runs; Oren 11-14)" if FULL_ACCESS else "native sandbox",
                  promptBytes=(input_dir / "prompt.txt").stat().st_size, retries=0,
                  tokens=usage[-1] if usage else None)
    put_json(result_dir / "runtime.json", result)
    if not result["cleanupVerified"]:
        raise RuntimeError("Provider cleanup unverified")
    if result["timedOut"] or result["exitCode"] != 0:
        return {"status": "timeout" if result["timedOut"] else "failed"}
    try:
        answer = json.loads(providers.terminal_answer(output))
    except (ValueError, RuntimeError):
        return {"status": "invalid"}
    return {"status": "completed", "answer": answer}


def stream_event(line):
    try:
        row = json.loads(line)
        return row if isinstance(row, dict) else {}
    except ValueError:
        return {}


def generate_arm(root, label, arm, record, setup, pair_dir, target):
    old = pair_dir / "historical"
    if hashes(old) != record["historical"] or hashes(pair_dir / label) != record["rendered"][label]["hashes"]:
        raise ValueError("Prepared call input hashes changed")
    question = read_json(old / "question.json")
    worker = question["kind"] == "WORK"
    sandbox = root / "stage" / label
    workspace = sandbox / "work"
    workspace.mkdir(parents=True)
    inputs = sandbox / "input"
    inputs.mkdir()
    if worker:
        base = checked_base(pair_dir, record)
        for name in base:
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pair_dir / "base" / name, path)
        repo = target / "git"
        repo.mkdir()
        git(repo, "init", "--quiet", "--template=")
        tree = git_stage(repo, workspace, {name: mode for name, (mode, _) in base.items()})
        if tree != record["base"]["tree"]:
            raise ValueError("Staged Git tree differs from the recorded base")
    attachments = {name: (old / name).read_bytes() for name in record["evidence"]}
    rendered = render(relocate(question, Path(record["run"]), workspace,
                               [inputs / name for name in attachments]), arm,
                      [inputs / name for name in attachments])
    for name, data in {**rendered, "schema.json": (old / "schema.json").read_bytes(), **attachments}.items():
        put(inputs / name, data)
    result = invoke(setup, inputs, workspace, target, record, root / "private", worker)
    if worker:
        diff, outside = capture(repo, workspace, base, tree, question["facts"]["Write scope"])
        put(target / "source.diff", diff)
        evidence = workspace.parent / "scratch/evidence"
        for name in sorted(delivered_files(evidence, {})):
            if not name.lower().endswith(".png"):
                continue
            output = target / "evidence" / safe_path(name)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(evidence / name, output)
        result.update(scopeViolation=bool(outside), outsideScope=outside)
    return result


def generate(root, semantic):
    verify_renderer()
    private = root / "private"
    prepared = read_json(private / "prepared.json")["cases"]
    key = read_json(private / "key.sealed.json")
    if (private / "samples.json").exists():
        raise ValueError("Use a separate experiment for real generation")
    setup = installed_codex()
    setup_path = private / "terra.json"
    if setup_path.exists():
        if read_json(setup_path) != setup:
            raise ValueError("Terra runtime changed since the experiment began")
    else:
        put_json(setup_path, setup)
    batch = private / ("semantic" if semantic else "work")
    batch.mkdir()
    for pair, entry in key.items():
        case, labels = entry["case"], entry["labels"]
        record = prepared[pair]
        if (case in WORK) == semantic or record["state"] != "prepared":
            continue
        arms = list(labels)
        RNG.shuffle(arms)
        for arm in arms:
            label = labels[arm]
            target = batch / label
            target.mkdir()
            try:
                result = generate_arm(root, label, arm, record, setup, private / pair, target)
            except RuntimeError as error:
                put_json(target / "outcome.json", {"status": "failed", "reason": str(error)})
                raise
            except (OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
                result = {"status": "failed", "reason": str(error)}
            put_json(target / "outcome.json", result)
            print(f"{pair}/{label} {result['status']}", flush=True)


def import_samples(root, source):
    """Import offline plumbing samples using the private labels. Samples can never recommend B."""
    private = root / "private"
    key = read_json(private / "key.sealed.json")
    prepared = read_json(private / "prepared.json")["cases"]
    values = read_json(source)
    eligible = {label for pair, entry in key.items() if prepared[pair]["state"] == "prepared"
                for label in entry["labels"].values()}
    if not isinstance(values, dict) or not set(values) <= eligible:
        raise ValueError("Sample outputs must use prepared private labels")
    if (private / "work").exists() or (private / "semantic").exists():
        raise ValueError("Use a separate experiment for sample outputs")
    put_json(private / "samples.json", values)


def blind_outcome(private, label, record, contract, samples, delivered):
    if samples is not None:
        raw = samples.get(label, {"status": "missing"})
        if "diff" in raw:
            out = delivered / label
            out.mkdir()
            put(out / "source.diff", raw["diff"].encode())
    else:
        item = private / ("work" if record["kind"] == "WORK" else "semantic") / label
        raw = read_json(item / "outcome.json") if (item / "outcome.json").is_file() else {"status": "missing"}
        if (item / "source.diff").is_file():
            out = delivered / label
            out.mkdir()
            put(out / "source.diff", (item / "source.diff").read_bytes())
            if (item / "evidence").is_dir():
                shutil.copytree(item / "evidence", out / "evidence")
    status = raw["status"]
    answer = raw.get("answer")
    if status == "completed":
        try:
            schema.validate(answer, contract)
        except ValueError:
            status = "invalid"
    outcome = {"status": status}
    if raw.get("scopeViolation"):
        outcome["scopeViolation"] = True
    if status == "completed":
        if record["kind"] == "WORK":
            outcome["receipt"] = answer["answer"]
        else:
            outcome["answer"] = answer
    return outcome


def packet_files(folder):
    result = {}
    for name in sorted(delivered_files(folder, {})):
        if name != "packet.json":
            result[name] = digest((folder / name).read_bytes())
    return result


def package(root, destination):
    private = root / "private"
    key_path = private / "key.sealed.json"
    key = read_json(key_path)
    records = read_json(private / "prepared.json")["cases"]
    samples_path = private / "samples.json"
    samples = read_json(samples_path) if samples_path.exists() else None
    destination.mkdir()
    packet = {"keyCommitment": digest(key_path.read_bytes()), "sample": samples is not None, "pairs": {}}
    for pair, record in records.items():
        entry = key[pair]
        row = packet["pairs"][pair] = {"state": record["state"], "folded": entry["case"] in FOLDED}
        if record["state"] != "prepared":
            continue
        question = read_json(private / pair / "historical" / "question.json")
        attachments = record["evidence"]
        reviewer_facts = relocate(question, Path(record["run"]), Path("workspace"),
                                  [Path(name) for name in attachments])["facts"]
        row["assignment"] = {key: value for key, value in reviewer_facts.items()
                             if key not in ("Execution facts", "Available tools and skills",
                                            "Design guidance used by UI workers")}
        row["kind"] = record["kind"]
        if row["kind"] == "WORK":
            row["base"] = record["base"]["tree"]
            row["assignment"]["Base paths"] = list(record["base"]["files"])
            for name in checked_base(private / pair, record):
                output = destination / pair / "base" / name
                output.parent.mkdir(parents=True, exist_ok=True)
                put(output, (private / pair / "base" / name).read_bytes())
        if attachments:
            evidence = destination / pair
            evidence.mkdir()
            for name, expected in attachments.items():
                data = (private / pair / "historical" / name).read_bytes()
                if digest(data) != expected:
                    raise ValueError(f"Attachment changed: {name}")
                put(evidence / name, data)
        row["outputs"] = {
            label: blind_outcome(private, label, record, question["schema"], samples, destination)
            for label in sorted(entry["labels"].values())
        }
    packet["files"] = packet_files(destination)
    put_json(destination / "packet.json", packet)
    print(f"Packaged {len(packet['pairs'])} pairs in {destination}")


def require_finding(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields) | {"evidence"}:
        raise ValueError(f"Expected {', '.join(fields)} and cited evidence")
    if not isinstance(value["evidence"], str) or not value["evidence"].strip():
        raise ValueError("A finding requires a cited operation, artifact or observation")


def resolve_ballots(packet, ballots):
    pairs = packet["pairs"]
    if len(ballots) != 2 or any(set(ballot) - pairs.keys() for ballot in ballots):
        raise ValueError("Supply two reviewer ballots keyed by packet pair")
    locked = {}
    for pair, row in pairs.items():
        outputs = row.get("outputs", {})
        if not outputs or any(out["status"] != "completed" for out in outputs.values()):
            continue
        if any(pair not in ballot for ballot in ballots):
            continue
        first, second = (ballot[pair] for ballot in ballots)
        if row["kind"] == "WORK":
            for vote in (first, second):
                if set(vote) != set(AXES) | {"guards", "reruns"} or set(vote["guards"]) != outputs.keys():
                    raise ValueError("WORK ballots need axes, guard findings and independent reruns")
                if set(vote["reruns"]) != outputs.keys() or not all(
                        isinstance(value, str) and value.strip() for value in vote["reruns"].values()):
                    raise ValueError("Cite the independent rerun results or skipped checks for both arms")
                for axis in AXES:
                    require_finding(vote[axis], ("winner",))
                    if vote[axis]["winner"] not in (*outputs, "tie"):
                        raise ValueError("Use an opaque winner label or tie")
                for flags in vote["guards"].values():
                    require_finding(flags, GUARDS)
                    if any(type(flags[guard]) is not bool for guard in GUARDS):
                        raise ValueError("Guard findings must be boolean")
            locked[pair] = {axis: first[axis]["winner"] if first[axis]["winner"] == second[axis]["winner"]
                            else "tie" for axis in AXES}
            locked[pair]["regressions"] = [label for label in outputs if any(
                vote["guards"][label][guard] for vote in (first, second) for guard in GUARDS)]
        else:
            fields = ("preserved", "concreteSource", "uncertaintyPreserved") if row["folded"] else (
                "preserved",)
            for vote in (first, second):
                if set(vote) != outputs.keys():
                    raise ValueError("Semantic ballots must grade each opaque label")
                for finding in vote.values():
                    require_finding(finding, fields)
                    if any(type(finding[field]) is not bool for field in fields):
                        raise ValueError("Semantic findings must be boolean")
            locked[pair] = {label: all(vote[label][field] for vote in (first, second) for field in fields)
                            for label in outputs}
    return locked


def verified_packet(path):
    data = path.read_bytes()
    packet = json.loads(data)
    if packet["files"] != packet_files(path.parent):
        raise ValueError("The packet files changed after packaging")
    return data, packet


def lock_scores(packet_path, ballot_paths, destination):
    data, packet = verified_packet(packet_path)
    ballots = [read_json(path) for path in ballot_paths]
    if len({path.resolve() for path in ballot_paths}) != 2:
        raise ValueError("Supply one ballot file from each reviewer")
    scores = resolve_ballots(packet, ballots)
    put_json(destination, {"packetHash": digest(data), "scores": scores, "ballots": ballots})


def reveal(root, packet_path, lock_path, destination):
    data, packet = verified_packet(packet_path)
    lock = read_json(lock_path)
    if lock["packetHash"] != digest(data) or lock["scores"] != resolve_ballots(packet, lock["ballots"]):
        raise ValueError("Scores must be locked against this packet")
    private = root / "private"
    key_data = (private / "key.sealed.json").read_bytes()
    if packet["keyCommitment"] != digest(key_data):
        raise ValueError("The locked packet does not match the sealed arm key")
    key = json.loads(key_data)
    prepared = read_json(private / "prepared.json")["cases"]
    rows, wins, losses = {}, set(), []
    for pair, entry in key.items():
        case, labels = entry["case"], entry["labels"]
        row = packet["pairs"][pair]
        grade = lock["scores"].get(pair)
        b = labels["B"]
        outputs = row.get("outputs", {})
        rows[case] = {"state": row["state"], "grades": grade,
                      "outcomes": {arm: outputs.get(label, {}).get("status", row["state"])
                                   for arm, label in labels.items()}}
        if row["state"] != "prepared":
            rows[case]["reason"] = prepared[pair]["reason"]
        b_out = outputs.get(b, {})
        if b_out.get("status") == "invalid" or b_out.get("scopeViolation"):
            losses.append(case)
        if grade is None:
            continue
        if case in WORK:
            if grade["codeQuality"] == b:
                wins.add(case)
            if b in grade["regressions"] or any(grade[axis] == labels["A"] for axis in AXES[1:]):
                losses.append(case)
        elif not grade[b]:
            losses.append(case)
    complete = all(case in rows and rows[case]["grades"] is not None
                   for case in CASES if case != RENDER_ONLY)
    advances = complete and len(wins) >= 4 and WHOLE <= wins and not losses and not packet["sample"]
    costs = {}
    for pair, entry in key.items():
        costs[entry["case"]] = {}
        for arm, label in entry["labels"].items():
            batch = "work" if entry["case"] in WORK else "semantic"
            runtime = private / batch / label / "runtime.json"
            row = read_json(runtime) if runtime.is_file() else {}
            costs[entry["case"]][arm] = {name: row.get(name) for name in ("seconds", "tokens", "retries")}
            costs[entry["case"]][arm]["promptBytes"] = row.get(
                "promptBytes", prepared[pair].get("rendered", {}).get(label, {}).get("promptBytes"))
    put_json(destination, {"sample": packet["sample"], "nextCohort": "B" if advances else "A",
                           "materialRegressions": sorted(set(losses)), "codeQualityWins": sorted(wins),
                           "complete": complete, "cases": rows, "costs": costs, "key": key})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Render the recorded cohort offline; default is all cases")
    prep.add_argument("root", type=Path)
    prep.add_argument("--recordings", type=Path, default=RIG)
    prep.add_argument("--case", choices=CASES, action="append")
    gen = commands.add_parser("generate", help="Run a fresh isolated Terra call per eligible arm")
    gen.add_argument("root", type=Path)
    gen.add_argument("--run-terra", required=True, action="store_true")
    gen.add_argument("--semantic", action="store_true", help="Generate semantic pairs instead of WORK")
    gen.add_argument("--full-access", action="store_true",
                     help="Skip the native sandbox preflight and run like Seldon's own calls (Windows elevated sandbox cannot deny :root)")
    sample = commands.add_parser("import-samples", help="Import sample outcomes by private label")
    sample.add_argument("root", type=Path)
    sample.add_argument("outputs", type=Path)
    pack = commands.add_parser("package", help="Write the blind packet and source diffs outside private")
    pack.add_argument("root", type=Path)
    pack.add_argument("packet", type=Path)
    lock = commands.add_parser(
        "lock", help="Freeze two blinded reviewer ballots before revealing the key",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
            "Give each reviewer the packet only. For WORK, copy the pair's base and apply each opaque\n"
            "label's source.diff in an independent sandbox. Use the same peer inputs and rerun checks\n"
            "independently, including the documented journey where owned. Cite skips and failures.\n"
            "Inspect disagreements while blind. Unresolved axis disagreements count as ties.\n\n"
            "Each ballot is a JSON object keyed by opaque pair. WORK entries contain codeQuality,\n"
            "completeness and fidelity, each with {winner: <label or tie>, evidence: <citation>}.\n"
            "Trace a likely source change, exercise the accepted journey and compare it to the dream.\n"
            "Include reruns keyed by label with a citation to independent observations, and guards\n"
            "keyed by label with scopeViolation, falseApproval, unsupportedProof and evidence.\n"
            "Semantic entries are keyed by label with preserved and evidence. Folded JUDGE also\n"
            "requires concreteSource and uncertaintyPreserved. All guard and semantic flags are\n"
            "booleans. Save both ballots and the lock outside the packet directory."))
    lock.add_argument("packet", type=Path)
    lock.add_argument("reviewers", nargs=2, type=Path)
    lock.add_argument("--output", required=True, type=Path)
    unseal = commands.add_parser("reveal", help="Reveal a locked packet and compute the next-cohort choice")
    unseal.add_argument("root", type=Path)
    unseal.add_argument("packet", type=Path)
    unseal.add_argument("lock", type=Path)
    unseal.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.root.resolve(), args.recordings.resolve(), tuple(args.case or CASES))
        elif args.command == "generate":
            global FULL_ACCESS
            FULL_ACCESS = args.full_access
            generate(args.root.resolve(), args.semantic)
        elif args.command == "import-samples":
            import_samples(args.root, args.outputs)
        elif args.command == "package":
            if args.packet.resolve().is_relative_to(args.root.resolve() / "private"):
                raise ValueError("Put the blind packet outside the private directory")
            package(args.root, args.packet)
        elif args.command == "lock":
            lock_scores(args.packet, args.reviewers, args.output)
        elif args.command == "reveal":
            reveal(args.root, args.packet, args.lock, args.output)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"replay: {error}\n")


if __name__ == "__main__":
    main()
