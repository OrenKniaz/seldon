"""R7: FLOW repairs its named owner even when another unit shares the feature."""
from test_journey import unit_ref
import json
import pytest
from seldon import plan
from test_journey import Scripted, finish, prepare

class RequirementFlow(Scripted):
    def __call__(self, q):
        reply = json.loads(super().__call__(q))
        if q['kind'] == 'DETAIL':
            body = reply['answer']
            body['edits'] = [e for e in body['edits'] if e['targetHandle'] != 'f2']
            for edit in body['edits']:
                if edit['kind'] == 'requirement':
                    edit['typedBody']['features'] = ['f1']
        if q['kind'] == 'FLOW' and any('interaction blocked' in row['stdout']
                                      for row in q['facts']['Final observations']):
            target = next(r for r, body in q['facts']['Requirements'].items()
                          if 'value-001' in body['acceptance'])
            reply['answer'].update(verdict='gap', gaps=[{"severity": "must-fix", 'target': target, 'owners': [self.refs[1]],
                'correction': 'Make the blocked interaction ready and exercise it'}])
        return json.dumps(reply)

@pytest.mark.fast
def test_requirement_gap_preserves_other_feature_owner_to_c(tmp_path):
    """Protect shared-feature ownership; disjoint golden flows miss feature-wide over-repair."""
    slot = tmp_path/'delivery'
    run, _ = prepare(slot, count=2)
    run.provider = RequirementFlow(slot, count=2)
    native = slot/'native.py'
    text = native.read_text()
    text = text.replace("    Path(name, 'check.py').write_text(source)",
        "    if number == 1:\n"
        "        Path(name, 'interaction.txt').write_text('blocked' if turn == 1 else 'ready')\n"
        "        source += \"print('interaction', Path('unit-001/interaction.txt').read_text())\\n\"\n"
        "    Path(name, 'check.py').write_text(source)")
    native.write_text(text)
    try:
        while run.store.cursor().state != 'FLOW-JUDGE':
            assert not run.store.cursor().decision
            run.step()
        document = plan.execution(run.store)
        features = [r['features'] for r in plan.select(document,'requirement').values()]
        assert features[0] == features[1]
        before = run.store.unit(unit_ref(run, 2))
        assert run.step() == 'EXECUTE'
        assert run.store.unit(unit_ref(run, 1)).state == 'REWORK'
        assert run.store.unit(unit_ref(run, 2)) == before
        finish(run)
        assert run.store.unit(unit_ref(run, 2)) == before
        assert (run.project/'unit-001/interaction.txt').read_text() == 'ready'
    finally:
        run.close()
