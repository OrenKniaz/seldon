"""C20: project retained chunk records for older runs."""

import json


def progress(store):
    reviews = {row["chunk"]: (ref, row) for ref, _, row in store.records("lead2_chunk_review")}
    return {row.get("delivery", row["feature"]): (ref, row, reviews.get(ref))
            for ref, _, row in store.records("lead2_chunk")}


def feature_status(order, chunks, sent):
    features = {}
    for ref, name in order.items():
        key, _, verdict = chunks.get(ref, chunks.get(name, (None, {}, None)))
        result = "building"
        if key:
            result = "under review"
        if verdict:
            review, assessment = verdict
            if assessment["findings"]:
                result = "fixing" if review in sent else "failed"
            elif assessment.get("verdict") == "can't-test-here":
                result = "not-verifiable-here"
            elif assessment.get("verdict") != "pending":
                result = "passed"
        features[name] = result
    return features


def sent_feedback(store, last):
    current = store.db.execute("""
        SELECT body -> 'execution' -> 'feedback' FROM record
        WHERE kind = 'question' AND body ->> 'kind' = 'WORK' ORDER BY id DESC LIMIT 1
    """).fetchone()
    return set(last.get("feedback", [])) | set(json.loads(current[0]) if current and current[0] else [])
