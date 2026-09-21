'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const TileSelection=require('../web/tile-selection.js');
const {rectangle,cut,regionAt,bounds}=TileSelection;
let passed=0;
function test(name,fn){fn();passed++;console.log(`ok - ${name}`);}
function count(mask){return mask.reduce((total,value)=>total+value,0);}
function partition(mask,width,height,line){
  const before=Array.from(mask),pathBefore=JSON.stringify(line),result=cut(mask,width,height,line);
  assert.deepEqual(Array.from(mask),before,'cut must not change the input mask');
  assert.equal(JSON.stringify(line),pathBefore,'cut must not change the drawn path');
  const union=new Uint8Array(mask.length);
  for(const region of result.regions){
    assert.ok(region instanceof Uint8Array);
    assert.equal(region.length,mask.length);assert.ok(count(region)>0);
    assert.notEqual(region,mask);
    for(let i=0;i<region.length;i++){assert.ok(region[i]===0||region[i]===1);union[i]+=region[i];assert.ok(union[i]<=1,'pieces must not overlap');}
  }
  assert.deepEqual(Array.from(union),before,'every selected pixel must belong to exactly one piece');
  return result;
}

test('rectangle creates every selected pixel and independent masks',()=>{
  const a=rectangle(7,5),b=rectangle(7,5);assert.equal(count(a),35);assert.equal(a.length,35);
  a[0]=0;assert.equal(b[0],1);assert.deepEqual(bounds(b,7,5),{x:0,y:0,width:7,height:5});
});

test('horizontal arbitrary-pixel cut keeps both complete halves',()=>{
  const result=partition(rectangle(11,9),11,9,[{x:0,y:3},{x:11,y:3}]);
  assert.equal(result.split,true);assert.equal(result.originalRegionCount,1);
  assert.deepEqual(result.regions.map(count),[33,66]);
  assert.deepEqual(bounds(result.regions[0],11,9),{x:0,y:0,width:11,height:3});
});

test('vertical cut can extend beyond both selection boundaries',()=>{
  const result=partition(rectangle(10,7),10,7,[{x:3,y:-2},{x:3,y:12}]);
  assert.equal(result.split,true);assert.deepEqual(result.regions.map(count),[21,49]);
});

test('diagonal through pixel centers does not remove its line pixels',()=>{
  const result=partition(rectangle(7,7),7,7,[{x:0,y:0},{x:7,y:7}]);
  assert.equal(result.regions.length,2);assert.equal(result.split,true);
  assert.deepEqual(result.regions.map(count),[28,21]);
  for(let i=0;i<7;i++)assert.equal(regionAt(result.regions,7,7,i,i),0);
});

test('reversing a diagonal path gives the same deterministic partition',()=>{
  const line=[{x:0,y:1.3},{x:4.1,y:2.4},{x:9,y:7.8}];
  const a=partition(rectangle(9,8),9,8,line),b=partition(rectangle(9,8),9,8,[...line].reverse());
  assert.equal(a.split,true);assert.deepEqual(a.regions,b.regions);
});

test('a curved freehand cut follows each bend without an 8px grid',()=>{
  const line=[{x:0,y:2.2},{x:2.4,y:2.8},{x:3.3,y:5.7},{x:7.1,y:3.3},{x:10,y:5.1}];
  const result=partition(rectangle(10,8),10,8,line);
  assert.equal(result.regions.length,2);assert.equal(result.split,true);
  assert.equal(regionAt(result.regions,10,8,3,4),0);
  assert.equal(regionAt(result.regions,10,8,7,4),1);
  assert.notEqual(regionAt(result.regions,10,8,0,0),regionAt(result.regions,10,8,9,7));
});

test('closed freehand loop isolates its interior without losing outline pixels',()=>{
  const line=[{x:1.2,y:1.1},{x:5.4,y:1.7},{x:5.1,y:5.3},{x:1.1,y:5.7},{x:1.2,y:1.1}];
  const result=partition(rectangle(7,7),7,7,line);
  assert.equal(result.regions.length,2);assert.equal(result.split,true);
  assert.notEqual(regionAt(result.regions,7,7,3,3),regionAt(result.regions,7,7,0,0));
});

test('a closed rectangle has an exactly bounded interior piece',()=>{
  const result=partition(rectangle(8,8),8,8,[{x:2,y:1},{x:6,y:1},{x:6,y:5},{x:2,y:5},{x:2,y:1}]);
  const interior=result.regions[regionAt(result.regions,8,8,3,3)];
  assert.equal(count(interior),16);assert.deepEqual(bounds(interior,8,8),{x:2,y:1,width:4,height:4});
});

test('a line stopping inside the selection does not invent a split',()=>{
  for(const line of [[{x:0,y:3},{x:4,y:3}],[{x:2,y:3},{x:5,y:3}],[],[{x:3,y:3}]]){
    const result=partition(rectangle(8,7),8,7,line);assert.equal(result.split,false);assert.equal(result.regions.length,1);
  }
});

test('open cuts must reach actual mask boundaries, not just pass its outer pixel centers',()=>{
  const result=partition(rectangle(5,5),5,5,[{x:.25,y:2},{x:4.75,y:2}]);
  assert.equal(result.complete,false);assert.equal(result.split,false);assert.equal(result.regions.length,1);
  const thin=partition(rectangle(1,4),1,4,[{x:.1,y:2},{x:.9,y:2}]);
  assert.equal(thin.complete,false);assert.equal(thin.split,false);
});

test('a selected shape can be cut from its own boundary inside the mask dimensions',()=>{
  const mask=new Uint8Array(8*8);for(let y=2;y<6;y++)for(let x=2;x<6;x++)mask[y*8+x]=1;
  const result=partition(mask,8,8,[{x:2,y:3},{x:6,y:3}]);
  assert.equal(result.complete,true);assert.equal(result.split,true);assert.deepEqual(result.regions.map(count),[4,12]);
});

test('a line along the outside border does not split',()=>{
  const result=partition(rectangle(5,6),5,6,[{x:0,y:0},{x:5,y:0}]);
  assert.equal(result.split,false);assert.equal(result.regions.length,1);
});

test('an empty selection has no regions and no bounds',()=>{
  const mask=new Uint8Array(24),result=partition(mask,6,4,[{x:0,y:2},{x:6,y:2}]);
  assert.equal(result.split,false);assert.equal(result.regions.length,0);assert.equal(bounds(mask,6,4),null);
  assert.equal(regionAt(result.regions,6,4,2,2),-1);
});

test('cuts respect holes and never select pixels outside the original mask',()=>{
  const mask=rectangle(9,9);for(let y=3;y<6;y++)for(let x=3;x<6;x++)mask[y*9+x]=0;
  const result=partition(mask,9,9,[{x:0,y:4.5},{x:9,y:4.5}]);
  assert.equal(result.split,true);assert.equal(result.regions.length,2);
  assert.equal(regionAt(result.regions,9,9,4,4),-1);
});

test('preexisting disconnected pieces do not count as a new split',()=>{
  const mask=rectangle(7,5);for(let y=0;y<5;y++)mask[y*7+3]=0;
  const result=partition(mask,7,5,[]);assert.equal(result.originalRegionCount,2);assert.equal(result.regions.length,2);assert.equal(result.split,false);
});

test('a polyline may create more than two selectable pieces',()=>{
  const result=partition(rectangle(8,8),8,8,[{x:0,y:2},{x:8,y:2},{x:8,y:6},{x:0,y:6}]);
  assert.equal(result.split,true);assert.equal(result.regions.length,3);assert.deepEqual(result.regions.map(count),[16,32,16]);
});

test('center-line ties assign whole pixels consistently',()=>{
  const horizontal=partition(rectangle(6,5),6,5,[{x:0,y:2.5},{x:6,y:2.5}]);
  const vertical=partition(rectangle(6,5),6,5,[{x:2.5,y:0},{x:2.5,y:5}]);
  assert.deepEqual(horizontal.regions.map(count),[18,12]);assert.deepEqual(vertical.regions.map(count),[15,15]);
});

test('region lookup uses fractional pixel coordinates and excludes outside points',()=>{
  const result=cut(rectangle(6,4),6,4,[{x:3,y:0},{x:3,y:4}]);
  assert.equal(regionAt(result.regions,6,4,2.99,1.1),0);assert.equal(regionAt(result.regions,6,4,3,1.1),1);
  for(const [x,y]of[[-.1,0],[6,0],[0,-1],[0,4],[NaN,0]])assert.equal(regionAt(result.regions,6,4,x,y),-1);
});

test('one-pixel-wide selections and repeated line points work',()=>{
  const result=partition(rectangle(1,5),1,5,[{x:0,y:2},{x:0,y:2},{x:1,y:2}]);
  assert.equal(result.split,true);assert.deepEqual(result.regions.map(count),[2,3]);
});

test('invalid dimensions, masks and paths fail without modifying inputs',()=>{
  assert.throws(()=>rectangle(0,3),/positive whole/);assert.throws(()=>rectangle(2.5,3),/positive whole/);
  assert.throws(()=>cut(new Uint8Array(5),2,3,[]),/match/);
  assert.throws(()=>cut([0,2,1,0],2,2,[]),/0 or 1/);
  const mask=rectangle(2,2),before=Array.from(mask);
  assert.throws(()=>cut(mask,2,2,[{x:NaN,y:0}]),/finite/);
  assert.throws(()=>cut(mask,2,2,null),/array/);assert.deepEqual(Array.from(mask),before);
});

test('the browser UMD exposes only the pure geometry API',()=>{
  const context={};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/tile-selection.js'),'utf8'),context);
  assert.deepEqual(Object.keys(context.TileSelection).sort(),['bounds','cut','rectangle','regionAt']);assert.ok(Object.isFrozen(context.TileSelection));
});

console.log(`Pixel selection geometry passed: ${passed} tests.`);
