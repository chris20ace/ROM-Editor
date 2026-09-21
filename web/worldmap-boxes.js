/* Whole-map splitting and persistent positions of the map boxes on the canvas. */
(function(root){
  'use strict';
  root.WorldMapBoxes={create};
  function create(d){
    const {S,$,api,map,screen,render,toast,status,selectMap,ensureBuffer,dialog,esc}=d;
    let positions={},revision=null,originals=new Map(),undoMoves=[],selected=new Set(),marqueeReady=false;
    const active=()=>S.mode==='terrain'&&['arrange','splitmap'].includes(S.tool);
    const minimum=axis=>axis==='vertical'?8:7;
    const length=(row,axis)=>axis==='vertical'?row.width:row.height;
    function defaultAxis(row){const first=row.width>=row.height?'vertical':'horizontal';return length(row,first)>=2*minimum(first)?first:first==='vertical'?'horizontal':'vertical';}
    const included=row=>!!row&&(!d.included||d.included(row));
    const selectedRows=()=>S.maps.filter(row=>selected.has(row.name)&&included(row));
    function sync(){
      for(const name of selected)if(!included(map(name)))selected.delete(name);
      const arranging=S.tool==='arrange',working=S.busy||S.finishing,count=selected.size;
      $('#map-box-options').hidden=!active();
      $('#expand-map').disabled=!S.ready||working||!!S.drag||!map();
      $('#combine-map-boxes').hidden=!arranging;
      $('#combine-map-boxes').disabled=!S.ready||working||!!S.drag||count<2;
      $('#map-box-hint').textContent=S.tool==='splitmap'
        ? 'Split the whole route: draw a straight line across its box, or choose Split in half.'
        : marqueeReady?'Drag a rectangle around map boxes. Shift adds to the group.'
          : 'Shift-click maps to add or remove them. Drag a selected map to move the group. Preview connections to apply this layout to the game.';
      $('#map-box-count').hidden=!arranging;
      $('#map-box-count').textContent=`${count} map${count===1?'':'s'} selected`;
      $('#select-map-boxes').hidden=!arranging;
      $('#select-map-boxes').disabled=!S.ready||working||!!S.drag;
      $('#select-map-boxes').setAttribute('aria-pressed',String(marqueeReady));
      $('#clear-map-selection').hidden=!arranging;
      $('#clear-map-selection').disabled=!count||working||!!S.drag;
      $('#split-midpoint').hidden=S.tool!=='splitmap';
      $('#split-midpoint').disabled=!S.ready||working||!map();
      $('#undo-map-move').hidden=!arranging;
      $('#undo-map-move').disabled=!undoMoves.length||working||!!S.drag;
    }
    function focusMap(name){
      if(!included(map(name)))return;
      if(!selected.has(name))selected=new Set([name]);
      sync();render();
    }
    function clearSelection(){if(S.busy||S.finishing)return;cancel();selected.clear();marqueeReady=false;sync();render();}
    function updateBounds(){
      if(!S.catalog)return;
      S.catalog.bounds=d.boundsOf(S.maps);
      for(const group of [...(S.catalog.groups||[]),...(S.catalog.sections||[])]){
        const rows=(group.names||[]).map(name=>map(name)).filter(Boolean);if(rows.length)group.bounds=d.boundsOf(rows);
      }
    }
    function apply(){for(const row of S.maps){const p=positions[row.name]||originals.get(row.name);if(p){row.x=p.x;row.y=p.y;}}updateBounds();render();}
    async function load(){
      const value=await api('/api/worldmap/positions');revision=value.revision;positions=value.positions;
      originals=new Map(S.maps.map(row=>[row.name,{x:row.x,y:row.y}]));undoMoves=[];selected.clear();marqueeReady=false;apply();sync();
    }
    async function savePositions(updates){
      const saved=await api('/api/worldmap/positions',{revision,positions:updates});revision=saved.revision;positions=saved.positions;apply();sync();d.positionsChanged?.();
    }
    function restoreOrigins(drag){for(const[name,origin]of Object.entries(drag.origins||{})){const row=map(name);if(row)Object.assign(row,origin);}updateBounds();}
    function cancel(){
      const drag=S.drag,armed=marqueeReady;marqueeReady=false;
      if(!drag?.mapBox){if(armed){sync();render();}return armed;}
      if(drag.mapBox==='move')restoreOrigins(drag);
      if(drag.mapBox==='marquee')selected=new Set(drag.previous);
      S.drag=null;sync();render();return true;
    }
    async function pointerDown(event,p,row,press){
      if(!active()||event.button!==0)return false;
      if(S.pointerActive!==event.pointerId||S.press!==press)return true;
      if(S.tool==='splitmap'){
        if(!row)return false;
        if(S.selected!==row.name||!d.buffer(row.name))selectMap(row.name);
        S.drag={mapBox:'split',name:row.name,start:p,last:p,origin:{x:row.x,y:row.y}};sync();render();return true;
      }
      const additive=event.shiftKey||event.ctrlKey||event.metaKey;
      if(marqueeReady||!row){
        const previous=new Set(selected);marqueeReady=false;
        S.drag={mapBox:'marquee',start:p,last:p,previous,additive};
        if(!additive)selected.clear();sync();render();return true;
      }
      if(!included(row))return true;
      if(additive){
        if(selected.has(row.name))selected.delete(row.name);
        else{selected.add(row.name);if(S.selected!==row.name||!d.buffer(row.name))selectMap(row.name);}
        sync();render();return true;
      }
      if(!selected.has(row.name))selected=new Set([row.name]);
      // Catalog geometry is ready even when this map's artwork is still loading.
      if(S.selected!==row.name||!d.buffer(row.name))selectMap(row.name);
      const origins=Object.fromEntries(selectedRows().map(item=>[item.name,{x:item.x,y:item.y}]));
      S.drag={mapBox:'move',name:row.name,start:p,last:p,origins};sync();render();return true;
    }
    function cutFor(drag){
      const row=map(drag.name),dx=Math.abs(drag.last.x-drag.start.x),dy=Math.abs(drag.last.y-drag.start.y);
      const axis=dx>dy?'horizontal':dy>dx?'vertical':defaultAxis(row);
      const span=length(row,axis),min=minimum(axis);
      const coordinate=axis==='vertical'?(drag.start.x+drag.last.x)/2-row.x:(drag.start.y+drag.last.y)/2-row.y;
      return {axis,cut:Math.max(min,Math.min(span-min,Math.round(coordinate)))};
    }
    function marqueeBounds(drag){return {x:Math.min(drag.start.x,drag.last.x),y:Math.min(drag.start.y,drag.last.y),right:Math.max(drag.start.x,drag.last.x),bottom:Math.max(drag.start.y,drag.last.y)};}
    function updateMarquee(drag){
      const area=marqueeBounds(drag),next=new Set(drag.additive?drag.previous:[]);
      if(area.right>area.x&&area.bottom>area.y)for(const row of S.maps){
        if(included(row)&&row.x>=area.x&&row.y>=area.y&&row.x+row.width<=area.right&&row.y+row.height<=area.bottom)next.add(row.name);
      }
      selected=next;
    }
    function pointerMove(p){
      const drag=S.drag;if(!drag?.mapBox)return false;drag.last=p;
      if(drag.mapBox==='move'){
        const dx=Math.round(p.x-drag.start.x),dy=Math.round(p.y-drag.start.y);
        for(const[name,origin]of Object.entries(drag.origins)){const row=map(name);if(row){row.x=origin.x+dx;row.y=origin.y+dy;}}
      }
      if(drag.mapBox==='marquee'){updateMarquee(drag);sync();}
      render();return true;
    }
    async function finish(drag){
      if(!drag?.mapBox)return false;
      if(drag.mapBox==='split'){const {axis,cut}=cutFor(drag);await split(axis,cut);return true;}
      if(drag.mapBox==='marquee'){
        updateMarquee(drag);marqueeReady=false;
        if(selected.size&&!selected.has(S.selected))selectMap(selected.values().next().value);
        sync();render();return true;
      }
      const updates={},previous={};
      for(const[name,origin]of Object.entries(drag.origins)){
        const row=map(name);if(!row||row.x===origin.x&&row.y===origin.y)continue;
        updates[name]={x:row.x,y:row.y};previous[name]=positions[name]?{...positions[name]}:null;
      }
      if(!Object.keys(updates).length){sync();return true;}
      S.finishing=true;status();
      try{await savePositions(updates);undoMoves.push(previous);toast(`${Object.keys(updates).length===1?'Map position':'Map positions'} saved. Use Connections → Preview connections to update game paths. Undo map move restores the group.`);}
      catch(err){restoreOrigins(drag);toast(err.message,true);}
      finally{S.finishing=false;status();render();}
      return true;
    }
    async function undoMove(){
      if(!undoMoves.length||S.busy||S.finishing||S.drag)return;
      S.finishing=true;status();const entry=undoMoves[undoMoves.length-1];
      try{await savePositions(entry);undoMoves.pop();toast('Map move undone.');}catch(err){toast(err.message,true);}finally{S.finishing=false;status();render();}
    }
    async function split(axis,cut){
      if(!S.ready||S.busy||S.finishing)return;const row=map();if(!row)return;
      if(row.width<16&&row.height<14){toast('Connected route halves need at least 8 columns or 7 rows each. This map is too small to split.',true);return;}
      axis||=defaultAxis(row);if(length(row,axis)<2*minimum(axis)){toast('Choose the other direction. Each connected half needs at least 8 columns or 7 rows.',true);return;}cut??=Math.floor(length(row,axis)/2);cut=Math.max(minimum(axis),Math.min(length(row,axis)-minimum(axis),cut));
      const form=dialog(`Split ${row.display_name||row.name}`,`<p>Create two separate editable route boxes. The original keeps the left or top half; the new map gets the other half. A walking connection joins them.</p><div class="fields-grid"><label class="field">Direction<select name="axis" id="route-split-axis"><option value="vertical"${axis==='vertical'?' selected':''}${row.width<16?' disabled':''}>Vertical · left and right</option><option value="horizontal"${axis==='horizontal'?' selected':''}${row.height<14?' disabled':''}>Horizontal · top and bottom</option></select></label><label class="field">Split after tile<input name="cut" id="route-split-cut" type="number" min="${minimum(axis)}" max="${length(row,axis)-minimum(axis)}" value="${cut}" required></label><label class="field wide">Name for the second map<input name="new_name" placeholder="${esc(row.name)}Part2" pattern="[A-Za-z][A-Za-z0-9_]{0,63}" maxlength="64"></label></div><p id="route-split-dimensions"></p><canvas id="route-split-preview" class="route-split-preview" aria-label="Preview of two separate route boxes"></canvas><p>The preview checks connections, events and source scripts before creating either half. Saving creates a backed-up source edit.</p>`,'Preview split');
      const source=d.buffer(row.name),preview=$('#route-split-preview');
      function drawPreview(){
        const direction=$('#route-split-axis').value,span=length(row,direction),min=minimum(direction),input=$('#route-split-cut');input.min=min;input.max=span-min;
        const n=Math.max(min,Math.min(span-min,Number(input.value)||min));
        $('#route-split-dimensions').textContent=direction==='vertical'?`${n} × ${row.height} tiles + ${row.width-n} × ${row.height} tiles`:`${row.width} × ${n} tiles + ${row.width} × ${row.height-n} tiles`;
        if(!source)return;const scale=Math.min(5,480/(row.width+3),250/(row.height+3)),c=preview.getContext('2d');preview.width=Math.ceil((row.width+(direction==='vertical'?3:0))*scale);preview.height=Math.ceil((row.height+(direction==='horizontal'?3:0))*scale);c.imageSmoothingEnabled=false;const art=source.raster||d.rasterize(source);
        if(direction==='vertical'){c.drawImage(art,0,0,n*16,row.height*16,0,0,n*scale,row.height*scale);c.drawImage(art,n*16,0,(row.width-n)*16,row.height*16,(n+3)*scale,0,(row.width-n)*scale,row.height*scale);}
        else{c.drawImage(art,0,0,row.width*16,n*16,0,0,row.width*scale,n*scale);c.drawImage(art,0,n*16,row.width*16,(row.height-n)*16,0,(n+3)*scale,row.width*scale,(row.height-n)*scale);}
      }
      $('#route-split-axis').onchange=()=>{$('#route-split-cut').value=Math.floor(($('#route-split-axis').value==='vertical'?row.width:row.height)/2);drawPreview();};$('#route-split-cut').oninput=drawPreview;drawPreview();
      const values=await form;if(!values)return;
      if(!await d.requireSavedWorld())return;
      S.busy=true;status();let details,body;
      try{const b=await ensureBuffer(row.name);body={name:row.name,revision:b.data.revision,axis:values.axis,cut:Number(values.cut)};if(values.new_name.trim())body.new_name=values.new_name.trim();details=await api('/api/worldmap/split/preview',body);}
      catch(err){toast(err.message,true);return;}finally{S.busy=false;status();}
      const confirmed=await dialog('Create two route boxes',`<p>${details.parts.map(part=>`<strong>${esc(part.name)}</strong> · ${part.width} × ${part.height} tiles`).join('<br>')}</p><p>Terrain, encounters, events and walking links are included in this split.</p>${(details.warnings||[]).map(message=>`<p>${esc(message)}</p>`).join('')}<p>You can restore this split from Saved history. After creating it, use <strong>Move maps</strong> to drag either box.</p>`,'Create two route boxes');
      if(!confirmed)return;
      S.busy=true;status();const origin={x:row.x,y:row.y};let result;
      try{result=await api('/api/worldmap/split',{...body,new_name:details.new_name,world_revision:details.world_revision});await d.reloadWorld(row.name,false);
        const updates={};for(const [i,part] of result.parts.entries())updates[part.name]={x:origin.x+part.origin_x+(i&&body.axis==='vertical'?3:0),y:origin.y+part.origin_y+(i&&body.axis==='horizontal'?3:0)};
        await savePositions(updates);undoMoves=[];
      }catch(err){toast(result?'The route split was saved. Reload the world to refresh its boxes. '+err.message:err.message,true);return;}
      finally{S.busy=false;status();}
      d.setTool('arrange');d.fitBounds(d.boundsOf(result.parts.map(part=>map(part.name))));toast('Route split into two editable maps. Drag either box to position it.');
    }
    function draw(){
      if(!active())return;const drag=S.drag;ctxSave();
      if(S.tool==='splitmap'){
        const row=drag?.mapBox==='split'?map(drag.name):map();if(!row){d.ctx.restore();return;}
        const p=screen(row.x,row.y),w=row.width*S.scale,h=row.height*S.scale;
        const {axis,cut}=drag?.mapBox==='split'?cutFor(drag):{axis:defaultAxis(row),cut:Math.floor(length(row,defaultAxis(row))/2)};
        const x=p.x+(axis==='vertical'?cut*S.scale:0),y=p.y+(axis==='horizontal'?cut*S.scale:0);
        d.ctx.beginPath();d.ctx.moveTo(x,y);d.ctx.lineTo(axis==='vertical'?x:x+w,axis==='vertical'?y+h:y);d.ctx.strokeStyle='#fff';d.ctx.lineWidth=5;d.ctx.stroke();d.ctx.strokeStyle='#c74472';d.ctx.lineWidth=2;d.ctx.setLineDash([6,4]);d.ctx.stroke();
      }else{
        d.ctx.strokeStyle='#18758a';d.ctx.lineWidth=3;d.ctx.setLineDash([8,4]);
        for(const row of selectedRows()){const p=screen(row.x,row.y);d.ctx.strokeRect(p.x-2,p.y-2,row.width*S.scale+4,row.height*S.scale+4);}
        if(drag?.mapBox==='marquee'){
          const area=marqueeBounds(drag),p=screen(area.x,area.y),w=(area.right-area.x)*S.scale,h=(area.bottom-area.y)*S.scale;
          d.ctx.fillStyle='#18758a';d.ctx.globalAlpha=.12;d.ctx.fillRect(p.x,p.y,w,h);d.ctx.globalAlpha=1;d.ctx.lineWidth=1.5;d.ctx.setLineDash([5,3]);d.ctx.strokeRect(p.x,p.y,w,h);
        }
      }
      d.ctx.restore();
    }
    function ctxSave(){d.ctx.save();d.ctx.setLineDash([]);}
    $('#split-midpoint').onclick=()=>split();$('#undo-map-move').onclick=undoMove;
    $('#select-map-boxes').onclick=()=>{if(!active()||S.tool!=='arrange'||S.busy||S.finishing||S.drag)return;marqueeReady=!marqueeReady;sync();render();};
    $('#clear-map-selection').onclick=clearSelection;
    return {selectionNames:()=>selectedRows().map(row=>row.name),positionRevision:()=>revision,load,sync,active,pointerDown,pointerMove,finish,cancel,draw,split,focusMap,hasOverride:name=>!!positions[name],
      key(event){if(event.key!=='Escape'||!active()||S.busy||S.finishing)return false;if(cancel()){event.preventDefault();return true;}if(S.tool==='arrange'&&selected.size){clearSelection();event.preventDefault();return true;}return false;}};
  }
})(typeof globalThis!=='undefined'?globalThis:this);
