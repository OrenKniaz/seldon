"""Intake and reviewer values; explicit offline observations, no provider execution.

Missing derived catalog hashes are computed; supplied hashes must match. Choice
hashes cover exactly provider, executable, executableDigest, version, model and
effort. Catalog digests cover the normalized catalog with only digest omitted.
Observations are supplied evidence, not independently verified entitlement.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

Recipe = str
Value = dict[str, Any]
SIZES = ("tiny", "small", "medium", "large")


class IntakeDecisionV1(TypedDict):
    schema: Literal["dot-intake-v1"]
    requestKind: Literal["new", "feature", "fix", "refactor", "continue"]
    sizeEstimate: Literal["tiny", "small", "medium", "large"]
    recipe: Recipe
    baselineRevisions: Value
    delta: Value
    ambiguity: Value
    consequence: Value
    dependencyBreadth: Value
    constraints: Value
    rationale: str
    assumptions: list[str]
    expectedSupervision: str
    nextGate: str
    reviewerDecisionId: str
    confirmation: Value | None
    supersedes: Value | None
    adaptation: Value | None


class ReviewerCatalogV1(TypedDict):
    schema: Literal["dot-reviewer-catalog-v1"]
    id: str
    observedAt: str
    providers: list[Value]
    digest: str


class ReviewerSelectionV1(TypedDict):
    schema: Literal["dot-reviewer-selection-v1"]
    catalogId: str | None
    catalogDigest: str | None
    reviewers: list[Value]
    stages: list[str]
    assurance: Value
    confirmation: Value | None
    recommendation: Value


def _refuse(code: str, reason: str) -> None:
    raise ValueError(f"{code}: {reason}")


def canonical_digest(value: object) -> str:
    """SHA256 of finite UTF8 JSON, sorted keys, compact separators, no ASCII escaping.

    Callers must omit self-hash fields; no fields are implicitly stripped here.
    """
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("value-json-invalid: expected finite UTF8 JSON") from error
    return hashlib.sha256(encoded).hexdigest()


def _object(value: Any, keys: str, path: str, optional: str = "") -> Value:
    required, allowed = set(keys.split()), set((keys + " " + optional).split())
    if not isinstance(value, dict):
        _refuse("value-shape-invalid", f"{path} must be an object")
    missing, extra = required - value.keys(), value.keys() - allowed
    if missing or extra:
        _refuse(
            "value-shape-invalid", f"{path} missing={sorted(missing)} unexpected={sorted(extra)}"
        )
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _refuse("value-text-invalid", f"{path} must be a nonempty string")
    return value


def _enum(value: Any, choices: tuple[str, ...], path: str) -> str:
    if not isinstance(value, str) or value not in choices:
        _refuse("value-enum-invalid", f"{path} must be one of {choices}")
    return value


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _refuse("value-hash-invalid", f"{path} must be lowercase SHA256")
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        _refuse("value-bool-invalid", f"{path} must be boolean")
    return value


def _list(value: Any, path: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list) or (nonempty and not value):
        _refuse("value-list-invalid", f"{path} must be {'nonempty ' if nonempty else ''}list")
    return value


def _strings(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    return sorted({_text(item, path) for item in _list(value, path, nonempty=nonempty)})


def _authority(value: Any, path: str) -> Value | None:
    if value is None:
        return None
    item = _object(
        value, "approvalId eventId origin statement sourceRef authorizedScopeDigest", path
    )
    for key in ("approvalId", "statement", "sourceRef"):
        _text(item[key], f"{path}.{key}")
    if type(item["eventId"]) is not int or item["eventId"] < 1:
        _refuse("authority-invalid", f"{path}.eventId must be a positive event identity")
    _enum(item["origin"], ("user", "standing-delegation"), f"{path}.origin")
    _hash(item["authorizedScopeDigest"], f"{path}.authorizedScopeDigest")
    return deepcopy(item)


def propose(request: Value, baseline: Value | None, obligations: list[Value]) -> IntakeDecisionV1:
    """Gather baseline, ambiguity and dependency facts; the planner judges process.

    Size and recipe are proposals, never fact-derived minimums. User overrides
    retain exact words/source for interpretation; none of this grants consent.
    """
    canonical_digest([request, baseline, obligations])
    fields = (
        "requestKind sizeEstimate delta ambiguity consequence dependencyBreadth constraints "
        "rationale assumptions expectedSupervision nextGate reviewerDecisionId confirmation "
        "supersedes adaptation"
    )
    result = deepcopy(_object(request, fields, "request", "schema recipe sizeOverride"))
    if "schema" in result:
        _enum(result["schema"], ("dot-intake-v1",), "request.schema")
    _enum(result["requestKind"], ("new", "feature", "fix", "refactor", "continue"), "requestKind")
    _enum(result["sizeEstimate"], SIZES, "sizeEstimate")
    for key in ("rationale", "expectedSupervision", "nextGate", "reviewerDecisionId"):
        _text(result[key], key)
    result["assumptions"] = _strings(result["assumptions"], "assumptions")
    delta = _object(
        result["delta"],
        "summary nodeIds outcome successCriteria ownershipAreas interfaceIds",
        "delta",
    )
    for key in ("summary", "outcome"):
        _text(delta[key], f"delta.{key}")
    for key in ("nodeIds", "successCriteria", "ownershipAreas", "interfaceIds"):
        delta[key] = _strings(
            delta[key], f"delta.{key}", nonempty=key in ("successCriteria", "ownershipAreas")
        )
    ambiguity = _object(result["ambiguity"], "level reasons", "ambiguity")
    _enum(ambiguity["level"], ("low", "material", "high"), "ambiguity.level")
    ambiguity["reasons"] = _strings(
        ambiguity["reasons"], "ambiguity.reasons", nonempty=ambiguity["level"] != "low"
    )
    consequence = _object(result["consequence"], "level reversibility risks", "consequence")
    _enum(
        consequence["level"], ("ordinary", "hard-invariant", "public-boundary"), "consequence.level"
    )
    _text(consequence["reversibility"], "consequence.reversibility")
    consequence["risks"] = _strings(
        consequence["risks"], "consequence.risks", nonempty=consequence["level"] != "ordinary"
    )
    breadth = _object(
        result["dependencyBreadth"], "producerIds crossWaveContracts", "dependencyBreadth"
    )
    for key in breadth:
        breadth[key] = _strings(breadth[key], f"dependencyBreadth.{key}")
    constraints = _object(
        result["constraints"], "runtime security", "constraints"
    )
    for key in ("runtime", "security"):
        constraints[key] = _strings(constraints[key], f"constraints.{key}")
    obligation_ids = set()
    for obligation in _list(obligations, "obligations"):
        _object(obligation, "id requiredForFullVision", "obligation")
        identity = _text(obligation["id"], "obligation.id")
        _bool(obligation["requiredForFullVision"], "obligation.requiredForFullVision")
        if identity in obligation_ids:
            _refuse("intake-obligation-duplicate", identity)
        obligation_ids.add(identity)
    previous = None
    revisions = {"publishedRevision": None, "spineRevision": None}
    if baseline is not None:
        _object(baseline, "publishedRevision spineRevision obligationIds recipe", "baseline")
        for key in revisions:
            revisions[key] = baseline[key]
            if baseline[key] is not None:
                _hash(baseline[key], f"baseline.{key}")
        missing = (
            set(_strings(baseline["obligationIds"], "baseline.obligationIds")) - obligation_ids
        )
        if missing:
            _refuse("intake-obligation-loss", f"missing baseline obligations: {sorted(missing)}")
        previous = baseline["recipe"]
        if previous is not None:
            _text(previous, "baseline.recipe")
    recipe = result.get("recipe")
    if recipe is not None:
        _text(recipe, "recipe")
    override = result.get("sizeOverride")
    if override is not None:
        _object(override, "size words sourceRef", "sizeOverride")
        _enum(override["size"], SIZES, "sizeOverride.size")
        for key in ("words", "sourceRef"):
            _text(override[key], "sizeOverride." + key)
    result["confirmation"] = _authority(result["confirmation"], "confirmation")
    if result["supersedes"] is not None:
        ref = _object(result["supersedes"], "id revision", "supersedes")
        _text(ref["id"], "supersedes.id")
        _hash(ref["revision"], "supersedes.revision")
    adaptation = result["adaptation"]
    if adaptation is not None:
        _object(
            adaptation,
            "previousRecipe reason affectedObligationIds evidenceRefs authority",
            "adaptation",
        )
        if previous is None or adaptation["previousRecipe"] != previous:
            _refuse("intake-adaptation-invalid", "previousRecipe must match baseline.recipe")
        _text(adaptation["reason"], "adaptation.reason")
        adaptation["affectedObligationIds"] = _strings(
            adaptation["affectedObligationIds"], "adaptation.affectedObligationIds"
        )
        unknown = set(adaptation["affectedObligationIds"]) - obligation_ids
        if unknown:
            _refuse("intake-adaptation-invalid", f"unknown obligations: {sorted(unknown)}")
        adaptation["evidenceRefs"] = _strings(
            adaptation["evidenceRefs"], "adaptation.evidenceRefs", nonempty=True
        )
        adaptation["authority"] = _authority(adaptation["authority"], "adaptation.authority")
        if adaptation["authority"] is None:
            _refuse("intake-adaptation-invalid", "adaptation requires supplied authority")
    result.update(schema="dot-intake-v1", recipe=recipe, baselineRevisions=revisions)
    return result


def routing(store, node):
    """Expose a bound size/process verdict and the user's visible override proposal."""
    from codex_harness.core.approvals import assess

    judgment = assess(store, "planning-size", dict(intake=node,
        baseline=store.spine().to_dict(), question="Judge tiny/small/medium/large and an "
        "appropriate collapsed process in meaning.size and meaning.process. Map brownfield "
        "existing behavior/evidence first. Do not derive ceremony from counts or labels. "
        "If sizeOverride is supplied, interpret its exact words/source and record boolean "
        "meaning.overrideAccepted; user choice owns the size override."), subject_ids=store.spine().ids())
    meaning = judgment["meaning"]
    valid = judgment["positiveCredit"] and meaning.get("size") in SIZES and isinstance(meaning.get("process"), str) and bool(meaning["process"].strip())
    override = node["fields"].get("sizeOverride")
    if override is not None and type(meaning.get("overrideAccepted")) is not bool:
        valid = False
    return dict(state="judged" if valid or judgment["concreteGap"] else "pending-judgment",
                judgment=judgment, size=(override["size"] if override and meaning.get("overrideAccepted") else meaning.get("size")) if valid else None,
                process=meaning.get("process") if valid else None, userOverride=override,
                overrideAvailable=True, next="User may override the displayed size in their own words")


def _effort(value: Any, path: str) -> Value:
    if not isinstance(value, dict):
        _refuse(
            "reviewer-effort-invalid", f"{path} must explicitly name effort or mark unsupported"
        )
    kind = _enum(value.get("kind"), ("named", "unsupported"), f"{path}.kind")
    _object(value, "kind value" if kind == "named" else "kind", path)
    if kind == "named":
        _text(value["value"], f"{path}.value")
    return value


def _model(value: Any) -> str:
    model = _text(value, "model")
    if model.lower() in ("default", "latest", "auto") or model.lower().endswith("-latest"):
        _refuse("reviewer-model-unresolved", f"exact observed model required: {model}")
    return model


def validate_catalog(observation: Value) -> ReviewerCatalogV1:
    """Normalize supplied evidence or an explicit offline request; preserve choice order.

    Catalog entries are offerings, never access or review-path proof. Legacy
    available claims are normalized to offered; actual launches observe access.
    No wall-clock expiry is invented: freshness is selection's exact catalog binding.
    """
    if isinstance(observation, dict) and observation.get("discovery") == "offline":
        from codex_harness.core.project_init import offline_reviewers

        _object(observation, "discovery", "offline observation", "userModels")
        observation = {
            "schema": "dot-reviewer-catalog-v1",
            "id": f"RC-{uuid4()}",
            "observedAt": datetime.now(UTC).isoformat(),
            "providers": offline_reviewers(observation.get("userModels")),
        }
    canonical_digest(observation)
    result = deepcopy(_object(observation, "schema id observedAt providers", "catalog", "digest"))
    _enum(result["schema"], ("dot-reviewer-catalog-v1",), "catalog.schema")
    identity = _text(result["id"], "catalog.id")
    try:
        if not identity.startswith("RC-"):
            raise ValueError
        UUID(identity[3:])
    except ValueError as error:
        raise ValueError("reviewer-catalog-id-invalid: expected RC-<uuid>") from error
    try:
        observed = datetime.fromisoformat(_text(result["observedAt"], "catalog.observedAt"))
        if observed.tzinfo is None:
            raise ValueError
    except ValueError as error:
        raise ValueError(
            "reviewer-catalog-time-invalid: expected timezone-aware timestamp"
        ) from error
    legacy_observation = deepcopy(result)
    legacy_available = False
    providers, choices = set(), set()
    for provider in _list(result["providers"], "catalog.providers"):
        _object(
            provider,
            "provider executable executableDigest version installed authentication "
            "discovery source choices reason",
            "provider",
        )
        name = _text(provider["provider"], "provider.provider")
        if name in providers:
            _refuse("reviewer-provider-duplicate", name)
        providers.add(name)
        executable = provider["executable"]
        if executable is not None:
            _text(executable, "provider.executable")
            if not (
                PureWindowsPath(executable).is_absolute() or PurePosixPath(executable).is_absolute()
            ):
                _refuse("reviewer-executable-invalid", "executable must be absolute")
        if provider["executableDigest"] is not None:
            _hash(provider["executableDigest"], "provider.executableDigest")
        if provider["version"] is not None:
            _text(provider["version"], "provider.version")
        if provider["installed"] is not None:
            _bool(provider["installed"], "provider.installed")
        _enum(provider["authentication"], ("available", "unavailable", "unknown"), "authentication")
        _enum(
            provider["discovery"],
            ("observed", "unsupported", "blocked", "failed", "unknown"),
            "discovery",
        )
        _text(provider["reason"], "provider.reason")
        source = provider["source"]
        if source is not None:
            _object(source, "kind reference digest", "provider.source")
            _enum(source["kind"], ("supported-list", "provider-local-metadata"), "source.kind")
            _text(source["reference"], "source.reference")
            _hash(source["digest"], "source.digest")
        for choice in _list(provider["choices"], "provider.choices"):
            _object(
                choice, "model effort availability reviewPathSupported reason", "choice", "choiceId"
            )
            _model(choice["model"])
            _effort(choice["effort"], "choice.effort")
            _enum(
                choice["availability"],
                ("offered", "available", "unavailable", "unknown"),
                "choice.availability",
            )
            _bool(choice["reviewPathSupported"], "choice.reviewPathSupported")
            _text(choice["reason"], "choice.reason")
            if choice["availability"] == "available":
                legacy_available = True
                choice["availability"] = "offered"
            binding = {
                key: provider[key]
                for key in ("provider", "executable", "executableDigest", "version")
            }
            binding.update(model=choice["model"], effort=choice["effort"])
            digest = canonical_digest(binding)
            if "choiceId" in choice and _hash(choice["choiceId"], "choice.choiceId") != digest:
                _refuse("reviewer-choice-hash-mismatch", f"{name}/{choice['model']}")
            if digest in choices:
                _refuse("reviewer-choice-duplicate", digest)
            choices.add(digest)
            choice["choiceId"] = digest
    digest = canonical_digest({key: value for key, value in result.items() if key != "digest"})
    if "digest" in result and _hash(result["digest"], "catalog.digest") != digest:
        if legacy_available and result["digest"] == canonical_digest(
            {key: value for key, value in legacy_observation.items() if key != "digest"}
        ):
            _refuse(
                "reviewer-catalog-refresh-required",
                "Legacy catalog access claims need explicit re-selection: record a new "
                "offering observation through reviewer-catalog with a NEW RC-UUID, "
                "availability=offered and no digest; then record reviewer-choice against "
                "the returned catalog ID/digest and explicitly confirm it. Existing "
                "catalogs, selections and review evidence are retained as history.",
            )
        _refuse(
            "reviewer-catalog-hash-mismatch", "supplied digest differs from normalized observation"
        )
    result["digest"] = digest
    return result


def validate_selection(
    catalog: ReviewerCatalogV1 | None, selection: ReviewerSelectionV1, author_sessions: Value
) -> dict:
    """Collect catalog/effort defects; valid draft assurance never authorizes execution."""
    current = validate_catalog(catalog) if catalog is not None else None
    canonical_digest([selection, author_sessions])
    selected = deepcopy(
        _object(
            selection,
            "schema catalogId catalogDigest reviewers stages assurance confirmation recommendation",
            "selection",
        )
    )
    _enum(selected["schema"], ("dot-reviewer-selection-v1",), "selection.schema")
    issues = []
    if current is not None:
        _text(selected["catalogId"], "selection.catalogId")
        _hash(selected["catalogDigest"], "selection.catalogDigest")
        if selected["catalogId"] != current["id"] or selected["catalogDigest"] != current["digest"]:
            issues.append(
                "reviewer-catalog-stale: selection must bind the exact current catalog ID and digest"
            )
    else:
        # No invented observation: B binds the planner's exact proposed tuples.
        selected["catalogId"] = selected["catalogDigest"] = None
    reviewers = _list(selected["reviewers"], "selection.reviewers", nonempty=True)
    for index, reviewer in enumerate(reviewers):
        try:
            _object(reviewer, "name provider choiceId model effort", "reviewer")
            _effort(reviewer["effort"], f"reviewer[{index}].effort")
        except ValueError as error:
            issues.append(str(error))
    if issues:
        raise ValueError("; ".join(issues))
    choices = {
        choice["choiceId"]: (provider, choice)
        for provider in (current["providers"] if current else [])
        for choice in provider["choices"]
    }
    selected["stages"] = _strings(selected["stages"], "selection.stages", nonempty=True)
    for stage in selected["stages"]:
        _enum(stage, ("plan", "readiness", "wave-acceptance", "full-vision"), "selection.stage")
    assurance = _object(
        selected["assurance"],
        "independentRequired minIndependentReviewers crossProviderRequired basisRefs",
        "assurance",
    )
    _bool(assurance["independentRequired"], "assurance.independentRequired")
    _bool(assurance["crossProviderRequired"], "assurance.crossProviderRequired")
    minimum = assurance["minIndependentReviewers"]
    if (
        type(minimum) is not int
        or minimum < 0
        or (assurance["independentRequired"] and minimum == 0)
    ):
        _refuse(
            "reviewer-assurance-invalid",
            "independentRequired needs a positive minimum; minimum must be nonnegative",
        )
    assurance["basisRefs"] = _strings(assurance["basisRefs"], "assurance.basisRefs", nonempty=True)
    selected["confirmation"] = _authority(selected["confirmation"], "confirmation")
    recommendation = _object(
        selected["recommendation"], "choiceIds reason catchRateEvidenceRefs", "recommendation"
    )
    # Recommendations and reviewer order express preferences, not sets.
    recommended = _list(recommendation["choiceIds"], "recommendation.choiceIds")
    for identity in recommended:
        _hash(identity, "recommendation.choiceId")
        if current is not None and identity not in choices:
            _refuse("reviewer-choice-absent", f"recommended choice {identity} absent")
    if len(recommended) != len(set(recommended)):
        _refuse("reviewer-choice-duplicate", "recommendation repeats a choice")
    if current is None:
        recommendation["choiceIds"] = []
    _text(recommendation["reason"], "recommendation.reason")
    recommendation["catchRateEvidenceRefs"] = _strings(
        recommendation["catchRateEvidenceRefs"], "catchRateEvidenceRefs"
    )
    sessions = _object(
        author_sessions, "implementationSessionIds reviewerSessionIds", "author_sessions"
    )
    authors = set(_strings(sessions["implementationSessionIds"], "implementationSessionIds"))
    reviewer_sessions = sessions["reviewerSessionIds"]
    if not isinstance(reviewer_sessions, dict):
        _refuse(
            "reviewer-session-invalid", "reviewerSessionIds must map reviewer names to session IDs"
        )
    for name, session in reviewer_sessions.items():
        _text(name, "reviewerSessionIds.name")
        _text(session, "reviewerSessionIds.session")
    names, providers, models = set(), set(), set()
    unmet, independence = [], []
    for reviewer in reviewers:
        name = _text(reviewer["name"], "reviewer.name")
        if name in names:
            _refuse("reviewer-name-duplicate", name)
        names.add(name)
        _text(reviewer["provider"], "reviewer.provider")
        _model(reviewer["model"])
        if current is not None:
            _hash(reviewer["choiceId"], "reviewer.choiceId")
            if reviewer["choiceId"] not in choices:
                _refuse("reviewer-choice-absent", f"{name}: exact chosen identity absent")
            provider, choice = choices[reviewer["choiceId"]]
            if reviewer["provider"] != provider["provider"] or any(
                reviewer[key] != choice[key] for key in ("model", "effort")
            ):
                _refuse("reviewer-tuple-mismatch", f"{name}: exact provider/model/effort differs")
        else:
            from codex_harness.core.provider_launch import get_adapter

            get_adapter(reviewer["provider"])
            reviewer["choiceId"] = None
        providers.add(reviewer["provider"])
        models.add(reviewer["model"])
        # Catalog assertions cannot establish or deny actual protected access.
        # The first real review launch is the probe; successful current review
        # and independence gates remain owned by their existing consumers.
    extra_sessions = set(reviewer_sessions) - names
    if extra_sessions:
        _refuse(
            "reviewer-session-invalid",
            f"session identities for unselected names: {sorted(extra_sessions)}",
        )
    counts = Counter(reviewer_sessions.values())
    qualifying = []
    for reviewer in reviewers:
        name = reviewer["name"]
        session = reviewer_sessions.get(name)
        if session is None:
            independence.append(f"reviewer-independence-unknown: {name} has no session identity")
        elif session in authors:
            independence.append(f"reviewer-is-author: {name}")
        elif counts[session] > 1:
            independence.append(f"reviewer-session-shared: {name}")
        else:
            qualifying.append(name)
    if len(qualifying) < minimum:
        independence.append(
            f"reviewer-independent-count: required {minimum}, qualifying {len(qualifying)}"
        )
    if minimum:
        unmet.extend(independence)
    diversity = (
        "cross-provider"
        if len(providers) > 1
        else "single-provider/different-model"
        if len(models) > 1
        else "single-provider/same-model"
    )
    return {
        "selection": selected,
        "independence": {
            "required": minimum,
            "qualifyingReviewerNames": sorted(qualifying),
            "unmet": sorted(independence),
        },
        "diversity": diversity,
        "unmetRequirements": sorted(unmet),
    }
