"""C3/C4/I10: construct bounded questions and retain typed provider answers."""

import time
from pathlib import Path
from . import evidence, plan, questions
from .providers import role_for


class Inference:
    def _call(self, cursor, kind, refs, _facts=None, **context):
        role = role_for(kind, context)
        refs = list(dict.fromkeys(([cursor["kickoff"]] if kind in ("ALIGN", "PLAN", "WORK") else []) + refs))
        facts = {ref: self.store.read(ref) for ref in refs}
        facts.update({b["request"]: self.store.read(b["request"]) for b in list(facts.values()) if "words" in b and "request" in b})
        if kind == "JUDGE":
            observations = evidence.facts(self.store, [r for r, b in facts.items() if "outputRefs" in b],
                                          cursor["candidate"], self.store.plan(cursor["revision"]))
            facts.update({r: {k: v for k, v in body.items() if k not in ("inputs", "obligations", "testIdentity", "scopes")}
                          for r, body in observations.items()})
        if cursor.get("plan") in facts:
            nodes = self.store.plan(cursor["revision"])["nodes"]
            facts[cursor["plan"]] = {"document": {"nodes": {r: n for r, n in nodes.items()
                if (n["kind"] not in ("bead", "wave") and (kind != "JUDGE" or n["kind"] != "test" or
                    context["purpose"] == "demo" or n["body"]["stage"] == "verification")) or
                (kind == "WORK" and r == context["unit"]) or (kind == "REPAIR" and n["kind"] == "bead")}}}
            if kind == "WORK":
                scope = self.store.units()[context["unit"]]["contract"]
                allowed = {"vision", context["unit"], *scope["requirements"], *scope["tests"]}
                allowed.update(f for r in scope["requirements"] for f in nodes[r]["body"]["features"])
                facts[cursor["plan"]]["document"]["nodes"] = {r: n for r, n in nodes.items() if r in allowed}
            facts.pop(cursor["vision"], None)
        if cursor["kickoff"] in facts:
            facts[cursor["kickoff"]] = ({"words": facts[cursor["kickoff"]]["words"], "capabilities": self.setup} if kind == "ALIGN" else
                {k: v for k, v in self.setup.items() if k not in ("providers", "codexCommand", "factFiles", "userOrigin", "evidenceDirectory")})
        if kind == "REPAIR":
            facts.update({r: self.store.read(r) for r in cursor.get("observationScope", [])})
        if kind == "WORK" and cursor.get("repairCause"):
            facts[cursor["repairCause"]] = self.store.read(cursor["repairCause"])
        if kind in ("PLAN", "REPAIR", "REVIEW", "JUDGE", "WORK"):
            context["handles"] = [r for r, n in self.store.plan(cursor["revision"])["nodes"].items()
                                  if n["kind"] != "wave" and (n["kind"] != "bead" or kind == "REPAIR")] if cursor["revision"] else ["vision"]
            if kind == "REPAIR":
                context["units"] = list(plan.select(self.store.plan(cursor["revision"]), "bead")) if cursor["revision"] else []
            context["paths"] = [p for b in plan.select(self.store.plan(cursor["revision"]), "bead").values()
                                for p in b["testPaths" if kind == "JUDGE" and context["purpose"] == "RED" else "ownedPaths"]] if cursor["revision"] else []
            if kind == "JUDGE" and context["purpose"] == "RED":
                context["handles"] = context["assessments"]
        if kind == "WORK":
            context["handles"] = list(allowed)
            context["paths"] = scope["ownedPaths"]
        sources = {ref: ref for ref in facts}
        for handle, path in (self.setup.get("factFiles", {}) if kind not in ("ALIGN", "DECIDE") else {}).items():
            facts[handle], sources[handle] = {"path": path}, cursor["kickoff"]
        for ref, body in list(facts.items()):
            if kind in ("DECIDE", "REPAIR") and "words" in body:
                body.pop("subject", None)
            if isinstance(body.get("answer"), str):
                body["answer"] = self.store.read(body["answer"])["answer"]
            for key in body.get("artifactRefs", []) + ([] if kind == "JUDGE" and context["purpose"] == "RED" else list(body.get("files", {}).values())):
                content = (self.store.root / "artifacts" / key).read_bytes()
                facts[f"artifact:{key}"] = ({"imagePath": str(self.store.root / "artifacts" / key),
                    "instruction": "This actual browser screenshot is attached to the question; look at it to judge visual quality"}
                    if content.startswith((b"\x89PNG", b"\xff\xd8")) else content[:8192].decode("utf-8", errors="replace"))
                sources[f"artifact:{key}"] = ref
        facts = _facts if _facts is not None else {ref: {"sourceRecord": sources[ref], "revision": cursor["revision"],
                "boundedContent": body} for ref, body in facts.items()}
        question = questions.build(kind, facts, context)
        with self.store.transaction():
            question_ref = self.store.record("question", question)
            attempt = self.store.record("attempt", {"operation": kind, "question": question_ref,
                                                     "provider": self.setup["providers"][role],
                                                     "deadline": time.time() + getattr(self.provider, "timeout", 30)})
            if kind == "WORK":
                unit = self.store.units()[context["unit"]]
                unit["active"]["invocation"] = attempt
                self.store.unit(context["unit"], unit)
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
        answer = questions.ask(self.provider, question, record, check)
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
        self.store.record("attempt_end", {"attempt": invocation["attempt"], "result": ref})
        return ref

    def _recover_call(self, attempt):
        replies = [b for _, _, b in self.store.records("provider_reply") if b["attempt"] == attempt]
        if not replies or replies[-1]["errors"]:
            return None
        invocation = self.store.read(attempt)
        import json
        answer = json.loads(replies[-1]["rawAnswer"])["answer"]
        recorded = [r for r, _, b in self.store.records("answer") if b["attempt"] == attempt]
        report = recorded[-1] if recorded else self._answer({"attempt": attempt, "answer": answer,
            "question": invocation["question"], "provider": invocation["provider"]})
        return report, replies[-1]["finishedAt"]
