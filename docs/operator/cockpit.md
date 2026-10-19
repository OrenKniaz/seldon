# Read-only product cockpit

The root of `codex_harness.core.status_server` serves the Option A / revision 2.1 cockpit. The existing workspace remains at `/legacy`; `/board` and `/api/status` retain their existing behavior. The server binds to loopback and rejects non-GET methods. This UI adds no operational, approval, acceptance, process or provider controls. Decisions stay in the user's provider chat.

Start with the existing source interpreter and this checkout's `src` on `PYTHONPATH`:

```powershell
$env:PYTHONPATH = '<absolute-checkout>/src'
& '<prepared-python>' -B -m codex_harness.core.status_server `
  --workspace '<recorded-project-workspace>' --runs '<recorded-runs-root>' --port 0
```

The printed loopback URL contains the actual free port. No installation, provider invocation or credential change is required. Loopback/read-only operation does **not** solve the repository's retained authenticated-localhost obligation.

## Entry modes

| Entry | Data | Meaning |
| --- | --- | --- |
| `/?mode=live` | GET `/api/status` | Current canonical snapshot for the server's selected workspace/runs. Missing sources stay unavailable. |
| `/?mode=history&project=linkshelf` | Unchanged public linkshelf run and planning extracts | Historical records, including the recorded parent links, repair story and proxy statement. Not a live run. |
| `/?mode=history&project=csv2md` | Unchanged public csv2md extract | Recorded decisions, options, phase history and run events. Not a new request for approval. |
| `/?mode=synthetic` | Explicit synthetic test JSON | Ten capabilities, nine waves, 120 work items, one blocker affecting three items, two decisions, five recorded identities including two advisory-quiet observations. No actual agents or product proof. |

The public JSON inputs under `tests/browser/cockpit/fixtures/public/` are byte-preserved copies of the accepted design inputs. They are not reference solutions or evaluators. `fixtures/synthetic/stress.json` is separately labelled and contains synthetic planning/run records. Source checkouts serve these exact named files for inspection and browser checks. Fixtures are not wheel runtime assets: history/synthetic modes in an installation without those test inputs show an unavailable-source state, never live or invented data.

Only the named cockpit assets, existing legacy pages and four named fixture URLs are served. Browser input cannot choose filesystem paths. There are no remote fonts, scripts or runtime dependencies. Source strings are inserted using DOM text, including hostile or malformed strings; evidence paths and recorded commands are displayed, not executed or made into operational links.

## The main interaction

Forest is the default palette; navy, light and plain-language controls are available on every view. Body text is 18px; secondary text and compact controls remain at least 15px. States use words and symbols. The sticky **Where / Blocking / Needs you** summary, **Right now** sentence and proof/acceptance rail remain visible during scrolling, including at 375px.

The macro map contains one vision and at most ten capability nodes. Product areas appear only when explicitly recorded in the optional projection. Missing area metadata produces no invented group or heading. Larger capability lists remain available through a searchable five-result picker. Wave navigation shows at most nine buttons and has the same bounded search pattern.

Opening a capability replaces the overview. Breadcrumbs distinguish vision, recorded product area, capability and user stories. One story path is expanded; done/later work stays grouped. A group inspector shows at most 20 items per page. Cross-branch dependencies open the actual linked work record rather than adding crossing graph edges. Returning to the overview preserves the selected wave and capability outline. View, lens, selection, theme and plain mode are local URL state; they do not mutate the plan.

The board's counts describe canonical work records, not the number of visible cards. Without canonical spine membership, the population is labelled **Recorded run work**, never Global or vision coverage. Capability, story and selected-wave counts describe their own linked work records. Unknown membership or status stays explicit; a capability's completed work is not a product-proof numerator. A shared work item can legitimately contribute to multiple branch scopes, but is counted once in the global work list. The board retains a separate unknown-state column when the source's state cannot be classified.

The historical timeline shows recorded phase/run events. Moving it changes the visible event range; it does **not** manufacture earlier work states or editorial execution waves. Historical decisions remain inspectable after their recorded choice. The inspector preserves proposals versus agreements and their sources; it does not infer a repair owner or agreed action from mail prose.

## Optional data and evidence truth

The existing snapshot is sufficient to display canonical features, stories, requirements, work records, plan stage, decisions and crew identities when present. The additive `cockpit` projection and every child field are optional. This UI does not own or modify that producer. One **What we track for this plan** chip explains coverage; unknown elements use quiet dashes or neutral rings with accessible unknown labels. Missing/null values never become zeroes, successful checks, approvals or authority. Explicit null blocker/crew fields override raw fallbacks. Absent fields can use raw work/crew only with a known matching publication; stale runs remain inspectable under **Earlier runs** and supply no current work category, orphan IDs or actionable crew.

Historical linkshelf table rows decode their original `parents_json` and `fields_json` safely, preserving stable story/work links. Its approval remains a separate recorded event with no manufactured `accepted` boolean. The displayed 5/5 result uses the exact approval-bound acceptance run, all passing results and its recorded product-run repository path (the old extract has no subject field). csv2md's 8/8 uses its explicit product-subject scenario/run bindings. These are recorded global scenario checks, not whole-vision percentages or per-capability numerators. Both proxy approvals remain dogfood-only, origin unverified and distinct from personal acceptance; original rebind provenance stays inspectable. History's three answers summarize the passing recorded checks and proxy approval, while personal review remains optional. Historical crew describes workers used in that run, without liveness claims.

- A current plan phase is labelled **Current**, not an execution wave. A plan-bound **draft plan** remains inspectable without a runtime run; its order, declared phase and selected lens never mark one current or done. Live waves require the original runtime binding.
- The two nullable rings are explicitly labelled **product scenario checks** and **requirement validation**. Scenario counts and validated-work-item counts have different denominators. They are never labelled whole-vision proof, proven real use or personal acceptance. Missing binding fields, oracle subjects, stale source/publication revisions and failed refreshes do not earn numerators. A raw completed or `requirement-validated` work-state string does not populate either ring.
- Whole-vision proof is unavailable until its canonical accounting exists. No phase percentage, bead-completion percentage or synthetic design percentage replaces it.
- Personal, proxy and recorded-origin-unverified approval have separate labels. Project approval is never extrapolated to capability acceptance. A recorded user approval with `humanOriginVerified=false` remains unverified. No decisions, acceptance not ready and ready for review are distinct; readiness is shown only when explicitly sourced.
- Blocker next owner, action, affected IDs and proposed/agreed status require recorded fields. Otherwise each says it is not tracked. Raw mail and evidence references remain inspectable as source material.
- Crew provider/model values are recorded identities. Quietness/stuck observations are advisory, not verified process liveness. Terminal observations do not prove descendant cleanup. Available usage values stay in the recorded identity details; missing usage remains unknown.

Live polling uses only GET `/api/status`. Legacy work/crew fallback requires `publication.up_to_date === true` plus the matching published run revision. Missing, null, false or string-valued freshness is unconfirmed, even when the old run matches the published revision; its records remain under Earlier runs and cannot supply current done, crew or zero-blocker facts. No trusted-freshness flag is invented. An unsuccessful refresh preserves the last snapshot with an explicit stale label; the two proof numerators become unavailable. A malformed first response shows an unavailable state. History or synthetic data is never used as a live fallback. Switching sources prevents a late response from the old source overwriting the new selection.

In a recorded `plan-validated` run, a bead with status `reused` and `integratedValidation: passed` counts as done work, labelled **done (reused from an earlier validated attempt)** in its details. Missing/failed validation or an unvalidated run leaves reuse unknown. Actual linkshelf history therefore has five done work items and no unknown work states, including its capability scopes. Reuse never fills proof rings or supplies a personal acceptance claim.

`currentEligibility(snapshot)` owns the shared current-run decision. It requires explicit fresh publication, nonempty string identities (excluding the unavailable sentinel), a matching selected original run, and consistent optional source revisions. An explicit `runBinding` must say `current` and name that same run; historical, unavailable, null, unknown or foreign bindings stay unconfirmed. An absent optional binding can use the confirmed canonical publication. The one decision gates current work, raw/projected crew, runtime blockers and live waves. Requirement validation retains its separate evidence checks and current runtime binding. Independently plan-bound product scenario checks do not require a dispatch run. History uses a separate recorded-run path and gains no current authority.

Editable-plan answers have their own plan consistency check. A projected blocker with only `review-findings:` refs survives before publication/execution, as do current planning questions and gates. A contradictory optional `source.planRevision` makes those projected answers unknown. When a plan finding and stale `run:` facts share a snapshot, only the plan finding and plan questions remain current. Explicit nulls stay unknown. An empty/unknown unbound runtime population never becomes zero blockers. This preserves the Understand/Shape planning answers without granting runtime authority.

The complete plan/runtime mapping is:

| Consumer | Source and scope |
| --- | --- |
| Topology, titles, promise, declared membership | Canonical spine remains usable before execution. Optional enrichment requires a matching recorded plan identity; it cannot replace canonical labels/parents or append foreign capabilities. |
| Planning answers | Projected questions and `review-findings:` blockers require the matching plan identity. Legacy canonical questions/gates remain available when no optional projection contradicts them. Nulls do not fall through. |
| Draft waves | Matching plan/source revision and explicit draft status/items; no current wave, done state or runtime advancement inferred. |
| Product scenario checks | Matching recorded plan/source identity, consistent optional publication identity, product subject, original acceptance-run/build identifiers and evidence refs. Failed refresh, stale/missing plan binding, oracle, malformed or explicit null stays unknown. No dispatch run is required. |
| Requirement validation | Separate original claim/spec/publication/unmocked integration evidence supplied by its owner; no scenario/work-state substitution. The actual current canonical snapshots keep this null. |
| Current work, crew, runtime blockers, live waves | Strict shared runtime eligibility. Earlier runs remain historical. Validated historical reuse remains recorded done work with neutral proof rings. |
| Acceptance/readiness and history | Recorded acceptance origin, readiness and explicit unknowns stay separate from check totals or done work. Earlier events/mail are labelled recorded history and establish no current authority or agreement. |

The header and Vault consume `cockpit.vision.proof.productScenarios` independently of branch rings. DOT's actual local scenario producer snapshot yields **4/4 globally**, **1/1 for F1**, and **F2 unknown**; its three unlinked/ambiguous scenarios remain only in the global total. The UI displays these as **Product scenario checks**, never as whole-vision, real-use or personal acceptance proof. The historical 5/5 and 8/8 paths remain separately labelled recorded runs.

## Verification and retained limits

The **Recorded reviews** button opens the existing keyboard-accessible inspector.
It shows current-manifest and historical roundtable records separately, reviewer
and provider, **Model requested**, original counts and recorded outcomes. Expand a
finding cluster to see every member's text, suggestion, disposition and recorded
reason. Missing fields stay unknown; simulated returns are prominently source-only.
There are no review launch, disposition or approval controls. Completed results
do not establish current reviewer activity, effective model identity or product proof.

**Right now** consumes the server's revision-bound plain summary. Raw questions,
gate details and findings remain inspectable separately. A confirmation prerequisite
is not an observation of an active native prompt. Arbitrary chat questions and live
reviewer counts are outside this projection. Older snapshots use qualified recorded
status wording; failed refreshes retain the existing stale-data warning.
An existing confirmation item can show **Recorded confirmation: title - answer
in chat** with a small recorded understanding excerpt. Its notice explicitly says
the chat connection and whether a prompt is still open are unknown. A stale,
consumed or missing request leaves the original prerequisite usable without that
excerpt. No answer or approval can be submitted from the cockpit.

The new `test_cockpit_server.py` checks root/query/asset/legacy routing, byte-preserved history assets, exact source JSON passthrough, denial of non-GET and unnamed paths, missing-source no-create behavior, and a real PlanStore → status snapshot → HTTP path without source mutations. Existing backend assertions are retained.

The browser function in `tests/browser/cockpit/checks.js` runs against an already available Playwright browser. Supply the preview's `baseURL` and an **external runtime** `outputDir`; it returns a structured result. It uses actual GETs for the public histories and synthetic fixture. Its explicit network interception covers optional/null/malformed/stale/live-failure contract cases; those cases are UI failure tests, not evidence of actual provider execution. Captures, check results, first-red logs, wheel outputs and candidate manifests belong outside product source.

The first regression on base `75eb64a8` failed for the missing cockpit root/assets and `/legacy` route, while read-only/status-preservation checks passed. The changed source was then checked with the prepared interpreter, affected existing Python tests, browser interactions, computed font/contrast/overflow checks, Ruff, formatting, diff checks and actual wheel asset/HTTP checks. The assigning root owns final integrated/canonical/packaging acceptance and any separately authorized publication.

Known limits / practical exit checks:

| Finding | Owner / disposition | Exit check |
| --- | --- | --- |
| Original live-wave runtime bindings and canonical whole-vision proof are absent from this slice. | Existing backend evidence owner; retained open obligation. | Original current candidate-bound evidence reaches the optional projection and the UI without synthetic or stale substitution. |
| Authenticated localhost remains unimplemented. | Existing security/runtime owner; unchanged retained obligation. | Authorized authentication work proves access control without exposing mutation controls. |
| The larger type, honest unavailable labels and area frames require vertical scrolling; some desktop crew/map content sits below the initial 900px viewport. | UI owner; nonblocking design trade-off. | Fresh-owner usability review confirms the three answers and navigation remain understandable at desktop and 375px. |
| Historical snapshots can include unmapped state vocabulary or missing work-item identity. | UI owner preserves the raw record and labels classification/identity unknown. | Producer documents an attributable classification or identity; do not infer it from a passing test or provider name. |
| Steering 1 reproduced stale-run orphan inflation, null blocker/crew fallback and run-only populations labelled Global on candidate faede0f9. | UI owner; repaired after four adapter failures and browser a/b/c failures were captured first. Existing negative checks retained. | Adapter regressions pass; browser confirms earlier-run exclusion, explicit-null precedence, matching binding and recorded-run scope. |
| Opus review 1 found repetitive unknown labels and hidden historical success/approval facts. | UI owner; consolidated coverage chip, quiet unknowns, recorded 5/5 and 8/8 checks, proxy scope/origin, past crew wording and history answers implemented. | Actual public-history interactions and 1440/375 captures remain readable; independent usability acceptance stays held. |
| Steering 2 reproduced matching old-run credit when publication freshness was absent, and undercounted validated reused history work. | UI owner; reproduced DOT's three browser reds and actual linkshelf undercount before repair. Explicit fresh publication is now required; validated reused work is classified separately from proof. | DOT's unchanged harness passes; adapter/browser regressions show five done and zero unknown on unchanged linkshelf bytes, while unvalidated reuse stays unknown. |
| Steering 3 found sentinel/numeric publication identities and contradictory/foreign explicit bindings could still gain current authority. | UI owner; five DOT matrix failures captured before the shared eligibility repair; null binding also reproduced. R2 classification and prior negative assertions retained. | DOT's corrected 29-case matrix passes against shipped normalize; current consumers share that result, while historical reused work and explicit-null precedence remain intact. |
| Steering 4 found the runtime guard erased a canonical editable-plan review blocker before execution; conflicting plan identities could also leak questions. | UI owner; DOT v2 actual SQLite/status_api check and four adapter failures captured before repair. Plan consistency and runtime eligibility now apply to their respective facts. | DOT v2 is green, mixed plan/stale-run cases retain only plan answers, explicit null/conflict negatives and all 29 freshness states pass. |
| Steering 4b found draft waves and independently bound acceptance checks still hidden by runtime ineligibility, plus optional projection overrides of canonical labels/topology. | UI owner; DOT's actual wave/scenario writer checks and browser reds captured first, then one complete consumer mapping applied. | Actual global 4/4, F1 1/1, F2 null, draft/no-current, stale/oracle/null negatives, prior plan blockers, runtime matrix and historical reuse pass. Unchanged layout suite evidence is reused. |
| Full-vision acceptance, actual worker/provider execution, fresh-user usability and Oren's personal acceptance are unperformed here. | Root / accepted evidence owners; not UI completion claims. | Separately authorized actual execution and exact-candidate review prove the accepted product promise. |

The last established outcome of this slice is the functioning read-only product surface and its bounded checks. Independent candidate review and the retained live-proof obligations remain separate from that outcome.
