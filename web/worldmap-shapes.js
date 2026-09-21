/* Expand source maps and combine adjoining boxes through backed-up previews. */
(function(root){
  'use strict';
  root.WorldMapShapes={create};
  function create(d){
    const {S,$,map,mapBoxes,dialog,esc,toast,status,api,ensureBuffer}=d;
    const available=()=>S.ready&&!S.busy&&!S.finishing&&!S.drag;
    const title=row=>row.display_name||row.name;
    function sizeAllowed(w,h){return w<=255&&h<=255&&(w+15)*(h+14)<=10240;}
    function canvasFor(width,height){
      const canvas=$('#reshape-preview'),scale=Math.min(6,480/width,220/height);
      canvas.width=Math.ceil(width*scale);canvas.height=Math.ceil(height*scale);
      const context=canvas.getContext('2d');context.imageSmoothingEnabled=false;
      return {canvas,context,scale};
    }
    async function buffersFor(names){
      S.busy=true;status();
      try{return await Promise.all(names.map(ensureBuffer));}
      finally{S.busy=false;status();}
    }
    async function applyPreview(kind,body){
      S.busy=true;status();let preview;
      try{preview=await api(`/api/worldmap/${kind}/preview`,{...body,positions_revision:mapBoxes.positionRevision()});}
      catch(error){toast(error.message,true);return;}
      finally{S.busy=false;status();}
      const verb=kind==='expand'?'Add space':'Combine boxes';
      const confirmed=await dialog(`${verb}: ${preview.width} × ${preview.height} tiles`,
        `<p><strong>${esc(body.name)}</strong> will be one editable ${preview.width} × ${preview.height} map.</p>`+
        (kind==='expand'?`<p>${preview.added_cells} blank terrain tiles will be added. Existing terrain and events keep their relative positions.</p>`:
          `<p>${body.maps.length} boxes will become one map. Terrain and events are placed together; internal walking seams disappear. The kept map supplies its name, border, settings and map callbacks.</p>`)+
        (preview.warnings||[]).map(w=>`<p class="reshape-warning">${esc(w)}</p>`).join('')+
        '<p>This saves a backed-up edit. Use <strong>Saved history</strong> to restore it.</p>',verb);
      if(!confirmed)return;
      S.busy=true;status();let saved;
      try{
        saved=await api(`/api/worldmap/${kind}`,{...body,positions_revision:preview.positions_revision,world_revision:preview.world_revision});
        await d.reloadWorld(body.name,true);
      }catch(error){toast(saved?'The map change was saved, but the view could not reload. Reload the page. '+error.message:error.message,true);return;}
      finally{S.busy=false;status();}
      d.setTool('arrange');toast(kind==='expand'?'Space added. Paint the new ground, or restore it from Saved history.':'Boxes combined into one editable map. Saved history can restore the separate maps.');
    }
    async function expand(){
      if(!available()||!map())return;
      const name=S.selected;
      if(!await d.requireSavedWorld())return;
      let b;try{[b]=await buffersFor([name]);}catch(error){toast(error.message,true);return;}
      const row=map(name);if(!row)return;
      const choices=[...new Set([S.erasers.get(name)||0,S.tile])].filter(id=>b.valid.has(id));
      if(!choices.length){toast('Choose a valid ground tile before adding space.',true);return;}
      const pending=dialog(`Add space to ${title(row)}`,
        '<p>Add blank rows or columns on any side. The existing terrain stays at its current world position.</p>'+
        `<div class="fields-grid">${[['top','Rows above'],['bottom','Rows below'],['left','Columns left'],['right','Columns right']].map(([side,label])=>
          `<label class="field">${label}<input id="expand-${side}" name="${side}" type="number" min="0" max="254" value="${side==='right'?4:0}" required></label>`).join('')}`+
        `<label class="field wide">Ground for new space<select id="expand-fill" name="fill_tile">${choices.map((id,i)=>`<option value="${id}">${i===0?'Background':'Selected brush'} · tile ${id}</option>`).join('')}</select></label></div>`+
        '<p id="reshape-dimensions" aria-live="polite"></p><canvas id="reshape-preview" class="route-split-preview" aria-label="Expanded map preview"></canvas><p id="reshape-size-warning" role="status"></p><p>New tiles start passable at elevation 3. Use Movement to adjust walkability.</p>','Preview expansion');
      function preview(){
        const sides=Object.fromEntries(['top','bottom','left','right'].map(side=>[side,Number($('#expand-'+side).value)]));
        const valid=Object.values(sides).every(value=>Number.isInteger(value)&&value>=0&&value<=254);
        const w=row.width+sides.left+sides.right,h=row.height+sides.top+sides.bottom,adds=Object.values(sides).some(Boolean);
        $('#reshape-dimensions').textContent=valid?`${row.width} × ${row.height} → ${w} × ${h} tiles`:'';
        $('#reshape-size-warning').textContent=!valid?'Enter whole numbers from 0 to 254.':!adds?'Add at least one row or column.':!sizeAllowed(w,h)?'This exceeds Emerald’s map size limit. Use fewer rows or columns.':'';
        $('#dialog-submit').disabled=!valid||!adds||!sizeAllowed(w,h);
        if(!valid||!sizeAllowed(w,h)){const canvas=$('#reshape-preview');canvas.width=canvas.height=0;return;}
        const {context,scale}=canvasFor(w,h),tile=Number($('#expand-fill').value);
        for(let y=0;y<h;y++)for(let x=0;x<w;x++)d.drawTile(context,b,tile,x*scale,y*scale,scale);
        context.drawImage(b.raster||d.rasterize(b),sides.left*scale,sides.top*scale,row.width*scale,row.height*scale);
        context.strokeStyle='#fff';context.lineWidth=2;context.strokeRect(sides.left*scale,sides.top*scale,row.width*scale,row.height*scale);
      }
      for(const side of ['top','bottom','left','right'])$('#expand-'+side).oninput=preview;
      $('#expand-fill').onchange=preview;preview();
      let values;try{values=await pending;}finally{$('#dialog-submit').disabled=false;}
      if(!values)return;
      const body={name,revision:b.data.revision,fill_tile:Number(values.fill_tile)};
      for(const side of ['top','bottom','left','right'])body[side]=Number(values[side]);
      await applyPreview('expand',body);
    }
    async function merge(){
      if(!available())return;
      const rows=mapBoxes.selectionNames().map(name=>map(name)).filter(Boolean);
      if(rows.length<2){toast('Use Move maps to select at least two adjoining boxes.',true);return;}
      const bounds=d.boundsOf(rows),area=rows.reduce((sum,row)=>sum+row.width*row.height,0);
      const overlap=rows.some((a,i)=>rows.slice(i+1).some(b=>a.x<b.x+b.width&&b.x<a.x+a.width&&a.y<b.y+b.height&&b.y<a.y+a.height));
      if(overlap||area!==bounds.width*bounds.height){toast('The selected boxes must touch and fill one rectangle, with no gaps or overlaps. Move them edge to edge first.',true);return;}
      if(!sizeAllowed(bounds.width,bounds.height)){toast('The combined rectangle exceeds Emerald’s map size limit. Select fewer boxes.',true);return;}
      if(!await d.requireSavedWorld())return;
      let buffers;try{buffers=await buffersFor(rows.map(row=>row.name));}catch(error){toast(error.message,true);return;}
      const first=buffers[0].data.layout;
      if(buffers.some(b=>b.data.layout.primary_tileset!==first.primary_tileset||b.data.layout.secondary_tileset!==first.secondary_tileset)){
        toast('These boxes use different terrain tilesets. Combining currently requires the same shared and local artwork.',true);return;
      }
      const kept=rows.some(row=>row.name===S.selected)?S.selected:rows[0].name;
      const pending=dialog('Combine adjoining map boxes',
        `<p>${rows.length} boxes → one ${bounds.width} × ${bounds.height} tile map.</p><label class="field">Map name and settings to keep<select name="name">${rows.map(row=>`<option value="${esc(row.name)}"${row.name===kept?' selected':''}>${esc(title(row))}</option>`).join('')}</select></label>`+
        '<label class="field reshape-encounters">Wild encounters<select name="encounter_policy"><option value="matching">Only combine if encounter tables match</option><option value="keep_primary">Use the kept map’s encounters for the whole map</option></select></label>'+
        '<canvas id="reshape-preview" class="route-split-preview" aria-label="Combined map preview"></canvas><p>Preview checks terrain, events, connections and story references before combining.</p>','Preview combination');
      const {context,scale}=canvasFor(bounds.width,bounds.height);
      rows.forEach((row,i)=>{context.drawImage(buffers[i].raster||d.rasterize(buffers[i]),(row.x-bounds.x)*scale,(row.y-bounds.y)*scale,row.width*scale,row.height*scale);context.strokeStyle='#fff';context.lineWidth=1;context.strokeRect((row.x-bounds.x)*scale,(row.y-bounds.y)*scale,row.width*scale,row.height*scale);});
      const values=await pending;if(!values)return;
      const body={name:values.name,maps:rows.map((row,i)=>({name:row.name,x:row.x,y:row.y,revision:buffers[i].data.revision}))};
      if(values.encounter_policy==='keep_primary')body.encounter_policy='keep_primary';
      await applyPreview('merge',body);
    }
    $('#expand-map').onclick=expand;$('#combine-map-boxes').onclick=merge;
    return {expand,merge};
  }
})(typeof globalThis!=='undefined'?globalThis:this);
