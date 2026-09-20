(() => {
  'use strict';

  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const state = {
    view: 'overview', overview: null, token: '', dirty: false, busy: false,
    file: null, record: null, recordKind: 'pokemon', records: [], fields: [],
    fileListRequest: 0, fileRequest: 0, recordListRequest: 0, recordRequest: 0,
  };
  const viewNames = { overview: 'Overview', pokemon: 'Pokémon', moves: 'Moves', files: 'Source explorer', changes: 'My changes' };
  let toastTimer;
  let fileSearchTimer;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function number(value) { return value == null ? '—' : Number(value).toLocaleString(); }
  function size(value) {
    const bytes = Number(value || 0);
    if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(bytes % 1048576 ? 1 : 0)} MiB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${bytes} bytes`;
  }
  function assetUrl(path) {
    return String(path || '').startsWith('/asset?') ? path : `/asset?path=${encodeURIComponent(path || '')}`;
  }
  function labelId(value) { return String(value).replace(/^SPECIES_|^MOVE_/, '').replaceAll('_', ' '); }
  function setLoading(container, message = 'Loading…') {
    container.replaceChildren(el('div', 'row-loading loading-block', message));
  }
  function empty(container, title, description, icon = '◈') {
    const wrap = el('div', 'empty-state');
    wrap.append(el('span', 'empty-icon', icon), el('h2', '', title), el('p', '', description));
    container.replaceChildren(wrap);
  }
  function errorPanel(container, message, retry) {
    empty(container, 'Something needs another try', message, '↻');
    if (retry) {
      const button = el('button', 'button secondary retry-button', 'Try again');
      button.addEventListener('click', retry);
      $('.empty-state', container).append(button);
    }
  }
  function toast(message, error = false) {
    const node = $('#toast');
    clearTimeout(toastTimer);
    node.textContent = message;
    node.classList.toggle('error', error);
    node.hidden = false;
    toastTimer = setTimeout(() => { node.hidden = true; }, error ? 7000 : 4200);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json', 'X-Workbench-Token': state.token } : {}), ...options.headers },
    });
    let data;
    try { data = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
    if (!response.ok) {
      const detail = typeof data.error === 'string' ? data.error : data.error?.message || data.message;
      const err = new Error(detail || `Request failed (${response.status}).`);
      err.status = response.status;
      throw err;
    }
    return data;
  }
  function confirmAction(title, message, action, cancel = 'Keep editing') {
    const dialog = $('#confirm-dialog');
    if (dialog.open) return Promise.resolve(false);
    $('#confirm-title').textContent = title;
    $('#confirm-description').textContent = message;
    $('#confirm-action').textContent = action;
    $('button[value="cancel"]', dialog).textContent = cancel;
    dialog.returnValue = 'cancel';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
      dialog.showModal();
    });
  }
  async function canLeave() {
    if (state.busy) { toast('Let this save finish before switching files.'); return false; }
    if (state.dirty && !await confirmAction('Keep your unsaved edits?', 'You have changes that have not been saved. You can keep editing, or discard them and continue.', 'Discard edits')) return false;
    state.dirty = false;
    return true;
  }
  function setDirty(dirty) {
    state.dirty = dirty;
    const indicator = $('.save-status', state.view === 'files' ? $('#file-detail') : $('#record-detail'));
    if (indicator) {
      indicator.classList.toggle('dirty', dirty);
      indicator.textContent = dirty ? '● Unsaved changes' : '✓ Changes saved locally';
    }
    const button = $('.save-button', state.view === 'files' ? $('#file-detail') : $('#record-detail'));
    if (button) button.disabled = state.busy || !dirty;
    document.title = `${dirty ? '• ' : ''}${viewNames[state.view]} · Emerald Workbench`;
  }
  function setBusy(busy, container) {
    state.busy = busy;
    $$('button, input, textarea', container).forEach(node => { node.disabled = busy; });
    if (!busy) {
      const save = $('.save-button', container);
      if (save) save.disabled = !state.dirty;
    }
  }
  function displaySprite(image) {
    // GBA palette index zero is transparent. Apply that only to the displayed
    // thumbnail; the original source image remains untouched.
    image.addEventListener('load', () => {
      try {
        if (!image.naturalWidth || image.naturalWidth > 512 || image.naturalHeight > 4096) return;
        const canvas = document.createElement('canvas');
        canvas.width = image.naturalWidth;
        canvas.height = Math.min(image.naturalHeight, image.naturalWidth);
        const context = canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(image, 0, 0);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
        const [r, g, b] = pixels.data;
        for (let i = 0; i < pixels.data.length; i += 4) {
          if (pixels.data[i] === r && pixels.data[i + 1] === g && pixels.data[i + 2] === b) pixels.data[i + 3] = 0;
        }
        context.putImageData(pixels, 0, 0);
        image.src = canvas.toDataURL('image/png');
      } catch { /* The original image is still a usable fallback. */ }
    }, { once: true });
    image.addEventListener('error', () => { image.hidden = true; }, { once: true });
  }
  function sprite(path, name, className) {
    const image = el('img', className);
    image.alt = name;
    image.loading = 'lazy';
    displaySprite(image);
    image.src = assetUrl(path);
    return image;
  }
  async function navigation(view, options = {}) {
    if (!Object.hasOwn(viewNames, view)) view = 'overview';
    const same = state.view === view;
    if (same && !options.path && options.category === undefined) return;
    if (!await canLeave()) return;
    state.view = view;
    state.fileRequest++;
    state.recordRequest++;
    $$('.view').forEach(node => { node.hidden = true; });
    $(`#view-${view === 'pokemon' || view === 'moves' ? 'records' : view}`).hidden = false;
    $$('.nav-item').forEach(node => {
      node.classList.toggle('active', node.dataset.view === view);
      if (node.dataset.view === view) node.setAttribute('aria-current', 'page');
      else node.removeAttribute('aria-current');
    });
    $('#breadcrumb-view').textContent = viewNames[view];
    document.title = `${viewNames[view]} · Emerald Workbench`;
    history.replaceState(null, '', `#${view}`);
    if (view === 'files') {
      if (options.category !== undefined) $('#file-category').value = options.category;
      if (options.path) {
        $('#file-search').value = '';
        $('#file-category').value = '';
      }
      loadFiles();
      if (options.path) loadFile(options.path, true);
      else if (options.category !== undefined) {
        state.file = null;
        renderCategoryIntro(options.category);
      } else if (state.file) loadFile(state.file.path, true);
    } else if (view === 'pokemon' || view === 'moves') {
      loadRecords(view);
    } else if (view === 'changes') loadChanges();
    if (!same) window.scrollTo({ top: 0, behavior: 'auto' });
  }
  function renderOverview(data) {
    state.overview = data;
    const rom = data.rom || {};
    const card = $('#rom-card');
    card.classList.remove('loading-block');
    const copy = el('div', 'rom-copy');
    copy.append(el('h2', 'rom-title', 'Pokémon Emerald · USA / Europe'));
    const pieces = [rom.game_code, rom.size ? size(rom.size) : null, rom.sha1 ? `SHA-1 ${rom.sha1.slice(0, 12)}…` : null].filter(Boolean);
    const metadata = el('p', 'rom-meta', pieces.join('  /  '));
    if (rom.sha1) metadata.title = `SHA-1: ${rom.sha1}`;
    copy.append(metadata);
    const verified = rom.matches === true;
    const pill = el('span', `verified-pill${verified ? '' : ' unverified'}`, verified ? '✓  Original ROM verified' : 'ⓘ  Check ROM identification');
    card.replaceChildren(el('span', 'cartridge-icon', '◇'), copy, pill);
    const counts = data.counts || {};
    $('#stats-grid').replaceChildren(...[
      [counts.files, 'Source files to explore'], [counts.pokemon, 'Pokémon species'],
      [counts.moves, 'Battle moves'],
      [counts.maps, 'Maps in the world'],
    ].map(([value, label]) => {
      const stat = el('div', 'stat');
      stat.append(el('div', 'stat-value', number(value)), el('div', 'stat-label', label));
      return stat;
    }));
    const icons = { pokemon: '◉', moves: 'ϟ', trainers: '♙', items: '◇', maps: '▦', story: '☷', graphics: '▧', audio: '♫', engine: '⚙', build: '⌘' };
    $('#category-grid').replaceChildren(...(data.categories || []).map(category => {
      const card = el('button', 'category-card');
      const top = el('div', 'category-card-top');
      top.append(el('span', 'category-icon', icons[category.id] || '▤'), el('span', 'category-arrow', '↗'));
      const foot = el('div', 'category-card-footer');
      const labels = { maps: 'Maps + layouts', graphics: 'Art assets', audio: 'Sound assets', build: 'Build setup' };
      foot.append(el('span', '', `${number(category.count)} files`), el('span', 'category-tag', labels[category.id] || (category.editable ? 'Editable source' : 'Explore source')));
      card.append(top, el('h3', '', category.title), el('p', '', category.description), foot);
      card.addEventListener('click', () => navigation(category.id === 'pokemon' || category.id === 'moves' ? category.id : 'files', { category: category.id }));
      return card;
    }));
    $('#file-category').replaceChildren(el('option', '', 'All systems'), ...(data.categories || []).map(category => {
      const option = el('option', '', category.title); option.value = category.id; return option;
    }));
    $('#file-category').firstElementChild.value = '';
    const build = $('#build-note');
    build.replaceChildren(document.createTextNode(data.build?.note || 'Edits are saved to source. Compiling a new ROM is not configured in this workbench.'));
    if (data.provenance?.repository) {
      const provenance = el('span', '', ` Editable source comes from the matching public decompilation: ${data.provenance.repository}${data.provenance.commit ? ` · ${data.provenance.commit.slice(0, 8)}` : ''}.`);
      provenance.title = data.provenance.commit || '';
      build.append(provenance);
    }
  }
  function renderCategoryIntro(id) {
    const category = state.overview?.categories?.find(item => item.id === id);
    const container = $('#file-detail');
    if (!category) { empty(container, 'A closer look starts here', 'Select a file to view an asset or edit its source.', '▤'); return; }
    empty(container, category.title, category.description, '▤');
    const wrap = $('.empty-state', container);
    const links = el('div', 'entrypoint-list');
    links.append(el('span', 'field-label', 'GOOD PLACES TO START'));
    (category.entrypoints || []).forEach(path => {
      const button = el('button', 'entrypoint-button', `${path}  ↗`);
      button.addEventListener('click', () => {
        const name = path.split('/').pop();
        if (name.includes('.') || name === 'Makefile') loadFile(path);
        else { $('#file-search').value = path; loadFiles(); }
      });
      links.append(button);
    });
    wrap.append(links);
    if (['maps', 'graphics', 'audio'].includes(id)) wrap.append(el('p', 'entrypoint-note', 'Use World editor for terrain and events, Region map for PokéNav, and Story & trainers for the campaign. Custom artwork and music use external graphics and audio tools.'));
  }
  async function loadFiles() {
    const request = ++state.fileListRequest;
    const category = $('#file-category').value;
    const query = $('#file-search').value.trim();
    const list = $('#file-list');
    setLoading(list, 'Finding source files…');
    try {
      const data = await api(`/api/files?category=${encodeURIComponent(category)}&q=${encodeURIComponent(query)}`);
      if (request !== state.fileListRequest) return;
      const files = data.files || [];
      const total = data.total ?? files.length;
      $('#file-count').textContent = files.length < total ? `Showing ${number(files.length)} of ${number(total)}` : `${number(total)} ${total === 1 ? 'file' : 'files'}`;
      if (!files.length) { list.replaceChildren(el('p', 'list-message', 'No files match. Try a shorter path or another system.')); return; }
      list.replaceChildren(...files.map(file => {
        const button = el('button', `file-row${state.file?.path === file.path ? ' selected' : ''}`);
        button.dataset.path = file.path;
        const parts = file.path.split('/');
        const name = el('span', 'file-name');
        name.append(el('span', `file-type-dot ${file.kind || ''}`), document.createTextNode(parts.pop()));
        button.append(name, el('span', 'file-parent', parts.join('/') || '/'));
        button.title = `${file.path} · ${size(file.size)}`;
        button.addEventListener('click', () => loadFile(file.path));
        const item = el('div');
        item.setAttribute('role', 'listitem');
        item.append(button);
        return item;
      }));
    } catch (error) {
      if (request !== state.fileListRequest) return;
      $('#file-count').textContent = 'Unable to load';
      errorPanel(list, error.message, loadFiles);
    }
  }
  async function loadFile(path, alreadyConfirmed = false) {
    if (!alreadyConfirmed && !await canLeave()) return;
    const request = ++state.fileRequest;
    const container = $('#file-detail');
    setLoading(container, 'Opening source file…');
    try {
      const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
      if (request !== state.fileRequest || state.view !== 'files') return;
      state.file = data;
      state.dirty = false;
      renderFile(data);
      $$('.file-row').forEach(node => node.classList.toggle('selected', node.dataset.path === path));
    } catch (error) {
      if (request !== state.fileRequest) return;
      errorPanel(container, error.message, () => loadFile(path, true));
    }
  }
  function fileHeader(data) {
    const header = el('div', 'detail-header');
    const info = el('div', 'detail-file-info');
    info.append(el('h2', 'detail-name', data.path.split('/').pop()), el('p', 'detail-path', data.path));
    const tags = el('div', 'detail-tags');
    tags.append(el('span', 'tag', `${String(data.kind || 'file').toUpperCase()} · ${size(data.size)}`));
    if (data.changed) tags.append(el('span', 'tag modified', 'Modified · original backed up'));
    if (data.editable) tags.append(el('span', 'tag', 'Editable source'));
    info.append(tags); header.append(info);
    const download = el('a', 'button secondary small', 'Download ↓');
    download.href = data.asset_url || assetUrl(data.path); download.download = data.path.split('/').pop();
    header.append(download);
    return header;
  }
  function renderFile(data) {
    const container = $('#file-detail');
    container.replaceChildren(fileHeader(data));
    const content = el('div', 'file-content');
    if (typeof data.content === 'string') {
      const editor = el('textarea', 'code-editor');
      editor.setAttribute('aria-label', `Source of ${data.path}`);
      editor.spellcheck = false;
      editor.autocapitalize = 'off';
      editor.wrap = 'off';
      editor.value = data.content;
      editor.readOnly = !data.editable;
      editor.addEventListener('input', () => setDirty(editor.value !== state.file.content));
      editor.addEventListener('keydown', event => {
        if (event.key === 'Tab' && !event.shiftKey && !editor.readOnly) {
          event.preventDefault();
          const start = editor.selectionStart;
          editor.setRangeText('    ', start, editor.selectionEnd, 'end');
          setDirty(editor.value !== state.file.content);
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (state.dirty && !state.busy) saveFile(); }
      });
      content.append(editor);
    } else if (data.kind === 'image' || /\.png$/i.test(data.path)) {
      const preview = el('div', 'asset-preview');
      const image = el('img'); image.alt = data.path; image.src = data.asset_url || assetUrl(data.path);
      image.addEventListener('error', () => empty(preview, 'This image could not be displayed', 'Download the asset to inspect it with an image editor.', '▧'));
      preview.append(image); content.append(preview);
    } else if (/\.wav$/i.test(data.path)) {
      const preview = el('div', 'asset-preview');
      const audio = el('audio'); audio.controls = true; audio.preload = 'metadata'; audio.src = data.asset_url || assetUrl(data.path);
      audio.setAttribute('aria-label', data.path); preview.append(audio); content.append(preview);
    } else {
      const isAudio = /\.(mid|midi|aif|aiff)$/i.test(data.path);
      empty(content, isAudio ? 'A piece of the game’s soundtrack' : 'This file needs a specialized editor', isAudio ? 'Music data is editable in a MIDI or audio editor. Download it to explore, and see the guide for the source workflow.' : 'Binary map layouts, palettes, and other packed assets use specialized tools. The guide points you to the right editor.', isAudio ? '♫' : '▦');
    }
    container.append(content);
    const footer = el('div', 'editor-footer');
    footer.append(el('span', 'save-status', data.changed ? '✓ Saved locally · original backed up' : data.editable ? 'Original source · backup on first save' : 'Preview · specialized editing tools may be needed'));
    const buttons = el('div', 'button-group');
    if (data.changed && data.editable) {
      const restore = el('button', 'button secondary danger', 'Restore original');
      restore.addEventListener('click', restoreFile); buttons.append(restore);
    }
    if (data.editable && typeof data.content === 'string') {
      const save = el('button', 'button primary save-button', 'Save source'); save.disabled = true;
      save.addEventListener('click', saveFile); buttons.append(save);
    }
    footer.append(buttons); container.append(footer);
  }
  function inlineError(container, error) {
    $('.inline-error', container)?.remove();
    const box = el('div', 'notice error inline-error');
    box.setAttribute('role', 'alert');
    const message = error.status === 409 ? `${error.message} Your unsaved edits are still here. Copy them before reloading this file.` : error.message;
    box.textContent = message;
    container.insertBefore(box, container.children[1] || null);
  }
  async function saveFile() {
    if (!state.file || !state.dirty || state.busy) return;
    const container = $('#file-detail');
    const content = $('.code-editor', container).value;
    const path = state.file.path;
    setBusy(true, container);
    try {
      await api('/api/file', { method: 'POST', body: JSON.stringify({ path, content, expected_sha256: state.file.sha256 }) });
      state.dirty = false;
      const data = await api(`/api/file?path=${encodeURIComponent(path)}`);
      state.file = data;
      renderFile(data);
      toast('Source saved. Your original version is backed up.');
      refreshChangeCount();
    } catch (error) { inlineError(container, error); }
    finally { setBusy(false, container); setDirty(state.dirty); }
  }
  async function restoreFile() {
    if (!state.file || state.busy) return;
    if (!await confirmAction('Restore the original source?', `Restore the original ${state.file.path}? This will replace your current edits to this file.`, 'Restore original', 'Cancel')) return;
    const container = $('#file-detail');
    const path = state.file.path;
    setBusy(true, container);
    try {
      await api('/api/restore', { method: 'POST', body: JSON.stringify({ path, expected_sha256: state.file.sha256 }) });
      state.dirty = false;
      state.file = await api(`/api/file?path=${encodeURIComponent(path)}`);
      renderFile(state.file);
      toast('Original source restored.');
      refreshChangeCount();
    } catch (error) { inlineError(container, error); }
    finally { setBusy(false, container); setDirty(state.dirty); }
  }
  async function loadRecords(kind) {
    const request = ++state.recordListRequest;
    const previousId = state.recordKind === kind ? state.record?.id : null;
    state.recordKind = kind;
    state.record = null;
    state.records = [];
    $('#records-title').textContent = viewNames[kind];
    $('#records-description').textContent = kind === 'pokemon' ? 'Explore the original creatures. Their species data stays protected while you build your world.' : 'Explore the moves behind every battle. Shape power, accuracy, and more.';
    $('#record-search').placeholder = `Search ${kind === 'pokemon' ? 'Pokémon' : 'moves'}…`;
    $('#record-search').value = '';
    $('#record-list-label').textContent = kind === 'pokemon' ? 'POKÉMON' : 'MOVES';
    setLoading($('#record-list'), `Finding ${kind === 'pokemon' ? 'Pokémon' : 'moves'}…`);
    empty($('#record-detail'), kind === 'pokemon' ? 'Choose a Pokémon' : 'Choose a move', 'Select a name to explore its attributes.', kind === 'pokemon' ? '◉' : 'ϟ');
    try {
      const data = await api(`/api/records?kind=${kind}`);
      if (request !== state.recordListRequest || state.view !== kind) return;
      state.records = data.records || [];
      state.fields = data.fields || [];
      renderRecordsList();
      if (previousId && state.records.some(record => record.id === previousId)) loadRecord(previousId, true);
      else if (state.records.length) {
        const starter = state.records.find(record => String(record.id).toUpperCase().includes(kind === 'pokemon' ? 'TREECKO' : 'TACKLE')) || state.records[0];
        loadRecord(starter.id, true);
      }
    } catch (error) { if (request === state.recordListRequest) errorPanel($('#record-list'), error.message, () => loadRecords(kind)); }
  }
  function renderRecordsList() {
    const query = $('#record-search').value.trim().toLowerCase();
    const records = state.records.filter(record => `${record.name} ${record.id}`.toLowerCase().includes(query));
    $('#record-count').textContent = `${number(records.length)} ${state.recordKind === 'pokemon' ? 'Pokémon' : 'moves'}`;
    if (!records.length) { $('#record-list').replaceChildren(el('p', 'list-message', 'No matches. Try a name or source identifier.')); return; }
    $('#record-list').replaceChildren(...records.map(record => {
      const row = el('button', `record-row${state.record?.id === record.id ? ' selected' : ''}`);
      row.dataset.id = String(record.id);
      if (record.sprite) row.append(sprite(record.sprite, '', 'record-sprite'));
      else row.append(el('span', 'record-sprite-placeholder', state.recordKind === 'pokemon' ? '◉' : 'ϟ'));
      const copy = el('span');
      copy.append(el('span', 'record-row-name', record.name || labelId(record.id)), el('span', 'record-row-id', labelId(record.id)));
      row.append(copy);
      row.addEventListener('click', () => loadRecord(record.id));
      const item = el('div');
      item.setAttribute('role', 'listitem');
      item.append(row);
      return item;
    }));
  }
  async function loadRecord(id, alreadyConfirmed = false) {
    if (!alreadyConfirmed && !await canLeave()) return;
    const request = ++state.recordRequest;
    const kind = state.recordKind;
    setLoading($('#record-detail'), 'Reading game data…');
    try {
      const record = await api(`/api/record?kind=${kind}&id=${encodeURIComponent(id)}`);
      if (request !== state.recordRequest || state.view !== kind) return;
      state.record = record;
      state.dirty = false;
      renderRecord(record);
      $$('.record-row').forEach(row => row.classList.toggle('selected', row.dataset.id === String(id)));
    } catch (error) { if (request === state.recordRequest) errorPanel($('#record-detail'), error.message, () => loadRecord(id, true)); }
  }
  function readRecordFields() {
    const values = {};
    $$('#record-form input[data-key]').forEach(input => {
      values[input.dataset.key] = input.type === 'number' && input.value !== '' ? Number(input.value) : input.value;
    });
    return values;
  }
  function renderRecord(record) {
    const container = $('#record-detail');
    const hero = el('div', 'record-hero');
    const art = el('div', 'record-hero-art');
    if (record.sprite) art.append(sprite(record.sprite, record.name, ''));
    else art.append(el('span', 'move-symbol', state.recordKind === 'pokemon' ? '◉' : 'ϟ'));
    const copy = el('div', 'record-hero-copy');
    copy.append(el('span', 'record-kicker', `${state.recordKind === 'pokemon' ? 'POKÉMON' : 'MOVE'} / ${labelId(record.id)}`), el('h2', '', record.name || labelId(record.id)));
    const source = el('button', 'record-source', `${record.source} ↗`);
    source.title = 'Open this source file';
    source.addEventListener('click', () => navigation('files', { path: record.source }));
    copy.append(source); hero.append(art, copy);
    const form = el('form', 'record-form'); form.id = 'record-form';
    form.append(el('h3', 'form-section-title', record.readonly ? 'ORIGINAL POKÉMON · READ ONLY' : 'EDITABLE ATTRIBUTES'));
    const fields = el('div', 'record-fields');
    const definitions = state.fields.length ? state.fields : Object.keys(record.fields || {}).map(key => ({ key, label: key, type: typeof record.fields[key] === 'number' ? 'number' : 'text' }));
    definitions.forEach((definition, index) => {
      if (!Object.hasOwn(record.fields || {}, definition.key)) return;
      const field = el('div', 'record-field');
      const input = el('input');
      const id = `record-field-${index}`;
      const label = el('label', '', definition.label || definition.key); label.htmlFor = id;
      input.id = id; input.name = definition.key; input.dataset.key = definition.key;
      input.type = definition.type === 'number' ? 'number' : 'text';
      input.value = record.fields[definition.key] ?? '';
      input.disabled = !!record.readonly;
      input.required = true;
      input.autocomplete = 'off';
      input.spellcheck = false;
      if (input.type === 'number') {
        input.step = '1';
        if (definition.min !== undefined) input.min = definition.min;
        if (definition.max !== undefined) input.max = definition.max;
      }
      input.addEventListener('input', () => {
        const values = readRecordFields();
        setDirty(Object.keys(values).some(key => String(values[key]) !== String(state.record.fields[key])));
      });
      field.append(label, input);
      if (definition.min !== undefined || definition.max !== undefined) {
        const hint = el('p', 'field-range', definition.min !== undefined && definition.max !== undefined ? `Range: ${definition.min} – ${definition.max}` : definition.min !== undefined ? `Minimum: ${definition.min}` : `Maximum: ${definition.max}`);
        hint.id = `${id}-hint`; input.setAttribute('aria-describedby', hint.id); field.append(hint);
      } else if (input.type === 'text') field.append(el('p', 'field-range', 'Source constant or expression'));
      fields.append(field);
    });
    form.append(fields, el('p', 'record-help', record.readonly ? 'Pokémon definitions are preserved for your rebuild. Choose which Pokémon appear as starters, trainer teams, and encounters without changing the species.' : 'Move types, effects, and other fields can be edited in the source file. Changes take effect after compilation.'));
    form.addEventListener('submit', event => { event.preventDefault(); saveRecord(); });
    const footer = el('div', 'editor-footer');
    footer.append(el('span', 'save-status', 'Original preserved on first save'));
    const buttons = el('div', 'button-group');
    const sourceButton = el('button', 'button secondary', 'View source ↗');
    sourceButton.addEventListener('click', () => navigation('files', { path: record.source }));
    const save = el('button', 'button primary save-button', 'Save changes'); save.type = 'submit'; save.setAttribute('form', 'record-form'); save.disabled = true;
    save.hidden = !!record.readonly;
    buttons.append(sourceButton, save); footer.append(buttons);
    container.replaceChildren(hero, form, footer);
  }
  async function saveRecord() {
    if (!state.record || state.record.readonly || !state.dirty || state.busy) return;
    const form = $('#record-form');
    if (!form.reportValidity()) return;
    const values = readRecordFields();
    const fields = Object.fromEntries(Object.entries(values).filter(([key, value]) => String(value) !== String(state.record.fields[key])));
    const { id, sha256 } = state.record;
    const kind = state.recordKind;
    const container = $('#record-detail');
    setBusy(true, container);
    try {
      await api('/api/record', { method: 'POST', body: JSON.stringify({ kind, id, fields, expected_sha256: sha256 }) });
      state.dirty = false;
      state.record = await api(`/api/record?kind=${kind}&id=${encodeURIComponent(id)}`);
      renderRecord(state.record);
      toast(`${state.record.name || 'Game data'} saved to source.`);
      refreshChangeCount();
    } catch (error) { inlineError(container, error); }
    finally { setBusy(false, container); setDirty(state.dirty); }
  }
  function updateChangeBadge(count) {
    $('#changes-badge').textContent = number(count);
    $('#changes-badge').hidden = !count;
  }
  async function refreshChangeCount() {
    try { const data = await api('/api/changes'); updateChangeBadge(data.count || 0); } catch { /* Nonblocking badge refresh. */ }
  }
  async function loadChanges() {
    const container = $('#changes-list');
    setLoading(container, 'Checking your saved edits…');
    try {
      const data = await api('/api/changes');
      updateChangeBadge(data.count || 0);
      $('#export-patch').hidden = !(data.files || []).length;
      if (!data.files?.length) {
        empty(container, 'A fresh start', 'Your saved changes will appear here. Open the world editor to begin rebuilding Emerald.', '⑂');
        const button = el('a', 'button secondary', 'Open world editor →');
        button.href = '/world';
        $('.empty-state', container).append(button);
        return;
      }
      const heading = el('div', 'changes-heading');
      heading.append(el('span', '', `${number(data.count || data.files.length)} MODIFIED SOURCE FILE${data.files.length === 1 ? '' : 'S'}`), el('span', '', 'LOCAL BACKUPS'));
      container.replaceChildren(heading, ...data.files.map(file => {
        const row = el('div', 'change-row');
        const copy = el('div', 'change-copy');
        copy.append(el('div', 'change-path', file.path), el('div', 'change-meta', file.deleted ? 'Deleted · previous file backed up' : file.created ? 'New source file · included in export' : 'Original preserved · ready to export'));
        const button = el('button', 'button secondary small', 'Open source ↗');
        button.addEventListener('click', () => navigation('files', { path: file.path }));
        row.append(el('span', 'change-marker', file.deleted ? '−' : '~'), copy);
        if (!file.deleted) row.append(button);
        return row;
      }));
    } catch (error) { errorPanel(container, error.message, loadChanges); }
  }
  async function start() {
    $$('.nav-item[data-view]').forEach(button => button.addEventListener('click', () => navigation(button.dataset.view)));
    $$('[data-go]').forEach(button => button.addEventListener('click', () => navigation(button.dataset.go)));
    $('.brand').addEventListener('click', event => { event.preventDefault(); navigation('overview'); });
    $('#file-search').addEventListener('input', () => { clearTimeout(fileSearchTimer); fileSearchTimer = setTimeout(loadFiles, 200); });
    $('#file-category').addEventListener('change', loadFiles);
    $('#record-search').addEventListener('input', renderRecordsList);
    window.addEventListener('beforeunload', event => { if (state.dirty || state.busy) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && (state.view === 'files' || state.view === 'pokemon' || state.view === 'moves')) {
        event.preventDefault(); if (state.view === 'files') saveFile(); else saveRecord();
      }
    });
    $$('.starter img').forEach(image => {
      if (image.complete && image.naturalWidth) { const src = image.src; displaySprite(image); image.src = src; }
      else displaySprite(image);
    });
    try {
      const [session, overview] = await Promise.all([api('/api/session'), api('/api/overview')]);
      state.token = session.token;
      renderOverview(overview);
      refreshChangeCount();
      const initial = location.hash.slice(1);
      if (initial && initial !== 'overview' && Object.hasOwn(viewNames, initial)) navigation(initial, {path:initial==='files'?new URLSearchParams(location.search).get('path'):undefined});
    } catch (error) {
      const alert = $('#global-error');
      alert.textContent = `The local workspace could not be loaded: ${error.message} Check that the workbench server is running, then refresh this page.`;
      alert.hidden = false;
      $('#rom-card').classList.remove('loading-block');
      $('#rom-card').textContent = 'Workspace unavailable';
    }
  }
  start();
})();
