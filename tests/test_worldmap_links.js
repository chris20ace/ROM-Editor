'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),test=require('node:test');
const code=fs.readFileSync(require('node:path').join(__dirname,'../web/worldmap-links.js'),'utf8');
const copy=v=>v===undefined?undefined:JSON.parse(JSON.stringify(v));
function harness(options={}){
  const rows=[{name:'A',x:10,y:20,width:20,height:20},{name:'B',x:30,y:22,width:20,height:20}];
  const S={ready:true,busy:false,finishing:false,drag:null,selected:'A',maps:rows,buffers:new Map(),category:'all'};
  const nodes=new Map(),calls=[],dialogs=[],toasts=[],reloads=[],follows=[],fits=[],draws=[];
  const $=q=>{if(!nodes.has(q))nodes.set(q,{value:q==='#world-links-scope'?'outdoors':'',checked:false,innerHTML:'',textContent:'',setAttribute(){}});return nodes.get(q);};
  const map=(name=S.selected)=>rows.find(r=>r.name===name);
  const edge={from_name:'A',to_name:'B',direction:'right',offset:0};
  const catalog={revision:'source-r1',positions_revision:'positions-r1',edges:[edge],maps:rows,audit:{portals:[],issues:[]}};
  const result={revision:'source-preview',positions_revision:'position-preview',scope_names:['A','B'],added:[{...edge,offset:2}],removed:[edge],warnings:[],errors:[],can_apply:true,...options.preview};
  let resolveDialog;
  const ctx={save(){},restore(){},setLineDash(){},beginPath(){},moveTo(x,y){draws.push(['move',x,y]);},lineTo(x,y){draws.push(['line',x,y]);},stroke(){},arc(){},fill(){},closePath(){},strokeRect(){}};
  const deps={S,$,map,mapBoxes:{selectionNames:()=>options.names||['A','B'],positionRevision:()=>'canvas-current'},ctx,
    esc:v=>String(v),toast:(...v)=>toasts.push(v),status(){},render(){},screen:(x,y)=>({x,y}),included:()=>true,renderPlaces(){},
    api:async(url,body)=>{calls.push({url,body:copy(body)});if(options.error===url&&body!==undefined)throw Error('request failed');if(body===undefined)return copy(catalog);return url.endsWith('/preview')?copy(result):{transaction:'saved'};},
    dialog:(title,html,button,info)=>{dialogs.push({title,html,button,info});return new Promise(r=>{resolveDialog=r;});},
    requireSavedWorld:async()=>options.saved!==false,
    reloadWorld:async(...args)=>{reloads.push(args);if(options.reloadError)throw Error('refresh failed');},
    selectMap:async name=>{follows.push(name);S.selected=name;},fitSelection:()=>fits.push('selection'),fitBounds:b=>fits.push(b),editWarp(){}};
  const sandbox=vm.createContext({});vm.runInContext(code,sandbox);
  const controller=sandbox.WorldMapLinks.create(deps);
  return {controller,S,$,map,catalog,calls,dialogs,toasts,reloads,follows,fits,draws,edgePoints:sandbox.WorldMapLinks.edgePoints,
    resolve:v=>resolveDialog(v),waitDialog:async()=>{for(let i=0;i<12&&!dialogs.length;i++)await Promise.resolve();assert.ok(dialogs.length);}};
}
test('walking endpoints follow exact source offset and current box positions without distortion',()=>{
  const h=harness(),e={from_name:'A',to_name:'B',direction:'right',offset:0};
  let p=h.edgePoints(e,h.map);assert.deepEqual(copy(p.a),{x:30,y:30});assert.deepEqual(copy(p.b),{x:30,y:32});assert.equal(p.aligned,false);
  p=h.edgePoints({...e,offset:2},h.map);assert.deepEqual(copy(p.a),{x:30,y:31});assert.deepEqual(copy(p.b),{x:30,y:31});assert.equal(p.aligned,true);
  h.map('B').x++;assert.equal(h.edgePoints({...e,offset:2},h.map).aligned,false);
});
test('corner-only or unresolved links do not invent a crossing point',()=>{
  const h=harness();assert.equal(h.edgePoints({from_name:'A',to_name:'B',direction:'right',offset:20},h.map),null);
  assert.equal(h.edgePoints({from_name:'A',to_name:'Missing',direction:'right',offset:0},h.map),null);
});
test('preview applies exact reviewed source and position revisions then reloads',async()=>{
  const h=harness(),run=h.controller.preview();await h.waitDialog();assert.equal(h.calls.length,2);assert.equal(h.calls[1].body.positions_revision,'canvas-current');
  assert.match(h.dialogs[0].html,/Remove old links/);assert.equal(h.dialogs[0].button,'Apply to game');h.resolve(true);await run;
  assert.deepEqual(h.calls[2],{url:'/api/worldmap/links',body:{revision:'source-preview',positions_revision:'position-preview'}});
  assert.deepEqual(h.reloads,[['A',false]]);assert.equal(h.S.busy,false);
});
test('selected scope sends only selected map names; cancellation never commits',async()=>{
  const h=harness();h.$('#world-links-scope').value='selection';const run=h.controller.preview();await h.waitDialog();assert.deepEqual(h.calls[1].body.names,['A','B']);h.resolve(null);await run;assert.equal(h.calls.length,2);assert.equal(h.reloads.length,0);
});
test('overlaps and unchanged plans cannot offer Apply to game',async()=>{
  for(const proposal of[{errors:['A overlaps B'],can_apply:false},{added:[],removed:[],can_apply:false}]){
    const h=harness({preview:proposal}),run=h.controller.preview();await h.waitDialog();assert.equal(h.dialogs[0].info,true);h.resolve(true);await run;assert.equal(h.calls.length,2);
  }
});
test('unsaved cancellation, busy state, and single selection perform no network writes',async()=>{
  for(const setup of[{saved:false},{names:['A']},{busy:true},{drag:true},{finishing:true},{unready:true}]){
    const h=harness(setup);if(setup.names)h.$('#world-links-scope').value='selection';if(setup.busy)h.S.busy=true;if(setup.drag)h.S.drag={};if(setup.finishing)h.S.finishing=true;if(setup.unready)h.S.ready=false;
    await h.controller.preview();assert.equal(h.calls.length,0);
  }
});
test('failed preview never opens a confirmation and failed commit never reloads',async()=>{
  const h=harness({error:'/api/worldmap/links/preview'});await h.controller.preview();assert.equal(h.dialogs.length,0);assert.match(h.toasts[0][0],/failed/);assert.equal(h.S.busy,false);
  const c=harness({error:'/api/worldmap/links'}),run=c.controller.preview();await c.waitDialog();c.resolve(true);await run;assert.equal(c.reloads.length,0);assert.equal(c.S.busy,false);
});
test('a successful commit followed by refresh failure reports saved state accurately',async()=>{
  const h=harness({reloadError:true}),run=h.controller.preview();await h.waitDialog();h.resolve(true);await run;assert.match(h.toasts.at(-1)[0],/were saved/);
});
test('Follow destination centers on the exact local entrance tile in its moved map',async()=>{
  const h=harness();await h.controller.follow('B',{x:4,y:6});assert.deepEqual(h.follows,['B']);assert.deepEqual(copy(h.fits[0]),{x:27,y:22,width:15,height:13});
});
test('fixed portals draw from exact tile centers; dynamic destinations never get guessed',async()=>{
  const h=harness();h.catalog.edges=[];h.catalog.audit.portals=[{status:'fixed',source:{map:'A',index:0,x:2,y:3},destination:{map:'B',index:1,x:4,y:5}},{status:'dynamic',source:{map:'A',index:1,x:3,y:4},destination:null}];
  await h.$('#world-links-toggle').onclick();h.controller.draw();assert.deepEqual(h.draws[0],['move',12.5,23.5]);assert.deepEqual(h.draws[1],['line',34.5,27.5]);assert.equal(h.draws.filter(x=>x[0]==='move').length,2); // shaft and arrow head, no dynamic line
});
