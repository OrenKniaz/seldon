"""C15/R9: final evidence belongs to its integrated candidate; fanout survives restart."""
import pytest
from seldon import gitwork, plan, proof, wave_verify
from test_journey import prepare
from fanout_journey import final_fanout_journey
from lineage_journey import unit_lineage

@pytest.mark.fast
@pytest.mark.parametrize('stage,case', [('FLOW','restart'),('PRODUCT','restart'),
                                       ('FLOW','uncertain'),('PRODUCT','uncertain')])
def test_final_subject_dispatch(tmp_path,stage,case):
    """Protect saved subject replies and honest uncertainty; clean golden never interrupts final dispatch."""
    final_fanout_journey(tmp_path/'dispatch',stage,case)

@pytest.mark.fast
def test_repeated_unit_failure_reopens_with_user_direction(tmp_path):
    """Protect durable recurrence after user direction; finite golden repairs miss renewed retry loops."""
    unit_lineage(tmp_path/'recurrence')

@pytest.mark.fast
def test_failed_integrated_candidate_routes_its_check_owner(tmp_path):
    """Protect observed candidate-bound failures; semantic FLOW gaps cannot expose stale proof reuse."""
    run,_ = prepare(tmp_path/'candidate',count=2)
    try:
        while run.store.cursor().state != 'WAVE-VERIFY':
            assert not run.store.cursor().decision
            run.step()
        previous = run.store.cursor().candidate
        before = run.store.units()
        (run.project/'unit-002/value.txt').write_text('lost')
        _,broken,_ = gitwork.snapshots(run.project,previous,['**'],[],run.store.cursor().scratch)
        gitwork.checkout(run.project,broken)
        observations = wave_verify.verify(run.store,broken)
        gaps = proof.gaps(run.store,broken,observations)
        failure, = [g for g in gaps if g['units'] == ['u002']]
        assert failure['commit'] == broken and broken != previous
        assert 'AssertionError' in run.store.read(failure['source'])['stderr']
        assert all(run.store.read(r)['commit'] == broken for r in observations)
        assert run.store.units() == before
        gitwork.unchanged(run.project,broken)
    finally:
        run.close()
