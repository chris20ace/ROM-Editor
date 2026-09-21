/* Pixel selection geometry. Map data and artwork are deliberately not involved. */
(function(root,factory){
  'use strict';
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.TileSelection=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';

  function sizeOf(width,height){
    if(!Number.isSafeInteger(width)||!Number.isSafeInteger(height)||width<1||height<1||!Number.isSafeInteger(width*height)){
      throw new RangeError('Selection width and height must be positive whole numbers.');
    }
    return width*height;
  }

  function checkedMask(mask,width,height){
    const size=sizeOf(width,height);
    if(!mask||mask.length!==size)throw new RangeError('The selection mask must match its width and height.');
    for(let i=0;i<size;i++)if(mask[i]!==0&&mask[i]!==1)throw new TypeError('Selection mask pixels must be 0 or 1.');
    return size;
  }

  function rectangle(width,height){return new Uint8Array(sizeOf(width,height)).fill(1);}

  function bounds(mask,width,height){
    checkedMask(mask,width,height);
    let left=width,top=height,right=-1,bottom=-1;
    for(let y=0;y<height;y++)for(let x=0;x<width;x++)if(mask[y*width+x]){
      left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
    }
    return right<0?null:{x:left,y:top,width:right-left+1,height:bottom-top+1};
  }

  // A pixel belongs to exactly one region. A line blocks the graph edges it
  // crosses between neighboring pixel centers; the line never erases pixels.
  function components(mask,width,height,rightBlocked,downBlocked,collect){
    const seen=new Uint8Array(mask.length),queue=new Uint32Array(mask.length),regions=[];
    let count=0;
    for(let start=0;start<mask.length;start++){
      if(!mask[start]||seen[start])continue;
      count++;const region=collect?new Uint8Array(mask.length):null;
      let head=0,tail=1;queue[0]=start;seen[start]=1;
      while(head<tail){
        const i=queue[head++],x=i%width,y=Math.floor(i/width);if(region)region[i]=1;
        function add(j,blocked){if(!blocked&&mask[j]&&!seen[j]){seen[j]=1;queue[tail++]=j;}}
        if(x>0)add(i-1,rightBlocked?.[i-1]);
        if(x+1<width)add(i+1,rightBlocked?.[i]);
        if(y>0)add(i-width,downBlocked?.[i-width]);
        if(y+1<height)add(i+width,downBlocked?.[i]);
      }
      if(region)regions.push(region);
    }
    return {count,regions};
  }

  function touchesBoundary(mask,width,height,point){
    // Coordinates describe pixel edges (0..width/height), not pixel indices.
    // Empty pixels also lie outside the selected shape, including mask holes.
    const epsilon=1e-6;
    for(const dx of [-epsilon,epsilon])for(const dy of [-epsilon,epsilon]){
      const x=Math.floor(point.x+dx),y=Math.floor(point.y+dy);
      if(x<0||y<0||x>=width||y>=height||!mask[y*width+x])return true;
    }
    return false;
  }

  function cut(mask,width,height,path){
    const size=checkedMask(mask,width,height);
    if(!Array.isArray(path))throw new TypeError('The cut path must be an array of pixel coordinates.');
    for(const point of path)if(!point||!Number.isFinite(point.x)||!Number.isFinite(point.y)){
      throw new TypeError('Each cut point needs finite x and y pixel coordinates.');
    }
    const originalRegionCount=components(mask,width,height,null,null,false).count;
    const first=path[0],last=path[path.length-1];
    const closed=path.length>2&&Math.hypot(first.x-last.x,first.y-last.y)<1e-6;
    const complete=closed||path.length>1&&touchesBoundary(mask,width,height,first)&&touchesBoundary(mask,width,height,last);
    if(!complete)return {regions:components(mask,width,height,null,null,true).regions,split:false,originalRegionCount,closed,complete:false};
    const rightBlocked=new Uint8Array(size),downBlocked=new Uint8Array(size);
    const points=closed&&(first.x!==last.x||first.y!==last.y)?[...path,first]:path;

    // Resolve lines passing exactly through centers consistently: center pixels
    // lie on the upper/left side of horizontal/vertical cuts. Unequal offsets
    // also resolve common diagonal ties, without snapping to an 8px/16px grid.
    const offsetX=1e-7,offsetY=2.61803398875e-7;
    for(let i=1;i<points.length;i++){
      const a={x:points[i-1].x+offsetX,y:points[i-1].y+offsetY};
      const b={x:points[i].x+offsetX,y:points[i].y+offsetY},dx=b.x-a.x,dy=b.y-a.y;
      if(dy!==0){
        const first=Math.max(0,Math.ceil(Math.min(a.y,b.y)-.5));
        const last=Math.min(height-1,Math.floor(Math.max(a.y,b.y)-.5));
        for(let y=first;y<=last;y++){
          const t=(y+.5-a.y)/dy,x=Math.floor(a.x+t*dx-.5);
          if(x>=0&&x+1<width)rightBlocked[y*width+x]=1;
        }
      }
      if(dx!==0){
        const first=Math.max(0,Math.ceil(Math.min(a.x,b.x)-.5));
        const last=Math.min(width-1,Math.floor(Math.max(a.x,b.x)-.5));
        for(let x=first;x<=last;x++){
          const t=(x+.5-a.x)/dx,y=Math.floor(a.y+t*dy-.5);
          if(y>=0&&y+1<height)downBlocked[y*width+x]=1;
        }
      }
    }
    const result=components(mask,width,height,rightBlocked,downBlocked,true);
    return {regions:result.regions,split:result.count>originalRegionCount,originalRegionCount,closed,complete:true};
  }

  // x/y are pixel coordinates and may include the pointer's fractional part.
  // A missing pixel, an empty mask, or a point outside the selection returns -1.
  function regionAt(regions,width,height,x,y){
    sizeOf(width,height);
    if(!Array.isArray(regions))throw new TypeError('Regions must be an array of selection masks.');
    if(!Number.isFinite(x)||!Number.isFinite(y)||x<0||y<0||x>=width||y>=height)return -1;
    const i=Math.floor(y)*width+Math.floor(x);
    for(let region=0;region<regions.length;region++){
      if(!regions[region]||regions[region].length!==width*height)throw new RangeError('Every region mask must match its width and height.');
      if(regions[region][i])return region;
    }
    return -1;
  }

  return Object.freeze({rectangle,cut,regionAt,bounds});
});
