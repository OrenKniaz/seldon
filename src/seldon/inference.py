"""C3/C4/I10: construct bounded questions and retain typed provider answers."""

import time
from pathlib import Path
from . import evidence, plan, questions
from .providers import ProviderFailure, role_for


class Inference:
    def _call(self, cursor, kind, refs, _facts=None, **context):
        role = role_for(kind, context)
        refs = list(dict.fromkeys(([cursor["kickoff"]] if kind in ("ALIGN", "PLAN", "WORK") else []) + refs))
        facts = {ref: self.store.read(ref) for ref in refs}
        facts.update({b["request"]: self.store.read(b["request"]) for b in list(facts.values()) if "words" in b and "request" in b})
        document = self.store.plan(cursor["revision"]) if cursor["revision"] else {"nodes": {}}
        nodes = document["nodes"]
        allowed = set(nodes)
        if kind in ("WORK", "JUDGE"):
            scope = self.store.units()[context["unit"]]["contract"] if kind == "WORK" else context
            allowed = {"vision", *scope["requirements"], *scope["tests"]}
            allowed.update(f for r in scope["requirements"] for f in nodes[r]["body"]["features"])
            if kind == "WORK":
                allowed.add(context["unit"])
        if cursor.get("plan") in facts:
            facts[cursor["plan"]] = {"document": {"nodes": {r: n for r, n in nodes.items() if r in allowed and
                (n["kind"] not in ("bead", "wave") or (kind == "WORK" and r == context["unit"]) or
                 (kind == "REPAIR" and n["kind"] == "bead"))}}}
            facts.pop(cursor["vision"], None)
        if cursor["kickoff"] in facts:
            facts[cursor["kickoff"]] = ({"words": facts[cursor["kickoff"]]["words"], "capabilities": self.setup} if kind == "ALIGN" else
                {k: v for k, v in self.setup.items() if k not in ("providers", "codexCommand", "factFiles", "userOrigin", "evidenceDirectory")})
        if kind == "REPAIR":
            facts.update({r: self.store.read(r) for r in cursor.get("observationScope", [])})
        if kind == "WORK" and cursor.get("repairCause"):
            facts[cursor["repairCause"]] = self.store.read(cursor["repairCause"])
        if kind in ("PLAN", "REPAIR", "REVIEW", "JUDGE", "WORK"):
            context["handles"] = [r for r in allowed if nodes[r]["kind"] != "wave" and
                                  (nodes[r]["kind"] != "bead" or kind == "REPAIR")] if nodes else ["vision"]
            context["paths"] = list(plan.file_owners(document))
            if kind == "REPAIR":
                context["units"] = list(plan.select(document, "bead"))
        if kind == "JUDGE":
            observations = evidence.facts(self.store, [r for r, b in facts.items() if "outputRefs" in b], cursor["candidate"], document)
            paths = {p for o in observations.values() for p in (o["testPaths"] if context["purpose"] in ("RED", "demo") else o["inputs"])}
            unit = context["coverage"]["unit"]
            if unit != "integration":
                own = set(self.store.units()[unit]["contract"]["ownedPaths"])
                paths &= own | {p for o in observations.values() for p in o["testPaths"]}
            if "tryItGuide" in context["assessments"]:
                paths.add("TRY.txt")
            context["paths"] = sorted(paths)
            if context["purpose"] == "demo":
                context.update(handles=[r for r, n in nodes.items() if n["kind"] not in ("bead", "wave")], paths=list(plan.file_owners(document)))
            facts[cursor["candidate"]] = {"files": {p: h for p, h in facts[cursor["candidate"]]["files"].items() if p in paths}}
            facts.update({r: {k: v for k, v in body.items() if k not in
                ("inputs", "obligations", "testIdentity", "scopes", "testSources")}
                for r, body in observations.items()})
        sources = {ref: ref for ref in facts}
        for handle, path in (self.setup.get("factFiles", {}) if kind not in ("ALIGN", "DECIDE") else {}).items():
            facts[handle], sources[handle] = {"path": path}, cursor["kickoff"]
        for ref, body in list(facts.items()):
            if kind in ("DECIDE", "REPAIR") and "words" in body:
                body.pop("subject", None)
            if isinstance(body.get("answer"), str):
                body["answer"] = self.store.read(body["answer"])["answer"]
            for key in ([] if kind == "JUDGE" and context["purpose"] == "RED" else body.get("artifactRefs", [])) + list(body.get("files", {}).values()):
                content = (self.store.root / "artifacts" / key).read_bytes()
                if content.startswith((b"\x89PNG", b"\xff\xd8")):
                    body = {"imagePath": str(self.store.root / "artifacts" / key)}
                else:
                    try:
                        body = content.decode("utf-8")
                    except UnicodeDecodeError:
                        body = {"artifactPath": str(self.store.root / "artifacts" / key), "bytes": len(content)}
                facts[f"artifact:{key}"] = body
                sources[f"artifact:{key}"] = ref
        facts = _facts if _facts is not None else {ref: {"sourceRecord": sources[ref], "revision": cursor["revision"],
                "boundedContent": body} for ref, body in facts.items()}
        try:
            if kind == "JUDGE" and len(context["assessments"]) > 4:
                raise OverflowError("Page the obligation inventory")
            question = questions.build(kind, facts, context)
        except OverflowError:
            if kind != "JUDGE" or "evidenceCoverage" in context:
                raise
            targets = context["assessments"]
            parts = ((facts, {"assessments": targets[i:i + 4], "coverage": {
                "unit": context["coverage"]["unit"], "obligations": targets[i:i + 4]}})
                for i in range(0, len(targets), 4)) if len(targets) > 4 else (
                (page, {"evidenceCoverage": coverage}) for page, coverage in questions.pages(facts))
            pages = []
            for page, extra in parts:
                call = self._call(cursor, kind, refs, _facts=page, **{**context, **extra})
                pages.append(self._answer(call))
                if call["answer"]["verdict"] != "satisfied" or call["answer"]["gaps"] or not all(a["satisfied"] for a in call["answer"]["assessments"]):
                    return {"pages": pages, "answer": call["answer"]}
            return {"pages": pages, "answer": {**call["answer"], "citations": pages,
                "assessments": [{"target": t, "satisfied": True, "why": "Established by all recorded coverage pages"} for t in targets]}}
        question_ref = attempt = None
        def invoke(question):
            nonlocal question_ref, attempt
            previous = None
            for recovery in range(3):
                timeout = (self.provider.timeout_for(kind, context) if hasattr(self.provider, "timeout_for")
                           else self.setup["providers"][role].get("timeout", 30))
                with self.store.transaction():
                    question_ref = self.store.record("question", question)
                    attempt = self.store.record("attempt", {"operation": kind, "question": question_ref,
                        "provider": self.setup["providers"][role], "timeout": timeout,
                        "deadline": time.time() + timeout, "retryOf": previous})
                    if kind == "WORK":
                        unit = self.store.units()[context["unit"]]
                        unit["active"]["invocation"] = attempt
                        self.store.unit(context["unit"], unit)
                try:
                    return self.provider(question)
                except ProviderFailure as error:
                    with self.store.transaction():
                        failure = self.store.record("provider_failure", {"attempt": attempt,
                            "reason": str(error), "transient": error.transient,
                            "cleanupVerified": error.cleanup_verified, "receipt": error.receipt})
                        self.store.record("attempt_end", {"attempt": attempt, "result": failure, "finishedAt": time.time()})
                    if not error.transient or not error.cleanup_verified:
                        raise
                    if recovery == 2:
                        raise ProviderFailure(f"{kind}: two fresh retries exhausted; {error}",
                                              True, True, error.receipt) from error
                    previous = attempt
        def record(body):
            with self.store.transaction():
                return self.store.record("provider_reply", {**body, "attempt": attempt, "finishedAt": time.time()})
        def check(answer):
            if kind == "PLAN":
                plan.create(self.store.read(cursor["vision"])["answer"], answer)
            elif kind == "REPAIR":
                plan.apply(self.store.plan(cursor["revision"]) if cursor["revision"] else {"nodes": {"vision": {
                    "kind": "vision", "body": plan.vision_body(self.store.read(cursor["vision"])["answer"])}}}, answer["edits"])
            if kind == "JUDGE" and sorted(a["target"] for a in answer["assessments"]) != sorted(context["assessments"]):
                raise ValueError("Judge must assess the exact requested inventory once")
        answer = questions.ask(invoke, question, record, check)
        invocation = {"question": question_ref, "attempt": attempt, "answer": answer,
                      "provider": self.setup["providers"][role]}
        needs = answer.get("neededFacts", []) if answer.get("verdict") == "uncertain" else [
            {"target": g["target"], "question": g["correction"]} for g in answer.get("gaps", []) if g.get("cause") == "evidence"]
        if needs:
            with self.store.transaction():
                uncertain = self._answer(invocation)
            additions = {}
            for need in needs:
                target = need["target"]
                if target in self.setup.get("factFiles", {}):
                    content = Path(self.setup["factFiles"][target]).read_text(encoding="utf-8")
                    if content != facts[target]["boundedContent"]:
                        additions[target] = {**facts[target], "boundedContent": content}
            if _facts is not None or not additions:
                raise ValueError("Unresolved question: " + "; ".join(n["question"] for n in needs))
            self.store.record("fact_gather", {"cause": uncertain, "supplied": list(additions)})
            return self._call(cursor, kind, refs, _facts={**facts, **additions}, **context)
        return invocation

    def _answer(self, invocation):
        ref = self.store.record("answer", invocation)
        if "attempt" in invocation:
            self.store.record("attempt_end", {"attempt": invocation["attempt"], "result": ref})
        return ref

    def _recover_call(self, attempt):
        replies = [b for _, _, b in self.store.records("provider_reply") if b.get("attempt") == attempt]
        if not replies or replies[-1]["errors"]:
            return None
        invocation = self.store.read(attempt)
        import json
        answer = json.loads(replies[-1]["rawAnswer"])["answer"]
        recorded = [r for r, _, b in self.store.records("answer") if b.get("attempt") == attempt]
        report = recorded[-1] if recorded else self._answer({"attempt": attempt, "answer": answer,
            "question": invocation["question"], "provider": invocation["provider"]})
        return report, replies[-1]["finishedAt"]
