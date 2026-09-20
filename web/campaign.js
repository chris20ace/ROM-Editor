(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const state = {tab: 'story', dirty: false, busy: false, token: '', meta: null, trainers: [],
    current: null, storyMode: 'dialogue', dialogueLabel: '', dialogueChanges: {},
    trainerFilter: 'gym', query: '', loadId: 0, newConversation: null};
  let toastTimer;
  const formats = {NO_ITEM_DEFAULT_MOVES: 'Default moves · no held items', ITEM_DEFAULT_MOVES: 'Default moves · held items',
    NO_ITEM_CUSTOM_MOVES: 'Custom moves · no held items', ITEM_CUSTOM_MOVES: 'Custom moves · held items'};

  function el(tag, cls, text) { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; }
  function toast(message, error = false) {
    const node = $('#toast'); node.textContent = message; node.className = error ? 'error' : ''; node.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, error ? 8500 : 4500);
  }
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Workbench-Token': state.token}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
    return data;
  }
  function dirty(value = true) { state.dirty = value; syncSave(); }
  function syncSave() {
    $('#save').disabled = state.busy || !state.dirty;
    $('#save').textContent = state.busy ? 'Saving…' : 'Save changes';
    $('#save-state').textContent = state.busy ? 'Backing up your source' : state.dirty ? '● Unsaved changes' : 'All changes saved';
    $('#editor').inert = state.busy; $('#sidebar').inert = state.busy;
    document.querySelectorAll('[data-tab]').forEach(button => button.disabled = state.busy);
  }
  async function leave() {
    if (state.busy) return false;
    if (!state.dirty) return true;
    const dialog = $('#discard-dialog');
    if (dialog.open) return false;
    dialog.showModal();
    return new Promise(resolve => {
      function finish(value) { dialog.close(); $('#stay').onclick = null; $('#discard').onclick = null; dialog.oncancel = null; if (value) dirty(false); resolve(value); }
      $('#stay').onclick = () => finish(false); $('#discard').onclick = () => finish(true);
      dialog.oncancel = event => { event.preventDefault(); finish(false); };
    });
  }
  function options(rows, value) {
    const select = el('select');
    for (const row of rows) { const option = el('option', '', row.name); option.value = row.id; select.append(option); }
    select.value = value;
    return select;
  }
  function field(label, input, full = false) { const wrapper = el('label', 'field' + (full ? ' full' : ''), label); wrapper.append(input); return wrapper; }
  function selectField(label, rows, value, update, full = false) {
    const input = options(rows, value); input.addEventListener('change', () => { update(input.value); dirty(); }); return field(label, input, full);
  }
  function textField(label, value, update, settings = {}) {
    const input = el('input'); input.value = value; Object.assign(input, settings);
    input.addEventListener('input', () => { update(settings.type === 'number' ? Number(input.value) : input.value); dirty(); });
    return field(label, input);
  }
  function toggle(label, checked, update) {
    const wrapper = el('label', 'switch'); const input = el('input'); input.type = 'checkbox'; input.checked = checked;
    input.addEventListener('change', () => { update(input.checked); dirty(); }); wrapper.append(input, el('span', '', label)); return wrapper;
  }
  function button(text, fn, cls = '') { const node = el('button', cls, text); node.type = 'button'; node.addEventListener('click', fn); return node; }
  function heading(title, note, chip) {
    const node = el('div', 'section-header'); const text = el('div'); text.append(el('h2', '', title), el('p', '', note)); node.append(text);
    if (chip) node.append(el('span', 'chip', chip)); return node;
  }
  function notice(text) { return el('div', 'notice', text); }
  function normalizeDialogue(text) { return text.replace(/\r\n|\r|\n/g, '\\n'); }
  async function openConversation() {
    if (state.busy || !state.current || state.tab !== 'story') return;
    const map = state.current.map, hadChanges = state.dirty;
    if (!(await leave())) return;
    if (hadChanges) await loadRecord(map);
    if (!state.current || state.current.map !== map) return;
    const prefix = `${map}_EventScript_Conversation`;
    let number = 1;
    while (state.current.content.includes(prefix + number)) number++;
    $('#conversation-label').value = prefix + number;
    $('#conversation-prefix').textContent = `Start the label with ${map}_EventScript_`;
    $('#conversation-text').value = '';
    $('#conversation-error').hidden = true;
    $('#conversation-dialog').showModal();
    $('#conversation-text').focus();
  }
  async function createConversation(event) {
    event.preventDefault();
    if (state.busy || !state.current) return;
    const dialog = $('#conversation-dialog'), record = state.current;
    const label = $('#conversation-label').value.trim();
    const text = normalizeDialogue($('#conversation-text').value.trim());
    if (!label.startsWith(record.map + '_EventScript_')) {
      $('#conversation-error').textContent = `The script label must start with ${record.map}_EventScript_.`;
      $('#conversation-error').hidden = false; return;
    }
    state.busy = true; syncSave();
    dialog.querySelectorAll('input,textarea,button').forEach(node => node.disabled = true);
    $('#conversation-error').hidden = true;
    $('#conversation-save').textContent = 'Saving conversation…';
    try {
      await api('/api/campaign/dialogue', {map:record.map,revision:record.revision,label,text});
      state.newConversation = {map:record.map,label};
      state.storyMode = 'dialogue';
      dialog.close();
      await loadRecord(record.map);
      if (state.current?.map === record.map) {
        state.dialogueLabel = label.replace('_EventScript_', '_Text_');
        renderStory();
      }
      toast('Conversation saved. Its script label is ready to use on an NPC.');
    } catch (error) {
      $('#conversation-error').textContent = error.message; $('#conversation-error').hidden = false;
    } finally {
      state.busy = false; syncSave();
      dialog.querySelectorAll('input,textarea,button').forEach(node => node.disabled = false);
      $('#conversation-save').textContent = 'Save conversation to source';
    }
  }
  function sprite(species) {
    const node = el('div', 'sprite'); const found = state.meta.species.find(row => row.id === species);
    if (found?.sprite) node.style.backgroundImage = `url("${found.sprite}")`;
    node.role = 'img'; node.setAttribute('aria-label', found?.name || species); return node;
  }
  function renderSidebar() {
    const side = $('#sidebar'); side.replaceChildren();
    if (state.tab === 'starters') {
      side.append(el('h2', 'side-title', 'The first choice'), el('p', 'side-help', 'Choose the three Pokémon offered in Professor Birch’s bag. Each slot can use any of the original 386 species.'));
      const note = el('div', 'side-note'); note.append(el('strong', '', 'Pokémon stay themselves'), document.createTextNode('Only the selection changes. Species, stats, evolutions, artwork and learnsets remain untouched.'));
      side.append(note); return;
    }
    const trainer = state.tab === 'trainers';
    side.append(el('h2', 'side-title', trainer ? 'Choose a battle' : 'Choose a place'),
      el('p', 'side-help', trainer ? 'Every initial battle and rematch has its own record.' : 'Dialogue and events belong to the map where they happen.'));
    if (trainer) {
      const select = options([{id:'gym',name:'Gym leaders & rematches'}, {id:'all',name:'All trainers'}, {id:'rival',name:'Rival battles'}, {id:'elite',name:'Elite Four & Champion'}], state.trainerFilter);
      select.className = 'search'; select.setAttribute('aria-label', 'Trainer category'); select.addEventListener('change', () => { state.trainerFilter = select.value; renderRecordList(); }); side.append(select);
    }
    const search = el('input', 'search'); search.type = 'search'; search.placeholder = trainer ? 'Search trainers…' : 'Search maps…'; search.value = state.query;
    search.setAttribute('aria-label', trainer ? 'Search trainers' : 'Search maps'); search.addEventListener('input', () => { state.query = search.value; renderRecordList(); });
    side.append(search, el('div', 'count'), el('div', 'record-list')); renderRecordList();
  }
  function renderRecordList() {
    const list = $('.record-list'); if (!list) return;
    const trainer = state.tab === 'trainers'; const query = state.query.toLowerCase();
    let rows = trainer ? state.trainers : state.meta.maps;
    if (trainer && state.trainerFilter !== 'all') rows = rows.filter(row => state.trainerFilter === 'gym' ? row.gym : state.trainerFilter === 'rival' ? /TRAINER_(?:MAY|BRENDAN)_/.test(row.id) : /TRAINER_(?:SIDNEY|PHOEBE|GLACIA|DRAKE|WALLACE)$/.test(row.id));
    rows = rows.filter(row => `${row.name} ${row.id}`.toLowerCase().includes(query));
    $('.count').textContent = `${rows.length} ${trainer ? 'battles' : 'maps'}`;
    list.replaceChildren();
    for (const row of rows) {
      const selected = state.current && (trainer ? state.current.id : state.current.map) === row.id;
      const node = button(trainer ? row.name : row.name, async () => {
        if (selected || !(await leave())) return; await loadRecord(row.id);
      }, `record${selected ? ' selected' : ''}`);
      if (trainer) node.append(el('small', '', row.label));
      node.setAttribute('aria-pressed', String(!!selected)); list.append(node);
    }
    if (!rows.length) list.append(el('p', 'side-help', 'No matching records. Try a broader search.'));
  }
  async function loadRecord(id) {
    const generation = ++state.loadId; $('#editor').replaceChildren(el('div', 'empty', 'Opening source…'));
    try {
      const path = state.tab === 'starters' ? '/api/campaign/starters' : state.tab === 'trainers' ? `/api/campaign/trainers?id=${encodeURIComponent(id)}` : `/api/campaign/story?map=${encodeURIComponent(id)}`;
      const data = await api(path); if (generation !== state.loadId) return;
      state.current = data; state.dialogueChanges = {}; state.dialogueLabel = data.dialogues?.[0]?.label || '';
      dirty(false); renderEditor(); renderRecordList();
    } catch (error) { if (generation === state.loadId) { state.current = null; $('#editor').replaceChildren(el('div', 'empty', error.message)); toast(error.message, true); } }
  }
  async function changeTab(tab, id) {
    if (!(await leave())) return;
    state.tab = tab; state.query = ''; state.current = null;
    document.querySelectorAll('[data-tab]').forEach(node => node.setAttribute('aria-selected', String(node.dataset.tab === tab)));
    $('#editor').setAttribute('aria-labelledby', `tab-${tab}`); renderSidebar();
    await loadRecord(id || (tab === 'story' ? 'LittlerootTown' : tab === 'trainers' ? 'TRAINER_ROXANNE_1' : null));
  }
  function renderEditor() { if (!state.current) return; if (state.tab === 'starters') renderStarters(); else if (state.tab === 'trainers') renderTrainer(); else renderStory(); }
  function renderStarters() {
    const root = $('#editor'); root.replaceChildren(heading('Three beginnings.', 'The first Pokémon your player will meet.', '386 SPECIES'));
    const cards = el('div', 'starter-grid');
    state.current.species.forEach((species, index) => {
      const card = el('article', 'starter-card'); const picture = sprite(species); const select = options(state.meta.species, species);
      select.setAttribute('aria-label', `Starter slot ${index + 1}`);
      select.addEventListener('change', () => { state.current.species[index] = select.value; card.querySelector('.sprite').replaceWith(sprite(select.value)); dirty(); });
      card.append(el('h3', '', `SLOT 0${index + 1}`), picture, select, el('p', '', ['Left Poké Ball', 'Middle Poké Ball', 'Right Poké Ball'][index])); cards.append(card);
    });
    root.append(cards, notice('This changes the Birch bag selection. Rival battle teams, scripted gifts and dialogue still refer to their own records. Edit those under Trainers & gyms and Story & dialogue when designing your opening.'),
      notice('No species definitions are changed: the selected Pokémon keeps its original stats, types, evolution, moves, artwork and cry.'));
  }
  function renderTrainer() {
    const record = state.current, data = record.fields, root = $('#editor');
    root.replaceChildren(heading(data.trainerName, record.id.replace('TRAINER_', '').replaceAll('_', ' '), `${record.party.length} / 6 POKÉMON`));
    const grid = el('div', 'field-grid');
    grid.append(textField('Battle name · 10 characters', data.trainerName, value => data.trainerName = value, {maxLength:10}),
      selectField('Trainer class', state.meta.classes, data.trainerClass, value => data.trainerClass = value),
      selectField('Battle portrait', state.meta.pictures, data.trainerPic, value => data.trainerPic = value));
    const music = data.encounterMusic_gender.split('|').map(s => s.trim());
    let musicName = music.find(s => s !== 'F_TRAINER_FEMALE'); let female = music.includes('F_TRAINER_FEMALE');
    const updateMusic = () => data.encounterMusic_gender = (female ? 'F_TRAINER_FEMALE | ' : '') + musicName;
    grid.append(selectField('Encounter music', state.meta.music, musicName, value => { musicName = value; updateMusic(); }),
      toggle('Female trainer flag', female, value => { female = value; updateMusic(); }), toggle('Double battle', data.doubleBattle, value => data.doubleBattle = value));
    root.append(grid);
    const sub = el('div', 'subheading'); const title = el('div'); title.append(el('h3', '', 'Battle team'), el('p', '', 'Levels, held items and moves belong to this trainer’s team.'));
    const add = button('+ Add Pokémon', () => {
      const mon = {species:'SPECIES_POOCHYENA',lvl:5,iv:0};
      if (!record.variant.startsWith('NO_ITEM')) mon.heldItem = 'ITEM_NONE';
      if (record.variant.includes('CUSTOM')) mon.moves = ['MOVE_TACKLE','MOVE_NONE','MOVE_NONE','MOVE_NONE'];
      record.party.push(mon); dirty(); renderTrainer();
    }); add.disabled = record.party.length >= 6; sub.append(title, add); root.append(sub);
    root.append(selectField('Team format', state.meta.partyFormats.map(id => ({id,name:formats[id]})), record.variant, value => {
      record.variant = value;
      record.party.forEach(mon => {
        if (value.startsWith('NO_ITEM')) delete mon.heldItem; else mon.heldItem ??= 'ITEM_NONE';
        if (!value.includes('CUSTOM')) delete mon.moves; else mon.moves ??= ['MOVE_TACKLE','MOVE_NONE','MOVE_NONE','MOVE_NONE'];
      }); renderTrainer();
    }));
    const list = el('div', 'party-list'); list.style.marginTop = '15px';
    record.party.forEach((mon, index) => {
      const card = el('article', 'party-card'), top = el('div', 'row'); top.append(el('span', 'party-number', `PARTY ${String(index + 1).padStart(2,'0')}`));
      const remove = button('Remove', () => { record.party.splice(index, 1); dirty(); renderTrainer(); }, 'remove'); remove.disabled = record.party.length <= 1; top.append(remove); card.append(top);
      const fields = el('div', 'field-grid'); fields.append(selectField('Pokémon', state.meta.species, mon.species, value => mon.species = value, true),
        textField('Level', mon.lvl, value => mon.lvl = value, {type:'number',min:1,max:100}),
        textField('IV strength · 0–255', mon.iv, value => mon.iv = value, {type:'number',min:0,max:255}));
      if ('heldItem' in mon) fields.append(selectField('Held item', state.meta.items, mon.heldItem, value => mon.heldItem = value, true));
      if (mon.moves) { const moves = el('div', 'moves-grid'); mon.moves.forEach((move, slot) => { const select = options(state.meta.moves, move); select.setAttribute('aria-label', `Party ${index+1} move ${slot+1}`); select.addEventListener('change', () => { mon.moves[slot] = select.value; dirty(); }); moves.append(select); }); fields.append(field('Moves · four slots', moves, true)); }
      card.append(fields); list.append(card);
    }); root.append(list);
    const advanced = el('details'); advanced.append(el('summary', '', 'Battle items & AI behavior'));
    const items = el('div', 'field-grid');
    for (let i = 0; i < 4; i++) items.append(selectField(`Trainer item ${i+1}`, state.meta.items, data.items[i] || 'ITEM_NONE', value => { while (data.items.length < 4) data.items.push('ITEM_NONE'); data.items[i] = value; }));
    const flags = new Set(data.aiFlags.split('|').map(v => v.trim()).filter(v => v !== '0')); const ai = el('div', 'ai-grid');
    state.meta.ai.forEach(row => ai.append(toggle(row.name, flags.has(row.id), value => { value ? flags.add(row.id) : flags.delete(row.id); data.aiFlags = [...flags].join(' | ') || '0'; })));
    advanced.append(items, ai); root.append(advanced, notice('Leader battles and rematches are separate records. Edit the gym’s map scripts for dialogue, badges, rewards, puzzles and story conditions. The world editor controls the leader’s map sprite and position.'));
  }
  function renderStory() {
    const record = state.current, root = $('#editor');
    root.replaceChildren(heading(record.map.replace(/([a-z0-9])([A-Z])/g, '$1 $2'), 'Rewrite conversations and the events around them.', `${record.dialogues.length} TEXT BLOCKS`));
    const modes = el('div', 'mode-buttons');
    for (const [mode,title] of [['dialogue','Dialogue'],['source','Full event script']]) modes.append(button(title, async () => {
      if (mode === state.storyMode || !(await leave())) return;
      state.storyMode = mode; await loadRecord(record.map);
    }, mode === state.storyMode ? 'chosen' : ''));
    const tools = el('div', 'story-tools'); tools.append(modes, button('+ New conversation', openConversation, 'new-conversation')); root.append(tools);
    if (state.newConversation?.map === record.map) {
      const saved = el('div', 'notice conversation-saved');
      saved.append(el('strong', '', 'Conversation saved to source.'), el('p', '', 'Paste this label into the NPC’s “Script to run” field in World studio, then save the map.'));
      const row = el('div', 'row'), input = el('input', 'mono grow'); input.value = state.newConversation.label; input.readOnly = true; input.setAttribute('aria-label', 'New conversation script label');
      input.addEventListener('focus', () => input.select());
      row.append(input, button('Copy label', async () => {
        input.focus(); input.select();
        try { await navigator.clipboard.writeText(input.value); toast('Script label copied.'); }
        catch { toast('The script label is selected. Press Ctrl+C to copy it.'); }
      })); saved.append(row); root.append(saved);
    }
    if (state.storyMode === 'source') {
      root.append(notice('Advanced story authoring: event scripts control flags, conditions, cutscenes, gifts, battles and progression. Keep referenced labels intact. This editor saves source text; script logic is verified when you build and playtest.'));
      const source = el('textarea', 'source-editor'); source.spellcheck = false; source.value = record.content; source.setAttribute('aria-label', 'Full map event script');
      source.addEventListener('input', () => { record.content = source.value; dirty(); });
      source.addEventListener('keydown', event => { if (event.key === 'Tab') { event.preventDefault(); const a = source.selectionStart, b = source.selectionEnd; source.setRangeText('\t', a, b, 'end'); record.content = source.value; dirty(); } });
      root.append(source); return;
    }
    if (!record.dialogues.length) {
      root.append(notice('This map has no local conversations yet. Choose New conversation to write one, then attach its script label to an NPC in World studio. You can also write an event using Full event script.')); return;
    }
    const picker = options(record.dialogues.map(row => ({id:row.label,name:row.label.replace(record.map + '_Text_', '').replace(/([a-z0-9])([A-Z])/g,'$1 $2')})), state.dialogueLabel);
    picker.className = 'dialogue-picker'; picker.setAttribute('aria-label', 'Dialogue block'); picker.addEventListener('change', () => { state.dialogueLabel = picker.value; renderStory(); }); root.append(picker);
    const current = record.dialogues.find(row => row.label === state.dialogueLabel); const text = state.dialogueChanges[current.label] ?? current.text;
    const textarea = el('textarea', 'dialogue-text'); textarea.value = text; textarea.spellcheck = false; textarea.setAttribute('aria-label', 'Dialogue text and game control codes');
    const preview = el('div', 'text-preview');
    function updatePreview(value) { preview.textContent = value.replace(/\\[nl]/g,'\n').replace(/\\p/g,'\n\n').replace(/\$$/, ''); }
    updatePreview(text);
    textarea.addEventListener('input', () => { state.dialogueChanges[current.label] = textarea.value; updatePreview(textarea.value); dirty(); });
    root.append(textarea, notice('Press Enter or use \\n for a new line, \\l to scroll one line, and \\p for a new page. Keep {PLAYER} and other placeholders where you want them. Every block ends with $. The preview shows text flow; the game determines exact wrapping.'), el('div','preview-label','TEXT PREVIEW'), preview);
  }
  async function save() {
    if (!state.current || !state.dirty || state.busy) return;
    state.busy = true; syncSave();
    const record = state.current;
    try {
      if (state.tab === 'starters') await api('/api/campaign/starters', {revision:record.revision,species:record.species});
      else if (state.tab === 'trainers') await api('/api/campaign/trainers', {revision:record.revision,id:record.id,fields:record.fields,party:record.party,variant:record.variant});
      else {
        const body = {revision:record.revision,map:record.map};
        if (state.storyMode === 'source') body.content = record.content;
        else body.dialogues = Object.entries(state.dialogueChanges).map(([label,text]) => ({label,text:normalizeDialogue(text)}));
        await api('/api/campaign/story', body);
      }
      dirty(false); await loadRecord(record.id || record.map); toast('Saved to source. Your previous version is backed up.');
      if (state.tab === 'trainers') { state.trainers = (await api('/api/campaign/trainers')).trainers; renderRecordList(); }
    } catch (error) { toast(error.message, true); }
    finally { state.busy = false; syncSave(); }
  }
  $('#save').addEventListener('click', save);
  $('#conversation-form').addEventListener('submit', createConversation);
  $('#conversation-cancel').addEventListener('click', () => { if (!state.busy) $('#conversation-dialog').close(); });
  $('#conversation-dialog').addEventListener('cancel', event => { if (state.busy) event.preventDefault(); });
  document.querySelectorAll('[data-tab]').forEach(node => node.addEventListener('click', () => { if (node.dataset.tab !== state.tab) changeTab(node.dataset.tab); }));
  document.querySelectorAll('a').forEach(anchor => anchor.addEventListener('click', async event => { if (!state.dirty && !state.busy) return; event.preventDefault(); if (await leave()) location.href = anchor.href; }));
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save(); } });
  (async () => {
    try {
      const [session, meta, trainers] = await Promise.all([api('/api/session'),api('/api/campaign'),api('/api/campaign/trainers')]);
      state.token = session.token; state.meta = meta; state.trainers = trainers.trainers;
      const params = new URLSearchParams(location.search); const requested = params.get('tab');
      await changeTab(['story','starters','trainers'].includes(requested) ? requested : 'story', params.get('map') || params.get('id'));
    } catch (error) { $('#editor').replaceChildren(el('div', 'empty', error.message)); $('#save-state').textContent = 'Could not open source'; toast(error.message, true); }
  })();
})();
