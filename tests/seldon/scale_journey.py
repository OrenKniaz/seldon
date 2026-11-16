"""P2/R17: five-wave stub journey, using the real loop and real worker/check processes."""
import json
import sys
from copy import deepcopy
from pathlib import Path

from seldon import plan
from seldon.run import Run
from real_hello import scripted_answer
from test_journey import NATIVE, Scripted, VISION, finish


class Scale(Scripted):
    def __init__(self, slot):
        super().__init__(slot, 'scale-five', 50)

    def __call__(self, q):
        kind = q['kind']
        if kind == 'ALIGN':
            answer = deepcopy(VISION)
            answer['scopeIn'] = [f'Read the ten values of wave {n}' for n in range(1,6)]
            answer['size'] = 'large'
        elif kind == 'ARCHITECT':
            answer = {'areas': [dict(handle=f'area-{w}', responsibility=f'Values of wave {w}',
                      scope=[f'unit-{n:03}/**' for n in range((w-1)*10+1,w*10+1)],
                      commitments=[f'scopeIn:{w}', *(['quality:1'] if w==1 else [])]) for w in range(1,6)],
                      'contracts': [], 'decisions': [],
                      'waves': [dict(goal=f'Deliver values {(w-1)*10+1} through {w*10}',
                               commitments=[f'scopeIn:{w}', *(['quality:1'] if w==1 else [])]) for w in range(1,6)]}
        elif kind == 'COACH':
            question = str(q['facts']['Unresolved work']['failure'])
            if 'exact label' in question:
                answer = {'ask': {'question': 'Which label should the reader use?', 'options': [
                    {'label':'Keep the exact label','means':'Preserve the saved value label'},
                    {'label':'Use a short label','means':'Use a concise alias'}]}}
            else:
                answer = {'guidance': 'Repair the failing saved-value assertion; keep the original acceptance.'}
        elif kind == 'DECIDE' and q['facts']["User's words"].startswith('Other:'):
            answer = {'choice':'Other'}
        else:
            return super().__call__(q)
        answer['why'] = 'Keep the accepted values and progressive waves'
        return json.dumps({'answer': answer})


def drive(slot, resumed=False):
    slot = Path(slot)
    if not resumed:
        slot.mkdir()
        (slot/'mode').write_text('scale-five')
        (slot/'native.py').write_text(NATIVE, encoding='utf-8')
    else:
        (slot/'resumed').write_text('resume wave 3')

    provider = Scale(slot)

    def user(request):
        if request['gate'] == 'UNIT':
            document = plan.execution(run.store)
            peer = next(r for r,u in plan.select(document,'unit').items() if 'unit-013/**' in u['scope'])
            assert run.store.unit(peer).state == 'WORKING', 'question blocked its unrelated peer'
            label = scripted_answer(request, run.store, {})
            assert label == 'Keep the exact label'
            assert (slot/'peer-running').exists()
            (slot/'peer-at-answer').write_text((slot/'peer-running').read_text())
            (slot/'peer-answered').write_text(label)
            return label
        return scripted_answer(request, run.store, {'CHECK-IN': ['Other: keep the exact saved labels']})

    run = (Run.reopen(slot/'product',provider,user) if resumed else
           Run(slot/'product',slot/'workers','Read fifty named values over five waves',
               provider.setup,provider,user,capacity=6))
    try:
        # Missing producer options must fail visibly, never become an approval dict.
        finish(run)
        assert run.store.cursor().wave == 5, 'C reached before all five waves'
        assert (slot/'resumed').exists(), 'C reached without the wave-3 kill'
        assert (slot/'peer-finished').read_text() == (slot/'peer-at-answer').read_text(), 'answer killed the running peer'
        for n in range(1,51):
            assert (run.project/f'unit-{n:03}/value.txt').read_text() == f'value-{n:03}'
        rows = run.store.records()
        requests = [b for _,k,b in rows if k == 'decision_request' and b['gate'] == 'UNIT']
        assert [r['ask']['question'] for r in requests] == ['Which label should the reader use?'], 'planner-answerable failure escaped to the user'
        done = set()
        for _,k,b in rows:
            if k == 'transition' and b['unit'] != 'guide':
                assert not (b['unit'] in done and b['next'] == 'WORKING'), 'DONE unit redone'
                if b['next'] == 'DONE':
                    done.add(b['unit'])
        from run_report import report
        text,_ = report(slot)
        (slot/'report.txt').write_text(text,encoding='utf-8')
        assert 'Keep the exact label' in text and 'Planner-answered questions:' in text
    finally:
        run.close()


if __name__ == '__main__':
    drive(Path(sys.argv[1]), '--resume' in sys.argv)
