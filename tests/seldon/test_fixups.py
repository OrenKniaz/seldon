"""R7/R8/C23: a final check repair survives a controller restart with siblings intact."""
import pytest
from seldon.run import Run
from test_journey import approve, finish, prepare

@pytest.mark.fast
def test_final_check_fixup_reopens_without_redoing_siblings(tmp_path):
    """Protect admitted final fixups across reopen; golden wave faults never restart at admission."""
    slot = tmp_path/'repair'
    run, provider = prepare(slot,'wave-fault',2)
    try:
        while run.store.cursor().state != 'WAVE-VERIFY':
            assert not run.store.cursor().decision
            run.step()
        before = run.store.unit('u002')
        assert run.step() == 'EXECUTE'
        assert run.store.unit('u001').state == 'REWORK'
        assert run.store.unit('u002') == before
        assert any('integrated mismatch' in str(b['gaps'])
                   for _, _, b in run.store.records('unit_fixup'))
        run.close()
        run = Run.reopen(slot/'product',provider,approve)
        finish(run)
        assert run.store.unit('u002') == before
        launches = __import__('json').loads((slot/'launch-unit-001.jsonl').read_text().splitlines()[-1])
        assert launches['resumed']
    finally:
        run.close()
