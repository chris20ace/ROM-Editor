(() => {
  'use strict';
  if(window.parent!==window)return;
  // Retained same-origin editors share this page; hiding one never discards its form.
  const source = path => `/?path=${encodeURIComponent(path)}#files`;
  const mapQuery = name => encodeURIComponent(name || 'LittlerootTown');
  const tools = [
    {id:'story', group:'This place', label:'Story & dialogue', detail:'Conversations, events, puzzles and progression.', map:true, url:name=>`/campaign?tab=story&map=${mapQuery(name)}`},
    {id:'map', group:'This place', label:'Map settings & artwork', detail:'Dimensions, tilesets, music, weather and map properties.', map:true, url:name=>`/world?map=${mapQuery(name)}&tab=map`},
    {id:'connections', group:'This place', label:'Paths, doors & stairs', detail:'Connect outdoor edges and both ends of entrances.', map:true, url:name=>`/connections?map=${mapQuery(name)}`},
    {id:'areas', group:'This place', label:'Buildings & interiors', detail:'Organize rooms, floors and buildings; add new interiors.', map:true, url:name=>`/areas?map=${mapQuery(name)}`},
    {id:'starters', group:'Campaign', label:'Starter Pokémon', detail:'Choose the three Pokémon offered at the start.', url:()=>'/campaign?tab=starters'},
    {id:'trainers', group:'Campaign', label:'Trainers & gym leaders', detail:'Teams, levels, portraits, items and battle settings.', url:()=>'/campaign?tab=trainers'},
    {id:'player', group:'Campaign', label:'Character appearance', detail:'Edit players, trainers, gym leaders, NPCs, and objects.', url:()=>'/player'},
    {id:'region', group:'Campaign', label:'PokéNav region picture', detail:'Edit the in-game region artwork and named grid.', url:()=>'/region'},
    {id:'encounters', group:'Game systems', label:'Wild encounters', detail:'Edit the source table of encounters across all maps.', url:()=>source('src/data/wild_encounters.json')},
    {id:'items', group:'Game systems', label:'Items & economy', detail:'Edit item definitions; shops and rewards use scripts.', url:()=>source('src/data/items.h')},
    {id:'moves', group:'Game systems', label:'Moves & battles', detail:'Inspect and edit the game’s move records.', url:()=>'/#moves'},
    {id:'graphics', group:'Game systems', label:'Graphics & interface', detail:'Browse artwork and graphics source references.', url:()=>source('src/graphics.c')},
    {id:'audio', group:'Game systems', label:'Music & sound', detail:'Browse sound tracks and edit their source references.', url:()=>source('sound/song_table.inc')},
    {id:'source', group:'Game systems', label:'All source & logic', detail:'Advanced scripts, engine systems and all editable source files.', url:()=>'/#files'},
  ];
  const byId = new Map(tools.map(tool => [tool.id, tool]));
  const aliases = {settings:'map', mapsettings:'map', dialogue:'story', gyms:'trainers', campaign:'story', buildings:'areas'};
  const editors = new Map();
  const allowedPaths = new Set(['/', '/index.html', '/world', '/world.html', '/campaign', '/campaign.html', '/region', '/connections', '/areas', '/player', '/guide']);
  const state = {selected:null, active:null, sourceChanged:false, opening:false, closing:false, hooks:null, returnFocus:null};
  const node = (tag, className, text) => {const element=document.createElement(tag);if(className)element.className=className;if(text!==undefined)element.textContent=text;return element;};
  const nice = name => String(name || '').replace(/([a-z\d])([A-Z])/g,'$1 $2').replace(/_/g,' ');
  const launch = node('button','worldtools-launch','Edit story, settings & more');launch.type='button';launch.id='worldtools-open';launch.disabled=true;
  launch.setAttribute('aria-haspopup','dialog');
  const panel = node('dialog','worldtools-dialog');panel.id='worldtools-panel';panel.setAttribute('aria-labelledby','worldtools-title');
  const header=node('header','worldtools-header'), heading=node('div','worldtools-heading');
  const title=node('h2','','Your game editors');title.id='worldtools-title';
  const context=node('p','worldtools-context','Choose a place on the world, then open its editor.');
  const back=node('button','worldtools-back','← Back to world');back.type='button';
  heading.append(title,context);header.append(heading,back);
  const body=node('div','worldtools-body'),nav=node('nav','worldtools-nav');nav.setAttribute('aria-label','Game editors');
  let group='';
  const toolButtons=new Map();
  for(const tool of tools){
    if(tool.group!==group){group=tool.group;nav.append(node('h3','',group));}
    const button=node('button','worldtools-tool',tool.label);button.type='button';button.title=tool.detail;button.dataset.worldTool=tool.id;
    button.addEventListener('click',()=>open(tool.id));nav.append(button);toolButtons.set(tool.id,button);
  }
  const stage=node('section','worldtools-stage'), caption=node('div','worldtools-caption');
  const description=node('p','worldtools-description'),resumeLabel=node('label','worldtools-resume','Open editors');
  const resume=node('select');resume.setAttribute('aria-label','Resume an open editor');resumeLabel.append(resume);caption.append(description,resumeLabel);
  const frames=node('div','worldtools-frames'),message=node('p','worldtools-message');message.setAttribute('role','status');message.hidden=true;
  stage.append(caption,message,frames);body.append(nav,stage);
  const footer=node('footer','worldtools-footer','Save inside each editor to update source. Returning to the world keeps unsaved editor forms open. Pokémon species stay protected.');
  panel.append(header,body,footer);document.body.append(panel);
  (document.querySelector('.world-heading')||document.querySelector('.header')||document.body).append(launch);

  function report(text,bad=false){message.textContent=text;message.hidden=!text;message.classList.toggle('worldtools-error',bad);}
  function selection(name){state.selected=typeof name==='object'?name?.name:name;context.textContent=state.selected?`Selected on the world: ${nice(state.selected)}`:'Choose a place on the world to edit its local settings.';for(const tool of tools)toolButtons.get(tool.id).disabled=Boolean(tool.map&&!state.selected);}
  function currentSelection(){const selected=state.hooks?.getSelection?.();return typeof selected==='string'?selected:state.selected;}
  function pending(entry){
    try{const event=new entry.frame.contentWindow.Event('beforeunload',{cancelable:true});entry.frame.contentWindow.dispatchEvent(event);return event.defaultPrevented;}
    catch{return false;}
  }
  function updateResume(){
    const active=resume.value;resume.replaceChildren();
    for(const [key,entry] of editors){const option=node('option','',`${pending(entry)?'● ':''}${entry.label}`);option.value=key;resume.append(option);}
    resume.value=state.active?.key||active;resumeLabel.hidden=editors.size<2;
  }
  function activate(entry){
    state.active=entry;for(const item of editors.values())item.frame.hidden=item!==entry;
    for(const [id,button] of toolButtons){button.classList.toggle('active',id===entry.tool);button.setAttribute('aria-current',id===entry.tool?'true':'false');}
    title.textContent=entry.label;description.textContent=entry.detail;updateResume();
    report(entry.loaded?'':'Opening the editor…');
    if(!panel.open){state.returnFocus=document.activeElement;panel.showModal();}
    back.focus();
  }
  async function guard(){
    if(!state.hooks?.beforeOpen)return false;
    return await state.hooks.beforeOpen()===true;
  }
  async function open(toolId='story', options={}){
    const tool=byId.get(aliases[toolId]||toolId);if(!tool)return false;
    const selected=options.map||currentSelection();selection(currentSelection());
    if(tool.map&&!selected)return false;
    return openUrl(tool.url(selected), {tool:tool.id,label:tool.map?`${tool.label} · ${nice(selected)}`:tool.label,detail:tool.detail});
  }
  async function openUrl(href, info={}){
    if(state.opening||state.closing)return false;
    const url=new URL(href,location.origin);
    if(url.origin!==location.origin||!allowedPaths.has(url.pathname))return false;
    state.opening=true;launch.disabled=true;
    try{
      if(!await guard())return false;
      const key=url.pathname+url.search+url.hash;
      let entry=editors.get(key);
      if(!entry){
        const frame=node('iframe','worldtools-frame');frame.hidden=true;frame.title=info.label||'Source editor';
        entry={key,frame,tool:info.tool||'source',label:info.label||'Source editor',detail:info.detail||'Edit this part of your game without leaving the world.',loaded:false,mutations:new Set()};
        editors.set(key,entry);frame.addEventListener('load',()=>loaded(entry));frame.src=url.href;frames.append(frame);
      }
      activate(entry);return true;
    }catch(error){report(error.message,true);return false;}
    finally{state.opening=false;launch.disabled=!state.hooks;}
  }
  function mappedInfo(url){
    let id=url.pathname.startsWith('/campaign')?(url.searchParams.get('tab')||'story'):
      url.pathname.startsWith('/world')?'map':url.pathname==='/connections'?'connections':url.pathname==='/region'?'region':url.pathname==='/areas'?'areas':url.pathname==='/player'?'player':url.hash==='#moves'?'moves':'source';
    const tool=byId.get(id)||byId.get('source'), name=url.searchParams.get('map');
    return{tool:tool.id,label:`${tool.label}${name?' · '+nice(name):''}`,detail:tool.detail};
  }
  function intercept(event, embedded=false){
    if(event.defaultPrevented||event.button>0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const anchor=event.target.closest?.('a[href]');if(!anchor||anchor.hasAttribute('download')||anchor.target==='_blank')return;
    const url=new URL(anchor.href,location.origin);if(url.origin!==location.origin)return;
    if(url.pathname==='/worldmap'){
      event.preventDefault();event.stopImmediatePropagation();
      if(panel.open||embedded)returnToWorld(url.searchParams.get('map'));return;
    }
    if(!allowedPaths.has(url.pathname))return;
    // In-page source navigation keeps that editor's own unsaved-change guard.
    if(embedded&&anchor.getAttribute('href').startsWith('#'))return;
    event.preventDefault();event.stopImmediatePropagation();openUrl(url.href,mappedInfo(url));
  }
  function loaded(entry){
    try{
      const win=entry.frame.contentWindow,doc=win.document,url=new URL(win.location.href);
      if(url.origin!==location.origin)return;
      if(url.pathname==='/worldmap'){returnToWorld(url.searchParams.get('map'));return;}
      doc.body.classList.add('worldtools-embedded');
      doc.body.dataset.worldtoolsPage=url.pathname.split('/')[1]||'source';
      const style=doc.createElement('link');style.rel='stylesheet';style.href='/worldtools.css';doc.head.append(style);
      doc.addEventListener('click',event=>intercept(event,true),true);
      doc.addEventListener('input',updateResume);doc.addEventListener('change',updateResume);
      // Observe successful source saves; never change request bodies, tokens or responses.
      const fetch=win.fetch.bind(win);
      win.fetch=function(resource,init){
        const method=String(init?.method||resource?.method||'GET').toUpperCase();
        const requestUrl=new URL(typeof resource==='string'?resource:resource?.url||'',win.location.href);
        const mutation=method==='POST'&&requestUrl.origin===location.origin&&requestUrl.pathname.startsWith('/api/');
        const promise=fetch(resource,init);if(!mutation)return promise;
        entry.mutations.add(promise);
        promise.then(response=>{if(response.ok){state.sourceChanged=true;updateResume();}},()=>{}).finally(()=>entry.mutations.delete(promise));
        return promise;
      };
      entry.loaded=true;if(state.active===entry)report('');updateResume();
    }catch(error){if(state.active===entry)report(`Could not open this editor: ${error.message}`,true);}
  }
  async function close(preferredMap=null, forceRefresh=false){
    if(state.closing)return false;state.closing=true;back.disabled=true;
    try{
      await Promise.allSettled([...editors.values()].flatMap(entry=>[...entry.mutations]));
      const changed=state.sourceChanged||forceRefresh;
      const refreshed=await state.hooks?.onClose?.(preferredMap,{sourceChanged:changed});
      if(refreshed===false){report('The editor is kept open. Finish the pending world operation before returning.',true);return false;}
      state.sourceChanged=false;panel.close();selection(currentSelection());state.returnFocus?.focus?.();return true;
    }catch(error){report(`Could not refresh the world: ${error.message}`,true);return false;}
    finally{state.closing=false;back.disabled=false;}
  }
  function returnToWorld(name){return close(typeof name==='string'?name:null,true);}
  function configure(hooks){state.hooks=hooks;launch.disabled=false;selection(currentSelection());return window.WorldTools;}
  launch.addEventListener('click',()=>state.active?(byId.get(state.active.tool)?.map?open(state.active.tool):openUrl(state.active.key,state.active)):open('story'));
  back.addEventListener('click',()=>close());
  resume.addEventListener('change',()=>{const entry=editors.get(resume.value);if(entry)openUrl(entry.key,entry);});
  panel.addEventListener('cancel',event=>{event.preventDefault();close();});
  panel.addEventListener('keydown',event=>event.stopPropagation());
  document.addEventListener('click',event=>{if(!panel.contains(event.target))intercept(event);},true);
  window.addEventListener('worldmapselection',event=>selection(event.detail?.name));
  window.addEventListener('beforeunload',event=>{if([...editors.values()].some(entry=>entry.mutations.size||pending(entry))){event.preventDefault();event.returnValue='';}});
  window.WorldTools={configure,open,selection,close,returnToWorld};
})();
