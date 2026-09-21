(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const ACTIVE = new Set(['snapshotting', 'queued', 'building']);
  const LABELS = {snapshotting:'Saving build copy',queued:'Waiting to build',building:'Building',succeeded:'Ready to download',failed:'Build failed',cancelled:'Cancelled'};
  const state = {token:'',catalog:null,selected:null,job:null,busy:false,refreshing:false,timer:null,log:'',actionError:'',operation:''};
  const node = (tag, className, text) => { const result = document.createElement(tag); if (className) result.className = className; if (text != null) result.textContent = text; return result; };
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':state.token},body:JSON.stringify(body)});
    let data; try { data = await response.json(); } catch { throw new Error('The local builder did not respond. Check that the editor is running, then refresh.'); }
    if (!response.ok) throw new Error(data.error || data.message || `Could not contact the builder (${response.status}).`);
    return data;
  }
  function error(message) { $('#page-error').textContent = message || ''; $('#page-error').hidden = !message; }
  function date(value) { if (!value) return '—'; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString([], {dateStyle:'medium',timeStyle:'short'}); }
  function size(value) { return Number(value) > 0 ? `${(Number(value) / 1048576).toFixed(1)} MB` : ''; }
  function downloadUrl(job) {
    if (job?.status !== 'succeeded' || !job.download_url) return null;
    const url = new URL(job.download_url, location.origin);
    return url.origin === location.origin && url.pathname === '/api/build/download' ? url.href : null;
  }
  function badge(job) { return `badge ${ACTIVE.has(job?.status) ? 'running' : job?.status === 'succeeded' ? 'succeeded' : job?.status === 'failed' ? 'failed' : ''}`; }
  function syncButton() {
    const active = ACTIVE.has(state.catalog?.active_job?.status) || ACTIVE.has(state.job?.status);
    $('#start-build').disabled = state.busy || !state.token || !state.catalog?.toolchain?.ready || active;
    $('#start-build').textContent = state.busy ? 'Starting build…' : active ? 'Build in progress…' : 'Build ROM →';
    $('#rom-name').disabled = state.busy || active;
    const canRetry = ['failed', 'cancelled'].includes(state.job?.status);
    $('#retry-section').hidden = !canRetry;
    $('#retry-build').disabled = !canRetry || state.busy || active || !state.token || !state.catalog?.toolchain?.ready;
    $('#retry-build').textContent = state.busy && state.operation === 'retry' ? 'Retrying saved copy…' : 'Retry saved copy';
  }
  function renderCatalog() {
    const toolchain = state.catalog.toolchain || {};
    $('#toolchain-status').classList.toggle('ready', !!toolchain.ready);
    $('#toolchain-message').textContent = toolchain.message || (toolchain.ready ? 'Local builder ready.' : 'The local builder is being prepared. This page will update when it is ready.');
    const root = $('#build-history'); root.replaceChildren();
    const jobs = state.catalog.history || [];
    if (!jobs.length) root.append(node('p','empty','No builds yet. Your first adventure starts above.'));
    for (const job of jobs) {
      const row = node('div','history-row'), name = node('div','history-name');
      name.append(node('strong','',job.name || 'Emerald adventure'), node('small','',date(job.created_at)));
      const actions = node('div','history-actions'), inspect = node('button','text-button','View log'); inspect.type = 'button';
      inspect.setAttribute('aria-label', `View log for ${job.name || 'build'} from ${date(job.created_at)}`);
      inspect.addEventListener('click', () => { state.actionError = ''; selectJob(job.id, true); }); actions.append(inspect);
      const url = downloadUrl(job);
      if (url) { const link = node('a','button','Download .gba ↓'); link.href = url; link.download = ''; actions.append(link); }
      row.append(name, node('span',badge(job),LABELS[job.status] || job.status), node('span','history-size',size(job.size)), actions); root.append(row);
    }
    syncButton();
  }
  function renderJob(job) {
    state.job = job; const running = ACTIVE.has(job.status), previous = state.log;
    $('#build-heading').textContent = job.name || 'Your adventure';
    $('#build-badge').className = badge(job); $('#build-badge').textContent = LABELS[job.status] || job.status;
    $('#build-message').textContent = job.message || (running ? 'Building a playable ROM from your saved game. You can leave this page and come back.' : job.status === 'succeeded' ? 'Your .gba file is ready.' : job.status === 'failed' ? 'The build stopped before producing a playable file.' : 'This build was cancelled.');
    $('#build-progress').hidden = !running;
    $('#build-progress').setAttribute('aria-valuetext', job.stage || LABELS[job.status] || 'Building');
    const facts = $('#build-facts'); facts.replaceChildren(); facts.hidden = false;
    const add = (label, value) => { if (value) facts.append(node('dt','',label), node('dd','',value)); };
    add('Saved copy', date(job.created_at));
    if (running) add('Current step', job.stage || LABELS[job.status]);
    if (job.finished_at) add('Finished', date(job.finished_at));
    add('ROM size', size(job.size));
    if (job.source_sha256) add('Source version', job.source_sha256.slice(0, 12));
    if (job.attempt_count > 1) add('Build attempt', String(job.attempt_count));
    const failed = job.status === 'failed';
    $('#build-error').hidden = !failed;
    $('#build-error').textContent = failed ? `${job.error || 'The compiler could not complete this build.'} Check the log below. After correcting compiler setup, retry the saved copy. For changes to your game, save them and start a new build.` : '';
    const download = $('#download-rom'), url = downloadUrl(job); download.hidden = !url;
    if (url) download.href = url; else download.removeAttribute('href');
    $('#playtest-note').hidden = !url;
    $('#log-details').hidden = false;
    if (failed) $('#log-details').open = true;
    $('#log-status').textContent = running ? 'Updating live' : 'Complete';
    state.log = String(job.log || '');
    if (state.log !== previous) {
      const log = $('#build-log'), atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 45;
      log.textContent = state.log || 'Waiting for build output…';
      if (atEnd) log.scrollTop = log.scrollHeight;
    } else if (!state.log) $('#build-log').textContent = 'Waiting for build output…';
    syncButton();
  }
  async function selectJob(id, focus = false) {
    state.selected = id;
    try {
      const job = await api(`/api/build/jobs?id=${encodeURIComponent(id)}`);
      if (state.selected !== id) return;
      if (!state.actionError) error(''); renderJob(job);
      if (focus) { $('#log-details').open = true; $('#build-heading').scrollIntoView({behavior:'smooth',block:'start'}); }
    } catch (issue) { error(issue.message); }
  }
  function schedule() {
    clearTimeout(state.timer);
    const active = ACTIVE.has(state.catalog?.active_job?.status) || ACTIVE.has(state.job?.status);
    state.timer = setTimeout(refresh, active ? 2200 : state.catalog?.toolchain?.ready ? 12000 : 5000);
  }
  async function refresh() {
    if (state.refreshing || state.busy) { schedule(); return; }
    state.refreshing = true; $('#refresh').disabled = true;
    try {
      if (!state.token) state.token = (await api('/api/session')).token;
      state.catalog = await api('/api/build'); if (!state.actionError) error(''); renderCatalog();
      const selected = state.selected || state.catalog.active_job?.id || state.catalog.history?.[0]?.id;
      if (selected) await selectJob(selected);
    } catch (issue) { error(issue.message); }
    finally { state.refreshing = false; $('#refresh').disabled = false; schedule(); }
  }
  async function start(event) {
    event.preventDefault(); if ($('#start-build').disabled) return;
    state.busy = true; state.operation = 'start'; state.actionError = ''; clearTimeout(state.timer); error(''); syncButton();
    try {
      const name = $('#rom-name').value.trim();
      const job = await api('/api/build/start', name ? {name} : {});
      state.selected = job.id; state.catalog.active_job = job; renderJob(job); $('#log-details').open = true;
    } catch (issue) { state.actionError = issue.message; error(issue.message); }
    finally { state.busy = false; state.operation = ''; syncButton(); await refresh(); }
  }
  async function retry() {
    if ($('#retry-build').disabled || !state.job) return;
    const id = state.job.id;
    state.busy = true; state.operation = 'retry'; state.actionError = ''; clearTimeout(state.timer); error(''); syncButton();
    try {
      const job = await api('/api/build/retry', {id});
      state.selected = job.id; state.catalog.active_job = job; renderJob(job); $('#log-details').open = true;
    } catch (issue) { state.actionError = issue.message; error(issue.message); }
    finally { state.busy = false; state.operation = ''; syncButton(); await refresh(); }
  }
  $('#build-form').addEventListener('submit', start);
  $('#retry-build').addEventListener('click', retry);
  $('#refresh').addEventListener('click', () => { state.actionError = ''; refresh(); });
  $('#copy-log').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(state.log); $('#copy-log').textContent = 'Copied'; setTimeout(() => $('#copy-log').textContent = 'Copy log', 1800); }
    catch { const range = document.createRange(); range.selectNodeContents($('#build-log')); const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); $('#copy-log').textContent = 'Selected — press Ctrl+C'; }
  });
  (async () => {
    try { state.token = (await api('/api/session')).token; await refresh(); }
    catch (issue) { error(issue.message); $('#toolchain-message').textContent = 'Could not contact the local editor. Refresh to try again.'; }
  })();
})();
