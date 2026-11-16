"""C23: planning resumes recorded area answers without replaying completed work."""
import json
import pytest
from seldon.run import Run
from test_journey import Scripted, approve, finish, prepare

@pytest.mark.fast
def test_area_product_journey(tmp_path):
    """Protect interrupted DETAIL; worker-only reopen misses lost completed planning answers."""
    slot = tmp_path/'areas'
    run, _ = prepare(slot, 'contracts', 2)
    class Interrupted(Scripted):
        interrupt = True
        def __call__(self, q):
            if q['kind'] == 'DETAIL' and q['facts']['Area']['handle'] == 'area-2' and self.interrupt:
                self.interrupt = False
                raise RuntimeError('controller interruption')
            return super().__call__(q)
    provider = Interrupted(slot, 'contracts', 2)
    run.provider = provider
    try:
        while run.store.cursor().state != 'DETAIL':
            run.step()
        with pytest.raises(RuntimeError, match='controller interruption'):
            run.step()
        assert any(b['area'] == 'area-1' for _, _, b in run.store.records('area_detail'))
        run.close()
        class Resumed(Interrupted):
            interrupt = False
            def __call__(self, q):
                assert not (q['kind'] == 'DETAIL' and q['facts']['Area']['handle'] == 'area-1')
                return super().__call__(q)
        run = Run.reopen(slot/'product', Resumed(slot,'contracts',2), approve)
        finish(run)
    finally:
        run.close()
