"""C11: unresolved semantic review stays visible at B and in the final outcome."""
import json
import pytest
from seldon.decisions import Waiting
from seldon.run import Run
from test_journey import Scripted, approve, finish, prepare

@pytest.mark.fast
@pytest.mark.parametrize('verdict', ['gap','uncertain'])
def test_review_leftovers_survive_b_and_reopen(tmp_path, verdict):
    """Protect honest unresolved review after its repair; adequate golden misses false completion."""
    slot = tmp_path/verdict
    run, _ = prepare(slot)
    class Review(Scripted):
        def __call__(self,q):
            if q['kind'] == 'REVIEW':
                body = {'verdict':verdict,'why':'The reader interaction remains unproven'}
                if verdict == 'gap':
                    body['gaps'] = [{'target':'plan','correction':'Exercise the reader interaction'}]
                else:
                    body['neededFacts'] = ['An observation of the reader interaction']
                return json.dumps({'answer':body})
            return super().__call__(q)
    provider = Review(slot)
    run.provider = provider
    def user(item):
        if item['gate'] == 'B':
            raise Waiting()
        return approve(item)
    run.user = user
    try:
        assert run.run().state == 'B'
        review = run.store.read(run.store.cursor().review)
        assert review['answer']['verdict'] == ('not judged' if verdict == 'uncertain' else 'gap')
        if verdict == 'gap':
            assert 'Exercise the reader interaction' in str(review['answer']['gaps'])
        else:
            assert review['notJudged']
        run.close()
        run = Run.reopen(slot/'product',provider,approve)
        finish(run)
        if verdict == 'uncertain':
            assert run.store.records('outcome')[-1][2]['verification_complete'] is False
    finally:
        run.close()
