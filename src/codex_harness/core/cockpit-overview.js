// Plan overview: phase, gates, next step, beads/workers and review status at a glance.
// Read-only view over the same status payload the cockpit reads; it never decides gates.
(() => {
  'use strict';
  const root = document.getElementById('overview');
  if (!root) return;

  const PENDING = {
    'approval:align': 'Waiting for your Align approval',
    'approval:vision-lock': 'Waiting for your Vision Lock approval',
    'passing-audit-required': 'Needs a passing test audit',
    'successful-review-required': 'Needs a fresh plan review',
  };

  function source() {
    const params = new URLSearchParams(location.search);
    const mode = params.get('mode') || 'live';
    if (mode === 'synthetic') return '/fixtures/synthetic.json';
    if (mode === 'history') return '/fixtures/' + (params.get('project') || 'linkshelf') + '.json';
    return '/api/status';
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  const short = (value) => (typeof value === 'string' && value ? value.slice(0, 12) : '');
  const obj = (value) => (value && typeof value === 'object' && !Array.isArray(value) ? value : {});
  const list = (value) => (Array.isArray(value) ? value : []);

  function gates(plan) {
    const approval = obj(plan.approval);
    const resolution = obj(plan.reviewResolution);
    const audit = obj(resolution.audit);
    const review = obj(resolution.review);
    const publication = obj(plan.publication);
    const acceptance = obj(plan.acceptance);
    const vision = plan.vision_lock_stale
      ? ['warn', 'Needs re-approval']
      : (approval.gate === 'vision-lock' && approval.granted) || plan.vision_locked_revision
        ? ['ok', 'Approved']
        : ['todo', 'Not yet'];
    const auditState = audit.current === true
      ? ['ok', plan.proofLevel === 'prospective' ? 'Passing · prospective' : 'Passing']
      : audit.current === false ? ['warn', 'Needed for this version'] : ['todo', 'Not run'];
    const quality = review.qualityStatus;
    const reviewState = review.current === true
      ? [quality === 'warn' || quality === 'fail' ? 'warn' : 'ok', quality === 'warn' ? 'Current · warning' : quality === 'fail' ? 'Current · failing quality' : 'Current']
      : review.current === false ? ['warn', 'Needs a fresh review'] : ['todo', 'Not yet'];
    const published = publication.up_to_date
      ? ['ok', 'Up to date · ' + short(plan.published_revision)]
      : plan.published_revision ? ['warn', 'Out of date'] : ['todo', 'Not yet'];
    const accepted = acceptance.accepted ? ['ok', 'Accepted'] : ['todo', 'Not yet'];
    return [
      ['Vision lock', ...vision],
      ['Test audit', ...auditState],
      ['Plan review', ...reviewState],
      ['Published', ...published],
      ['Product accepted', ...accepted],
    ];
  }

  function nextStep(plan) {
    if (plan.pending_gate) return PENDING[plan.pending_gate] || 'Waiting on: ' + String(plan.pending_gate).replace(/-/g, ' ');
    if (plan.open_clarifications) return plan.open_clarifications + ' open question(s) need your answer in chat';
    if (plan.open_findings) return plan.open_findings + ' review finding(s) need a response';
    const publication = obj(plan.publication);
    if (plan.phase === 'publish' && publication.up_to_date) return 'Plan published · workers can start once you approve execution in chat';
    if (plan.phase === 'execute') return 'Workers are building the published plan';
    if (plan.phase === 'accept') return obj(plan.acceptance).accepted ? 'Product accepted' : 'Check the delivered product against the locked vision';
    return 'Continue planning in chat';
  }

  function stepper(plan) {
    const phases = list(plan.phases);
    return el('ol', { class: 'ov-steps', 'aria-label': 'Plan phases' },
      phases.map((phase, index) => el('li', {
        class: 'ov-step ov-' + (phase.state || 'upcoming'),
        'aria-current': phase.state === 'current' ? 'step' : null,
        title: phase.label,
      }, el('span', { class: 'ov-dot', 'aria-hidden': 'true' }, phase.state === 'done' ? '✓' : String(index + 1)),
      el('span', { class: 'ov-step-label' }, phase.label))));
  }

  function beadRows(data, plan) {
    const beads = list(plan.spine).filter((node) => node && node.kind === 'bead');
    const runs = list(data.runs);
    const current = runs.find((run) => run && run.runId === data.currentRunId);
    const workers = new Map(list(current && current.beads).map((bead) => [bead.id, bead]));
    const readiness = obj(plan.readiness);
    const eligible = new Set(list(readiness.eligibleBeads));
    const waiting = new Map(list(readiness.pendingProducers).map((row) => [row.beadId, row.producerIds || []]));
    if (!beads.length) return el('p', { class: 'ov-muted' }, 'No beads planned yet.');
    return el('ul', { class: 'ov-beads' }, beads.map((bead) => {
      const worker = workers.get(bead.id);
      const state = worker
        ? worker.status + ' · ' + [worker.provider, worker.model].filter(Boolean).join(' / ')
        : waiting.has(bead.id) ? 'waits for ' + waiting.get(bead.id).join(', ')
          : eligible.has(bead.id) ? 'ready to start' : 'planned';
      return el('li', {},
        el('span', { class: 'ov-id' }, bead.id),
        el('span', { class: 'ov-title' }, bead.title || ''),
        el('span', { class: 'ov-state' }, state));
    }));
  }

  function reviewBlock(data, plan) {
    const rows = list(plan.roundtable);
    const reviews = obj(data.reviews);
    const count = (group) => Object.values(obj(group)).reduce((sum, items) => sum + list(items).length, 0);
    const review = obj(obj(plan.reviewResolution).review);
    const items = [];
    for (const row of rows) items.push(el('li', {}, el('strong', {}, row.reviewer + ' '), row.text || ''));
    if (!rows.length) items.push(el('li', { class: 'ov-muted' }, 'No plan review recorded yet.'));
    items.push(el('li', {}, `Findings: ${plan.open_findings || 0} open · ${count(reviews.resolved)} resolved · ${count(reviews.deferred)} deferred`));
    if ((review.qualityStatus === 'warn' || review.qualityStatus === 'fail') && review.advice) {
      items.push(el('li', { class: 'ov-warn' }, '⚠ ' + review.advice));
    }
    return el('ul', { class: 'ov-review' }, items);
  }

  function render(data) {
    const plan = obj(data && data.plan);
    root.replaceChildren();
    if (!plan.phase) {
      root.append(el('div', { class: 'ov-card' }, el('h2', {}, 'Plan overview'),
        el('p', { class: 'ov-muted' }, source() === '/api/status' ? 'No plan found for this workspace yet. Start one in chat.' : 'This recorded source has no plan facts to summarise.')));
      return;
    }
    const phases = list(plan.phases);
    const index = phases.findIndex((phase) => phase.state === 'current');
    const label = index >= 0 ? phases[index].label : plan.phase;
    root.append(el('div', { class: 'ov-card' },
      el('div', { class: 'ov-head' },
        el('div', {},
          el('p', { class: 'ov-eyebrow' }, 'Plan overview'),
          el('h2', {}, plan.name || 'Untitled plan'),
          el('p', { class: 'ov-muted' }, `${label} · step ${index >= 0 ? index + 1 : '?'} of ${phases.length || 11}`)),
        el('div', { class: 'ov-next', role: 'status' }, el('span', {}, 'Next'), el('strong', {}, nextStep(plan)))),
      stepper(plan),
      el('ul', { class: 'ov-gates', 'aria-label': 'Gates' }, gates(plan).map(([name, tone, text]) =>
        el('li', { class: 'ov-gate ov-' + tone },
          el('span', { class: 'ov-gate-mark', 'aria-hidden': 'true' }, tone === 'ok' ? '✓' : tone === 'warn' ? '!' : '○'),
          el('span', {}, el('span', { class: 'ov-gate-name' }, name), el('span', { class: 'ov-gate-text' }, text))))),
      el('div', { class: 'ov-grid' },
        el('section', { 'aria-label': 'Beads and workers' }, el('h3', {}, 'Beads & workers'), beadRows(data, plan)),
        el('section', { 'aria-label': 'Review status' }, el('h3', {}, 'Review'), reviewBlock(data, plan)))));
  }

  let busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(source(), { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      render(await response.json());
    } catch (error) {
      root.replaceChildren(el('div', { class: 'ov-card' }, el('p', { class: 'ov-muted' }, 'Plan overview unavailable: ' + error.message)));
    } finally {
      busy = false;
    }
  }

  refresh();
  setInterval(refresh, 5000);
  document.getElementById('source')?.addEventListener('change', () => setTimeout(refresh, 50));
})();
