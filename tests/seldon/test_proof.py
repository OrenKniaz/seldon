"""R9: runtime writes belong to observations, never the published source candidate."""

import json
import time
from pathlib import Path

from seldon import gitwork, plan, proof, wave_verify
from seldon.providers import ROLES
from seldon.store import RunRow, Store

from test_journey import VISION, finish, planned, prepare


RUNTIME = r'''
import base64, json, os, shutil, tempfile, time
from pathlib import Path
control = Path(CONTROL)
folder = Path(__file__).parent
evidence = Path(os.environ['SELDON_EVIDENCE_DIR'])
evidence.joinpath('screen.png').write_bytes(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
    'AAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))
scratch = Path(tempfile.mkdtemp())
assert not scratch.is_relative_to(Path.cwd())
assert not evidence.is_relative_to(Path.cwd())
assert len({os.environ[k] for k in ('TEMP', 'TMP', 'TMPDIR')}) == 1
print(json.dumps({'scratch': str(scratch), 'evidence': str(evidence)}))
fixture = folder / '.fixture-live'
fixture.mkdir()
try:
    if (control / 'wave').exists():
        (control / ('ready-' + folder.name)).write_text('ready')
        deadline = time.monotonic() + 10
        while len(list(control.glob('ready-*'))) < 4 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(list(control.glob('ready-*'))) == 4, 'wave checks did not overlap'
        assert len(list(Path.cwd().glob('unit-*/.fixture-live'))) == 1, 'shared checkout'
        time.sleep(.05 * int(folder.name.split('-')[1]))
finally:
    shutil.rmtree(fixture)
'''


def wave_timing(root):
    """Measure four ordinary parallel commands through the same real wave observer."""
    project, scratch = root / 'product', root / 'scratch'
    project.mkdir(parents=True)
    scratch.mkdir()
    base = gitwork.init(project)
    document = plan.create(VISION, planned(4, 'hermetic'))
    for number in range(1, 5):
        folder = project / f'unit-{number:03}'
        folder.mkdir()
        (folder / 'check.py').write_text("print('observed value')\n")
    gitwork.git(project, 'add', '--all')
    candidate = gitwork.commit(project, gitwork.git(project, 'write-tree'), base)
    gitwork.checkout(project, candidate)
    store = Store(project)
    try:
        kickoff = store.record('kickoff', {'setup': {'providers': ROLES}, 'capacity': 4})
        store.cursor(RunRow(kickoff=kickoff, scratch=str(scratch), candidate=candidate,
                            revision=store.revision(document)))
        start = time.monotonic()
        refs = wave_verify.verify(store, candidate)
        seconds = time.monotonic() - start
        assert len(refs) == 4 and not proof.failures(store, refs)
        (root / 'wave4-seconds.txt').write_text(f'{seconds:.3f}')
        print(f'hermetic wave-of-4: {seconds:.3f}s', flush=True)
    finally:
        store.close()


def hermetic_journey(root):
    """Reach C despite runtime writes, with concurrent fixtures and durable judge images.

    Catches shared-checkout interference and checks corrupting the candidate or later proof.
    Existing workers only read source; their evidence checks miss scratch and transient races.
    """
    wave_timing(root / 'timing')
    slot = root / 'delivery'
    run, _ = prepare(slot, 'hermetic', 4)
    native = slot / 'native.py'
    prefix = RUNTIME.replace('CONTROL', repr(str(slot)))
    tail = ("\nPath(__file__).with_name('value.txt').write_text('runtime corruption')\n"
            "Path(__file__).with_name('.runtime-output').write_text('left by check')\n")
    native.write_text(native.read_text().replace(
        '    Path(name, "check.py").write_text(source)',
        f'    source = {prefix!r} + source + {tail!r}\n'
        '    Path(name, "check.py").write_text(source)'))
    try:
        while run.store.cursor().state != 'WAVE-VERIFY':
            assert not run.store.cursor().decision, run.store.records('unit_failure')
            run.step()
        candidate = run.store.cursor().candidate
        (slot / 'wave').touch()
        assert run.step() == 'FLOW-JUDGE'
        (slot / 'wave').unlink()
        assert len(list(slot.glob('ready-*'))) == 4
        _, rows, units = finish(run)
        assert run.store.cursor().candidate == candidate
        gitwork.unchanged(run.project, candidate)
        assert all(u.recoveries == 0 for u in units.values())
        for number in range(1, 5):
            assert (run.project / f'unit-{number:03}/value.txt').read_text() == f'value-{number:03}'
        observed = [b for _, k, b in rows if k == 'observation' and b['unit'] != 'guide']
        paths = [json.loads(b['stdout'].splitlines()[0]) for b in observed]
        assert len({p['scratch'] for p in paths}) == len(paths)
        assert len({p['evidence'] for p in paths}) == len(paths)
        assert all(Path(p['evidence'], 'screen.png').is_file() for p in paths)
        for cwd in {Path(b['cwd']) for b in observed}:
            assert not list(cwd.glob('unit-*/.runtime-output'))
            assert not list(cwd.glob('unit-*/.fixture-*'))
            for value in cwd.glob('unit-*/value.txt'):
                assert value.read_text() == 'value-' + value.parent.name.split('-')[1]
        for _, kind, question in rows:
            if kind == 'question' and question['kind'] in ('JUDGE', 'FLOW', 'PRODUCT'):
                assert question['images']
                assert all(Path(p).read_bytes().startswith(b'\x89PNG') for p in question['images'])
        assert not list(run.project.glob('unit-*/.runtime-output'))
        assert not run.store.records('unit_failure')
    finally:
        run.close()
