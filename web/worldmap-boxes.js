/* Whole-map splitting and persistent positions of the map boxes on the canvas. */
(function(root){
  'use strict';
  root.WorldMapBoxes={create};
  function create(d){
    const {S,$,api,map,screen,render,toast,status,selectMap,ensureBuffer,dialog,esc}=d;
    let positions={},revision=null,originals=new Map(),undoMoves=[];
    const active=()=>S.mode==='terrain'&&['arrange','splitmap'].includes(S.tool);
    const minimum=axis=>axis==='vertical'?8:7;
    const length=(row,axis)=>axis==='vertical'?row.width:row.height;
    function defaultAxis(row){const first=row.width>=row.height?'vertical':'horizontal';return length(row,first)>=2*minimum(first)?first:first==='vertical'?'horizontal':'vertical';}
    function sync(){
      $('#map-box-options').hidden=!active();
      $('#map-box-hint').textContent=S.tool==='splitmap'
        ? 'Split the whole route: draw a straight line across its box, or choose Split in half.'
        : 'Drag a map box to place it on the world canvas. Positions save automatically. Use Connections to change walking links.';
      $('#split-midpoint').hidden=S.tool!=='splitmap';
      $('#split-midpoint').disabled=!S.ready||S.busy||S.finishing||!map();
      $('#undo-map-move').hidden=S.tool!=='arrange';
      $('#undo-map-move').disabled=!undoMoves.length||S.busy||S.finishing;
    }
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
      originals=new Map(S.maps.map(row=>[row.name,{x:row.x,y:row.y}]));undoMoves=[];apply();sync();
    }
    async function savePositions(updates){
      const saved=await api('/api/worldmap/positions',{revision,positions:updates});revision=saved.revision;positions=saved.positions;apply();sync();
    }
    function cancel(){const drag=S.drag;if(!drag?.mapBox)return false;if(drag.mapBox==='move'){const row=map(drag.name);Object.assign(row,drag.origin);}S.drag=null;sync();render();return true;}
    async function pointerDown(event,p,row,press){
      if(!active()||event.button!==0||!row)return false;
      // Map placement uses catalog geometry; artwork can load while the box is dragged.
      if(S.selected!==row.name||!d.buffer(row.name))selectMap(row.name);
      if(S.pointerActive!==event.pointerId||S.press!==press||!active())return true;
      S.drag={mapBox:S.tool==='arrange'?'move':'split',name:row.name,start:p,last:p,origin:{x:row.x,y:row.y}};render();return true;
    }
    function cutFor(drag){
      const row=map(drag.name),dx=Math.abs(drag.last.x-drag.start.x),dy=Math.abs(drag.last.y-drag.start.y);
      const axis=dx>dy?'horizontal':dy>dx?'vertical':defaultAxis(row);
      const span=length(row,axis),min=minimum(axis);
      const coordinate=axis==='vertical'?(drag.start.x+drag.last.x)/2-row.x:(drag.start.y+drag.last.y)/2-row.y;
      return {axis,cut:Math.max(min,Math.min(span-min,Math.round(coordinate)))};
    }
    function pointerMove(p){const drag=S.drag;if(!drag?.mapBox)return false;drag.last=p;if(drag.mapBox==='move'){const row=map(drag.name);row.x=Math.round(drag.origin.x+p.x-drag.start.x);row.y=Math.round(drag.origin.y+p.y-drag.start.y);}render();return true;}
    async function finish(drag){
      if(!drag?.mapBox)return false;
      if(drag.mapBox==='split'){const {axis,cut}=cutFor(drag);await split(axis,cut);return true;}
      const row=map(drag.name),next={x:row.x,y:row.y};if(next.x===drag.origin.x&&next.y===drag.origin.y)return true;
      S.finishing=true;status();
      const previous=positions[row.name]?{...positions[row.name]}:null;
      try{await savePositions({[row.name]:next});undoMoves.push({name:row.name,previous});toast('Map position saved. Drag another box, or use Undo map move.');}
      catch(err){Object.assign(row,drag.origin);toast(err.message,true);}
      finally{S.finishing=false;status();render();}
      return true;
    }
    async function undoMove(){
      if(!undoMoves.length||S.busy||S.finishing)return;
      S.finishing=true;status();const entry=undoMoves[undoMoves.length-1];
      try{await savePositions({[entry.name]:entry.previous});undoMoves.pop();toast('Map move undone.');}catch(err){toast(err.message,true);}finally{S.finishing=false;status();render();}
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
      if(!active())return;const drag=S.drag,row=drag?.mapBox?map(drag.name):map();if(!row)return;const p=screen(row.x,row.y),w=row.width*S.scale,h=row.height*S.scale;
      ctxSave();
      if(S.tool==='splitmap'){
        const {axis,cut}=drag?.mapBox==='split'?cutFor(drag):{axis:defaultAxis(row),cut:Math.floor(length(row,defaultAxis(row))/2)};
        const x=p.x+(axis==='vertical'?cut*S.scale:0),y=p.y+(axis==='horizontal'?cut*S.scale:0);
        d.ctx.beginPath();d.ctx.moveTo(x,y);d.ctx.lineTo(axis==='vertical'?x:x+w,axis==='vertical'?y+h:y);d.ctx.strokeStyle='#fff';d.ctx.lineWidth=5;d.ctx.stroke();d.ctx.strokeStyle='#c74472';d.ctx.lineWidth=2;d.ctx.setLineDash([6,4]);d.ctx.stroke();
      }else{d.ctx.strokeStyle='#18758a';d.ctx.lineWidth=3;d.ctx.setLineDash([8,4]);d.ctx.strokeRect(p.x-2,p.y-2,w+4,h+4);}
      d.ctx.restore();
    }
    function ctxSave(){d.ctx.save();d.ctx.setLineDash([]);}
    $('#split-midpoint').onclick=()=>split();$('#undo-map-move').onclick=undoMove;
    return {load,sync,active,pointerDown,pointerMove,finish,cancel,draw,split,hasOverride:name=>!!positions[name]};
  }
})(typeof globalThis!=='undefined'?globalThis:this);
