/* Show game destinations and apply reviewed walking links from canvas geometry. */
(function(root){
  'use strict';
  const opposite={up:'down',down:'up',left:'right',right:'left'};
  function edgePoints(edge,map){
    const a=map(edge.from_name),b=map(edge.to_name);
    if(!a||!b||!opposite[edge.direction]||!Number.isInteger(edge.offset))return null;
    const horizontal=['left','right'].includes(edge.direction),axis=horizontal?'height':'width';
    const start=Math.max(0,edge.offset),end=Math.min(a[axis],edge.offset+b[axis]);
    if(end<=start)return null;
    const middle=(start+end)/2,local=middle-edge.offset;
    const points={left:[a.x,a.y+middle,b.x+b.width,b.y+local],right:[a.x+a.width,a.y+middle,b.x,b.y+local],up:[a.x+middle,a.y,b.x+local,b.y+b.height],down:[a.x+middle,a.y+a.height,b.x+local,b.y]};
    const [x,y,tx,ty]=points[edge.direction];
    return {a:{x,y},b:{x:tx,y:ty},aligned:x===tx&&y===ty,horizontal,start,end};
  }
  function create(d){
    const {S,$,map,mapBoxes,api,esc,dialog,toast,status,render,screen,ctx}=d;
    let catalog=null,opened=false,loading=false,serial=0,viewKey='',loadError='';
    const ready=()=>S.ready&&!S.busy&&!S.finishing&&!S.drag&&!loading;
    const name=n=>{const row=map(n);return row?(d.mapTitle?d.mapTitle(row):row.display_name||n):n||'Unknown destination';};
    const portals=()=>catalog?.audit?.portals||[];
    const directions={up:'North',down:'South',left:'West',right:'East',dive:'Dive',emerge:'Surface'};
    function sourceOf(p){return p.source||p.from;}
    function destinationOf(p){return p.destination||p.to;}
    function relevant(a,b){return $('#world-links-all').checked||a===S.selected||b===S.selected;}
    async function load(){
      const request=++serial;loading=true;loadError='';sync();
      try{const value=await api('/api/worldmap/links');if(request===serial){catalog=value;viewKey='';}}
      catch(error){if(request===serial){catalog=null;loadError=error.message;}}
      finally{if(request===serial){loading=false;sync();render();}}
      return catalog;
    }
    function summary(){
      if(loading)return 'Loading game connections…';
      if(loadError)return 'Could not load connections: '+loadError;
      if(!catalog)return 'Open connections to see where each entrance leads.';
      const edges=catalog.edges||[],misaligned=edges.filter(e=>{const p=edgePoints(e,map);return !p||!p.aligned;});
      const issues=catalog.audit?.issues||[];
      return `${misaligned.length} walking directions differ from the canvas · ${issues.length} entrance markers to review. Preview connections after moving boxes to update the game.`;
    }
    function sync(){
      $('#world-links-toggle').disabled=!S.ready||S.busy||S.finishing;
      $('#world-links-toggle').setAttribute('aria-pressed',String(opened));
      $('#world-links-panel').hidden=!opened;
      $('#world-links-summary').textContent=summary();
      const selected=mapBoxes.selectionNames();
      $('#world-links-sync').disabled=!ready()||($('#world-links-scope').value==='selection'&&selected.length<2);
      $('#world-links-scope').disabled=!ready();
      if(!opened)return;
      const key=[serial,S.selected,loading,loadError,mapBoxes.positionRevision(),S.buffers?.get(S.selected)?.dirty].join('|');
      if(key!==viewKey){viewKey=key;renderList();}
      for(const button of $('#world-links-content').querySelectorAll?.('button')||[])button.disabled=!ready();
    }
    function renderList(){
      const container=$('#world-links-content');
      if(!catalog){container.innerHTML='<p>'+esc(loadError||'Loading destinations…')+'</p>';return;}
      const entries=[];
      for(const edge of [...(catalog.edges||[]),...(catalog.special_edges||[])]){
        if(edge.from_name!==S.selected)continue;
        const points=edgePoints(edge,map),special=!opposite[edge.direction];
        entries.push({heading:`${directions[edge.direction]||edge.direction} → ${name(edge.to_name)}`,
          detail:special?'Special game connection; kept when applying the layout.':`${points?.aligned?'Matches canvas':'Does not match canvas'} · offset ${edge.offset}`,
          bad:!special&&!points?.aligned,target:edge.to_name});
      }
      for(const portal of portals()){
        const from=sourceOf(portal),to=destinationOf(portal);if(from?.map!==S.selected)continue;
        const fixed=portal.status==='fixed';
        entries.push({heading:`Entrance ${from.index} (${from.x}, ${from.y}) → ${to?name(to.map):'Game-controlled destination'}`,
          detail:fixed?`Arrives at (${to.x}, ${to.y}) · entrance ${to.index}${portal.reciprocal?' · return linked':' · one-way / scripted return'}`:portal.reason||portal.message||portal.status,
          bad:!['fixed','dynamic','scripted'].includes(portal.status),target:fixed?to?.map:null,point:fixed?to:null,source:from});
      }
      const incoming=portals().filter(p=>destinationOf(p)?.map===S.selected&&sourceOf(p)?.map!==S.selected&&p.status==='fixed');
      const issues=catalog.audit?.issues||[];
      container.innerHTML=`<strong>${esc(name(S.selected))}</strong>`+(S.buffers?.get(S.selected)?.dirty?'<p class="link-warning">Save terrain and event edits to refresh these destinations.</p>':'')+
        (entries.length?entries.map((entry,i)=>`<div class="game-link-row${entry.bad?' link-warning':''}"><strong>${esc(entry.heading)}</strong><small>${esc(entry.detail)}</small>${entry.target?`<button class="text-button" data-link-follow="${i}">Show destination ↗</button>`:''}${entry.source?` <button class="text-button" data-link-edit="${i}">Edit doorway</button>`:''}</div>`).join(''):'<p>No outgoing walking links or entrances here.</p>')+
        (incoming.length?`<details><summary>${incoming.length} entrances lead here</summary>${incoming.map((p,i)=>`<button class="text-button incoming-link" data-incoming="${i}">${esc(name(sourceOf(p).map))} · entrance ${sourceOf(p).index}</button>`).join('')}</details>`:'')+
        (issues.length?`<details class="entrance-review"><summary>${issues.length} entrance markers to review</summary><p>Some original maps contain unused or script-controlled markers outside their bounds. These are preserved; review their scripts before changing them.</p>${issues.map((issue,i)=>`<div class="game-link-row"><button class="text-button incoming-link" data-review="${i}">${esc(name(issue.map))} · entrance ${issue.index}</button><small>${esc(issue.reason)}</small></div>`).join('')}</details>`:'');
      for(const [i,entry]of entries.entries()){
        const button=$(`[data-link-follow="${i}"]`,container);if(button)button.onclick=()=>follow(entry.target,entry.point);
        const edit=$(`[data-link-edit="${i}"]`,container);if(edit)edit.onclick=()=>d.editWarp(entry.source);
      }
      incoming.forEach((p,i)=>{$(`[data-incoming="${i}"]`,container).onclick=()=>follow(sourceOf(p).map,sourceOf(p));});
      issues.forEach((issue,i)=>{$(`[data-review="${i}"]`,container).onclick=()=>follow(issue.map,sourceOf(portals().find(p=>p.id===issue.id)||{}));});
    }
    async function follow(target,point){
      if(!ready()||!map(target))return;
      await d.selectMap(target);
      const row=map(target);if(!d.included(row)){S.category='all';$('#category-filter').value='all';d.renderPlaces();}
      if(point){d.fitBounds({x:row.x+point.x-7,y:row.y+point.y-6,width:15,height:13});}else d.fitSelection();
      viewKey='';sync();render();
    }
    function line(a,b,color,dashed){
      const p=screen(a.x,a.y),q=screen(b.x,b.y);ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;ctx.setLineDash(dashed?[6,4]:[]);
      ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();ctx.setLineDash([]);
      for(const point of[p,q]){ctx.beginPath();ctx.arc(point.x,point.y,3.5,0,Math.PI*2);ctx.fill();}
      const angle=Math.atan2(q.y-p.y,q.x-p.x);if(Math.hypot(q.x-p.x,q.y-p.y)>12){ctx.beginPath();ctx.moveTo(q.x,q.y);ctx.lineTo(q.x-9*Math.cos(angle-.5),q.y-9*Math.sin(angle-.5));ctx.lineTo(q.x-9*Math.cos(angle+.5),q.y-9*Math.sin(angle+.5));ctx.closePath();ctx.fill();}
    }
    function draw(){
      if(!opened||!catalog)return;ctx.save();
      const seen=new Set();
      for(const edge of catalog.edges||[]){
        if(!relevant(edge.from_name,edge.to_name))continue;
        const a=map(edge.from_name),b=map(edge.to_name);if(!a||!b||!d.included(a)||!d.included(b))continue;
        const key=[edge.from_name,edge.to_name].sort().join('|')+'|'+(edge.direction==='up'||edge.direction==='down'?'v':'h')+'|'+(edge.from_name<edge.to_name?edge.offset:-edge.offset);
        if(seen.has(key))continue;seen.add(key);const p=edgePoints(edge,map);if(!p)continue;
        if(p.aligned){const start=edge.direction==='up'||edge.direction==='down'?{x:a.x+p.start,y:p.a.y}:{x:p.a.x,y:a.y+p.start};const end=edge.direction==='up'||edge.direction==='down'?{x:a.x+p.end,y:p.a.y}:{x:p.a.x,y:a.y+p.end};line(start,end,'#148879',false);}
        else line(p.a,p.b,'#bf6037',true);
      }
      for(const portal of portals()){
        const from=sourceOf(portal),to=destinationOf(portal);if(!from||!relevant(from.map,to?.map))continue;
        const a=map(from.map),b=map(to?.map);if(!a||!d.included(a))continue;
        const start={x:a.x+from.x+.5,y:a.y+from.y+.5};
        if(portal.status==='fixed'&&b&&d.included(b))line(start,{x:b.x+to.x+.5,y:b.y+to.y+.5},'#557aba',true);
        else{const p=screen(start.x,start.y);ctx.strokeStyle=['dynamic','scripted'].includes(portal.status)?'#9973b4':'#c8513f';ctx.lineWidth=2;ctx.setLineDash([]);ctx.strokeRect(p.x-4,p.y-4,8,8);}
      }
      ctx.restore();
    }
    function diffRows(label,edges){return edges.length?`<details open><summary>${label}: ${edges.length} directions</summary><ul class="connection-diff">${edges.map(e=>`<li>${esc(name(e.from_name))} → ${esc(name(e.to_name))} · ${esc(directions[e.direction]||e.direction)} · offset ${esc(e.offset)}</li>`).join('')}</ul></details>`:'';}
    async function preview(){
      if(!ready())return;
      const selection=$('#world-links-scope').value==='selection',names=selection?mapBoxes.selectionNames():null;
      if(selection&&names.length<2){toast('Select at least two map boxes using Move maps.',true);return;}
      if(!await d.requireSavedWorld())return;
      S.busy=true;status();let proposal,body;
      try{const fresh=await api('/api/worldmap/links');catalog=fresh;body={revision:fresh.revision,positions_revision:mapBoxes.positionRevision()};if(names)body.names=names;proposal=await api('/api/worldmap/links/preview',body);}
      catch(error){toast(error.message,true);return;}finally{S.busy=false;viewKey='';status();render();}
      const changes=(proposal.added||[]).length+(proposal.removed||[]).length;
      const html=`<p>Match walking paths between ${proposal.scope_names.length} maps to their saved canvas positions. Edges must touch exactly. Doors, caves, stairs, dive links and links outside this selection keep their destinations.</p>`+
        diffRows('Add or replace',proposal.added||[])+diffRows('Remove old links',proposal.removed||[])+
        (proposal.errors||[]).map(text=>`<p class="link-warning">${esc(text)}</p>`).join('')+
        (proposal.warnings||[]).map(text=>`<p>${esc(text)}</p>`).join('')+
        `<p>${changes?'This changes the playable game source. Saved history can undo it.':'These walking connections already match the canvas.'}</p>`;
      if(!proposal.can_apply||!changes){await dialog(proposal.errors?.length?'Fix these map edges first':'Connections match',html,'',true);return;}
      if(!await dialog('Apply canvas connections to the game',html,'Apply to game'))return;
      S.busy=true;status();let saved;
      try{saved=await api('/api/worldmap/links',{...body,revision:proposal.revision,positions_revision:proposal.positions_revision});await d.reloadWorld(S.selected,false);await load();toast('Game walking connections now match this layout. Saved history can restore the previous links.');}
      catch(error){toast(saved?'Connections were saved, but the view could not reload. Reload the page. '+error.message:error.message,true);}
      finally{S.busy=false;status();}
    }
    $('#world-links-toggle').onclick=async()=>{if(!S.ready||S.busy||S.finishing)return;opened=!opened;sync();render();if(opened)await load();};
    $('#world-links-close').onclick=()=>{opened=false;sync();render();};
    $('#world-links-sync').onclick=preview;
    $('#world-links-scope').onchange=sync;
    $('#world-links-all').onchange=render;
    return {load,sync,draw,preview,follow,selectionChanged(){viewKey='';$('#world-links-panel').scrollTop=0;sync();render();},positionsChanged(){viewKey='';sync();render();}};
  }
  root.WorldMapLinks={create,edgePoints};
})(typeof globalThis!=='undefined'?globalThis:this);
