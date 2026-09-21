'use strict';
// Exercise the real controller and geometry/move helpers without a browser or
// source-file writes. Rendering dependencies record calls; map buffers are RAM.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const TileSelection=require('../web/tile-selection.js');
const TileMove=require('../web/tile-move.js');
const script=fs.readFileSync(path.join(__dirname,'../web/worldmap-selection.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const cases=[];
const test=(name,fn)=>cases.push([name,fn]);

function environment(){
  const nodes=new Map(),toasts=[],commits=[],deferred=new Map(),canvases=[];
  function context(){const operations=[];return new Proxy({operations},{get(target,key){if(key in target)return target[key];return (...args)=>operations.push([key,...args]);}});}
  function canvas(){const ctx=context(),value={width:0,height:0,getContext:()=>ctx,ctx};canvases.push(value);return value;}
  function $(selector){if(!nodes.has(selector))nodes.set(selector,{hidden:false,disabled:false,textContent:'',onclick:null});return nodes.get(selector);}
  const maps=[{name:'A',x:0,y:0,width:4,height:4},{name:'B',x:6,y:0,width:4,height:4}];
  const records=new Map(maps.map((row,index)=>[row.name,{name:row.name,width:row.width,height:row.height,
    cells:Array.from({length:16},(_,i)=>((index?5:3)<<12)|((i%4)<<10)|(index?16-i:i+1)),
    pixel_patches:{},border:[0,0,0,0],map:{id:`MAP_${row.name}`,object_events:[{x:1,y:1,script:'KeepMe'}],connections:[]},
    layout:{primary_tileset:'gTileset_Primary',secondary_tileset:'gTileset_Secondary'},
    tileset:{primary:'gTileset_Primary',secondary:'gTileset_Secondary',metatiles:Array.from({length:17},(_,id)=>({id,valid:true,layer_type:0,attribute:0}))}}]));
  const buffers=new Map([...records].map(([name,data])=>[name,{data:clone(data),raster:null}]));
  const S={mode:'terrain',tool:'move',selected:'A',scale:16,busy:false,finishing:false,drag:null,pointerActive:null,press:0,erasers:new Map([['A',0],['B',0]])};
  const map=(name=S.selected)=>maps.find(row=>row.name===name),buffer=(name=S.selected)=>buffers.get(name);
  const hit=p=>maps.find(row=>p.x>=row.x&&p.y>=row.y&&p.x<row.x+row.width&&p.y<row.y+row.height)||null;
  const snapshot=b=>clone({width:b.data.width,height:b.data.height,cells:b.data.cells,pixel_patches:b.data.pixel_patches||{},border:b.data.border,map:b.data.map,layout:b.data.layout});
  const snapshots=()=>Object.fromEntries([...buffers].map(([name,b])=>[name,snapshot(b)]));
  const ensureBuffer=async name=>{if(deferred.has(name))return deferred.get(name).promise;if(!buffers.has(name))buffers.set(name,{data:clone(records.get(name)),raster:null});return buffers.get(name);};
  let controller;
  const selectMap=async name=>{S.selected=name;controller.clear();await ensureBuffer(name);};
  const ctx=context(),dependencies={S,$,map,buffer,ensureBuffer,selectMap,hit,screen:(x,y)=>({x:x*S.scale,y:y*S.scale}),ctx,
    rasterize:b=>b.raster||(b.raster=canvas()),snapshot,
    commit:before=>commits.push(new Map([...before].map(([name,value])=>[name,clone(value)]))),
    render(){},toast:(message,bad=false)=>toasts.push({message,bad}),setTool:tool=>{S.tool=tool;controller.sync();},
    fitSelection:()=>{S.scale=16;},status(){}};
  const sandbox={TileSelection,TileMove,Uint8Array,Uint32Array,Map,Set,console,document:{createElement:tag=>{assert.equal(tag,'canvas');return canvas();}}};
  vm.runInNewContext(script,sandbox,{filename:'worldmap-selection.js'});controller=sandbox.WorldTileEditing.create(dependencies);
  async function down(x,y,modifiers={},pointerId=1){const press=++S.press;S.pointerActive=pointerId;const p={x,y};return controller.pointerDown({button:0,pointerId,...modifiers},p,hit(p),press);}
  const move=(x,y)=>controller.pointerMove({x,y});
  async function up(){const drag=S.drag;S.drag=null;S.pointerActive=null;return controller.finish(drag);}
  async function rectangle(x1,y1,x2,y2,modifiers={}){await down(x1,y1,modifiers);move(x2,y2);await up();}
  function undo(){const before=commits.pop();assert.ok(before);for(const[name,value]of before)Object.assign(buffer(name).data,clone(value));}
  function defer(name,{unload=false}={}){let resolve;const promise=new Promise(r=>resolve=r);if(unload)buffers.delete(name);deferred.set(name,{promise});return ()=>{deferred.delete(name);if(!buffers.has(name))buffers.set(name,{data:clone(records.get(name)),raster:null});resolve(buffer(name));};}
  const pixel=(name,x,y)=>{const b=buffer(name).data,index=Math.floor(y/16)*b.width+Math.floor(x/16),p=(y%16)*16+x%16;return b.pixel_patches?.[index]?.[p]??(b.cells[index]&1023)*256+p;};
  return {controller,S,$,buffers,map,buffer,down,move,up,rectangle,undo,defer,pixel,snapshot,snapshots,toasts,commits,ctx,canvases};
}

test('rectangle selection is nondestructive; moving it creates one exact Undo snapshot',async()=>{
  const e=environment(),before=e.snapshots();
  await e.rectangle(.1,.1,1.8,.2);assert.equal(e.controller.hasSelection(),true);assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);
  await e.down(.2,.2);e.move(1.2,1.2);await e.up();
  assert.equal(e.commits.length,1);assert.equal(e.commits[0].size,1);assert.deepEqual(e.commits[0].get('A'),before.A);
  const after=e.buffer('A').data;assert.equal(after.cells[5],before.A.cells[0]);assert.equal(after.cells[6],before.A.cells[1]);
  assert.equal(after.cells[0],before.A.cells[0]&0xfc00);assert.equal(after.cells[1],before.A.cells[1]&0xfc00);
  assert.deepEqual(after.map,before.A.map);assert.deepEqual(after.layout,before.A.layout);assert.deepEqual(e.snapshot(e.buffer('B')),before.B);
  e.undo();assert.deepEqual(e.snapshots(),before);
});

test('overlapping tile moves copy from the original selection, not partially erased cells',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,.1);
  await e.down(.2,.2);e.move(1.2,.2);await e.up();
  assert.equal(e.commits.length,1);assert.deepEqual(e.buffer().data.cells.slice(0,3),[before.A.cells[0]&0xfc00,before.A.cells[0],before.A.cells[1]]);
  e.undo();assert.deepEqual(e.snapshots(),before);
});

test('Shift-selected disjoint tiles move together, preserve their gaps, and share one Undo',async()=>{
  const e=environment(),before=e.snapshots();
  await e.rectangle(.1,.1,.1,.1);await e.rectangle(2.1,.1,2.1,.1,{shiftKey:true});
  assert.match(e.$('#tile-selection-hint').textContent,/^2 tiles selected/);assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);
  await e.down(.2,.2);e.move(.2,1.2);await e.up();
  const after=e.buffer('A').data.cells;
  assert.equal(e.commits.length,1);assert.equal(e.commits[0].size,1);
  assert.equal(after[4],before.A.cells[0]);assert.equal(after[6],before.A.cells[2]);
  assert.equal(after[0],before.A.cells[0]&0xfc00);assert.equal(after[2],before.A.cells[2]&0xfc00);
  assert.equal(after[1],before.A.cells[1]);assert.equal(after[5],before.A.cells[5]);
  assert.deepEqual(e.buffer('A').data.map,before.A.map);assert.deepEqual(e.snapshot(e.buffer('B')),before.B);
  e.undo();assert.deepEqual(e.snapshots(),before);
});

test('overlapping additive rectangles count each tile once and do not edit terrain',async()=>{
  const e=environment(),before=e.snapshots();
  await e.rectangle(.1,.1,1.9,.1);await e.rectangle(1.1,.1,2.9,.1,{shiftKey:true});
  assert.match(e.$('#tile-selection-hint').textContent,/^3 tiles selected/);assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
  await e.down(.2,.2);e.move(.2,1.2);await e.up();
  assert.deepEqual(e.buffer('A').data.cells.slice(4,7),before.A.cells.slice(0,3));assert.equal(e.commits.length,1);
});

for(const modifier of ['ctrlKey','metaKey'])test(`${modifier} adds a tile without moving the existing selection`,async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.rectangle(2.1,.1,2.1,.1,{[modifier]:true});
  assert.match(e.$('#tile-selection-hint').textContent,/^2 tiles selected/);assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
});

test('Alt removes tiles from a selection, preserving removed terrain when the remaining group moves',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,2.9,.1);
  await e.rectangle(1.1,.1,1.1,.1,{altKey:true});
  assert.match(e.$('#tile-selection-hint').textContent,/^2 tiles selected/);assert.deepEqual(e.snapshots(),before);
  await e.down(.2,.2);e.move(.2,1.2);await e.up();
  const after=e.buffer('A').data.cells;
  assert.equal(after[4],before.A.cells[0]);assert.equal(after[6],before.A.cells[2]);
  assert.equal(after[1],before.A.cells[1]);assert.equal(after[5],before.A.cells[5]);
  assert.equal(e.commits.length,1);e.undo();assert.deepEqual(e.snapshots(),before);
});

test('removing the last selected tile clears the selection without deleting terrain',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.rectangle(.1,.1,.1,.1,{altKey:true});
  assert.equal(e.controller.hasSelection(),false);assert.equal(e.$('#tile-selection-erase').disabled,true);
  assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
});

test('cancelling an additive rectangle restores exactly the previous selection',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.down(2.1,.1,{shiftKey:true});e.move(2.9,1.9);
  assert.match(e.$('#tile-selection-hint').textContent,/^3 tiles selected/);e.controller.cancel();
  assert.match(e.$('#tile-selection-hint').textContent,/^1 tile selected/);assert.equal(e.S.drag,null);assert.deepEqual(e.snapshots(),before);
  await e.down(.2,.2);e.move(.2,1.2);await e.up();
  const after=e.buffer('A').data.cells;
  assert.equal(after[4],before.A.cells[0]);assert.equal(after[2],before.A.cells[2]);assert.equal(after[6],before.A.cells[6]);
  assert.equal(e.commits.length,1);e.undo();assert.deepEqual(e.snapshots(),before);
});

test('the selection mode menu adds and removes without modifier keys, then returns to dragging',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  e.$('#tile-selection-mode').value='add';await e.rectangle(2.1,.1,2.1,.1);
  assert.match(e.$('#tile-selection-hint').textContent,/^2 tiles selected/);assert.equal(e.$('#tile-selection-mode').value,'replace');
  e.$('#tile-selection-mode').value='remove';await e.rectangle(.1,.1,.1,.1);
  assert.match(e.$('#tile-selection-hint').textContent,/^1 tile selected/);assert.equal(e.$('#tile-selection-mode').value,'replace');
  assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
  await e.down(2.2,.2);e.move(2.2,1.2);await e.up();
  assert.equal(e.buffer('A').data.cells[6],before.A.cells[2]);assert.equal(e.buffer('A').data.cells[0],before.A.cells[0]);assert.equal(e.commits.length,1);
});

test('adding across maps is rejected without discarding the current group or modifying either map',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,.1);
  assert.equal(await e.down(6.1,.1,{shiftKey:true}),true);await e.up();
  assert.equal(e.S.selected,'A');assert.equal(e.S.drag,null);assert.equal(e.controller.hasSelection(),true);
  assert.match(e.$('#tile-selection-hint').textContent,/^2 tiles selected/);assert.ok(e.toasts.at(-1).bad);assert.match(e.toasts.at(-1).message,/within the selected map/);
  assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
  await e.down(.2,.2);e.move(.2,1.2);await e.up();assert.deepEqual(e.buffer('A').data.cells.slice(4,6),before.A.cells.slice(0,2));
  assert.deepEqual(e.snapshot(e.buffer('B')),before.B);assert.equal(e.commits.length,1);
});

test('a selection assembled from additive rectangles still supports cutting and moving a pixel piece',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,.1);
  await e.rectangle(.1,1.1,1.9,1.9,{shiftKey:true});assert.match(e.$('#tile-selection-hint').textContent,/^4 tiles selected/);
  e.S.tool='cut';await e.down(0,0);e.move(1,1);e.move(2,2);await e.up();
  assert.equal(e.S.tool,'move');assert.match(e.$('#tile-selection-hint').textContent,/2 pieces/);assert.deepEqual(e.snapshots(),before);
  const chosen=e.pixel('A',2,24),untouched=e.pixel('A',25,2);
  await e.down(2/16,24/16);e.move(5/16,29/16);await e.up();
  assert.equal(e.pixel('A',5,29),chosen);assert.equal(e.pixel('A',25,2),untouched);assert.equal(e.commits.length,1);
  e.undo();assert.deepEqual(e.snapshots(),before);
});

test('a diagonal cut selects a piece and moves it by arbitrary individual pixels',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.05,.05,1.95,1.95);e.controller.draw();
  e.S.tool='cut';await e.down(0,0);e.move(1,1);e.move(2,2);await e.up();
  assert.equal(e.S.tool,'move');assert.match(e.$('#tile-selection-hint').textContent,/2 pieces/);assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);
  const chosen=e.pixel('A',2,24),untouched=e.pixel('A',25,2);
  await e.down(2/16,24/16);e.move(5/16,29/16);await e.up();
  assert.equal(e.commits.length,1);assert.equal(e.pixel('A',5,29),chosen);assert.equal(e.pixel('A',2,24),8*16+2);
  assert.equal(e.pixel('A',25,2),untouched);assert.ok(Object.keys(e.buffer().data.pixel_patches).length>0);
  assert.deepEqual(e.buffer().data.map,before.A.map);e.undo();assert.deepEqual(e.snapshots(),before);
});

test('an incomplete cut produces no edit and leaves the selection reusable',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,1.9);e.S.tool='cut';
  await e.down(.4,.5);e.move(1.5,.5);await e.up();
  assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);assert.ok(e.toasts.at(-1).bad);assert.equal(e.controller.hasSelection(),true);
});

test('a moved piece must fit completely; rejected partial overflow changes neither map',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,.1);
  await e.down(.2,.2);e.move(3.2,.2);await e.up();
  assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);assert.ok(e.toasts.at(-1).bad);assert.match(e.toasts.at(-1).message,/fit/);assert.equal(e.S.finishing,false);
});

test('dropping outside every map preserves the selection and all data',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.down(.2,.2);e.move(-4.8,.2);await e.up();
  assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);assert.ok(e.toasts.at(-1).bad);assert.equal(e.controller.hasSelection(),true);
});

test('cross-map moves snapshot both maps together and Undo restores both',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.down(.2,.2);e.move(6.2,.2);await e.up();
  assert.equal(e.commits.length,1);assert.equal(e.commits[0].size,2);assert.equal(e.buffer('B').data.cells[0],before.A.cells[0]);
  assert.equal(e.buffer('A').data.cells[0],before.A.cells[0]&0xfc00);assert.equal(e.S.selected,'B');assert.equal(e.controller.hasSelection(),true);
  e.undo();assert.deepEqual(e.snapshots(),before);
});

test('pointer cancellation during a move discards only its preview',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);
  await e.down(.2,.2);e.move(2.2,1.2);e.S.pointerActive=null;e.controller.cancel();
  assert.equal(e.S.drag,null);assert.equal(e.controller.hasSelection(),true);assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
  await e.down(.2,.2);e.move(1.2,.2);await e.up();assert.equal(e.commits.length,1);
});

test('cancelling the first rectangle restores the original empty selection',async()=>{
  const e=environment(),before=e.snapshots();await e.down(.1,.1);e.move(1.5,1.5);e.controller.cancel();
  assert.equal(e.S.drag,null);assert.equal(e.controller.hasSelection(),false);assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
});

test('Escape cancels an active drag without editing and prevents a later release from applying it',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);await e.down(.2,.2);e.move(1.2,.2);
  let prevented=false;assert.equal(e.controller.key({key:'Escape',preventDefault(){prevented=true;}}),true);assert.ok(prevented);
  assert.equal(e.controller.hasSelection(),false);await e.up();assert.deepEqual(e.snapshots(),before);assert.equal(e.commits.length,0);
});

test('pointer release while source artwork loads cannot start a delayed selection',async()=>{
  const e=environment(),resolve=e.defer('B',{unload:true});
  const pending=e.down(6.1,.1);e.S.pointerActive=null;assert.equal(e.S.drag,null);resolve();await pending;
  assert.equal(e.S.drag,null);assert.equal(e.controller.hasSelection(),false);assert.equal(e.commits.length,0);
});

test('clearing a pending source selection prevents a late drag even while its pointer remains down',async()=>{
  const e=environment(),resolve=e.defer('B',{unload:true});
  const pending=e.down(6.1,.1);assert.equal(e.S.selected,'B');e.controller.clear();
  resolve();await pending;
  assert.equal(e.S.drag,null);assert.equal(e.controller.hasSelection(),false);assert.equal(e.commits.length,0);
});

test('clearing while a destination loads cancels the move before any buffer mutation',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,.1,.1);await e.down(.2,.2);e.move(6.2,.2);
  const resolve=e.defer('B'),pending=e.up();assert.equal(e.S.finishing,true);e.controller.clear();resolve();await pending;
  assert.equal(e.S.finishing,false);assert.equal(e.commits.length,0);assert.deepEqual(e.snapshots(),before);assert.equal(e.controller.hasSelection(),false);
});

test('erase uses one reversible edit and leaves event metadata unchanged',async()=>{
  const e=environment(),before=e.snapshots();await e.rectangle(.1,.1,1.9,.1);e.controller.erase();
  assert.equal(e.commits.length,1);assert.equal(e.controller.hasSelection(),false);assert.equal(e.buffer().data.cells[0],before.A.cells[0]&0xfc00);
  assert.equal(e.buffer().data.cells[1],before.A.cells[1]&0xfc00);assert.deepEqual(e.buffer().data.map,before.A.map);e.undo();assert.deepEqual(e.snapshots(),before);
});

test('nonselection tools and non-left buttons do not consume pointer gestures',async()=>{
  const e=environment();e.S.tool='brush';assert.equal(await e.down(.1,.1),false);e.S.tool='move';
  assert.equal(await e.controller.pointerDown({button:2,pointerId:1},{x:.1,y:.1},e.map(),e.S.press),false);
  assert.equal(e.controller.hasSelection(),false);assert.equal(e.S.drag,null);assert.equal(e.commits.length,0);
});

(async()=>{let failed=0;for(const[name,fn]of cases){try{await fn();console.log(`ok - ${name}`);}catch(error){failed++;console.error(`not ok - ${name}\n${error.stack}`);}}if(failed)process.exitCode=1;else console.log(`World selection controller passed: ${cases.length} tests; all data stayed in isolated memory.`);})();
