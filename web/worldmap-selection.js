/* Pointer interaction for rectangular terrain selections and pixel-accurate cuts. */
(function (root) {
  'use strict';
  root.WorldTileEditing = {create};
  function create(d) {
    const {S, $, map, buffer, ensureBuffer, selectMap, screen, hit, rasterize,
      snapshot, commit, render, toast, setTool, ctx} = d;
    const geometry = root.TileSelection, moves = root.TileMove;
    let selection = null, version = 0;
    const active = () => S.mode === 'terrain' && ['move', 'cut'].includes(S.tool);
    const local = (row, p) => ({x: (p.x - row.x) * 16, y: (p.y - row.y) * 16});
    const relative = p => {const q=local(map(selection.name),p);return {x:q.x-selection.x,y:q.y-selection.y};};
    function selectionLabel(){
      selection.count??=selection.mask.reduce((sum,value)=>sum+value,0);
      return selection.pixel?`${selection.count.toLocaleString()} pixels selected`:`${selection.count/256} tile${selection.count===256?'':'s'} selected`;
    }
    function sync() {
      const bar=$('#tile-selection-options');bar.hidden=!active();
      $('#tile-selection-mode').hidden=S.tool!=='move';
      $('#tile-selection-mode').disabled=S.busy||S.finishing||!!S.drag;
      $('#tile-selection-erase').disabled=!selection||!!selection.regions||S.busy||S.finishing||!!S.drag;
      $('#tile-selection-clear').disabled=!selection||S.busy||S.finishing;
      $('#tile-selection-hint').textContent=selection?.regions
        ? `${selection.regions.length} pieces · Click and drag the piece you want to move.`
        : S.tool==='cut'
          ? (selection?'Draw from one selection edge to another, or draw a closed loop.':'Select an area with Select tiles first, or click a tile to select it.')
          : selection
            ? `${selectionLabel()} · Drag the highlight to move together. Shift adds; Alt removes.`
            : 'Drag a box around tiles, then drag the highlight to move them together. Shift adds; Alt removes.';
    }
    function clear() {version++;if(S.drag?.tileRegion)S.drag=null;selection=null;$('#tile-selection-mode').value='replace';sync();render();}
    function cancel() {if(S.drag?.tileRegion){selection=S.drag.previous??null;S.drag=null;}version++;sync();render();}
    function contains(p) {if(!selection)return false;const q=relative(p),x=Math.floor(q.x),y=Math.floor(q.y);return x>=0&&y>=0&&x<selection.width&&y<selection.height&&!!selection.mask[y*selection.width+x];}
    function rectangle(row,a,b) {
      const ax=Math.max(0,Math.min(row.width-1,Math.floor(a.x/16))),ay=Math.max(0,Math.min(row.height-1,Math.floor(a.y/16)));
      const bx=Math.max(0,Math.min(row.width-1,Math.floor(b.x/16))),by=Math.max(0,Math.min(row.height-1,Math.floor(b.y/16)));
      const width=(Math.abs(ax-bx)+1)*16,height=(Math.abs(ay-by)+1)*16;
      return {name:row.name,x:Math.min(ax,bx)*16,y:Math.min(ay,by)*16,width,height,mask:geometry.rectangle(width,height),pixel:false};
    }
    function trim(piece) {
      const b=geometry.bounds(piece.mask,piece.width,piece.height);if(!b)return null;
      const mask=new Uint8Array(b.width*b.height);
      for(let y=0;y<b.height;y++)mask.set(piece.mask.subarray((y+b.y)*piece.width+b.x,(y+b.y)*piece.width+b.x+b.width),y*b.width);
      return {...piece,x:piece.x+b.x,y:piece.y+b.y,width:b.width,height:b.height,mask,count:undefined,regions:null,overlay:null,overlays:null};
    }
    function combine(previous,rect,operation){
      if(operation==='replace'||!previous)return operation==='remove'?null:rect;
      const x=Math.min(previous.x,rect.x),y=Math.min(previous.y,rect.y);
      const width=Math.max(previous.x+previous.width,rect.x+rect.width)-x,height=Math.max(previous.y+previous.height,rect.y+rect.height)-y;
      const mask=new Uint8Array(width*height);
      for(let r=0;r<previous.height;r++)mask.set(previous.mask.subarray(r*previous.width,(r+1)*previous.width),(r+previous.y-y)*width+previous.x-x);
      for(let r=0;r<rect.height;r++)mask.fill(operation==='remove'?0:1,(r+rect.y-y)*width+rect.x-x,(r+rect.y-y)*width+rect.x-x+rect.width);
      return trim({...previous,x,y,width,height,mask});
    }
    function maskedImage(piece,tint) {
      const c=document.createElement('canvas');c.width=piece.width;c.height=piece.height;const context=c.getContext('2d');
      if(tint){context.fillStyle=tint;context.fillRect(0,0,c.width,c.height);}
      else {const b=buffer(piece.name);context.drawImage(b.raster||rasterize(b),piece.x,piece.y,piece.width,piece.height,0,0,c.width,c.height);}
      // Clear row runs outside the piece without reading or changing the source pixels.
      for(let y=0;y<piece.height;y++)for(let x=0;x<piece.width;){if(piece.mask[y*piece.width+x]){x++;continue;}const start=x;while(x<piece.width&&!piece.mask[y*piece.width+x])x++;context.clearRect(start,y,x-start,1);}
      return c;
    }
    function cutPoint(p) {
      const q=relative(p),snap=v=>Math.round(v);
      q.x=snap(q.x);q.y=snap(q.y);
      for(const axis of ['x','y']){const end=axis==='x'?selection.width:selection.height;if(Math.abs(q[axis])<=2)q[axis]=0;else if(Math.abs(q[axis]-end)<=2)q[axis]=end;}
      return q;
    }
    async function pointerDown(event,p,row,press) {
      if(!active()||event.button!==0)return false;
      const operation=S.tool==='move'?(event.altKey?'remove':event.shiftKey||event.ctrlKey||event.metaKey?'add':$('#tile-selection-mode').value||'replace'):'replace';
      if(operation!=='replace'&&selection&&row&&row.name!==selection.name){toast('Add or remove tiles within the selected map. Move maps selects entire map boxes.',true);return true;}
      if(selection?.regions&&contains(p)&&operation==='replace') {
        const q=relative(p),i=geometry.regionAt(selection.regions,selection.width,selection.height,Math.floor(q.x),Math.floor(q.y));
        if(i>=0){selection=trim({...selection,mask:selection.regions[i],pixel:true});setTool('move');}
      }
      if(S.tool==='move'&&contains(p)&&!selection.regions&&operation==='replace') {
        S.drag={tileRegion:'move',start:p,dx:0,dy:0,piece:maskedImage(selection),previous:selection};sync();render();return true;
      }
      if(S.tool==='cut'&&selection&&!selection.regions) {
        S.drag={tileRegion:'cut',path:[cutPoint(p)],previous:selection};sync();render();return true;
      }
      if(!row)return false;
      const loading=(row.name!==S.selected||!buffer(row.name))?selectMap(row.name):null;
      const expected=version;
      if(loading)await loading;
      // Selection changes clear the old piece; pointer release while loading cancels this gesture.
      if(S.pointerActive!==event.pointerId||S.press!==press||!active()||!buffer(row.name))return true;
      if(expected!==version)return true;
      if(S.scale<3){d.fitSelection();toast('Zoomed in. Drag again to select terrain.');return true;}
      const start=local(row,p),previous=selection;
      selection=combine(previous,rectangle(row,start,start),operation);
      if(S.tool==='cut'){toast('Tile selected. Draw a line across it, or use Select tiles to select a larger area.');sync();render();return true;}
      S.drag={tileRegion:'select',name:row.name,start,previous,operation};sync();render();return true;
    }
    function pointerMove(p) {
      const drag=S.drag;if(!drag?.tileRegion)return false;
      if(drag.tileRegion==='select'){selection=combine(drag.previous,rectangle(map(drag.name),drag.start,local(map(drag.name),p)),drag.operation);sync();}
      if(drag.tileRegion==='cut'){
        const q=cutPoint(p),last=drag.path[drag.path.length-1];
        if(q.x!==last.x||q.y!==last.y)drag.path.push(q);
      }
      if(drag.tileRegion==='move'){
        const step=selection.pixel?1:16;
        drag.dx=Math.round((p.x-drag.start.x)*16/step)*step;drag.dy=Math.round((p.y-drag.start.y)*16/step)*step;
      }
      render();return true;
    }
    async function finish(drag) {
      if(!drag?.tileRegion)return false;
      if(drag.tileRegion==='select')$('#tile-selection-mode').value='replace';
      if(drag.tileRegion==='cut'&&selection){
        const path=drag.path,first=path[0],last=path[path.length-1];
        if(path.length>3&&Math.hypot(first.x-last.x,first.y-last.y)<=3)path[path.length-1]={...first};
        const result=geometry.cut(selection.mask,selection.width,selection.height,path);
        if(result.split){selection={...selection,regions:result.regions,overlays:null};setTool('move');toast('Cut ready. Click and drag either piece.');}
        else toast('The line must cross the selection from edge to edge, or make a closed loop.',true);
      }
      if(drag.tileRegion==='move'&&selection&&(drag.dx||drag.dy)) {
        const old=selection,row=map(old.name),bounds=geometry.bounds(old.mask,old.width,old.height);
        const anchor={x:row.x+(old.x+drag.dx+bounds.x+.5)/16,y:row.y+(old.y+drag.dy+bounds.y+.5)/16};
        const target=hit(anchor),token=version;
        if(!target){toast('Drop the complete piece inside an editable map.',true);sync();render();return true;}
        S.finishing=true;d.status();
        try {
          const source=buffer(old.name),dest=await ensureBuffer(target.name);
          if(token!==version)return true;
          const destination={x:Math.round((row.x-target.x)*16+old.x+drag.dx),y:Math.round((row.y-target.y)*16+old.y+drag.dy)};
          const plan=moves.planMove(source.data,dest.data,old,destination,S.erasers.get(old.name)||0);
          const before=new Map([[old.name,snapshot(source)]]);if(target.name!==old.name)before.set(target.name,snapshot(dest));
          Object.assign(source.data,plan.source);Object.assign(dest.data,plan.target);rasterize(source);if(dest!==source)rasterize(dest);commit(before);
          if(target.name!==S.selected)await selectMap(target.name);
          selection={...old,x:destination.x,y:destination.y,name:target.name,overlay:null,overlays:null};
          toast(old.pixel?'Piece moved. Save world to keep it; Ctrl Z to undo.':`${old.count/256} tile${old.count===256?'':'s'} moved together. Save world to keep them; Ctrl Z to undo.`);
        }catch(err){toast(err.message,true);}finally{S.finishing=false;d.status();}
      }
      sync();render();return true;
    }
    function erase() {
      if(!selection||selection.regions||S.busy||S.finishing||S.drag)return;
      try{const b=buffer(selection.name),before=new Map([[selection.name,snapshot(b)]]),plan=moves.planClear(b.data,selection,S.erasers.get(selection.name)||0);Object.assign(b.data,plan);rasterize(b);commit(before);clear();toast('Selected terrain replaced with the eraser tile. Ctrl Z to undo.');}catch(err){toast(err.message,true);}
    }
    function draw() {
      if(!selection||!active())return;
      const row=map(selection.name);if(!row)return;const s=S.scale/16,drag=S.drag?.tileRegion?S.drag:null;
      const p=screen(row.x+selection.x/16,row.y+selection.y/16);ctx.save();ctx.imageSmoothingEnabled=false;
      if(selection.regions){selection.overlays||=selection.regions.map((mask,i)=>maskedImage({...selection,mask},['#67e8f9','#fbcf76','#e4a7f9','#9de59c'][i%4]));selection.overlays.forEach(c=>{ctx.globalAlpha=.36;ctx.drawImage(c,p.x,p.y,c.width*s,c.height*s);});}
      else {selection.overlay||=maskedImage(selection,'#6fe7ed');ctx.globalAlpha=.26;ctx.drawImage(selection.overlay,p.x,p.y,selection.width*s,selection.height*s);}
      ctx.globalAlpha=1;ctx.strokeStyle='#164a51';ctx.lineWidth=1.5;ctx.setLineDash([5,4]);ctx.strokeRect(p.x,p.y,selection.width*s,selection.height*s);ctx.setLineDash([]);
      if(drag?.tileRegion==='move'){ctx.globalAlpha=.94;ctx.drawImage(drag.piece,p.x+drag.dx*s,p.y+drag.dy*s,selection.width*s,selection.height*s);ctx.globalAlpha=1;ctx.strokeStyle='#fff';ctx.strokeRect(p.x+drag.dx*s,p.y+drag.dy*s,selection.width*s,selection.height*s);}
      if(drag?.tileRegion==='cut'){ctx.beginPath();drag.path.forEach((q,i)=>ctx[i?'lineTo':'moveTo'](p.x+q.x*s,p.y+q.y*s));ctx.strokeStyle='#fff';ctx.lineWidth=4;ctx.stroke();ctx.strokeStyle='#d44169';ctx.lineWidth=2;ctx.stroke();}
      ctx.restore();
    }
    $('#tile-selection-clear').onclick=clear;$('#tile-selection-erase').onclick=erase;
    return {active,sync,clear,cancel,draw,pointerDown,pointerMove,finish,erase,
      hasSelection:()=>!!selection,
      key(event){if(event.key==='Escape'&&(selection||S.drag?.tileRegion)){event.preventDefault();clear();return true;}if(['Delete','Backspace'].includes(event.key)&&active()&&selection){event.preventDefault();erase();return true;}return false;}};
  }
})(typeof globalThis!=='undefined'?globalThis:this);
