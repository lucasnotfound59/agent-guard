'use strict';
// agent-guard dashboard. Ported from FTC Knowledge Bank's rule dashboard: the launch token lives only in
// this page's memory, every value from the log is rendered as text (never as HTML), and the page polls a
// cheap version endpoint while it is visible.
(() => {
  // Read the token from the address bar once, then remove it so it isn't left in history or screenshots.
  const takeToken = () => {
    const found = new URLSearchParams(location.hash.slice(1)).get('token') || '';
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    return found;
  };
  let token = takeToken();

  // ---------------------------------------------------------------- helpers
  const $ = id => document.getElementById(id);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const append = (node, ...children) => { children.flat(Infinity).filter(Boolean).forEach(c => node.append(c)); return node; };
  const fill = (node, ...children) => { node.replaceChildren(); return append(node, ...children); };
  const list = v => Array.isArray(v) ? v : [];
  const badge = (text, tone) => el('span', 'badge ' + (tone || ''), text);
  const fmtNum = n => Number(n || 0).toLocaleString();
  const toDate = v => typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
  const fmtTime = v => {
    if (v === null || v === undefined || v === '') return '—';
    const d = toDate(v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false});
  };
  const ago = v => {
    if (v === null || v === undefined || v === '') return 'never';
    const s = (Date.now() - toDate(v).getTime()) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return Math.floor(s / 86400) + ' d ago';
  };
  const store = {
    get(key, fallback) { try { return localStorage.getItem('agent-guard:' + key) || fallback; } catch (_e) { return fallback; } },
    set(key, value) { try { localStorage.setItem('agent-guard:' + key, value); } catch (_e) { /* private window: keep the default */ } }
  };

  const EVENT_LABELS = {
    finding: 'Warning', suppressed: 'Kept on purpose', session_start: 'Session started', stop_summary: 'Unresolved at turn end',
    proposal: 'Rule proposed', approved: 'Rule approved', rejected: 'Rule rejected', hook_error: 'Hook error',
    git_hook_error: 'Git hook error', doctor: 'Doctor issues', doctor_error: 'Doctor error', project_check: 'Project check failed',
    toolbox: 'Toolbox change'
  };
  const TOOLBOX_ACTIONS = {
    'mcp.add': 'Added MCP server', 'mcp.copy': 'Copied MCP server', 'mcp.remove': 'Removed MCP server',
    'mcp.on': 'Switched MCP server on', 'mcp.off': 'Switched MCP server off', 'plugin.on': 'Switched plugin on',
    'plugin.off': 'Switched plugin off', 'plugin.remove': 'Removed plugin', 'skill.on': 'Switched skill on',
    'skill.off': 'Switched skill off', 'skill.remove': 'Removed skill', 'skill.share': 'Shared skill',
    'skill.save': 'Edited SKILL.md', 'agents_md.save': 'Edited AGENTS.md'
  };
  const KIND_LABELS = {
    'added-line-forbidden': 'forbidden line', 'added-line-required': 'required line', 'path-forbidden': 'forbidden path',
    'command-forbidden': 'command', 'commit-message': 'commit message', 'diff-size': 'diff size'
  };
  const eventLabel = e => EVENT_LABELS[e] || e;
  const eventTone = rec => {
    const e = rec.event;
    if (e === 'finding') return rec.severity === 'hard' ? 'danger' : 'warning';
    if (['hook_error', 'git_hook_error', 'doctor_error', 'project_check', 'rejected'].includes(e)) return 'danger';
    if (e === 'approved') return 'success';
    if (['proposal', 'stop_summary', 'doctor'].includes(e)) return 'warning';
    if (e === 'suppressed' || e === 'toolbox') return 'info';
    return '';
  };
  const where = rec => rec.path ? rec.path + (rec.line ? ':' + rec.line : '') : '';
  const eventTitle = rec => {
    switch (rec.event) {
      case 'finding': case 'suppressed': case 'proposal': case 'approved': case 'rejected': return rec.rule || '—';
      case 'session_start': return (rec.agent || '?') + ' session' + (rec.repoName ? ' in ' + rec.repoName : '');
      case 'stop_summary': return (rec.outstanding || 0) + ' unresolved: ' + list(rec.rules).join(', ');
      case 'doctor': return list(rec.issues)[0] || 'doctor issues';
      case 'project_check': return String(rec.detail || '').trim().split('\n')[0];
      case 'toolbox': return (TOOLBOX_ACTIONS[rec.action] || rec.action) + ' · ' + (rec.target || '');
      default: return rec.error || rec.event;
    }
  };
  const eventMeta = rec => [rec.event === 'toolbox' ? (list(rec.agents).join(', ') || rec.agent) : rec.agent,
    rec.repoName, where(rec), rec.kind, rec.event === 'toolbox' && rec.by ? 'started by ' + rec.by : null].filter(Boolean).join(' · ');
  const severityTone = s => s === 'hard' ? 'danger' : s === 'soft' ? 'warning' : '';
  const statusTone = s => s === 'active' ? 'success' : s === 'candidate' ? 'warning' : s === 'rejected' ? 'danger' : 'outline';
  const statusLabel = s => s === 'candidate' ? 'proposed' : (s || 'unreadable');

  // ---------------------------------------------------------------- state
  const VIEWS = ['overview', 'rules', 'activity', 'proposals', 'check', 'toolbox', 'health'];
  const state = {
    view: 'overview', window: store.get('window', '7d'), version: null, loaded: {},
    overview: null, rules: null, rulesRepo: '', ruleFilters: {q: '', status: '', severity: '', kind: '', origin: '', here: ''}, selectedRule: null,
    activity: null, activityFilters: {}, activityQ: '', activityOffset: 0, selectedEvent: null,
    proposals: null, dryRuns: {}, health: null, repos: [], online: false, pollBusy: false, requests: {},
    toolbox: null, toolTab: store.get('tool-tab', 'mcp'), selectedServer: null, selectedSkill: null, skillQ: '', agentsMd: null, skillEdit: null
  };
  if (!['24h', '7d', '30d', 'all'].includes(state.window)) state.window = '7d';

  let toastTimer;
  function toast(message, error = false) {
    const t = $('toast');
    t.textContent = message; t.classList.toggle('error', error); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, error ? 7000 : 3500);
  }
  function errorNotice(error, prefix) {
    return append(el('div', 'notice danger'), el('strong', '', prefix || 'Could not load this'), el('p', '', error.message || String(error)));
  }
  function copy(text) {
    navigator.clipboard.writeText(text).then(() => toast('Copied: ' + text), () => toast('Copy failed; select the text instead.', true));
  }
  function cmdRow(text) {
    const b = el('button', 'button small', 'Copy');
    b.type = 'button'; b.onclick = () => copy(text);
    return append(el('div', 'cmd-row'), el('code', '', text), b);
  }

  // ---------------------------------------------------------------- API
  async function api(path, params, body, timeoutMs = 30000) {
    if (!token) throw Object.assign(new Error('This page has no token. Open the full link that `guard dashboard` printed (it ends with #token=…).'), {code: 'token_missing'});
    const query = params ? new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v !== null && v !== undefined)).toString() : '';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch('/api/' + path + (query ? '?' + query : ''), {
        method: body === undefined ? 'GET' : 'POST', cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
        headers: {Authorization: 'Bearer ' + token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})
      });
      let data;
      try { data = await response.json(); } catch (_e) { throw new Error('The server sent something that is not JSON (HTTP ' + response.status + ').'); }
      if (data.apiVersion !== 1) throw Object.assign(new Error('This page and the server are different versions; restart `guard dashboard`.'), {code: 'unsupported_api'});
      if (!response.ok || data.ok === false) throw Object.assign(new Error(data.error?.message || 'Request failed (HTTP ' + response.status + ')'), {code: data.error?.code, status: response.status});
      connected(true);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The server took too long to answer; showing the last data.');
      if (error instanceof TypeError) { connected(false, error); throw new Error('Lost the connection to the dashboard server.'); }
      if (error.code === 'unauthorized' || error.code === 'token_missing') connected(false, error);
      throw error;
    } finally { clearTimeout(timer); }
  }

  function connected(ok, error) {
    state.online = ok;
    const c = $('connection');
    c.className = 'connection ' + (ok ? 'online' : 'offline');
    fill(c, el('i'), ok ? 'Connected' : 'Offline');
    const alert = $('connection-alert');
    if (ok) { alert.hidden = true; return; }
    alert.textContent = error && (error.code === 'token_missing' || error.code === 'unauthorized')
      ? error.message
      : 'Lost the connection to the dashboard server. Showing the last data; restart `guard dashboard` if it stopped.';
    alert.hidden = false;
  }
  function markRefreshed() { $('last-refresh').textContent = 'updated ' + new Date().toLocaleTimeString(undefined, {hour12: false}); }
  function setPending(n) {
    const b = $('proposal-count');
    b.textContent = n; b.hidden = !n;
    b.setAttribute('aria-label', n + ' proposals waiting');
  }

  // Only the newest response for a slot may render (a slow old one must not overwrite a newer one).
  async function latest(slot, task) {
    const ticket = (state.requests[slot] || 0) + 1;
    state.requests[slot] = ticket;
    const result = await task();
    return state.requests[slot] === ticket ? result : undefined;
  }

  // ---------------------------------------------------------------- navigation
  function switchView(view) {
    if (!VIEWS.includes(view)) view = 'overview';
    state.view = view;
    VIEWS.forEach(v => { $('view-' + v).hidden = v !== view; });
    document.querySelectorAll('#navigation .nav-button').forEach(b => {
      const on = b.dataset.view === view;
      b.classList.toggle('active', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    store.set('view', view);
    window.scrollTo(0, 0);
    loadView(view);
  }
  function openRule(id) { state.selectedRule = id; state.ruleFilters.q = ''; $('rule-search').value = ''; switchView('rules'); }
  function openActivity(filters, q = '', selected = null) {
    state.activityFilters = filters; state.activityQ = q; state.activityOffset = 0; state.selectedEvent = selected;
    $('activity-search').value = q;
    state.loaded.activity = null;
    switchView('activity');
  }

  async function loadView(view, force = false) {
    if (!force && state.loaded[view] && state.loaded[view] === state.version + '|' + state.window) return;
    const loaders = {overview: loadOverview, rules: loadRules, activity: loadActivity, proposals: loadProposals, check: loadCheck, toolbox: loadToolbox, health: loadHealth};
    try {
      await loaders[view]();
      state.loaded[view] = state.version + '|' + state.window;
    } catch (error) {
      if (state.view === view) toast(error.message, true);
    }
  }

  async function poll(force = false) {
    if (state.pollBusy) return;
    state.pollBusy = true;
    try {
      const v = await api('version');
      setPending(v.pending);
      $('log-path').textContent = v.logPath + ' · ' + fmtNum(v.logRecords) + ' records';
      const changed = v.version !== state.version;
      state.version = v.version;
      if (changed || force) await loadView(state.view, true);
      markRefreshed();
    } catch (error) {
      if (!state.online) connected(false, error);
    } finally { state.pollBusy = false; }
  }

  // ---------------------------------------------------------------- rank bars
  function rankRows(container, rows, opts = {}) {
    const max = Math.max(1, ...rows.map(r => r.count));
    if (!rows.length) return fill(container, el('p', 'muted small', opts.empty || 'Nothing in this window.'));
    return fill(container, rows.map(r => {
      const row = el('div', 'rank-row' + (r.onClick ? ' link' : ''));
      const label = el('span', 'rank-label', r.label);
      if (r.note) label.append(el('small', '', r.note));
      label.title = r.title || r.label;
      const bar = el('span', 'rank-bar');
      const fillBar = el('i', r.tone || '');
      fillBar.style.width = Math.max(2, Math.round(r.count / max * 100)) + '%';
      bar.append(fillBar);
      append(row, label, bar, el('span', 'rank-count', fmtNum(r.count)));
      if (r.onClick) {
        row.tabIndex = 0; row.setAttribute('role', 'button'); row.onclick = r.onClick;
        row.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); r.onClick(); } };
      }
      return row;
    }));
  }
  function eventRow(rec, onClick) {
    const b = el('button', 'call-row');
    b.type = 'button';
    append(b, el('span', 'call-meta call-time', fmtTime(rec.ts)),
      append(el('span'), el('div', 'call-title', eventTitle(rec)), el('div', 'call-meta', eventMeta(rec))),
      badge(rec.event === 'finding' ? rec.severity || 'warning' : eventLabel(rec.event), eventTone(rec)));
    b.onclick = onClick;
    return b;
  }

  // ---------------------------------------------------------------- overview
  async function loadOverview() {
    const d = await latest('overview', () => api('overview', {window: state.window}));
    if (!d) return;
    state.overview = d;
    setPending(d.rules.candidates);
    renderOverview(d);
  }
  function renderOverview(d) {
    const s = d.summary;
    const alerts = [];
    if (s.errors) {
      const n = append(el('div', 'notice danger'), el('strong', '', s.errors + ' agent-guard error(s) in this window. '), el('span', '', 'Last: ' + (s.last_error || '?') + ' '));
      const b = el('button', 'text-button', 'Show errors →'); b.onclick = () => openActivity({event: s.last_error_event || 'hook_error'});
      alerts.push(append(n, b));
    }
    if (d.rules.invalid) {
      const n = append(el('div', 'notice danger'), el('span', '', d.rules.invalid + ' rule file(s) are invalid and are being skipped. '));
      const b = el('button', 'text-button', 'See rules →'); b.onclick = () => { state.ruleFilters.status = ''; switchView('rules'); };
      alerts.push(append(n, b));
    }
    if (d.rules.candidates) {
      const n = append(el('div', 'notice warning'), el('span', '', d.rules.candidates + ' rule proposal(s) are waiting for you. '));
      const b = el('button', 'text-button', 'Review them →'); b.onclick = () => switchView('proposals');
      alerts.push(append(n, b));
    }
    fill($('overview-alerts'), alerts);

    const stat = (value, label, tone) => append(el('div', 'stat'), el('div', 'stat-value ' + (value && tone ? tone : ''), fmtNum(value)), el('span', 'stat-label', label));
    fill($('overview-stats'),
      stat(s.warnings, 'Warnings'), stat(s.hard, 'Hard (almost always wrong)', 'danger'), stat(s.kept, 'Kept on purpose'),
      stat(s.sessions, 'Agent sessions'), stat(s.unresolved_at_stop, 'Turns ended with warnings left', 'warning'));

    const rows = d.series.rows;
    const highest = Math.max(0, ...rows.map(r => r.hard + r.soft));
    const peak = Math.max(1, highest);
    const unitLabel = {hour: 'hour', day: 'day', week: 'week'}[d.series.unit];
    $('series-note').textContent = 'Per ' + unitLabel + ' · busiest ' + fmtNum(highest);
    const label = r => toDate(r.start).toLocaleString(undefined, d.series.unit === 'hour' ? {hour: '2-digit', minute: '2-digit', hour12: false} : {month: 'short', day: 'numeric'});
    const series = fill($('series'), rows.map(r => {
      const col = el('div', 'series-col');
      const total = r.hard + r.soft;
      col.title = label(r) + ': ' + r.hard + ' hard, ' + r.soft + ' soft';
      const seg = (n, cls) => { const x = el('div', 'seg ' + cls); x.style.height = (n / peak * 100) + '%'; return n ? x : null; };
      append(col, seg(r.soft, 'soft'), seg(r.hard, 'hard'), total ? null : el('div', 'base'));
      return col;
    }));
    series.setAttribute('role', 'img');
    series.setAttribute('aria-label', 'Warnings per ' + unitLabel + ': ' + rows.map(r => label(r) + ' ' + (r.hard + r.soft)).join(', '));
    if (rows.length) fill($('series-axis'), el('span', '', label(rows[0])), el('span', '', label(rows[Math.floor(rows.length / 2)])), el('span', '', label(rows[rows.length - 1])));

    rankRows($('top-rules'), s.by_rule.slice(0, 8).map(r => ({label: r.key, count: r.count, tone: r.severity, onClick: () => openRule(r.key)})), {empty: 'Nothing flagged in this window.'});
    rankRows($('top-agents'), s.by_agent.slice(0, 8).map(r => ({label: r.key, count: r.count, onClick: () => openActivity({event: 'finding', agent: r.key})})));
    const repoRows = s.by_repo.slice(0, 8);
    const dupes = new Set(repoRows.map(r => r.label).filter((l, i, all) => all.indexOf(l) !== i));
    const parent = key => String(key || '').split('/').slice(-2, -1)[0] || '';
    rankRows($('top-repos'), repoRows.map(r => ({label: r.label, note: dupes.has(r.label) ? 'in ' + parent(r.key) : '', title: r.key || '(no repo)', count: r.count, onClick: r.key ? () => openActivity({event: 'finding', repo: r.key}) : null})));
    rankRows($('habits'), s.habits.map(h => ({label: h.agent + ' · ' + h.rule, count: h.count, onClick: () => openActivity({event: 'finding', agent: h.agent, rule: h.rule})})), {empty: 'No repeated habits in this window.'});
    const recent = list(d.recent);
    fill($('recent'), recent.length ? recent.map(rec => eventRow(rec, () => openActivity({}, '', rec.id))) : el('p', 'empty-state', 'The log is empty for this window.'));
  }

  // ---------------------------------------------------------------- rules
  async function loadRules() {
    const d = await latest('rules', () => api('rules', {window: state.window, repo: state.rulesRepo}));
    if (!d) return;
    state.rules = d;
    state.repos = list(d.repos);
    renderRules();
  }
  function repoOptions(select, current, first) {
    const opts = [el('option', '', first)];
    opts[0].value = '';
    state.repos.forEach(r => { const o = el('option', '', r.label); o.value = r.path; opts.push(o); });
    if (current && !state.repos.some(r => r.path === current)) { const o = el('option', '', current); o.value = current; opts.push(o); }
    fill(select, opts);
    select.value = current || '';
  }
  function selectFilter(label, key, values, current, onChange) {
    const select = el('select');
    const all = el('option', '', 'All'); all.value = '';
    select.append(all);
    values.forEach(([value, text]) => { const o = el('option', '', text); o.value = value; select.append(o); });
    select.value = current || '';
    select.onchange = () => onChange(key, select.value);
    return append(el('label', '', label), select);
  }
  function ruleMatches(r, f) {
    if (f.status && r.status !== f.status) return false;
    if (f.severity && r.severity !== f.severity) return false;
    if (f.kind && r.check?.kind !== f.kind) return false;
    if (f.origin && r.origin !== f.origin) return false;
    if (f.here && r.here !== f.here) return false;
    if (!f.q) return true;
    const hay = [r.id, r.title, r.why, r.fix, JSON.stringify(r.check || {})].join(' ').toLowerCase();
    return hay.includes(f.q.toLowerCase());
  }
  function renderRules() {
    const d = state.rules;
    if (!d) return;
    repoOptions($('rules-repo'), state.rulesRepo, 'Global rules only');
    const o = d.overlay;
    if (o) {
      const box = el('div', 'notice' + (o.problems.length ? ' danger' : ''));
      append(box, el('strong', '', o.isRepo ? 'Overlay for ' + o.label : o.label + ' is not inside a git repository, so only global rules apply.'));
      if (o.isRepo) {
        const ul = el('ul');
        ul.append(el('li', '', o.disabled.length ? 'Disabled here: ' + o.disabled.map(x => x.id + ' (' + x.reason + ')').join('; ') : 'No global rules disabled here.'));
        if (o.excludePaths.length) ul.append(el('li', '', 'Contents not checked: ' + o.excludePaths.join(', ')));
        if (o.checks.length) ul.append(el('li', '', 'Commit-time checks: ' + o.checks.join(', ')));
        o.problems.forEach(p => ul.append(el('li', '', p)));
        box.append(ul);
      }
      fill($('rules-overlay'), box);
    } else fill($('rules-overlay'));

    const rules = list(d.rules);
    const uniq = key => [...new Set(rules.map(key).filter(Boolean))].sort();
    const f = state.ruleFilters;
    const setFilter = (key, value) => { f[key] = value; renderRules(); };
    const filters = [
      selectFilter('Status', 'status', uniq(r => r.status).map(s => [s, statusLabel(s)]), f.status, setFilter),
      selectFilter('Severity', 'severity', uniq(r => r.severity).map(s => [s, s]), f.severity, setFilter),
      selectFilter('Check', 'kind', uniq(r => r.check?.kind).map(k => [k, KIND_LABELS[k] || k]), f.kind, setFilter),
      selectFilter('Origin', 'origin', uniq(r => r.origin).map(s => [s, s]), f.origin, setFilter)
    ];
    if (o && o.isRepo) filters.push(selectFilter('In this repo', 'here', [['on', 'applies'], ['disabled', 'disabled'], ['off', 'not active']], f.here, setFilter));
    fill($('rule-filters'), filters);

    const order = {active: 0, candidate: 1, retired: 2, rejected: 3};
    const shown = rules.filter(r => ruleMatches(r, f))
      .sort((a, b) => (order[a.status] ?? 4) - (order[b.status] ?? 4) || b.stats.hits - a.stats.hits || a.id.localeCompare(b.id));
    $('rule-count').textContent = shown.length + ' of ' + rules.length + ' rules';
    if (state.selectedRule && !rules.some(r => r.id === state.selectedRule)) state.selectedRule = null;
    fill($('rule-list'), shown.length ? shown.map(r => {
      const b = el('button', 'list-item' + (r.id === state.selectedRule ? ' selected' : ''));
      b.type = 'button';
      const badges = append(el('div', 'badge-row'), badge(statusLabel(r.status), statusTone(r.status)), badge(r.severity, severityTone(r.severity)), badge(KIND_LABELS[r.check?.kind] || r.check?.kind || '?', 'outline'));
      if (r.origin === 'project') badges.append(badge('project rule', 'info'));
      if (r.here === 'disabled') badges.append(badge('disabled here', 'danger'));
      if (r.here === 'off') badges.append(badge('not active here', 'outline'));
      if (r.errors.length) badges.append(badge('invalid', 'danger'));
      append(b, append(el('div', 'item-id'), el('span', '', r.id), el('span', '', r.stats.hits ? fmtNum(r.stats.hits) + ' caught' : '')), el('h3', '', r.title || '(unreadable file)'), badges);
      b.onclick = () => { state.selectedRule = r.id; renderRules(); if (innerWidth < 860) $('rule-detail').scrollIntoView({behavior: 'smooth'}); };
      return b;
    }) : el('div', 'empty-state', 'No rules match these filters.'));
    renderRuleDetail(rules.find(r => r.id === state.selectedRule));
  }
  function section(title, ...children) { return append(el('section', 'field-section'), el('h3', '', title), children); }
  function prop(label, value) { return append(el('div', 'property'), el('span', 'label', label), el('span', 'value', value === undefined || value === null || value === '' ? '—' : value)); }
  function renderRuleDetail(r) {
    const box = $('rule-detail');
    if (!r) return fill(box, append(el('div', 'empty-state'), el('span', 'empty-symbol', '≡'), el('h2', '', 'Pick a rule'), el('p', '', 'See why it exists, what to do instead, and what it caught.')));
    const head = append(el('div', 'detail-header'), el('div', 'item-id', r.id), el('h2', '', r.title || '(unreadable file)'),
      append(el('div', 'badge-row'), badge(statusLabel(r.status), statusTone(r.status)), badge(r.severity, severityTone(r.severity)), badge(KIND_LABELS[r.check?.kind] || r.check?.kind || '?', 'outline'), r.origin === 'project' ? badge('project rule', 'info') : null));
    const body = el('div', 'detail-body');
    if (r.errors.length) body.append(append(el('div', 'notice danger'), el('strong', '', 'Invalid, so it is skipped everywhere:'), append(el('ul'), r.errors.map(e => el('li', '', e)))));
    if (r.status === 'candidate') body.append(append(el('div', 'notice warning'), el('strong', '', 'Proposed, not active. Approve or reject it in your own terminal:'), cmdRow('guard review'), cmdRow('guard approve ' + r.id)));
    if (r.why) body.append(section('Why', el('p', 'prose', r.why)));
    if (r.fix) body.append(section('Fix', el('p', 'prose', r.fix)));
    const c = r.check || {};
    const checkLines = [];
    const pats = c.patterns || (c.pattern ? [c.pattern] : []);
    pats.forEach(p => checkLines.push('match   ' + p));
    list(c.allow_patterns).forEach(p => checkLines.push('unless  ' + p));
    list(c.allow).forEach(p => checkLines.push('allow   ' + p));
    list(c.paths).forEach(p => checkLines.push('path    ' + p));
    if (c.max_files) checkLines.push('max files        ' + c.max_files);
    if (c.max_added_lines) checkLines.push('max added lines  ' + c.max_added_lines);
    ['ignore_case', 'multiline'].forEach(k => { if (c[k]) checkLines.push(k); });
    if (c.scan === false) checkLines.push('not reported by guard scan');
    if (checkLines.length) body.append(section('Check · ' + (KIND_LABELS[c.kind] || c.kind || '?'), el('pre', 'code', checkLines.join('\n'))));
    body.append(section('Files', append(el('div', 'property-grid'), prop('Applies to', list(r.applies_to).join(', ')), prop('Except', list(r.exclude).join(', ')), prop('Rule file', r.file))));
    const s = r.stats;
    const actions = el('div', 'badge-row');
    if (s.hits) { const b = el('button', 'button small', 'Show in activity'); b.onclick = () => openActivity({rule: r.id}); actions.append(b); }
    body.append(section('In this window', append(el('div', 'property-grid'), prop('Caught', fmtNum(s.hits)), prop('Kept on purpose', fmtNum(s.kept)), prop('Last caught', s.last ? ago(s.last) : 'not in this window'),
      prop('Agents', s.agents.map(a => a.key + ' ' + a.count).join(', ')), prop('Repos', s.repos.map(a => a.key + ' ' + a.count).join(', '))), actions));
    if (list(s.recent).length) body.append(section('Latest catches', s.recent.map(h => append(el('div', 'hit ' + (h.severity || '')),
      append(el('div', 'hit-head'), el('span', '', [h.agent, h.repoName, where(h)].filter(Boolean).join(' · ')), el('span', '', fmtTime(h.ts))), h.snippet ? el('code', '', h.snippet) : null))));
    const src = r.source || {};
    const srcProps = Object.entries(src).filter(([k]) => k !== 'evidence').map(([k, v]) => prop(k.replace(/_/g, ' '), String(v)));
    if (src.evidence || srcProps.length) body.append(section('Source', src.evidence ? append(el('div', 'evidence'), el('span', '', src.evidence)) : null, srcProps.length ? append(el('div', 'property-grid'), srcProps) : null));
    fill(box, head, body);
  }

  // ---------------------------------------------------------------- activity
  async function loadActivity() {
    const params = {window: state.window, offset: state.activityOffset, limit: 50, q: state.activityQ, ...state.activityFilters};
    const d = await latest('activity', () => api('activity', params));
    if (!d) return;
    state.activity = d;
    renderActivity();
  }
  function renderActivity() {
    const d = state.activity;
    if (!d) return;
    const f = state.activityFilters;
    const change = (key, value) => {
      if (value) f[key] = value; else delete f[key];
      state.activityOffset = 0; state.selectedEvent = null; loadActivity().catch(e => toast(e.message, true));
    };
    const facet = (label, key, fmt) => {
      const values = list(d.facets[key]).map(x => [x.key, (fmt ? fmt(x) : x.label) + ' (' + x.count + ')']);
      if (f[key] && !values.some(([v]) => v === f[key])) values.unshift([f[key], f[key] + ' (0)']);
      return selectFilter(label, key, values, f[key], change);
    };
    fill($('activity-filters'), facet('Event', 'event', x => eventLabel(x.key)), facet('Agent', 'agent'), facet('Rule', 'rule'), facet('Repo', 'repo'), facet('Severity', 'severity'), facet('Kind', 'kind'));
    $('activity-total').textContent = fmtNum(d.total) + ' event(s) match';
    const from = d.total ? d.offset + 1 : 0, to = d.offset + d.items.length;
    $('activity-page').textContent = from + '–' + to + ' of ' + fmtNum(d.total);
    $('activity-prev').disabled = d.offset === 0;
    $('activity-next').disabled = to >= d.total;
    const items = list(d.items);
    fill($('activity-list'), items.length ? items.map(rec => {
      const b = el('button', 'list-item' + (rec.id === state.selectedEvent ? ' selected' : ''));
      b.type = 'button';
      append(b, append(el('div', 'item-id'), el('span', '', fmtTime(rec.ts)), badge(rec.event === 'finding' ? rec.severity || 'warning' : eventLabel(rec.event), eventTone(rec))),
        el('h3', '', eventTitle(rec)), el('div', 'call-meta', eventMeta(rec)));
      b.onclick = () => { state.selectedEvent = rec.id; renderActivity(); if (innerWidth < 860) $('activity-detail').scrollIntoView({behavior: 'smooth'}); };
      return b;
    }) : el('div', 'empty-state', 'No events match.'));
    renderEventDetail(items.find(r => r.id === state.selectedEvent));
  }
  function renderEventDetail(rec) {
    const box = $('activity-detail');
    if (!rec) return fill(box, append(el('div', 'empty-state'), el('span', 'empty-symbol', '↳'), el('h2', '', 'Pick an event'), el('p', '', 'See every field that was logged.')));
    const head = append(el('div', 'detail-header'), el('div', 'item-id', eventLabel(rec.event) + ' · ' + fmtTime(rec.ts)), el('h2', '', eventTitle(rec)),
      append(el('div', 'badge-row'), rec.severity ? badge(rec.severity, severityTone(rec.severity)) : null, rec.kind ? badge(rec.kind, 'outline') : null, rec.agent ? badge(rec.agent, 'info') : null));
    const body = el('div', 'detail-body');
    if (rec.snippet) body.append(section('Line', el('pre', 'code', rec.snippet)));
    if (rec.evidence) body.append(section('Evidence', append(el('div', 'evidence'), el('span', '', rec.evidence))));
    if (rec.error) body.append(section('Error', el('pre', 'code', rec.error)));
    if (rec.detail) body.append(section('Detail', el('pre', 'code', rec.detail)));
    const skip = new Set(['id', 'snippet', 'evidence', 'error', 'detail', 'repoName']);
    const props = Object.entries(rec).filter(([k]) => !skip.has(k)).map(([k, v]) => prop(k, typeof v === 'object' ? JSON.stringify(v) : String(v)));
    body.append(section('Fields', append(el('div', 'property-grid'), props)));
    const actions = el('div', 'badge-row');
    if (rec.rule) { const b = el('button', 'button small', 'Open rule ' + rec.rule); b.onclick = () => openRule(rec.rule); actions.append(b); }
    if (rec.session) { const b = el('button', 'button small', 'This session only'); b.onclick = () => openActivity({}, rec.session); actions.append(b); }
    if (actions.childElementCount) body.append(section('Go to', actions));
    fill(box, head, body);
  }

  // ---------------------------------------------------------------- proposals
  async function loadProposals() {
    const d = await latest('proposals', () => api('proposals', {window: state.window}));
    if (!d) return;
    state.proposals = d;
    setPending(list(d.candidates).length);
    renderProposals();
  }
  function renderProposals() {
    const d = state.proposals;
    const cands = list(d.candidates);
    fill($('proposal-list'), cands.length ? cands.map(proposalCard) : append(el('section', 'panel'), append(el('div', 'empty-state'), el('span', 'empty-symbol', '✎'), el('h2', '', 'No proposals waiting'),
      el('p', '', 'When you correct an agent\'s habit, it proposes a rule with `guard propose`, and it shows up here.'))));
    const decided = list(d.decided);
    fill($('decided'), decided.length ? decided.map(rec => eventRow(rec, () => openRule(rec.rule))) : el('p', 'empty-state', 'Nothing approved or rejected in this window.'));
  }
  function proposalCard(r) {
    const card = el('section', 'panel proposal-card');
    const src = r.source || {};
    append(card, append(el('div', 'proposal-top'),
      append(el('div'), el('div', 'eyebrow', r.id), el('h2', '', r.title || '(unreadable file)'),
        append(el('div', 'badge-row'), badge(r.severity, severityTone(r.severity)), badge(KIND_LABELS[r.check?.kind] || r.check?.kind || '?', 'outline'), r.errors.length ? badge('invalid', 'danger') : badge('valid', 'success'))),
      el('div', 'muted small', 'Proposed by ' + (src.proposed_by || '?') + (src.proposed_at ? ' on ' + src.proposed_at : ''))));
    if (src.evidence) card.append(append(el('div', 'evidence'), el('span', '', src.evidence), el('small', '', 'Evidence the agent gave, in its words')));
    if (r.errors.length) card.append(append(el('div', 'notice danger'), el('strong', '', 'This proposal can\'t be approved as written:'), append(el('ul'), r.errors.map(e => el('li', '', e)))));
    card.append(append(el('div', 'two-col'), section('Why', el('p', 'prose', r.why || '—')), section('Fix', el('p', 'prose', r.fix || '—'))));
    const c = r.check || {};
    const pats = c.patterns || (c.pattern ? [c.pattern] : c.paths || []);
    if (pats.length) card.append(section('Matches · applies to ' + list(r.applies_to).join(', ') + (list(r.exclude).length ? ' except ' + r.exclude.join(', ') : ''), el('pre', 'code', pats.join('\n'))));
    const dry = el('div', 'dry-run');
    const known = state.dryRuns[r.id];
    if (known) renderDryRun(dry, known);
    const runBtn = el('button', 'button', known ? 'Dry-run again' : 'Dry-run against my repos');
    runBtn.type = 'button';
    runBtn.disabled = r.errors.length > 0;
    runBtn.onclick = async () => {
      runBtn.disabled = true; runBtn.textContent = 'Scanning your repos…';
      try {
        const res = await api('dry-run', null, {id: r.id}, 120000);
        state.dryRuns[r.id] = res;
        renderDryRun(dry, res);
        runBtn.textContent = 'Dry-run again';
      } catch (e) { fill(dry, errorNotice(e, 'Dry-run failed')); runBtn.textContent = 'Dry-run against my repos'; }
      finally { runBtn.disabled = false; }
    };
    card.append(section('Before you decide', el('p', 'muted small', 'Rules only warn on new lines, but a dry-run shows how much existing code already looks like this, so you can tell whether it is too broad.'), runBtn, dry));
    card.append(section('Decide in your terminal', cmdRow('guard review'), cmdRow('guard approve ' + r.id), cmdRow('guard reject ' + r.id + ' --reason "…"')));
    return card;
  }
  function renderDryRun(node, res) {
    const hits = list(res.hits);
    fill(node, hits.length
      ? append(el('div', 'notice warning'), el('strong', '', 'Already matches existing code in ' + hits.length + ' repo(s): '), el('span', '', hits.slice(0, 12).map(h => h.repo + ' (' + h.count + ')').join(', ')), el('p', 'muted small', 'Scanned in ' + res.seconds + ' s.'))
      : append(el('div', 'notice success'), el('strong', '', 'No existing matches in your repos. '), el('span', 'muted small', 'Scanned in ' + res.seconds + ' s.')));
  }

  // ---------------------------------------------------------------- check
  const CHECK_TEXT = {
    working: {label: '', hint: 'Reads the repo\'s uncommitted changes with git; changes nothing.'},
    staged: {label: '', hint: 'What the commit hook would check right now.'},
    diff: {label: 'Unified diff', hint: 'Paste the output of git diff or a patch.', placeholder: 'diff --git a/app.ts b/app.ts\n--- a/app.ts\n+++ b/app.ts\n@@ -1 +1,2 @@\n+const x: any = 1;'},
    command: {label: 'Shell command', hint: 'Checked like an agent\'s shell command.', placeholder: 'git push --force origin main'},
    message: {label: 'Commit message', hint: 'Checked like the commit-msg hook.', placeholder: 'fix(api): handle empty responses'}
  };
  async function loadCheck() {
    if (!state.repos.length) {
      const d = await api('rules', {window: state.window});
      state.repos = list(d.repos);
    }
    const select = $('check-repo');
    const current = select.value || store.get('check-repo', '');
    repoOptions(select, state.repos.some(r => r.path === current) ? current : '', 'No repo: global rules only');
    updateCheckMode();
  }
  function checkMode() { return document.querySelector('input[name="check-mode"]:checked').value; }
  function updateCheckMode() {
    const mode = checkMode(), spec = CHECK_TEXT[mode];
    $('check-text-block').hidden = !spec.label;
    $('check-text-label').textContent = spec.label;
    $('check-text').placeholder = spec.placeholder || '';
    $('check-text').style.minHeight = mode === 'diff' ? '260px' : '90px';
    $('check-hint').textContent = spec.hint;
  }
  async function runCheck(event) {
    event.preventDefault();
    const button = event.submitter || $('check-form').querySelector('button[type=submit]');
    const body = {mode: checkMode(), repo: $('check-repo').value || null, text: $('check-text').value};
    store.set('check-repo', body.repo || '');
    button.disabled = true;
    const out = $('check-result');
    out.classList.add('busy');
    try {
      const d = await api('check', null, body, 90000);
      fill(out, checkResult(d));
    } catch (e) { fill(out, errorNotice(e, 'Check failed')); }
    finally { button.disabled = false; out.classList.remove('busy'); }
  }
  function findingRow(f) {
    const ruleBtn = el('button', 'text-button rule-link', f.rule);
    ruleBtn.type = 'button'; ruleBtn.onclick = () => openRule(f.rule);
    const detail = append(el('div'), append(el('div'), ruleBtn, el('span', 'muted small', '  ' + f.title)),
      f.where ? el('p', 'mono', f.where) : null, f.snippet ? el('code', '', f.snippet) : null,
      f.suppressed ? append(el('p'), el('b', '', 'Kept: '), f.suppressed) : null,
      append(el('p'), el('b', '', 'Why: '), f.why), append(el('p'), el('b', '', 'Fix: '), f.fix));
    return append(el('div', 'finding'), append(el('div'), badge(f.severity, severityTone(f.severity))), detail);
  }
  function checkResult(d) {
    const panel = el('section', 'panel result-panel');
    const hard = d.findings.filter(f => f.severity === 'hard').length;
    append(panel, append(el('div', 'result-heading'), el('h2', '', d.findings.length ? d.findings.length + ' warning(s)' : 'No warnings'),
      append(el('div', 'badge-row'), hard ? badge(hard + ' hard', 'danger') : null, d.findings.length - hard ? badge((d.findings.length - hard) + ' soft', 'warning') : null, d.kept.length ? badge(d.kept.length + ' kept', 'info') : null)));
    const facts = [d.rules + ' rules', d.rootLabel ? 'repo ' + d.rootLabel : 'global rules only'];
    if (d.files !== null && d.files !== undefined) facts.push(d.files + ' changed file(s)');
    facts.push('checked ' + fmtTime(d.checkedAt));
    panel.append(el('p', 'muted small', facts.join(' · ')));
    if (d.disabled.length) panel.append(el('p', 'muted small', 'Disabled in this repo: ' + d.disabled.map(x => x.id + ' (' + x.reason + ')').join('; ')));
    if (d.problems.length) panel.append(append(el('div', 'notice danger'), el('strong', '', 'Problems in .agent-guard.yaml:'), append(el('ul'), d.problems.map(p => el('li', '', p)))));
    if (!d.findings.length && !d.kept.length) panel.append(el('div', 'notice success', d.files === 0 ? 'There were no changes to check.' : 'Nothing flagged. Pattern rules catch text, not intent, so this is not a review.'));
    d.findings.forEach(f => panel.append(findingRow(f)));
    if (d.kept.length) panel.append(section('Kept on purpose (agent-guard: allow)', d.kept.map(findingRow)));
    return panel;
  }

  // ---------------------------------------------------------------- health
  async function loadHealth(refresh = false) {
    const box = $('doctor-results');
    if (!state.health) fill(box, el('p', 'empty-state', 'Running guard doctor… (it walks your scan_roots, so this takes a few seconds)'));
    const d = await latest('health', () => api('health', {window: state.window, refresh: refresh ? '1' : ''}, undefined, 120000));
    if (!d) return;
    state.health = d;
    renderHealth();
  }
  function renderHealth() {
    const d = state.health;
    const results = list(d.results);
    const count = lvl => results.filter(r => r.level === lvl).length;
    const chip = (lvl, label) => append(el('div', 'chip ' + lvl), el('b', '', count(lvl)), el('span', '', label));
    fill($('health-stats'), append(el('div', 'chip-row'), chip('fail', 'failing'), chip('warn', 'warnings'), chip('ok', 'ok'), chip('info', 'notes')));
    $('health-checked').textContent = 'Checked ' + ago(d.checkedAt) + ' (' + fmtTime(d.checkedAt) + ')';
    const table = el('table');
    const thead = append(el('thead'), append(el('tr'), ['Agent', 'Installed', 'Hooks', 'Last event', 'Sessions', 'Warnings'].map(h => el('th', '', h))));
    const tbody = el('tbody');
    list(d.agents).forEach(a => {
      const hooks = {wired: badge('wired', 'success'), missing: badge('missing', 'danger'), none: badge('no hook support', 'outline'), 'n/a': badge('—', 'outline')}[a.hooks] || badge(a.hooks, 'outline');
      const lastCell = el('td', '', a.last ? ago(a.last) : 'never');
      if (a.last) lastCell.title = fmtTime(a.last);
      else if (a.installed && a.hooks === 'wired') lastCell.append(' ', badge('check its hooks run', 'warning'));
      append(tbody, append(el('tr'), append(el('td'), el('strong', '', a.title), a.title !== a.name ? el('div', 'muted small mono', a.name) : null),
        el('td', '', a.installed === null ? '—' : a.installed ? 'yes' : 'no'), append(el('td'), hooks), lastCell,
        el('td', 'num', fmtNum(a.sessions)), el('td', 'num', fmtNum(a.warnings))));
    });
    fill($('agents-table'), append(table, thead, tbody));
    const groups = [['fail', 'Failing', '✗'], ['warn', 'Warnings', '!'], ['ok', 'OK', '✓'], ['info', 'Notes', '·']];
    fill($('doctor-results'), groups.filter(([lvl]) => count(lvl)).map(([lvl, title, icon]) => append(el('div', 'doc-group'), el('h3', '', title),
      results.filter(r => r.level === lvl).map(r => append(el('div', 'doc-row'), el('span', 'doc-icon ' + lvl, icon), el('span', '', r.message))))));
  }

  // ---------------------------------------------------------------- toolbox
  async function loadToolbox() {
    const d = await latest('toolbox', () => api('toolbox'));
    if (!d) return;
    state.toolbox = d;
    renderToolbox();
    if (state.toolTab === 'agentsmd') await loadAgentsMd();
  }
  async function act(body, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return null;
    try {
      const r = await api('manage', null, {...body, versions: state.toolbox ? state.toolbox.versions : {}}, 60000);
      toast(r.done + (r.backup ? ' · backup: ' + r.backup : ''));
      await loadToolbox();
      return r;
    } catch (e) {
      toast(e.message, true);
      if (e.status === 409) await loadToolbox();
      return null;
    }
  }
  function stateChip(label, cls, onClick, title) {
    const chip = el(onClick ? 'button' : 'span', 'state ' + cls, label);
    if (title) chip.title = title;
    if (onClick) { chip.type = 'button'; chip.onclick = e => { e.stopPropagation(); onClick(); }; }
    return chip;
  }
  function switchTab(tab) {
    state.toolTab = tab;
    store.set('tool-tab', tab);
    document.querySelectorAll('#view-toolbox .tab').forEach(b => { const on = b.dataset.tab === tab; b.classList.toggle('active', on); b.setAttribute('aria-selected', on); });
    ['mcp', 'skills', 'plugins', 'agentsmd'].forEach(t => { $('tab-' + t).hidden = t !== tab; });
    if (tab === 'agentsmd') loadAgentsMd().catch(e => toast(e.message, true));
  }
  function renderToolbox() {
    const t = state.toolbox;
    $('count-mcp').textContent = t.mcp.servers.length;
    $('count-skills').textContent = t.skills.skills.length;
    $('count-plugins').textContent = t.plugins.plugins.length;
    renderMcp(); renderSkills(); renderPlugins();
  }

  function renderMcp() {
    const m = state.toolbox.mcp;
    fill($('mcp-errors'), m.agents.filter(a => a.error).map(a => append(el('div', 'notice danger'), el('strong', '', a.title + ': '), a.error)));
    if (!m.agents.length) { fill($('mcp-matrix'), el('p', 'empty-state', 'None of the agents agent-guard knows about with MCP support is installed.')); return renderMcpDetail(); }
    if (!m.servers.length) fill($('mcp-matrix'), el('p', 'empty-state', 'No MCP servers yet. Add one and pick the agents that should get it.'));
    else {
      const table = el('table', 'matrix');
      table.append(append(el('thead'), append(el('tr'), el('th', '', 'Server'), m.agents.map(a => { const th = el('th', 'agent', a.title); th.title = a.path; return th; }))));
      const body = el('tbody');
      m.servers.forEach(srv => {
        const tr = el('tr', 'row' + (srv.name === state.selectedServer ? ' selected' : ''));
        tr.tabIndex = 0;
        const name = append(el('td', 'name', srv.name), srv.differs ? append(el('span'), ' ', badge('differs', 'warning')) : null);
        append(tr, name, m.agents.map(a => {
          const st = srv.agents[a.name];
          const td = el('td', 'cell');
          if (!st) td.append(stateChip('—', 'absent', null, a.title + ' doesn\'t have it'));
          else td.append(stateChip(st, st, () => act({action: 'mcp.toggle', name: srv.name, agent: a.name, on: st !== 'on'}), 'Switch ' + (st === 'on' ? 'off' : 'on') + ' for ' + a.title));
          return td;
        }));
        const pick = () => { state.selectedServer = srv.name; renderMcp(); $('mcp-detail').scrollIntoView({behavior: 'smooth', block: 'nearest'}); };
        tr.onclick = pick; tr.onkeydown = e => { if (e.key === 'Enter') pick(); };
        body.append(tr);
      });
      fill($('mcp-matrix'), append(table, body));
    }
    renderMcpDetail();
  }
  function serverLines(s) {
    const lines = [];
    if (s.transport === 'stdio') {
      lines.push('run   ' + [s.command, ...s.args].join(' '));
      if (s.cwd) lines.push('in    ' + s.cwd);
      if (s.env.length) lines.push('env   ' + s.env.join(', ') + '  (values hidden)');
    } else {
      lines.push(s.transport + '  ' + s.url);
      if (s.headers.length) lines.push('headers ' + s.headers.join(', ') + '  (values hidden)');
    }
    return lines.join('\n');
  }
  function renderMcpDetail() {
    const box = $('mcp-detail');
    const m = state.toolbox.mcp;
    const srv = m.servers.find(s => s.name === state.selectedServer);
    if (!srv) { box.hidden = true; return; }
    box.hidden = false;
    const have = m.agents.filter(a => srv.agents[a.name]);
    const lack = m.agents.filter(a => !srv.agents[a.name]);
    const head = append(el('div', 'detail-header'), el('div', 'item-id', 'MCP server'), el('h2', '', srv.name),
      append(el('div', 'badge-row'), badge(srv.server.transport, 'info'), srv.differs ? badge('set up differently per agent', 'warning') : null));
    const body = el('div', 'detail-body');
    if (srv.differs) body.append(section('Definition per agent', Object.entries(srv.definedBy).map(([agent, def]) => append(el('div'), el('p', 'muted small', titleOf(agent)), el('pre', 'code', serverLines(def))))));
    else body.append(section('Definition', el('pre', 'code', serverLines(srv.server))));
    body.append(section('Agents that have it', have.map(a => {
      const st = srv.agents[a.name];
      const toggle = el('button', 'button small', st === 'on' ? 'Switch off' : 'Switch on');
      toggle.onclick = () => act({action: 'mcp.toggle', name: srv.name, agent: a.name, on: st !== 'on'});
      const remove = el('button', 'button small', 'Remove');
      remove.onclick = () => act({action: 'mcp.remove', name: srv.name, agent: a.name}, 'Remove ' + srv.name + ' from ' + a.title + '? Its config file is backed up first.');
      return append(el('div', 'agent-row'), append(el('span'), el('strong', '', a.title), el('div', 'muted small mono', a.path)), stateChip(st, st), append(el('span', 'badge-row'), toggle, remove));
    })));
    if (lack.length) {
      const picks = append(el('div', 'check-list'), lack.map(a => { const c = el('input'); c.type = 'checkbox'; c.value = a.name; return append(el('label'), c, a.title); }));
      const source = el('select');
      have.forEach(a => { const o = el('option', '', a.title); o.value = a.name; source.append(o); });
      const go = el('button', 'button primary small', 'Copy to the ticked agents');
      go.type = 'button';
      go.onclick = () => {
        const agents = [...picks.querySelectorAll('input:checked')].map(c => c.value);
        if (!agents.length) return toast('Tick at least one agent.', true);
        act({action: 'mcp.copy', name: srv.name, source: source.value, agents});
      };
      body.append(section('Copy to other agents', picks, append(el('div', 'form-footer'), append(el('label', 'window-picker'), 'from ', source), go),
        el('p', 'muted small', 'The definition, including secret values, is copied on the server and converted to each agent\'s format. Settings only one agent understands (timeouts, trust) are left out.')));
    }
    fill(box, head, body);
  }
  function titleOf(agent) {
    const all = [...(state.toolbox.mcp.agents || []), ...(state.toolbox.skills.agents || [])];
    return (all.find(a => a.name === agent) || {}).title || state.toolbox.plugins.titles[agent] || agent;
  }
  function openMcpAdd() {
    const m = state.toolbox.mcp;
    if (!m.agents.length) return toast('No installed agent with MCP support.', true);
    $('mcp-form').reset();
    fill($('mcp-form-error'));
    fill($('mcp-agents'), m.agents.map(a => { const c = el('input'); c.type = 'checkbox'; c.value = a.name; return append(el('label'), c, a.title); }));
    updateMcpTransport();
    $('mcp-dialog').showModal();
  }
  function updateMcpTransport() {
    const stdio = $('mcp-transport').value === 'stdio';
    $('mcp-stdio-fields').hidden = !stdio; $('mcp-http-fields').hidden = stdio;
  }
  const lines = v => v.split('\n').map(x => x.trim()).filter(Boolean);
  function pairs(v, sep) {
    const out = {};
    lines(v).forEach(line => { const i = line.indexOf(sep); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); });
    return out;
  }
  async function submitMcpAdd(e) {
    e.preventDefault();
    const agents = [...$('mcp-agents').querySelectorAll('input:checked')].map(c => c.value);
    const body = {action: 'mcp.add', name: $('mcp-name').value.trim(), agents, versions: state.toolbox.versions, server: {
      transport: $('mcp-transport').value, command: $('mcp-command').value.trim(), args: lines($('mcp-args').value),
      env: pairs($('mcp-env').value, '='), cwd: $('mcp-cwd').value.trim(), url: $('mcp-url').value.trim(), headers: pairs($('mcp-headers').value, ':')}};
    try {
      const r = await api('manage', null, body, 60000);
      $('mcp-dialog').close();
      toast(r.done + ' · backup: ' + r.backup);
      state.selectedServer = body.name;
      await loadToolbox();
    } catch (err) { fill($('mcp-form-error'), errorNotice(err, 'Not added')); if (err.status === 409) await loadToolbox(); }
  }

  function renderSkills() {
    const sk = state.toolbox.skills;
    const q = (state.skillQ || '').toLowerCase();
    const shown = sk.skills.filter(s => !q || (s.name + ' ' + s.description).toLowerCase().includes(q));
    $('skill-count').textContent = shown.length + ' of ' + sk.skills.length + ' skills';
    $('skill-pool').textContent = 'shared folder: ' + sk.pool;
    const table = el('table', 'matrix');
    table.append(append(el('thead'), append(el('tr'), el('th', '', 'Skill'), el('th', '', 'What it does'),
      sk.agents.map(a => { const th = el('th', 'agent', a.title); th.title = a.mode === 'native' ? 'reads the shared folder itself' : 'gets one link per skill'; return th; }))));
    const body = el('tbody');
    shown.forEach(s => {
      const tr = el('tr', 'row' + (s.name === state.selectedSkill ? ' selected' : ''));
      tr.tabIndex = 0;
      append(tr, append(el('td', 'name', s.name), s.locked ? append(el('span'), ' ', badge('required', 'info')) : null, s.parked ? append(el('span'), ' ', badge('off', 'warning')) : null),
        el('td', 'desc', s.description.length > 110 ? s.description.slice(0, 109) + '…' : s.description),
        sk.agents.map(a => {
          const st = s.agents[a.name];
          const td = el('td', 'cell');
          const canSwitch = a.mode === 'link' && !s.parked && ['on', 'off', 'missing'].includes(st) && !(s.locked && st === 'on');
          const tip = {pool: 'reads the shared folder', excluded: 'left out by its adapter (built in there)', 'off-all': 'switched off for every agent', missing: 'link missing: click to add it'}[st];
          td.append(stateChip(st, st, canSwitch ? () => act({action: 'skill.toggle', name: s.name, agent: a.name, on: st !== 'on'}) : null, tip || (canSwitch ? 'Switch ' + (st === 'on' ? 'off' : 'on') + ' for ' + a.title : '')));
          return td;
        }));
      const pick = () => { state.selectedSkill = s.name; renderSkills(); $('skill-detail').scrollIntoView({behavior: 'smooth', block: 'nearest'}); };
      tr.onclick = pick; tr.onkeydown = e => { if (e.key === 'Enter') pick(); };
      body.append(tr);
    });
    fill($('skill-matrix'), shown.length ? append(table, body) : el('p', 'empty-state', 'No skills match.'));
    renderSkillDetail();
    const un = sk.unshared;
    fill($('skill-unshared'), un.length ? un.map(u => {
      const share = el('button', 'button small primary', 'Share with every agent');
      share.disabled = u.inPool; share.title = u.inPool ? 'the shared folder already has a skill with this name' : '';
      share.onclick = () => act({action: 'skill.share', name: u.name, agent: u.agent});
      const edit = el('button', 'button small', 'Edit'); edit.onclick = () => openSkillEditor(u.name, u.agent);
      const remove = el('button', 'button small', 'Remove');
      remove.onclick = () => act({action: 'skill.remove', name: u.name, agent: u.agent}, 'Remove ' + u.name + ' from ' + titleOf(u.agent) + '? The folder is backed up first.');
      return append(el('div', 'plugin-row'), append(el('span'), el('strong', 'mono', u.name), el('div', 'muted small', titleOf(u.agent) + ' · ' + u.path)), el('span', 'muted small', u.description.slice(0, 80)), share, append(el('span', 'badge-row'), edit, remove));
    }) : el('p', 'empty-state', 'Every skill is shared. Nothing is stuck in one agent.'));
  }
  function renderSkillDetail() {
    const box = $('skill-detail');
    const s = state.toolbox.skills.skills.find(x => x.name === state.selectedSkill);
    if (!s) { box.hidden = true; return; }
    box.hidden = false;
    const head = append(el('div', 'detail-header'), el('div', 'item-id', s.path), el('h2', '', s.name),
      append(el('div', 'badge-row'), badge(s.kind === 'own' ? 'your skill (repo skills/)' : 'shared', 'info'), s.parked ? badge('off for every agent', 'warning') : badge('on', 'success'), s.locked ? badge('required by agent-guard', 'outline') : null));
    const body = el('div', 'detail-body');
    if (s.description) body.append(section('Description', el('p', 'prose', s.description)));
    const actions = el('div', 'badge-row');
    const edit = el('button', 'button small', 'Edit SKILL.md'); edit.onclick = () => openSkillEditor(s.name, null); actions.append(edit);
    if (!s.locked) {
      const all = el('button', 'button small', s.parked ? 'Switch on for every agent' : 'Switch off for every agent');
      all.onclick = () => act({action: 'skill.toggle', name: s.name, on: !!s.parked});
      const remove = el('button', 'button small', 'Remove');
      remove.onclick = () => act({action: 'skill.remove', name: s.name}, 'Remove ' + s.name + ' for every agent?' + (s.kind === 'own' ? ' This deletes its folder in the repo\'s skills/ (git can bring it back).' : '') + ' A backup is kept.');
      actions.append(all, remove);
    }
    body.append(section('Change', actions));
    fill(box, head, body);
  }
  async function openSkillEditor(name, agent) {
    try {
      const d = await api('text', {kind: 'skill', name, agent: agent || ''});
      state.skillEdit = {name, agent, version: d.version};
      $('skill-dialog-title').textContent = name + (agent ? ' (' + titleOf(agent) + ' only)' : '');
      $('skill-dialog-path').textContent = d.path;
      $('skill-text').value = d.text;
      $('skill-dialog-status').textContent = d.lines + ' lines';
      fill($('skill-form-error'));
      $('skill-dialog').showModal();
    } catch (e) { toast(e.message, true); }
  }
  async function saveSkill(e) {
    e.preventDefault();
    const se = state.skillEdit;
    try {
      const r = await api('manage', null, {action: 'text.save', kind: 'skill', name: se.name, agent: se.agent || null, text: $('skill-text').value, base: se.version});
      $('skill-dialog').close();
      toast('Saved ' + r.saved + ' · backup: ' + r.backup);
      await loadToolbox();
    } catch (err) { fill($('skill-form-error'), errorNotice(err, 'Not saved')); }
  }

  function renderPlugins() {
    const p = state.toolbox.plugins;
    fill($('plugin-errors'), Object.entries(p.errors).map(([a, msg]) => append(el('div', 'notice danger'), el('strong', '', titleOf(a) + ': '), msg)));
    const groups = {};
    p.plugins.forEach(x => (groups[x.agent] = groups[x.agent] || []).push(x));
    const notes = {codex: 'From [plugins] in ~/.codex/config.toml.', claude: 'From enabledPlugins in ~/.claude/settings.json.',
      opencode: 'npm plugins from opencode.json, and files in ~/.config/opencode/plugins (switched off by renaming to .off).', gemini: 'Extensions in ~/.gemini/extensions (shown only; manage them with `gemini extensions`).'};
    const names = Object.keys(groups);
    fill($('plugin-groups'), names.length ? names.map(agent => append(el('section', 'panel'),
      append(el('div', 'panel-heading'), append(el('div'), el('h2', '', titleOf(agent)), el('p', 'muted small', notes[agent] || ''))),
      groups[agent].map(x => {
        const st = x.enabled === null ? 'n/a' : x.enabled ? 'on' : 'off';
        const toggle = x.canToggle ? stateChip(st, st, () => act({action: 'plugin.toggle', agent, id: x.id, on: !x.enabled}), 'Switch ' + (x.enabled ? 'off' : 'on')) : stateChip(st, x.enabled === null ? 'pool' : st);
        const remove = el('button', 'button small', 'Remove');
        remove.hidden = !x.canRemove;
        remove.onclick = () => act({action: 'plugin.remove', agent, id: x.id}, 'Remove ' + x.id + ' from ' + titleOf(agent) + '? A backup is kept.');
        return append(el('div', 'plugin-row'), el('span', 'mono', x.id), x.locked ? badge('agent-guard\'s own', 'info') : badge(x.kind, 'outline'), toggle, remove);
      }))) : append(el('section', 'panel'), el('p', 'empty-state', 'No plugins found for the installed agents.')));
  }

  async function loadAgentsMd(force = false) {
    if (state.agentsMd && state.agentsMd.dirty && !force) return;
    const d = await api('text', {kind: 'agents_md'});
    state.agentsMd = {version: d.version, dirty: false, info: d};
    $('agentsmd-text').value = d.text;
    showAgentsMd(d);
  }
  function showAgentsMd(d) {
    $('agentsmd-info').textContent = d.path + (d.linkedFrom.length ? ' · linked into ' + d.linkedFrom.join(', ') : '');
    const lines = $('agentsmd-text').value.split('\n').length - ($('agentsmd-text').value.endsWith('\n') ? 1 : 0);
    const b = $('agentsmd-lines');
    b.textContent = lines + ' lines'; b.className = 'badge ' + (lines > 80 ? 'warning' : 'success');
    b.title = 'Every line costs context in every session of every agent; the repo asks for under ~80.';
    $('agentsmd-save').disabled = !state.agentsMd.dirty;
    $('agentsmd-status').textContent = state.agentsMd.dirty ? 'Unsaved changes' : 'Saved version ' + state.agentsMd.version;
  }
  async function saveAgentsMd(e) {
    e.preventDefault();
    const btn = $('agentsmd-save');
    btn.disabled = true;
    try {
      const r = await api('manage', null, {action: 'text.save', kind: 'agents_md', text: $('agentsmd-text').value, base: state.agentsMd.version});
      state.agentsMd = {version: r.version, dirty: false, info: r};
      showAgentsMd(r);
      toast('Saved AGENTS.md for every agent · backup: ' + r.backup);
    } catch (err) {
      toast(err.message, true);
      $('agentsmd-status').textContent = err.status === 409 ? 'Someone else changed it. Copy your text somewhere, press Reload, and redo the change.' : err.message;
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- wiring
  function bind() {
    document.querySelectorAll('#navigation .nav-button').forEach(b => { b.onclick = () => switchView(b.dataset.view); });
    document.querySelectorAll('[data-go]').forEach(b => { b.onclick = () => switchView(b.dataset.go); });
    document.querySelector('.brand').onclick = e => { e.preventDefault(); switchView('overview'); };
    $('refresh-all').onclick = () => poll(true);
    const win = $('window');
    win.value = state.window;
    win.onchange = () => { state.window = win.value; store.set('window', win.value); state.activityOffset = 0; loadView(state.view, true); };
    $('rules-repo').onchange = e => { state.rulesRepo = e.target.value; state.ruleFilters.here = ''; loadRules().catch(err => toast(err.message, true)); };
    let searchTimer;
    $('rule-search').oninput = e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.ruleFilters.q = e.target.value.trim(); renderRules(); }, 120); };
    $('clear-filters').onclick = () => { state.ruleFilters = {q: '', status: '', severity: '', kind: '', origin: '', here: ''}; $('rule-search').value = ''; renderRules(); };
    let activityTimer;
    $('activity-search').oninput = e => {
      clearTimeout(activityTimer);
      activityTimer = setTimeout(() => { state.activityQ = e.target.value.trim(); state.activityOffset = 0; loadActivity().catch(err => toast(err.message, true)); }, 250);
    };
    $('activity-clear').onclick = () => { $('activity-search').value = ''; openActivity({}); };
    $('activity-prev').onclick = () => { state.activityOffset = Math.max(0, state.activityOffset - 50); loadActivity().catch(err => toast(err.message, true)); };
    $('activity-next').onclick = () => { state.activityOffset += 50; loadActivity().catch(err => toast(err.message, true)); };
    document.querySelectorAll('input[name="check-mode"]').forEach(r => { r.onchange = updateCheckMode; });
    $('check-form').onsubmit = runCheck;
    $('health-refresh').onclick = async e => {
      const b = e.currentTarget;
      b.disabled = true; b.textContent = 'Running doctor…';
      try { await loadHealth(true); state.loaded.health = state.version + '|' + state.window; } catch (err) { toast(err.message, true); }
      finally { b.disabled = false; b.textContent = 'Run doctor again'; }
    };
    document.querySelectorAll('#view-toolbox .tab').forEach(b => { b.onclick = () => switchTab(b.dataset.tab); });
    if (!['mcp', 'skills', 'plugins', 'agentsmd'].includes(state.toolTab)) state.toolTab = 'mcp';
    switchTab(state.toolTab);
    $('mcp-add-open').onclick = openMcpAdd;
    $('mcp-transport').onchange = updateMcpTransport;
    $('mcp-form').onsubmit = submitMcpAdd;
    $('skill-form').onsubmit = saveSkill;
    document.querySelectorAll('dialog [data-close]').forEach(b => { b.onclick = () => b.closest('dialog').close(); });
    let skillTimer;
    $('skill-search').oninput = e => { clearTimeout(skillTimer); skillTimer = setTimeout(() => { state.skillQ = e.target.value.trim(); if (state.toolbox) renderSkills(); }, 120); };
    $('agentsmd-text').oninput = () => { if (state.agentsMd) { state.agentsMd.dirty = true; showAgentsMd(state.agentsMd.info); } };
    $('agentsmd-form').onsubmit = saveAgentsMd;
    $('agentsmd-reload').onclick = () => {
      if (state.agentsMd && state.agentsMd.dirty && !window.confirm('Discard your unsaved changes to AGENTS.md?')) return;
      loadAgentsMd(true).catch(err => toast(err.message, true));
    };
    window.addEventListener('beforeunload', e => { if (state.agentsMd && state.agentsMd.dirty) { e.preventDefault(); e.returnValue = ''; } });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
    // A restarted `guard dashboard` prints a new link; opening it in this tab only changes the #fragment.
    window.addEventListener('hashchange', () => {
      const fresh = takeToken();
      if (fresh && fresh !== token) { token = fresh; state.loaded = {}; poll(true); }
    });
  }

  bind();
  if (!token) connected(false, {code: 'token_missing', message: 'This page has no token. Open the full link that `guard dashboard` printed (it ends with #token=…).'});
  const startView = VIEWS.includes(store.get('view', '')) ? store.get('view', '') : 'overview';
  state.view = startView;
  poll().then(() => switchView(startView));
  setInterval(() => { if (!document.hidden) poll(); }, 3000);
})();
