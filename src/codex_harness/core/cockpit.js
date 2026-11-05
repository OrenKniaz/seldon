"use strict";

const byId = id => document.getElementById(id);
let previous = null;
let refreshOwner = null;

function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function details(title, value) {
  const node = document.createElement("details");
  node.append(element("summary", title), element("pre", JSON.stringify(value, null, 2)));
  return node;
}

function render(report) {
  const plan = report.plan;
  for (const id of ["status", "blockers", "next", "plan", "proof", "runs", "permissions"]) {
    byId(id).replaceChildren();
  }
  if (!plan || plan === "unavailable") {
    byId("status").append(element("p", "Plan unavailable. No storage was initialized."));
  } else {
    byId("status").append(
      element("h3", plan.name || "Plan"),
      element("p", `Phase: ${plan.phase} · Revision: ${plan.plan_revision}`)
    );
    byId("blockers").append(
      element("p", `Pending gate: ${plan.pending_gate || "none"}`),
      element("p", `Open questions: ${plan.open_clarifications} · Assumptions: ${plan.open_assumptions} · Findings: ${plan.open_findings}`)
    );
    for (const question of plan.clarifications || []) {
      byId("blockers").append(element("p", `${question.id}: ${question.question}`));
    }
    byId("blockers").append(details("Coverage gaps and recorded finding blockers", {
      coverage_gaps: plan.coverage_gaps,
      assumptions: plan.assumptions,
      reviewResolution: plan.reviewResolution
    }));
    byId("next").textContent = `Next: ${plan.next}`;
    for (const node of plan.spine || []) {
      const card = document.createElement("article");
      card.append(details(`${node.id} · ${node.kind} · ${node.title}`, node));
      if (node.kind === "bead") {
        card.append(element("p", `Owner: ${node.id} · Owned files: ${(node.fields?.ownedFiles || []).join(", ") || "not recorded"}`));
      }
      byId("plan").append(card);
    }
    byId("proof").append(
      element("p", `Publication freshness: ${plan.publication?.up_to_date === true ? "current" : "unconfirmed"}`),
      element("p", plan.proofLevelLabel || "Proof unavailable"),
      details("Canonical validated receipts and acceptance", {
        plan_revision: plan.plan_revision,
        publication: plan.publication,
        proofLevel: plan.proofLevel,
        roundtable: plan.roundtable,
        acceptance: plan.acceptance
      })
    );
  }
  if (Array.isArray(report.runs)) {
    for (const run of report.runs) {
      if (!run || run === "unavailable") {
        byId("runs").append(element("p", "Run record unavailable"));
        continue;
      }
      const current = plan?.publication?.up_to_date === true &&
        typeof plan.published_revision === "string" &&
        run.planRevision === plan.published_revision;
      const card = document.createElement("article");
      card.append(element("h3", `${run.runId} · ${run.status}`));
      card.append(element("p", current ? "Matches current publication" : "Historical or freshness unconfirmed"));
      card.append(element("p", `Recorded: ${run.startedAt || "unknown"} → ${run.finishedAt || "unknown"}`));
      for (const bead of run.beads || []) {
        card.append(element("p", `${bead.id} · ${bead.provider || "unknown"} / ${bead.model || "unknown"} · ${bead.status || "unknown"}`));
      }
      card.append(details("Recorded receipt (not revalidated by this view)", run));
      byId("runs").append(card);
    }
  } else {
    byId("runs").append(element("p", "Run records unavailable"));
  }
  byId("permissions").append(element("pre", JSON.stringify(report.savedPermissions, null, 2)));
}

async function refresh(manual = false) {
  if (refreshOwner && !manual) return;
  const previousOwner = refreshOwner;
  const controller = new AbortController();
  refreshOwner = controller;
  previousOwner?.abort(new Error("Replaced by manual refresh"));
  const deadline = Date.now() + 5000;
  const timeout = setInterval(() => {
    if (Date.now() >= deadline) controller.abort(new Error("Status request exceeded 5s wall-clock deadline"));
  }, 250);
  try {
    const response = await fetch("/api/status", {cache: "no-store", signal: controller.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const report = await response.json();
    if (refreshOwner !== controller) return;
    if (!report || typeof report !== "object" || !("plan" in report) || !("runs" in report)) {
      throw new Error("Status response unavailable");
    }
    const serialized = JSON.stringify(report);
    if (previous !== serialized) render(report);
    previous = serialized;
    byId("proof-warning")?.remove();
    byId("freshness").textContent = `Last received: ${new Date().toLocaleString()}`;
  } catch (error) {
    if (refreshOwner !== controller) return;
    if (!byId("proof-warning")) {
      const warning = element("p", "Proof freshness unconfirmed: refresh failed.");
      warning.id = "proof-warning";
      byId("proof").prepend(warning);
    }
    byId("freshness").textContent = previous === null ?
      `Status unavailable: ${error.message}` :
      `Stale snapshot — refresh failed; proof freshness is unconfirmed: ${error.message}`;
  } finally {
    clearInterval(timeout);
    if (refreshOwner === controller) refreshOwner = null;
  }
}

byId("refresh").addEventListener("click", () => refresh(true));
refresh();
setInterval(refresh, 5000);
