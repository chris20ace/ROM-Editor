(() => {
  'use strict';
  const $ = (q, root = document) => root.querySelector(q);
  const $$ = (q, root = document) => [...root.querySelectorAll(q)];
  const clone = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const nice = value => String(value || '').replace(/^OBJ_EVENT_GFX_|^MAP_TYPE_|^MAP_/, '').replace(/([a-z\d])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
  const eventKinds = ['object_events', 'warp_events', 'coord_events', 'bg_events'];
  const kindNames = { object_events: 'NPC / object', warp_events: 'Warp', coord_events: 'Trigger', bg_events: 'Interaction' };
  const kindColors = { object_events: '#3e844d', warp_events: '#4f8bb4', coord_events: '#c89439', bg_events: '#a06db3' };
  const kindLetters = { object_events: 'N', warp_events: 'W', coord_events: 'T', bg_events: 'S' };
  const S = { token: '', maps: [], areas: [], mapAreas: new Map(), mapDetails: new Map(), area: 'all', tilesets: [], objects: [], objectMap: new Map(), data: null, atlas: null, atlases: new Map(), tile: 0, background: 0,
    mode: 'terrain', tool: 'brush', tab: 'tiles', zoom: 2, mapFilter: 'all', history: [], future: [], saved: '', dirty: false,
    selected: null, hover: null, drag: null, place: false, object: 'OBJ_EVENT_GFX_BOY_1', busy: false, space: false, request: 0,
    images: new Map(), frame: null, toastTimer: null, dialogResolve: null };
  const canvas = $('#map-canvas'), ctx = canvas.getContext('2d'), viewport = $('#map-viewport');
  let fieldId = 0;

  function node(tag, className = '', text) { const n = document.createElement(tag); n.className = className; if (text !== undefined) n.textContent = text; return n; }
  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workbench-Token': S.token }, body: JSON.stringify(body) });
    let data; try { data = await res.json(); } catch { throw new Error(`The server returned an unreadable response (${res.status}).`); }
    if (!res.ok) throw new Error(typeof data.error === 'string' ? data.error : data.error?.message || data.message || `Request failed (${res.status}).`);
    return data;
  }
  function toast(message, error = false) {
    clearTimeout(S.toastTimer); $('#toast').textContent = message; $('#toast').classList.toggle('error', error); $('#toast').hidden = false;
    S.toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 8000 : 4200);
  }
  function error(message) { $('#error-banner').textContent = message || ''; $('#error-banner').hidden = !message; }
  function snapshot() { if (!S.data) return null; return clone({ width: S.data.width, height: S.data.height, cells: S.data.cells, border: S.data.border, map: S.data.map, layout: S.data.layout }); }
  function fingerprint() { return JSON.stringify(snapshot()); }
  function pairKey(layout) { return `${layout.primary_tileset}|${layout.secondary_tileset}`; }
  function applySnapshot(value) { Object.assign(S.data, clone(value)); const pair=S.atlases.get(pairKey(S.data.layout)); if(pair){S.atlas=pair.image;S.data.tileset=pair.metadata;} S.selected = null; S.hover = null; refresh(); }
  function record(before) {
    if (JSON.stringify(before) === fingerprint()) return false;
    S.history.push(before); if (S.history.length > 50) S.history.shift(); S.future = []; status(); return true;
  }
  function edit(callback, options = {}) {
    if (!S.data || S.busy) return; const before = snapshot(); callback(); record(before);
    if (options.refresh) refresh(); else { render(); if (options.events) renderEvents(); }
  }
  function status() {
    S.dirty = !!S.data && fingerprint() !== S.saved;
    $('#save').disabled = !S.data || !S.dirty || S.busy;
    $('#revert').disabled = !S.dirty || S.busy;
    $('#undo').disabled = !S.history.length || S.busy;
    $('#redo').disabled = !S.future.length || S.busy;
    $('#save-status').textContent = S.busy ? 'Working…' : S.dirty ? 'Unsaved changes' : 'All changes saved';
    $('#dirty-dot').classList.toggle('dirty', S.dirty);
    document.title = `${S.dirty ? '● ' : ''}${S.data ? nice(S.data.name) + ' · ' : ''}World Studio`;
  }
  function undo() { if (!S.history.length || S.busy) return; S.future.push(snapshot()); applySnapshot(S.history.pop()); }
  function redo() { if (!S.future.length || S.busy) return; S.history.push(snapshot()); applySnapshot(S.future.pop()); }
  function dialog(title, html, submit = 'Continue', options = {}) {
    if ($('#dialog').open) return Promise.resolve(null);
    $('#dialog-title').textContent = title; $('#dialog-content').innerHTML = html; $('#dialog-submit').textContent = submit;
    $('#dialog-submit').hidden = !!options.info; $('#dialog-cancel').textContent = options.info ? 'Got it' : 'Cancel';
    $('#dialog-submit').classList.toggle('danger', !!options.danger); $('#dialog').showModal();
    if (options.setup) options.setup($('#dialog-content'));
    return new Promise(resolve => { S.dialogResolve = resolve; });
  }
  function closeDialog(value) { $('#dialog').close(); const done = S.dialogResolve; S.dialogResolve = null; if (done) done(value); }
  function formValues(form) { return Object.fromEntries(new FormData(form)); }
  $('#dialog-form').addEventListener('submit', e => { e.preventDefault(); closeDialog(formValues(e.currentTarget)); });
  $('#dialog-cancel').onclick = () => closeDialog(null); $('#dialog-close').onclick = () => closeDialog(null);
  $('#dialog').addEventListener('cancel', e => { e.preventDefault(); closeDialog(null); });
  async function discardIfNeeded() {
    if (!S.dirty) return true;
    return !!await dialog('Leave these changes?', '<p>This map has unsaved changes. Save it first to keep them, or discard them and continue.</p>', 'Discard changes', { danger: true });
  }
  function loadImage(url) {
    return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error('The map artwork could not be loaded. Try reopening this map.')); image.src = url; });
  }
  async function openMap(name, force = false) {
    if (S.busy || (!force && S.data?.name === name)) return;
    if (!force && !await discardIfNeeded()) return;
    const req = ++S.request; S.busy = true; status(); error(''); $('#loading').hidden = false;
    try {
      const data = await api(`/api/world/map?name=${encodeURIComponent(name)}`);
      const atlas = await loadImage(data.tileset.atlas_url);
      if (req !== S.request) return;
      adoptMap(data, atlas); history.replaceState(null, '', `/world?map=${encodeURIComponent(data.name)}`);
      viewport.scrollTop = 0; viewport.scrollLeft = 0;
    } catch (err) { error(err.message); if (!S.data) $('#map-title').textContent = 'Could not open this map'; }
    finally { if (req === S.request) { S.busy = false; $('#loading').hidden = true; status(); } }
  }
  function adoptMap(data, atlas) {
    if(S.area!=='all'&&S.mapAreas.get(data.name)!==S.area){S.area=S.mapAreas.get(data.name)||'all';$('#area-select').value=S.area;}
    S.data = data; S.atlas = atlas; S.atlases.set(pairKey(data.layout),{image:atlas,metadata:data.tileset}); eventKinds.forEach(kind => { S.data.map[kind] ||= []; });
    S.history = []; S.future = []; S.selected = null; S.hover = null; S.drag = null; S.place = false;
    S.saved = fingerprint(); const valid = metadata().filter(t => t.valid !== false);
    if (!valid.some(t => t.id === S.tile)) S.tile = valid[0]?.id || 0;
    // The most common tile is a useful initial blank ground, and is always valid for this map.
    const counts = new Map(); data.cells.forEach(cell => counts.set(cell & 1023, (counts.get(cell & 1023) || 0) + 1));
    S.background = [...counts].sort((a,b) => b[1] - a[1])[0]?.[0] || 0;
    refresh();
  }
  function refresh() {
    if (!S.data) return;
    const valid = metadata().filter(t => t.valid !== false), fallback = valid[0]?.id || 0;
    if (!valid.some(t => t.id === S.tile)) S.tile = fallback;
    if (!valid.some(t => t.id === S.background)) S.background = fallback;
    $('#map-title').textContent = nice(S.data.name); $('#map-kind').textContent = nice(S.data.map.map_type || 'MAP').toUpperCase();
    $('#map-subtitle').textContent = `${S.data.width} × ${S.data.height} tiles · ${S.data.layout?.primary_tileset?.replace('gTileset_', '') || 'Primary'} / ${S.data.layout?.secondary_tileset?.replace('gTileset_', '') || 'Secondary'} tilesets`;
    $('#canvas-map-name').textContent = S.data.name; $('#canvas-map-size').textContent = `${S.data.width} × ${S.data.height}`;
    $('#campaign-link').href = `/campaign?map=${encodeURIComponent(S.data.name)}`;
    $('#connections-link').href = `/connections?map=${encodeURIComponent(S.data.name)}`;
    $('#area-link').href = `/areas?map=${encodeURIComponent(S.data.name)}`;
    renderMapList(); renderPalette(); renderTileSelection(); renderEvents(); renderProperties(); render(); status();
  }
  function applyAreas(catalog, currentMap) {
    const previous = S.area, firstLoad = S.areas.length === 0;
    S.areas = catalog.areas || [];
    S.mapAreas = new Map(S.areas.flatMap(area => area.maps.map(map => [map.name, area.id])));
    S.mapDetails = new Map(S.areas.flatMap(area => area.maps.map(map => [map.name, map])));
    const select = $('#area-select'), all = node('option', '', 'All areas'); all.value = 'all';
    select.replaceChildren(all);
    for (const [kind, label] of [['town','Towns & cities'],['route','Routes'],['dungeon','Caves & landmarks'],['special','Other areas']]) {
      const members = S.areas.filter(area => area.kind === kind);
      if (!members.length) continue;
      const group = node('optgroup'); group.label = label;
      for (const area of members) { const option = node('option', '', area.name); option.value = area.id; group.append(option); }
      select.append(group);
    }
    if (firstLoad) S.area = S.mapAreas.get(currentMap) || 'all';
    else if (previous === 'all' || S.areas.some(area => area.id === previous)) S.area = previous;
    else S.area = S.mapAreas.get(currentMap) || 'all';
    select.value = S.area;
    renderMapList();
  }
  function renderMapList() {
    const query = $('#map-search').value.toLowerCase().replace(/\s+/g, '');
    const selectedArea = S.areas.find(area => area.id === S.area);
    const areasById = new Map(S.areas.map(area => [area.id, area]));
    const order = new Map((selectedArea ? selectedArea.maps : S.areas.flatMap(area => area.maps)).map((map, index) => [map.name, index]));
    const filtered = S.maps.filter(m => {
      const detail = S.mapDetails.get(m.name), area = areasById.get(S.mapAreas.get(m.name));
      const searchable = `${m.name}${m.id}${detail?.display_name || nice(m.name)}${area?.name || ''}`.toLowerCase().replace(/\s+/g, '');
      const outdoors = detail ? detail.role === 'outdoors' : ['MAP_TYPE_TOWN','MAP_TYPE_CITY','MAP_TYPE_ROUTE','MAP_TYPE_OCEAN_ROUTE'].includes(m.map_type);
      return (S.area === 'all' || area?.id === S.area) && (!query || searchable.includes(query)) && (S.mapFilter !== 'outdoor' || outdoors);
    }).sort((a, b) => (order.get(a.name) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.name) ?? Number.MAX_SAFE_INTEGER) || a.name.localeCompare(b.name, undefined, {numeric:true}));
    $('#map-count').textContent = `${filtered.length.toLocaleString()} locations`;
    const frag = document.createDocumentFragment();
    const grouping = m => S.area === 'all' ? S.mapAreas.get(m.name) || 'other' : S.mapDetails.get(m.name)?.role || 'other';
    const counts = new Map(); filtered.forEach(m => counts.set(grouping(m), (counts.get(grouping(m)) || 0) + 1));
    let lastGroup = null;
    filtered.forEach(m => {
      const detail = S.mapDetails.get(m.name), group = grouping(m);
      if (group !== lastGroup) {
        const title = S.area === 'all' ? areasById.get(group)?.name || 'Other locations' : detail?.role_label || 'Other locations';
        const header = node('div', 'list-caption map-group-heading'); header.setAttribute('role','presentation');
        header.append(node('span','',title), node('span','',counts.get(group))); frag.append(header); lastGroup = group;
      }
      const b = node('button', `map-row${m.name === S.data?.name ? ' active' : ''}`); b.type = 'button'; b.setAttribute('role','listitem'); b.title = m.name;
      b.innerHTML = `<span class="map-row-icon">${/Route\d/.test(m.name) ? '⌁' : /(Town|City)$/.test(m.name) ? '⌂' : '▧'}</span><span class="map-row-copy"><strong>${esc(detail?.display_name || nice(m.name))}</strong><small>${esc(m.width ?? detail?.width ?? '?')} × ${esc(m.height ?? detail?.height ?? '?')} tiles${S.area === 'all' && detail ? ' · ' + esc(detail.role_label) : ''}</small></span>${m.name === S.data?.name ? '<span class="map-arrow">›</span>' : ''}`;
      b.onclick = () => openMap(m.name); frag.append(b); });
    $('#map-list').replaceChildren(frag); if (!filtered.length) $('#map-list').append(node('div','list-empty','No maps match your search.'));
  }
  function metadata() { return S.data?.tileset?.metatiles || []; }
  function tileMeta(id) { return metadata().find(t => t.id === id); }
  function drawTile(context, id, x, y, size = 16) {
    if (!S.atlas) return; const cols = S.data.tileset.columns || 16;
    context.imageSmoothingEnabled = false; context.drawImage(S.atlas, (id % cols) * 16, Math.floor(id / cols) * 16, 16,16,x,y,size,size);
  }
  function tileCanvas(id) { const c = node('canvas'); c.width = 16; c.height = 16; drawTile(c.getContext('2d'), id, 0,0); return c; }
  function renderPalette() {
    if (!S.data) return; const filter = $('#tile-filter').value, query = $('#tile-search').value.toLowerCase().trim();
    const used = new Set(S.data.cells.map(c => c & 1023));
    const tiles = metadata().filter(t => t.valid !== false && (filter === 'all' || filter === 'primary' && t.id < 512 || filter === 'secondary' && t.id >= 512 || filter === 'used' && used.has(t.id)) && (!query || `${t.id} 0x${t.id.toString(16).padStart(3,'0')} ${t.behavior_name || ''} ${t.behavior || ''}`.toLowerCase().includes(query)));
    $('#tile-count').textContent = `${tiles.length} tiles`; $('#tileset-name').textContent = `${S.data.layout?.primary_tileset?.replace('gTileset_', '') || 'Primary'} + ${S.data.layout?.secondary_tileset?.replace('gTileset_', '') || 'Secondary'}`;
    const frag = document.createDocumentFragment();
    tiles.forEach(t => { const b = node('button', `tile-button${t.id === S.tile ? ' active' : ''}`); b.type = 'button'; b.dataset.tile = t.id;
      b.title = `Tile ${t.id} · 0x${t.id.toString(16).padStart(3,'0')} · ${t.behavior_name || `behavior ${t.behavior ?? 0}`}`; b.setAttribute('aria-label', b.title); b.setAttribute('aria-pressed', String(t.id === S.tile));
      b.append(tileCanvas(t.id), node('span','',String(t.id))); b.onclick = () => { S.tile = t.id; if (S.mode !== 'terrain') mode('terrain'); renderTileSelection(); }; frag.append(b); });
    $('#tile-palette').replaceChildren(frag); if (!tiles.length) $('#tile-palette').append(node('div','list-empty','No matching tiles.'));
  }
  function renderTileSelection() {
    if (!S.data) return; ['selected-tile','background-tile'].forEach((id, i) => { const c = $(`#${id}`), context = c.getContext('2d'); context.clearRect(0,0,16,16); drawTile(context, i ? S.background : S.tile,0,0); });
    const meta = tileMeta(S.tile); $('#tile-name').textContent = `Tile ${S.tile} · ${S.tile < 512 ? 'Primary' : 'Local'}`;
    $('#tile-detail').textContent = `${meta?.behavior_name || `Behavior ${meta?.behavior ?? '?'}`} · layer ${meta?.layer_type ?? '?'}`;
    $('#background-id').textContent = S.background;
    $$('.tile-button').forEach(b => { const selected = Number(b.dataset.tile) === S.tile; b.classList.toggle('active',selected); b.setAttribute('aria-pressed',String(selected)); });
    $('#selection-info').textContent = S.mode === 'terrain' ? `Brush: tile ${S.tool === 'erase' ? S.background : S.tile}` : S.mode === 'collision' ? 'Movement attributes' : S.place ? 'Click map to place event' : 'Select or drag an event';
  }
  function renderObjects() {
    const query = $('#object-search').value.toLowerCase(); const objects = S.objects.filter(o => `${o.id} ${o.name}`.toLowerCase().includes(query));
    $('#object-count').textContent = `${objects.length} original sprites`; const frag = document.createDocumentFragment();
    objects.forEach(o => { const b = node('button',`object-card${S.object === o.id ? ' active' : ''}`); b.type = 'button'; b.title = o.id;
      if (o.preview_url) { const img = node('img'); img.src = o.preview_url; img.alt = ''; img.loading = 'lazy'; b.append(img); }
      else b.append(node('span','', '♙'));
      b.append(node('span','',nice(o.name || o.id).toLowerCase())); b.onclick = () => { S.object = o.id; mode('events'); $('#event-add-type').value = 'object_events'; S.place = true; syncPlace(); tab('objects'); renderObjects(); toast(`Place ${nice(o.name || o.id).toLowerCase()}: click a tile on the map.`); }; frag.append(b); });
    $('#object-palette').replaceChildren(frag); if (!objects.length) $('#object-palette').append(node('div','list-empty','No matching objects.'));
  }
  function sprite(id) {
    const object = S.objectMap.get(id); if (!object?.preview_url || object.preview_available === false) return null;
    if (!S.images.has(id)) { const img = new Image(); S.images.set(id,img); img.onload = () => render(); img.onerror = () => S.images.set(id,null); img.src = object.preview_url; }
    const img = S.images.get(id); return img?.complete && img.naturalWidth ? img : null;
  }
  function render() { if (S.frame !== null) return; S.frame = requestAnimationFrame(() => { S.frame = null; draw(); }); }
  function draw() {
    if (!S.data || !S.atlas) return; const {width,height,cells} = S.data;
    if (canvas.width !== width*16) canvas.width = width*16; if (canvas.height !== height*16) canvas.height = height*16;
    canvas.style.width = `${width*16*S.zoom}px`; canvas.style.height = `${height*16*S.zoom}px`;
    $('#zoom-fit').textContent = `${Math.round(S.zoom*100)}%`; ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = '#9cba7c'; ctx.fillRect(0,0,canvas.width,canvas.height);
    for (let i=0; i<cells.length; i++) drawTile(ctx, cells[i]&1023, (i%width)*16, Math.floor(i/width)*16);
    if (S.mode === 'collision') {
      ctx.font = '6px Consolas,monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      cells.forEach((cell,i) => { const x=(i%width)*16, y=Math.floor(i/width)*16, c=(cell>>10)&3, e=(cell>>12)&15;
        ctx.fillStyle = c ? '#ab443566' : '#20775245'; ctx.fillRect(x,y,16,16);
        ctx.fillStyle = '#112b30bb'; ctx.fillRect(x+1,y+4,14,8); ctx.fillStyle = '#fff'; ctx.fillText(`${c}/${e}`,x+8,y+8); });
    }
    if ($('#show-grid').checked) { ctx.beginPath(); ctx.strokeStyle = '#182c3255'; ctx.lineWidth = .5/S.zoom;
      for(let x=0;x<=width;x++){ctx.moveTo(x*16,0);ctx.lineTo(x*16,height*16);} for(let y=0;y<=height;y++){ctx.moveTo(0,y*16);ctx.lineTo(width*16,y*16);} ctx.stroke(); }
    if ($('#show-events').checked || S.mode === 'events') {
      eventKinds.forEach(kind => (S.data.map[kind]||[]).forEach((event,index) => {
        const x=Number(event.x)*16,y=Number(event.y)*16, selected=S.selected?.kind===kind&&S.selected.index===index;
        ctx.fillStyle=kindColors[kind]+(S.mode==='events'?'d9':'a0'); ctx.fillRect(x+1,y+1,14,14);
        if(kind==='object_events') { const img=sprite(event.graphics_id); if(img){const w=Math.min(img.naturalWidth,64),h=Math.min(img.naturalHeight,64);ctx.drawImage(img,0,0,img.naturalWidth,img.naturalHeight,x+8-w/2,y+16-h,w,h);} }
        ctx.fillStyle=kindColors[kind];ctx.fillRect(x+8,y+8,8,8);ctx.fillStyle='#fff';ctx.font='bold 6px Consolas,monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(kindLetters[kind],x+12,y+12);
        if(selected){ctx.strokeStyle='#fffbd1';ctx.lineWidth=2/S.zoom;ctx.strokeRect(x+.5,y+.5,15,15);ctx.strokeStyle='#233e32';ctx.lineWidth=.6/S.zoom;ctx.strokeRect(x-.5,y-.5,17,17);}
      }));
    }
    if(S.drag?.rectangle){const a=S.drag.start,b=S.drag.last;ctx.fillStyle='#fff7b54d';ctx.fillRect(Math.min(a.x,b.x)*16,Math.min(a.y,b.y)*16,(Math.abs(a.x-b.x)+1)*16,(Math.abs(a.y-b.y)+1)*16);ctx.strokeStyle='#fff4b1';ctx.lineWidth=1/S.zoom;ctx.strokeRect(Math.min(a.x,b.x)*16+.5,Math.min(a.y,b.y)*16+.5,(Math.abs(a.x-b.x)+1)*16-1,(Math.abs(a.y-b.y)+1)*16-1);}
    if(S.hover){ctx.strokeStyle='#fbffdb';ctx.lineWidth=1.5/S.zoom;ctx.strokeRect(S.hover.x*16+.5,S.hover.y*16+.5,15,15);if(S.mode==='events'&&S.place){ctx.fillStyle='#e9f6b655';ctx.fillRect(S.hover.x*16,S.hover.y*16,16,16);}}
  }
  function position(event, clamp = false) {
    if(!S.data)return null;const r=canvas.getBoundingClientRect();let x=Math.floor((event.clientX-r.left)/r.width*S.data.width),y=Math.floor((event.clientY-r.top)/r.height*S.data.height);
    if(clamp){x=Math.max(0,Math.min(S.data.width-1,x));y=Math.max(0,Math.min(S.data.height-1,y));}
    return x>=0&&y>=0&&x<S.data.width&&y<S.data.height?{x,y}:null;
  }
  function hover(position) {
    S.hover=position;if(position&&S.data){const cell=S.data.cells[position.y*S.data.width+position.x];$('#cursor-info').textContent=`X ${position.x} · Y ${position.y}   Tile ${cell&1023}   Collision ${(cell>>10)&3} / Elevation ${(cell>>12)&15}`;}
    render();
  }
  function movementBits(cell) {
    let value=cell;if($('#paint-collision').checked)value=(value&~0xc00)|((Number($('#collision').value)&3)<<10);
    if($('#paint-elevation').checked)value=(value&~0xf000)|((Math.max(0,Math.min(15,Number($('#elevation').value)||0)))<<12);return value;
  }
  function paintValue(cell) {
    if(S.mode==='collision')return movementBits(cell);
    let value=(cell&0xfc00)|(S.tool==='erase'?S.background:S.tile);
    if(!$('#preserve-movement').checked)value=(value&1023)|((Number($('#collision').value)&3)<<10)|((Math.max(0,Math.min(15,Number($('#elevation').value)||0)))<<12);return value;
  }
  function paintAt(p) { const i=p.y*S.data.width+p.x; S.data.cells[i]=paintValue(S.data.cells[i]); }
  function line(a,b) { let x=a.x,y=a.y;const dx=Math.abs(b.x-a.x),dy=-Math.abs(b.y-a.y),sx=a.x<b.x?1:-1,sy=a.y<b.y?1:-1;let err=dx+dy;while(true){paintAt({x,y});if(x===b.x&&y===b.y)break;const e=2*err;if(e>=dy){err+=dy;x+=sx;}if(e<=dx){err+=dx;y+=sy;}} }
  function flood(p) {
    const width=S.data.width,height=S.data.height,start=p.y*width+p.x, source=S.data.cells[start];
    const mask=S.mode==='terrain'?1023:($('#paint-collision').checked?0xc00:0)|($('#paint-elevation').checked?0xf000:0);
    if(!mask || (paintValue(source)&mask)===(source&mask))return;
    const pending=[start], seen=new Uint8Array(S.data.cells.length);seen[start]=1;
    while(pending.length){const i=pending.pop();if((S.data.cells[i]&mask)!==(source&mask))continue;S.data.cells[i]=paintValue(S.data.cells[i]);const x=i%width,y=Math.floor(i/width);
      const next=[];if(x>0)next.push(i-1);if(x<width-1)next.push(i+1);if(y>0)next.push(i-width);if(y<height-1)next.push(i+width);
      next.forEach(n=>{if(!seen[n]){seen[n]=1;pending.push(n);}});}
  }
  function pick(p) {const cell=S.data.cells[p.y*S.data.width+p.x];S.tile=cell&1023;$('#collision').value=(cell>>10)&3;$('#elevation').value=(cell>>12)&15;renderTileSelection();toast(`Picked tile ${S.tile} · collision ${(cell>>10)&3}, elevation ${(cell>>12)&15}`);}
  function eventAt(p) {const hits=[];eventKinds.forEach(kind=>(S.data.map[kind]||[]).forEach((event,index)=>{if(Number(event.x)===p.x&&Number(event.y)===p.y)hits.push({kind,index});}));if(!hits.length)return null;const at=hits.findIndex(h=>h.kind===S.selected?.kind&&h.index===S.selected.index);return hits[(at+1)%hits.length];}
  function eventValue() { return S.selected ? S.data?.map[S.selected.kind]?.[S.selected.index] : null; }
  function newEvent(p) {
    const type=$('#event-add-type').value; let kind=type,event={x:p.x,y:p.y,elevation:3};
    if(type==='object_events')Object.assign(event,{graphics_id:S.object,movement_type:'MOVEMENT_TYPE_FACE_DOWN',movement_range_x:0,movement_range_y:0,trainer_type:'TRAINER_TYPE_NONE',trainer_sight_or_berry_tree_id:'0',script:'0x0',flag:'0'});
    else if(type==='warp_events')Object.assign(event,{elevation:0,dest_map:S.data.map.id,dest_warp_id:'0'});
    else if(type==='coord_events')Object.assign(event,{type:'trigger',var:'VAR_TEMP_0',var_value:'0',script:'0x0'});
    else if(type==='weather'){kind='coord_events';Object.assign(event,{type:'weather',weather:'COORD_EVENT_WEATHER_SUNNY'});}
    else if(type==='hidden_item'){kind='bg_events';Object.assign(event,{type:'hidden_item',item:'ITEM_POTION',flag:'FLAG_HIDDEN_ITEM_ROUTE_111_STARDUST'});}
    else if(type==='secret_base'){kind='bg_events';Object.assign(event,{type:'secret_base',elevation:0,secret_base_id:'SECRET_BASE_RED_CAVE1_1'});}
    else Object.assign(event,{type:'sign',elevation:0,player_facing_dir:'BG_EVENT_PLAYER_FACING_ANY',script:'0x0'});
    edit(()=>{S.data.map[kind].push(event);S.selected={kind,index:S.data.map[kind].length-1};},{events:true});
    S.place=false;syncPlace();tab('events');renderEvents();
    if(type==='hidden_item')toast('Hidden item placed. Give it a unique hidden-item flag in its properties.');
    else if(type==='secret_base')toast('Secret base placed. Choose its correct secret-base ID in properties.');
    else if(type==='warp_events')toast('Warp placed. Set its destination map and destination warp index.');
  }
  canvas.addEventListener('pointerdown',event=>{
    if(!S.data||S.busy)return;
    if(S.space||event.button===1){startPan(event);return;}
    const p=position(event);if(!p)return;event.preventDefault();canvas.setPointerCapture(event.pointerId);
    if(event.button===2||S.tool==='picker'&&S.mode!=='events'){pick(p);return;}
    if(event.button!==0)return;
    if(S.mode==='events'){
      if(S.place){newEvent(p);return;}S.selected=eventAt(p);renderEvents();tab('events');render();
      if(S.selected)S.drag={event:true,before:snapshot(),start:p,last:p};return;
    }
    S.drag={before:snapshot(),start:p,last:p,rectangle:S.tool==='rectangle'};
    if(S.tool==='fill'){flood(p);finishStroke();}else if(!S.drag.rectangle)paintAt(p);render();
  });
  canvas.addEventListener('pointermove',event=>{
    if(S.drag?.pan){movePan(event);return;}
    const p=position(event,!!S.drag);hover(p);if(!S.drag||!p||S.busy)return;
    if(S.drag.event){const selected=eventValue();if(selected){selected.x=p.x;selected.y=p.y;}S.drag.last=p;render();return;}
    if(!S.drag.rectangle)line(S.drag.last,p);S.drag.last=p;render();
  });
  function finishStroke() {
    if(!S.drag)return;const drag=S.drag;S.drag=null;
    if(drag.pan){viewport.classList.remove('panning');return;}
    if(drag.rectangle){for(let y=Math.min(drag.start.y,drag.last.y);y<=Math.max(drag.start.y,drag.last.y);y++)for(let x=Math.min(drag.start.x,drag.last.x);x<=Math.max(drag.start.x,drag.last.x);x++)paintAt({x,y});}
    record(drag.before);render();if(drag.event)renderEvents();
  }
  canvas.addEventListener('pointerup',finishStroke);canvas.addEventListener('pointercancel',finishStroke);canvas.addEventListener('lostpointercapture',finishStroke);
  canvas.addEventListener('pointerleave',()=>{if(!S.drag)hover(null);});canvas.addEventListener('contextmenu',e=>e.preventDefault());
  function startPan(event){event.preventDefault();S.drag={pan:true,clientX:event.clientX,clientY:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.classList.add('panning');canvas.setPointerCapture(event.pointerId);}
  function movePan(event){viewport.scrollLeft=S.drag.left-(event.clientX-S.drag.clientX);viewport.scrollTop=S.drag.top-(event.clientY-S.drag.clientY);}
  viewport.addEventListener('pointerdown',e=>{if(e.target!==canvas&&(S.space||e.button===1))startPan(e);});
  function zoom(amount) { if(!S.data)return;const previous=S.zoom;S.zoom=Math.max(.5,Math.min(6,amount));render();requestAnimationFrame(()=>{viewport.scrollLeft=(viewport.scrollLeft+viewport.clientWidth/2)*S.zoom/previous-viewport.clientWidth/2;viewport.scrollTop=(viewport.scrollTop+viewport.clientHeight/2)*S.zoom/previous-viewport.clientHeight/2;}); }
  viewport.addEventListener('wheel',e=>{if(e.ctrlKey){e.preventDefault();zoom(S.zoom+(e.deltaY<0?.25:-.25));}},{passive:false});
  function mode(value){S.mode=value;S.place=false;$$('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===value));$('#paint-tools').hidden=value==='events';$('#event-tools').hidden=value!=='events';$('#movement-options').hidden=value!=='collision';$('#terrain-options').hidden=value!=='terrain';if(value==='events')tab('events');syncPlace();renderTileSelection();render();}
  function tab(value){S.tab=value;$$('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===value);b.setAttribute('aria-selected',String(b.dataset.tab===value));});$$('.inspector-panel').forEach(panel=>panel.hidden=panel.id!==`panel-${value}`);if(value==='map')renderProperties();if(value==='events')renderEvents();}
  function tool(value){S.tool=value;$$('[data-tool]').forEach(b=>b.classList.toggle('active',b.dataset.tool===value));$('#tool-hint').textContent={brush:'Paint tiles to reshape your world. Right-click to pick.',erase:'Paint with your chosen eraser tile. Set it in the Tiles panel.',fill:'Fill a connected area of matching tiles.',rectangle:'Drag a rectangle to paint a whole area.',picker:'Click the map to select an existing tile.'}[value];renderTileSelection();}
  function syncPlace(){$('#place-event').classList.toggle('selected',S.place);$('#select-event').classList.toggle('selected',!S.place);$('#selection-info').textContent=S.place?'Click map to place event':'Select or drag an event';render();}

  function field(label,key,value,options={}) {
    const wrap=node('label',`field${options.wide?' wide':''}`);wrap.append(node('span','',label));
    const input=node(options.select?'select':'input',options.mono?'mono':'');input.name=key;input.dataset.field=key;input.id=`world-field-${++fieldId}`;wrap.htmlFor=input.id;input.setAttribute('aria-label',label);
    if(options.select){options.select.forEach(v=>{const option=node('option','',Array.isArray(v)?v[1]:v);option.value=Array.isArray(v)?v[0]:v;input.append(option);});}
    else {input.type=options.type||'text';if(options.min!==undefined)input.min=options.min;if(options.max!==undefined)input.max=options.max;if(options.list)input.setAttribute('list',options.list);}
    input.value=value??'';wrap.append(input);if(options.hint)wrap.append(node('small','',options.hint));return wrap;
  }
  function eventDescription(event,kind){return kind==='object_events'?nice(event.graphics_id).toLowerCase():kind==='warp_events'?nice(event.dest_map):event.type==='hidden_item'?nice(event.item).toLowerCase():event.type==='secret_base'?'Secret base entrance':event.type==='weather'?'Weather trigger':event.script||event.type||kindNames[kind];}
  function renderEvents(){
    if(!S.data)return;const list=$('#event-list'),frag=document.createDocumentFragment();let count=0;
    eventKinds.forEach(kind=>(S.data.map[kind]||[]).forEach((event,index)=>{count++;const b=node('button',`event-row${S.selected?.kind===kind&&S.selected.index===index?' active':''}`);b.type='button';
      b.innerHTML=`<span class="event-tag ${kind}">${kindLetters[kind]}</span><span class="event-row-copy"><strong>${esc(eventDescription(event,kind))}</strong><small>${esc(kindNames[kind])} ${index} · ${event.x}, ${event.y}</small></span>`;
      b.onclick=()=>{S.selected={kind,index};S.place=false;mode('events');renderEvents();render();};frag.append(b);}));
    list.replaceChildren(frag);$('#event-count').textContent=count;if(!count)list.append(node('div','list-empty','No events yet. Use + Place to add one.'));
    const container=$('#event-inspector'),event=eventValue();container.replaceChildren();
    if(!event){const empty=node('div','event-empty','Select an event on the map or in the list below.');container.append(empty);return;}
    const {kind,index}=S.selected,form=node('div','event-form'),heading=node('div','event-form-title');heading.append(node('strong','',`${kindNames[kind]} ${index}`));const actions=node('div','event-actions');
    const duplicate=node('button','button small','Duplicate'),remove=node('button','button small danger','Delete');duplicate.onclick=duplicateEvent;remove.onclick=deleteEvent;actions.append(duplicate,remove);heading.append(actions);form.append(heading);
    const grid=node('div','fields-grid');grid.append(field('X position','x',event.x,{type:'number',min:0,max:S.data.width-1}),field('Y position','y',event.y,{type:'number',min:0,max:S.data.height-1}),field('Elevation','elevation',event.elevation,{type:'number',min:0,max:15}));
    if(kind==='object_events'){
      grid.append(field('Sprite','graphics_id',event.graphics_id,{wide:true,mono:true,list:'object-ids'}),field('Movement','movement_type',event.movement_type,{wide:true,mono:true}),field('Wander range X','movement_range_x',event.movement_range_x,{type:'number',min:0,max:15}),field('Wander range Y','movement_range_y',event.movement_range_y,{type:'number',min:0,max:15}),field('Script to run','script',event.script,{wide:true,mono:true}),field('Hide flag','flag',event.flag,{wide:true,mono:true}),field('Trainer type','trainer_type',event.trainer_type,{wide:true,mono:true}),field('Sight range / berry ID','trainer_sight_or_berry_tree_id',event.trainer_sight_or_berry_tree_id,{wide:true,mono:true}));
      if(event.local_id!==undefined)grid.append(field('Local identifier','local_id',event.local_id,{wide:true,mono:true}));
    }else if(kind==='warp_events'){
      grid.append(field('Destination warp index','dest_warp_id',event.dest_warp_id,{hint:'Zero-based index in the destination map.'}),field('Destination map','dest_map',event.dest_map,{wide:true,mono:true,list:'map-ids'}));
    }else if(event.type==='hidden_item'){grid.append(field('Item','item',event.item,{wide:true,mono:true}),field('Unique collected flag','flag',event.flag,{wide:true,mono:true,hint:'Use a unique hidden-item flag so another item does not disappear.'}));}
    else if(event.type==='secret_base'){grid.append(field('Secret base ID','secret_base_id',event.secret_base_id,{wide:true,mono:true}));}
    else if(event.type==='weather'){grid.append(field('Weather constant','weather',event.weather,{wide:true,mono:true}));}
    else {if(kind==='coord_events')grid.append(field('Trigger variable','var',event.var,{wide:true,mono:true}),field('Required value','var_value',event.var_value,{wide:true,mono:true}));if(kind==='bg_events')grid.append(field('Player facing direction','player_facing_dir',event.player_facing_dir,{wide:true,mono:true}));grid.append(field('Script to run','script',event.script,{wide:true,mono:true}));}
    $$('input,select',grid).forEach(input=>input.addEventListener('change',()=>{if(!input.reportValidity())return;const key=input.dataset.field;const val=input.type==='number'?Number(input.value):input.value.trim();edit(()=>{eventValue()[key]=val;});renderEventListOnly();}));form.append(grid);
    if(kind==='warp_events'){const destination=node('button','text-button inline-link','Open destination map →');destination.onclick=()=>{const match=S.maps.find(m=>m.id===eventValue().dest_map);if(match)openMap(match.name);else toast('Choose a valid destination map first.',true);};form.append(destination);}
    if(kind==='object_events'){
      const conversation=node('button','button conversation-button','Write conversation');conversation.type='button';conversation.onclick=writeConversation;form.append(conversation);
      form.append(node('p','form-help','Write what this person says when the player talks to them. Creates a new conversation script.'));
    }
    if(event.script!==undefined){const story=node('a','inline-link','Edit this map’s story & dialogue ↗');story.href=`/campaign?map=${encodeURIComponent(S.data.name)}`;form.append(story);}
    const details=node('details','event-json'),summary=node('summary','','Advanced event JSON');details.append(summary);const textarea=node('textarea');textarea.value=JSON.stringify(event,null,2);textarea.setAttribute('aria-label','Advanced event JSON');details.append(textarea);const apply=node('button','button small','Apply event JSON');apply.onclick=()=>{try{const parsed=JSON.parse(textarea.value);if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw new Error('Use a JSON object.');if(!Number.isInteger(parsed.x)||!Number.isInteger(parsed.y)||parsed.x<0||parsed.y<0||parsed.x>=S.data.width||parsed.y>=S.data.height)throw new Error('Event coordinates must be inside this map.');edit(()=>{S.data.map[kind][index]=parsed;},{events:true});toast('Event properties applied. Save map to write them.');}catch(err){toast(err.message,true);}};details.append(apply);form.append(details);container.append(form);
  }
  function renderEventListOnly(){const active=document.activeElement;const start=active?.selectionStart;renderEvents();const replacement=active?.dataset.field?$(`[data-field="${active.dataset.field}"]`,$('#event-inspector')):null;if(replacement){replacement.focus();if(replacement.type==='text'&&start!==null)replacement.setSelectionRange(start,start);}}
  async function writeConversation(){
    if(S.busy||S.selected?.kind!=='object_events'||!eventValue())return;
    const chosen=clone(S.selected),mapName=S.data.name,owner=S.data.events_owner||mapName;
    let label='',text='',lastError='';
    try{
      S.busy=true;status();
      let story=await api(`/api/campaign/story?map=${encodeURIComponent(owner)}`);
      let number=1;while(story.content.includes(`${owner}_EventScript_CustomNPC${number}:`))number++;
      label=`${owner}_EventScript_CustomNPC${number}`;
      for(;;){
        const values=await dialog('Write a conversation',`<p>Give this person something to say when the player talks to them. This creates a new script; save the map afterward to attach it to the person.</p>${lastError?`<p class="dialog-error" role="alert">${esc(lastError)}</p>`:''}<div class="fields-grid"><label class="field wide" for="conversation-label">Script label<input id="conversation-label" name="label" aria-label="Script label" pattern="[A-Za-z_][A-Za-z_0-9]*" required><small>A unique name starting with ${esc(owner)}_EventScript_.</small></label><label class="field wide" for="conversation-text">Conversation text<textarea id="conversation-text" name="text" aria-label="Conversation text" maxlength="16000" required placeholder="Welcome to our town!"></textarea><small>Enter line breaks for new lines. Keep lines short for the game’s dialogue box. Use \\p for a new page. Avoid double quotes.</small></label></div>`,'Create conversation',{setup:root=>{$('[name=label]',root).value=label;$('[name=text]',root).value=text;}});
        if(!values)return;label=values.label.trim();text=values.text;
        if(!label.startsWith(owner+'_EventScript_')){lastError=`The script label must start with ${owner}_EventScript_.`;continue;}
        const preflight=await api(`/api/world/map?name=${encodeURIComponent(mapName)}`);
        if(preflight.revision!==S.data.revision)throw new Error('The map changed in another editor. Your local edits are still here. Reload or resolve those changes before creating this conversation.');
        let created;
        try{created=await api('/api/campaign/dialogue',{map:owner,label,text,revision:story.revision});}
        catch(err){lastError=err.message;story=await api(`/api/campaign/story?map=${encodeURIComponent(owner)}`);continue;}
        const latest=await api(`/api/world/map?name=${encodeURIComponent(mapName)}`);
        const mapContent=value=>JSON.stringify({name:value.name,width:value.width,height:value.height,cells:value.cells,border:value.border,map:value.map,layout:value.layout});
        const unchanged=mapContent(preflight)===mapContent(latest);
        const onlyAppend=created.source===story.source&&typeof created.content==='string'&&created.content.startsWith(story.content)&&created.content.slice(story.content.length).includes(label+'::');
        // A story write must never bless a concurrent map edit as the new save baseline.
        if(unchanged&&onlyAppend)S.data.revision=latest.revision;
        const before=snapshot();S.data.map[chosen.kind][chosen.index].script=label;S.selected=chosen;record(before);renderEvents();render();
        if(!unchanged||!onlyAppend){error('The conversation was created and attached to your pending edits, but the source changed concurrently. Your local map edits are preserved; resolve the map conflict before saving.');toast('Conversation created. Resolve the map conflict before saving.',true);}
        else toast('Conversation created. Save map to attach it to this person.');
        return;
      }
    }catch(err){error(err.message);toast(err.message,true);}finally{S.busy=false;status();}
  }
  function duplicateEvent(){const event=eventValue();if(!event)return;const selected=clone(S.selected);edit(()=>{const copy=clone(event);delete copy.local_id;copy.x=Math.min(S.data.width-1,Number(copy.x)+1);S.data.map[selected.kind].push(copy);S.selected={kind:selected.kind,index:S.data.map[selected.kind].length-1};},{events:true});toast('Event duplicated. Check its script, flags, and destination before saving.');}
  async function deleteEvent(){if(!eventValue())return;const chosen=clone(S.selected),event=eventValue();const warning=chosen.kind==='warp_events'?'Deleting a warp changes the indexes of following warps. Update incoming warps in other maps that point to those indexes.':chosen.kind==='object_events'?'Story scripts may refer to this object by its local ID or position in the object list. Check related scripts after deleting it.':'Related story scripts may refer to this event.';
    if(!await dialog(`Delete this ${kindNames[chosen.kind].toLowerCase()}?`,`<p>${esc(eventDescription(event,chosen.kind))}</p><p>${esc(warning)} You can undo this before saving.</p>`,'Delete event',{danger:true}))return;
    edit(()=>{S.data.map[chosen.kind].splice(chosen.index,1);S.selected=null;},{events:true});}

  function renderProperties(){
    if(!S.data)return;const container=$('#map-properties'),frag=document.createDocumentFragment();
    const size=node('section','settings-section');size.innerHTML='<h4 class="settings-title">MAP DIMENSIONS</h4>';const grid=node('div','fields-grid');grid.append(field('Width (tiles)','width',S.data.width,{type:'number',min:1,max:255}),field('Height (tiles)','height',S.data.height,{type:'number',min:1,max:255}));size.append(grid);const resize=node('button','button','Resize map');resize.onclick=()=>resizeMap(Number($('[name=width]',grid).value),Number($('[name=height]',grid).value));size.append(resize,node('p','form-help','Expanded areas use your eraser tile. Shrinking crops the right and bottom edges.'));
    if(S.data.shared_with?.length)size.append(node('div','shared-warning',`Shared layout: ${S.data.shared_with.join(', ')}. Terrain, border, and size edits affect those maps too.`));
    if(S.data.events_shared_with?.length)size.append(node('div','shared-warning',`Shared events: ${S.data.events_shared_with.join(', ')}. Event edits are saved to ${S.data.events_owner || 'the shared event owner'}.`));frag.append(size);
    const artwork=node('section','settings-section');artwork.innerHTML='<h4 class="settings-title">LANDSCAPE ARTWORK</h4>';artwork.append(node('p','form-help','Each map combines a primary and secondary tileset. Choose a different pair to use artwork from anywhere in the game.'));
    const artFields=node('div','fields-grid');artFields.append(field('Primary tileset','primary_tileset',S.data.layout.primary_tileset,{wide:true,select:S.tilesets.filter(t=>!t.secondary).map(t=>[t.id,t.name])}),field('Secondary tileset','secondary_tileset',S.data.layout.secondary_tileset,{wide:true,select:S.tilesets.filter(t=>t.secondary).map(t=>[t.id,t.name])}));artwork.append(artFields);const switchArt=node('button','button','Change tilesets…');switchArt.onclick=()=>changeTilesets($('[name=primary_tileset]',artFields).value,$('[name=secondary_tileset]',artFields).value);artwork.append(switchArt);frag.append(artwork);
    const properties=node('section','settings-section');properties.innerHTML='<h4 class="settings-title">ATMOSPHERE & RULES</h4>';const fields=node('div','fields-grid');
    [['Music','music'],['Weather','weather'],['Map type','map_type'],['Region map section','region_map_section'],['Battle background','battle_scene']].forEach(([label,key])=>fields.append(field(label,key,S.data.map[key],{wide:true,mono:true})));
    $$('input',fields).forEach(input=>input.onchange=()=>edit(()=>{S.data.map[input.name]=input.value.trim();}));properties.append(fields);const checks=node('div','check-grid');
    [['Running','allow_running'],['Cycling','allow_cycling'],['Escape rope','allow_escaping'],['Display map name','show_map_name'],['Needs Flash','requires_flash']].forEach(([label,key])=>{const wrap=node('label');const input=node('input');input.type='checkbox';input.checked=!!S.data.map[key];input.id=`world-setting-${key}`;wrap.htmlFor=input.id;input.setAttribute('aria-label',label);input.onchange=()=>edit(()=>{S.data.map[key]=input.checked;});wrap.append(input,document.createTextNode(label));checks.append(wrap);});properties.append(checks);frag.append(properties);
    const border=node('section','settings-section');border.innerHTML='<h4 class="settings-title">OUTSIDE THE MAP</h4>';border.append(node('p','form-help','Click a border square to replace it with your selected tile. This pattern repeats beyond the playable edges.'));const borderGrid=node('div','border-grid');borderGrid.style.gridTemplateColumns=`repeat(${S.data.border_width||2},40px)`;
    (S.data.border||[]).forEach((cell,i)=>{const b=node('button');b.title=`Border tile ${i}: ${cell&1023}. Click to use tile ${S.tile}.`;b.append(tileCanvas(cell&1023));b.onclick=()=>edit(()=>{S.data.border[i]=(cell&0xfc00)|S.tile;},{refresh:true});borderGrid.append(b);});border.append(borderGrid);frag.append(border);
    const connections=node('section','settings-section');connections.innerHTML='<h4 class="settings-title">NEIGHBORING MAPS</h4>';const connectLink=node('a','button','Connect places visually →');connectLink.href=`/connections?map=${encodeURIComponent(S.data.name)}`;connections.append(connectLink,node('p','form-help','Use Connect places to choose both ends and save the return connection automatically. The advanced fields below change only this map. Save any pending edits before switching.'));
    (S.data.map.connections||[]).forEach((connection,i)=>{const card=node('div','connection'),head=node('div','connection-head');const direction=field('Direction',`direction-${i}`,connection.direction,{select:['up','down','left','right','dive','emerge']});head.append(direction);const remove=node('button','text-button','Remove');remove.onclick=()=>edit(()=>{S.data.map.connections.splice(i,1);},{refresh:true});head.append(remove);card.append(head);const destination=field('Neighbor map',`map-${i}`,connection.map,{mono:true,list:'map-ids'}),offset=field('Offset (tiles)',`offset-${i}`,connection.offset,{type:'number',min:-32768,max:32767});card.append(destination,offset);
      $('select',direction).onchange=e=>edit(()=>{connection.direction=e.target.value;});$('input',destination).onchange=e=>edit(()=>{connection.map=e.target.value.trim();});$('input',offset).onchange=e=>{if(e.target.reportValidity())edit(()=>{connection.offset=Number(e.target.value);});};
      const open=node('button','text-button inline-link','Open neighboring map →');open.onclick=()=>{const map=S.maps.find(m=>m.id===connection.map);if(map)openMap(map.name);else toast('Select a valid map identifier first.',true);};card.append(open);connections.append(card);});
    const add=node('button','button','＋ Add map connection');add.onclick=()=>edit(()=>{S.data.map.connections||=[];S.data.map.connections.push({map:S.maps.find(m=>m.name!==S.data.name)?.id||S.data.map.id,offset:0,direction:'up'});},{refresh:true});connections.append(add);frag.append(connections);
    const advanced=node('section','settings-section');advanced.innerHTML='<h4 class="settings-title">A FRESH START</h4>';advanced.append(node('p','form-help','Clear this location to your eraser tile, or create a new blank map with the current tilesets.'));
    const blank=node('button','button','Create a new blank map');blank.onclick=createMap;const clear=node('button','button danger','Clear this map…');clear.onclick=clearMap;const json=node('button','button','Advanced map JSON');json.onclick=mapJSON;advanced.append(blank,clear,json);frag.append(advanced);container.replaceChildren(frag);
  }
  async function resizeMap(width,height){
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>255||height>255||(width+15)*(height+14)>10240){toast('Use dimensions from 1–255 tiles within Emerald’s map buffer: (width + 15) × (height + 14) must be at most 10,240.',true);return;}
    if(width===S.data.width&&height===S.data.height)return;
    const outside=[];eventKinds.forEach(kind=>(S.data.map[kind]||[]).forEach(e=>{if(e.x>=width||e.y>=height)outside.push(e);}));
    if(outside.length){toast(`Move or delete ${outside.length} event${outside.length===1?'':'s'} outside the new boundaries before shrinking.`,true);return;}
    if((width<S.data.width||height<S.data.height)&&!await dialog('Crop the map?',`<p>Resize from ${S.data.width} × ${S.data.height} to ${width} × ${height} tiles. Tiles beyond the right and bottom boundaries will be removed. You can undo this before saving.</p>`,'Resize map'))return;
    edit(()=>{const cells=[];const blank=(3<<12)|S.background;for(let y=0;y<height;y++)for(let x=0;x<width;x++)cells.push(x<S.data.width&&y<S.data.height?S.data.cells[y*S.data.width+x]:blank);S.data.width=width;S.data.height=height;S.data.cells=cells;},{refresh:true});toast(`Map resized to ${width} × ${height} tiles.`);
  }
  async function changeTilesets(primary,secondary){
    if(!S.data||S.busy||primary===S.data.layout.primary_tileset&&secondary===S.data.layout.secondary_tileset)return;
    S.busy=true;status();error('');
    try{
      const preview=await api(`/api/world/map?name=${encodeURIComponent(S.data.name)}&primary=${encodeURIComponent(primary)}&secondary=${encodeURIComponent(secondary)}`);
      const image=await loadImage(preview.tileset.atlas_url),valid=new Set(preview.tileset.metatiles.filter(t=>t.valid!==false).map(t=>t.id));
      const missing=[...S.data.cells,...S.data.border].filter(cell=>!valid.has(cell&1023)).length;
      const fallback=valid.has(S.background)?S.background:preview.tileset.metatiles.find(t=>t.valid!==false&&/NORMAL/.test(t.behavior_name||''))?.id??[...valid][0];
      const answer=await dialog('Change the landscape artwork?',`<p>Existing tile numbers will use the selected tilesets’ artwork. Buildings, paths, and other features may look different.</p>${missing?`<p>${missing} tiles do not exist in this pair and will be replaced with tile ${fallback}.</p>`:''}<p>You can undo the change before saving.</p>`,'Change tilesets');
      if(!answer)return;const before=snapshot();S.data.layout.primary_tileset=primary;S.data.layout.secondary_tileset=secondary;S.atlases.set(`${primary}|${secondary}`,{image,metadata:preview.tileset});S.atlas=image;S.data.tileset=preview.tileset;
      S.data.cells=S.data.cells.map(cell=>valid.has(cell&1023)?cell:(cell&0xfc00)|fallback);S.data.border=S.data.border.map(cell=>valid.has(cell&1023)?cell:(cell&0xfc00)|fallback);
      if(!valid.has(S.tile))S.tile=fallback;if(!valid.has(S.background))S.background=fallback;record(before);refresh();toast('Tilesets changed. Your map now uses the selected landscape artwork.');
    }catch(err){error(err.message);toast(err.message,true);}finally{S.busy=false;status();}
  }
  async function clearMap(){if(!S.data)return;const values=await dialog('Start with a blank canvas?',`<p>Replace every terrain tile in ${esc(nice(S.data.name))} with eraser tile ${S.background}. Collision becomes passable and elevation becomes 3. The map keeps its size.</p><label class="compact-check"><input name="clear_events" type="checkbox"> Also remove this map’s events and connections</label><p class="form-help">Warps and scripts in other maps may still refer to this location. Review them when rebuilding. You can undo this change before saving.</p>`,'Clear map',{danger:true});if(!values)return;
    edit(()=>{S.data.cells.fill((3<<12)|S.background);if(values.clear_events){eventKinds.forEach(kind=>S.data.map[kind]=[]);S.data.map.connections=null;S.selected=null;}},{refresh:true});toast('Blank canvas ready. Choose tiles and start building.');}
  async function mapJSON(){if(!S.data)return;const result=await dialog('Advanced map properties','<p>Edit the map’s full event and property data. Keep its id, name, and layout unchanged.</p><textarea name="map_json" aria-label="Map JSON" spellcheck="false"></textarea>','Apply JSON',{setup:root=>{$('textarea',root).value=JSON.stringify(S.data.map,null,2);}});if(!result)return;
    try{const parsed=JSON.parse(result.map_json);if(!parsed||Array.isArray(parsed))throw new Error('Expected a map JSON object.');for(const key of ['name','id','layout'])if(parsed[key]!==S.data.map[key])throw new Error(`Keep the ${key} unchanged. Create a new map for a new identity.`);eventKinds.forEach(kind=>{if(!Array.isArray(parsed[kind]))throw new Error(`${kind} must be an array.`);});edit(()=>{S.data.map=parsed;S.selected=null;},{refresh:true});toast('Map JSON applied. Save to write the source files.');}catch(err){toast(err.message,true);}}
  async function createMap(){
    if(S.busy)return;
    const options=S.maps.map(m=>`<option value="${esc(m.name)}"${m.name===(S.data?.name||'LittlerootTown')?' selected':''}>${esc(nice(m.name))}</option>`).join('');
    const values=await dialog('A new corner of your world',`<p>Start with blank land, using the artwork and atmosphere from an existing map. Your new map has its own layout and no events or connections.</p><div class="fields-grid"><label class="field wide" for="new-map-name">Map name<input id="new-map-name" aria-label="Map name" name="name" placeholder="MyNewTown" pattern="[A-Z][A-Za-z0-9_]{0,63}" maxlength="64" required><small>Start with a capital letter. Use letters, numbers, and underscores.</small></label><label class="field wide" for="new-map-template">Tileset template<select id="new-map-template" aria-label="Tileset template" name="template">${options}</select></label><label class="field" for="new-map-width">Width<input id="new-map-width" aria-label="Width" name="width" type="number" min="1" max="255" value="30" required></label><label class="field" for="new-map-height">Height<input id="new-map-height" aria-label="Height" name="height" type="number" min="1" max="255" value="30" required></label></div><p class="form-help">Connect it to another location using a warp or map connection so players can reach it. Creating a map saves its initial source files.</p>`,'Create blank map');
    if(!values)return;if(!await discardIfNeeded())return;
    const width=Number(values.width),height=Number(values.height);if((width+15)*(height+14)>10240){toast('That map is too large for Emerald’s map buffer. Try smaller dimensions.',true);return;}
    S.busy=true;status();$('#loading').hidden=false;error('');
    try{
      const created=await api('/api/world/new',{name:values.name,template:values.template,width,height,blank:true});
      const data=created.map?.cells?created.map:created.cells?created:await api(`/api/world/map?name=${encodeURIComponent(values.name)}`);
      const [atlas,maps,areas]=await Promise.all([loadImage(data.tileset.atlas_url),api('/api/world/maps'),api('/api/areas')]);
      S.maps=maps.maps;S.tilesets=maps.tilesets||S.tilesets;populateMapIds();applyAreas(areas,data.name);
      adoptMap(data,atlas);history.replaceState(null,'',`/world?map=${encodeURIComponent(data.name)}`);toast(`Created ${nice(data.name)}. Your blank map is ready.`);
    }
    catch(err){error(err.message);toast(err.message,true);}finally{S.busy=false;$('#loading').hidden=true;status();}
  }
  async function save(){
    if(!S.data||!S.dirty||S.busy)return;finishStroke();let confirmShared=false;
    const shared=[...(S.data.shared_with||[]),...(S.data.events_shared_with||[])];
    if(shared.length){const result=await dialog('Save shared map data?',`<p>This location shares its layout or events with: ${esc([...new Set(shared)].join(', '))}.</p><p>Changes to shared data affect those locations too. Other map properties stay local.</p><label class="compact-check"><input type="checkbox" name="confirmed" required> I want to update the shared data.</label>`,'Save shared changes');if(!result)return;confirmShared=true;}
    S.busy=true;status();error('');
    try{const data=await api('/api/world/map',{name:S.data.name,revision:S.data.revision,...snapshot(),confirm_shared:confirmShared});const fresh=data.cells?data:data.map?.cells?data.map:await api(`/api/world/map?name=${encodeURIComponent(S.data.name)}`);S.data=fresh;eventKinds.forEach(kind=>{S.data.map[kind]||=[];});S.saved=fingerprint();S.history=[];S.future=[];const map=S.maps.find(m=>m.name===S.data.name);if(map){map.width=S.data.width;map.height=S.data.height;}refresh();toast(data.message||'Map saved. Terrain, events, and properties are written to your source project.');}
    catch(err){error(err.message);toast(err.message,true);}finally{S.busy=false;status();}
  }
  function populateMapIds(){$('#map-ids').replaceChildren(...S.maps.map(m=>{const o=node('option');o.value=m.id;o.label=nice(m.name);return o;}));}

  async function savedHistory(){
    if(S.busy)return;
    try{
      const result=await api('/api/transactions'),transactions=result.transactions;
      if(!transactions.length){await dialog('Your saved history','<p>Your map and campaign saves will appear here. You can restore the source files from before a save, including terrain and other binary files.</p>','Got it',{info:true});return;}
      const values=await dialog('Restore a saved edit',`<p>Choose a saved edit to restore all its source files to their previous versions. Restore newer edits first when they changed the same files.</p><label class="field">Saved edit<select name="transaction">${transactions.map(t=>`<option value="${esc(t.id)}">${esc(t.label)} · ${t.files.length} files</option>`).join('')}</select></label><div id="restore-files" class="restore-files"></div><p class="form-help">Restoring a “New blank map” edit removes that newly created map. Restoring an “Undo” edit reapplies the saved change.</p>`,'Restore previous files',{setup:root=>{const update=()=>{const transaction=transactions.find(t=>t.id===$('select',root).value);$('#restore-files',root).replaceChildren(...transaction.files.map(path=>node('div','',path)));};$('select',root).onchange=update;update();}});
      if(!values||!await discardIfNeeded())return;
      S.busy=true;status();const restored=await api('/api/transaction/undo',{id:values.transaction});
      const [maps,areas]=await Promise.all([api('/api/world/maps'),api('/api/areas')]);S.maps=maps.maps;S.tilesets=maps.tilesets||S.tilesets;populateMapIds();
      const current=S.maps.some(m=>m.name===S.data?.name)?S.data.name:'LittlerootTown';applyAreas(areas,current);
      S.busy=false;S.dirty=false;await openMap(current,true);toast(restored.message||'Previous source files restored.');
    }catch(err){toast(err.message,true);error(err.message);}finally{S.busy=false;status();}
  }

  $('#save').onclick=save;$('#undo').onclick=undo;$('#redo').onclick=redo;
  $('#saved-history').onclick=savedHistory;
  $('#revert').onclick=async()=>{if(await dialog('Discard unsaved changes?','<p>Reload the last saved version of this map. Your current unsaved changes will be discarded.</p>','Discard changes',{danger:true}))openMap(S.data.name,true);};
  $('#new-map').onclick=createMap;$('#map-search').oninput=renderMapList;
  $('#area-select').onchange=()=>{S.area=$('#area-select').value;$('#map-search').value='';renderMapList();};
  $$('[data-map-filter]').forEach(b=>b.onclick=()=>{S.mapFilter=b.dataset.mapFilter;$$('[data-map-filter]').forEach(x=>x.classList.toggle('active',x===b));renderMapList();});
  $$('[data-mode]').forEach(b=>b.onclick=()=>mode(b.dataset.mode));$$('[data-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$$('[data-tool]').forEach(b=>b.onclick=()=>tool(b.dataset.tool));
  $('#show-grid').onchange=render;$('#show-events').onchange=render;$('#tile-filter').onchange=renderPalette;$('#tile-search').oninput=renderPalette;$('#object-search').oninput=renderObjects;
  $('#set-background').onclick=()=>{S.background=S.tile;renderTileSelection();toast(`Eraser and blank areas now use tile ${S.background}.`);};
  $('#zoom-out').onclick=()=>zoom(S.zoom-.25);$('#zoom-in').onclick=()=>zoom(S.zoom+.25);$('#zoom-fit').onclick=()=>{if(S.data)zoom(Math.max(.5,Math.min(4,Math.floor(Math.min((viewport.clientWidth-84)/(S.data.width*16),(viewport.clientHeight-100)/(S.data.height*16))*4)/4)));};
  $('#place-event').onclick=()=>{S.place=true;syncPlace();toast('Click a map tile to place the selected event.');};$('#select-event').onclick=()=>{S.place=false;syncPlace();};
  $('#help').onclick=()=>dialog('A world, one tile at a time',`<p>Landscape features are made from 16 × 16 pixel tiles. People and props are objects; doors and story interactions are events.</p><ul class="tip-list"><li><kbd>B</kbd> Paint, <kbd>E</kbd> erase, <kbd>F</kbd> fill, <kbd>R</kbd> rectangle, <kbd>I</kbd> pick.</li><li>Right-click a tile to pick its artwork and movement values.</li><li><kbd>Ctrl Z</kbd> undo, <kbd>Ctrl Shift Z</kbd> redo, <kbd>Ctrl S</kbd> save.</li><li>Hold <kbd>Space</kbd> and drag to pan. <kbd>Ctrl</kbd> + scroll to zoom. Click the zoom percentage to fit the map.</li><li>In Movement, labels are collision / elevation. Collision 0 is passable; tile behavior can still restrict movement.</li><li>In Events, click to select and drag to move. Click a stacked marker again to cycle overlapping events.</li><li>To add an entire building, paint its roof, walls, and doorway tiles, then add a warp for its entrance.</li><li>Create new maps with the ＋ beside World studio. Connect them with warps and neighboring-map connections.</li><li>Story &amp; campaign edits starters, gym leaders, and scripts. Pokémon definitions remain preserved.</li><li>Saving updates source files. A playable ROM requires the separate build tools.</li></ul>`,'Got it',{info:true});
  document.addEventListener('keydown',event=>{
    const typing=/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)||event.target.isContentEditable;if($('#dialog').open)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();if(document.activeElement?.blur)document.activeElement.blur();save();return;}
    if(typing)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();event.shiftKey?redo():undo();return;}
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='y'){event.preventDefault();redo();return;}
    if(event.code==='Space'){event.preventDefault();S.space=true;viewport.classList.add('pan-ready');return;}
    if(event.key==='Escape'){S.place=false;S.selected=null;syncPlace();renderEvents();return;}
    if(event.key==='Delete'&&S.mode==='events'&&S.selected){event.preventDefault();deleteEvent();return;}
    const shortcut={b:'brush',e:'erase',f:'fill',r:'rectangle',i:'picker'}[event.key.toLowerCase()];if(shortcut&&!event.ctrlKey&&!event.metaKey){if(S.mode==='events')mode('terrain');tool(shortcut);}
  });
  document.addEventListener('keyup',event=>{if(event.code==='Space'){S.space=false;viewport.classList.remove('pan-ready');}});
  window.addEventListener('blur',()=>{S.space=false;viewport.classList.remove('pan-ready');finishStroke();});
  window.addEventListener('beforeunload',event=>{if(S.dirty){event.preventDefault();event.returnValue='';}});
  document.addEventListener('click',async event=>{const a=event.target.closest('a[href]');if(!a||!S.dirty||event.ctrlKey||event.metaKey||a.target==='_blank')return;if(a.origin===location.origin){event.preventDefault();if(await discardIfNeeded()){S.dirty=false;location.href=a.href;}}});
  async function init(){
    try{
      const [session,maps,objects,areas]=await Promise.all([api('/api/session'),api('/api/world/maps'),api('/api/world/objects'),api('/api/areas')]);
      S.token=session.token;S.maps=maps.maps;S.tilesets=maps.tilesets||[];S.objects=objects.objects;S.objectMap=new Map(S.objects.map(o=>[o.id,o]));
      const requested=new URLSearchParams(location.search).get('map');const requestedTab=new URLSearchParams(location.search).get('tab');const initial=S.maps.some(m=>m.name===requested)?requested:'LittlerootTown';
      applyAreas(areas,initial);populateMapIds();
      $('#object-ids').replaceChildren(...S.objects.map(o=>{const option=node('option');option.value=o.id;option.label=o.name||nice(o.id);return option;}));
      renderObjects();await openMap(initial,true);
      if(['tiles','objects','events','map'].includes(requestedTab))tab(requestedTab);
    }
    catch(err){$('#loading').hidden=true;$('#map-title').textContent='Your world needs another try';error(err.message);$('#save-status').textContent='Connection error';}
  }
  init();
})();
