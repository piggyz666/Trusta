import{r as Y,j as u,c as le}from"./client-C9yhCDaK.js";const ce=`
attribute vec2 aPosition; varying vec2 vUv;
void main () { vUv = aPosition * .5 + .5; gl_Position = vec4(aPosition, 0., 1.); }`,fe=`
precision highp float;
varying vec2 vUv;
uniform sampler2D uMask; uniform vec2 point, prev; uniform float radius, aspect, material, decay, active, seed;
float hash (vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7)) + seed) * 43758.5453); }
float noise (vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
float segment (vec2 p, vec2 a, vec2 b) { vec2 pa = p - a, ba = b - a; float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-6), 0., 1.); return length(pa - ba * h); }
void main () {
  vec4 m = texture2D(uMask, vUv);
  vec2 p = vUv; p.x *= aspect;
  vec2 a = point; a.x *= aspect; vec2 b = prev; b.x *= aspect;
  float d = segment(p, b, a);
  float rough = noise(p * 26.) * .55 + noise(p * 70.) * .35;      // torn, uneven brush edge
  float paint = active * smoothstep(radius, radius * .35, d + (rough - .45) * radius * .9);
  float open = max(m.r - decay, 0.);
  if (paint > open + .02) { m.g = material; }
  m.r = max(open, paint);
  gl_FragColor = m;
}`,he=`
precision highp float;
varying vec2 vUv;
uniform sampler2D uMask, uSdf;
uniform vec2 res; uniform float sdfScale, inflate, radius, time, seed, depth;
float hash (vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1)) + seed) * 43758.5453); }
float noise (vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
float sdf (vec2 uv) { return (texture2D(uSdf, uv).a - .5) * sdfScale; }      // pixels, > 0 inside the ink letters
float height (vec2 uv) {
  vec4 t = texture2D(uSdf, uv);
  if (t.b < .5) return -1.;
  float h = t.r + t.g / 255.;                                              // smooth pillow profile baked on the CPU
  vec2 q = uv * res / 22.;
  h += ((noise(q) - .5) * .014 + (noise(q * 2.3 + 3.) - .5) * .006) * (1. - h * .7);   // foil crinkles
  return h;
}
vec3 env (vec3 r) {
  float up = r.y;
  vec3 c = mix(vec3(.20, .21, .23), vec3(1.), smoothstep(-.15, .9, up));
  c *= 1. - .75 * exp(-pow((up + .02) * 7., 2.));                          // dark horizon band, the chrome look
  c += 3.2 * pow(max(dot(r, normalize(vec3(-.55, .65, .55))), 0.), 60.);  // key softbox
  c += 1.6 * pow(max(dot(r, normalize(vec3(.7, .25, .65))), 0.), 22.);    // fill
  c += .7 * pow(max(dot(r, normalize(vec3(.1, -.8, .6))), 0.), 6.) * vec3(.93, .92, .9); // floor bounce
  return c;
}
vec3 palette (float t) { return .55 + .45 * cos(6.28318 * (t + vec3(0., .33, .67))); }
vec3 shade (vec3 n, float id, float h) {
  vec3 v = vec3(0., 0., 1.);
  vec3 r = reflect(-v, n);
  vec3 e = env(r);
  float fres = pow(1. - max(n.z, 0.), 3.);
  vec3 col;
  if (id < .5) {        // champagne gold foil (the T of the logo)
    col = e * vec3(1.0, .74, .38) + vec3(.06, .03, 0.);
  } else if (id < 1.5) { // teal mirror foil (the ribbon of the logo)
    col = e * vec3(.18, .78, .72) + vec3(0., .03, .03);
  } else if (id < 2.5) { // pearl, iridescent
    vec3 irid = palette(dot(n.xy, vec2(.8, .5)) * 1.2 + fres * .8 + .15);
    col = mix(vec3(.92, .9, .95), irid, .45) * (.55 + .5 * e);
  } else if (id < 3.5) { // silver chrome
    col = e * vec3(.93, .95, 1.);
  } else {               // ink-black gloss
    col = vec3(.02) + e * .18 + vec3(1.) * pow(max(dot(r, normalize(vec3(-.55, .65, .55))), 0.), 80.) * 1.6;
  }
  col *= .55 + .45 * smoothstep(0., .35, h);                               // occlusion at the pinched edges
  return col;
}
void main () {
  vec2 px = 1. / res;
  vec4 m = texture2D(uMask, vUv);
  float s = sdf(vUv);
  vec2 p = vUv * res;
  float n = noise(p / 9.) * .5 + noise(p / 3.) * .3;
  float open = m.r + (n - .45) * .45;
  float reveal = smoothstep(.48, .52, open);
  float rim = smoothstep(.30, .40, open) * (1. - reveal);                 // torn ink rim around the opening

  vec3 paper = vec3(.929, .925, .910);
  vec3 col = paper;
  float ink = smoothstep(-.8, .8, s);
  col = mix(col, vec3(.04), ink);

  if (reveal > 0. || rim > 0.) {
    vec3 under = paper;
    float h = height(vUv);
    // soft shadow of the balloons on the paper
    float hs = height(vUv + vec2(-10., 14.) * px);
    under *= 1. - .22 * smoothstep(-1., .4, hs) * step(h, -.5);
    if (h > -.5) {
      vec2 e = px * 3.;
      float hx = max(height(vUv + vec2(e.x, 0.)), 0.) - max(height(vUv - vec2(e.x, 0.)), 0.);
      float hy = max(height(vUv + vec2(0., e.y)), 0.) - max(height(vUv - vec2(0., e.y)), 0.);
      vec3 nrm = normalize(vec3(-hx, -hy, depth));
      under = shade(nrm, floor(m.g * 8. + .5), h);
      under = mix(under, under * .4, smoothstep(.07, 0., h));              // pinched seam at the balloon outline
    }
    col = mix(col, under, reveal);
    // ragged black ink where the paper is torn, plus a few flecks
    float fleck = step(.86, noise(p / 4.5 + 7.)) * smoothstep(.22, .34, open) * smoothstep(-radius * .6, 0., s);
    col = mix(col, vec3(.03), clamp(rim * smoothstep(-28., 0., s) + fleck * (1. - reveal) * .9, 0., 1.));
  }
  gl_FragColor = vec4(col, 1.);
}`,ue=5;function oe(l,m,E,e,h){let r=0;e[0]=0,h[0]=-1/0,h[1]=1/0;for(let c=1;c<m;c++){let T=(l[c]+c*c-(l[e[r]]+e[r]*e[r]))/(2*c-2*e[r]);for(;T<=h[r];)r--,T=(l[c]+c*c-(l[e[r]]+e[r]*e[r]))/(2*c-2*e[r]);r++,e[r]=c,h[r]=T,h[r+1]=1/0}r=0;for(let c=0;c<m;c++){for(;h[r+1]<c;)r++;E[c]=(c-e[r])*(c-e[r])+l[e[r]]}}function ne(l,m,E){const e=Math.max(m,E),h=new Float64Array(e),r=new Float64Array(e),c=new Int32Array(e),T=new Float64Array(e+1);for(let v=0;v<m;v++){for(let o=0;o<E;o++)h[o]=l[o*m+v];oe(h,E,r,c,T);for(let o=0;o<E;o++)l[o*m+v]=r[o]}for(let v=0;v<E;v++){for(let o=0;o<m;o++)h[o]=l[v*m+o];oe(h,m,r,c,T);for(let o=0;o<m;o++)l[v*m+o]=Math.sqrt(r[o])}}function me(l,{word:m="TRUSTA",font:E='900 100px "Inter Variable", Inter, Arial Black, sans-serif'}={}){const e=l.getContext("webgl",{antialias:!1,alpha:!1,preserveDrawingBuffer:!1});if(!e)return null;const h=e.getExtension("OES_texture_half_float");e.getExtension("OES_texture_half_float_linear");const r=h?h.HALF_FLOAT_OES:e.UNSIGNED_BYTE;function c(n,i){const a=e.createShader(n);if(e.shaderSource(a,i),e.compileShader(a),!e.getShaderParameter(a,e.COMPILE_STATUS))throw new Error(e.getShaderInfoLog(a));return a}const T=c(e.VERTEX_SHADER,ce);function v(n){const i=e.createProgram();if(e.attachShader(i,T),e.attachShader(i,c(e.FRAGMENT_SHADER,n)),e.bindAttribLocation(i,0,"aPosition"),e.linkProgram(i),!e.getProgramParameter(i,e.LINK_STATUS))throw new Error(e.getProgramInfoLog(i));const a={},d=e.getProgramParameter(i,e.ACTIVE_UNIFORMS);for(let t=0;t<d;t++){const p=e.getActiveUniform(i,t).name;a[p]=e.getUniformLocation(i,p)}return{p:i,u:a,use(){e.useProgram(i)}}}const o={brush:v(fe),display:v(he)};e.bindBuffer(e.ARRAY_BUFFER,e.createBuffer()),e.bufferData(e.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),e.STATIC_DRAW),e.vertexAttribPointer(0,2,e.FLOAT,!1,0,0),e.enableVertexAttribArray(0);function w(n,i,a){const d=e.createTexture();e.bindTexture(e.TEXTURE_2D,d),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texImage2D(e.TEXTURE_2D,0,e.RGBA,n,i,0,e.RGBA,a,null);const t=e.createFramebuffer();return e.bindFramebuffer(e.FRAMEBUFFER,t),e.framebufferTexture2D(e.FRAMEBUFFER,e.COLOR_ATTACHMENT0,e.TEXTURE_2D,d,0),e.viewport(0,0,n,i),e.clearColor(0,0,0,1),e.clear(e.COLOR_BUFFER_BIT),{tex:d,fbo:t,w:n,h:i}}const A=n=>{e.bindFramebuffer(e.FRAMEBUFFER,n?n.fbo:null),e.viewport(0,0,n?n.w:l.width,n?n.h:l.height),e.drawArrays(e.TRIANGLES,0,3)},y=(n,i)=>(e.activeTexture(e.TEXTURE0+n),e.bindTexture(e.TEXTURE_2D,i),n);let R,S,F,b=null;const j=Math.random()*50;function C(){const n=l.width,i=l.height,a=Math.min(900,n),d=a/n,t=Math.max(2,Math.round(i*d)),p=document.createElement("canvas");p.width=a,p.height=t;const f=p.getContext("2d");f.font=E,f.textBaseline="alphabetic";try{f.letterSpacing="-4px"}catch{}const _=f.measureText(m),D=100*(a*.94)/_.width;f.font=E.replace("100px",`${D.toFixed(1)}px`);try{f.letterSpacing=`${(-.012*D).toFixed(1)}px`}catch{}const O=f.measureText(m),H=O.actualBoundingBoxAscent,ie=(a-O.width)/2,K=t*.56+H/2;f.fillStyle="#000",f.fillRect(0,0,a,t),f.fillStyle="#fff",f.fillText(m,ie,K);const se=f.getImageData(0,0,a,t).data,q=new Float64Array(a*t),J=new Float64Array(a*t);for(let s=0;s<a*t;s++){const x=se[s*4]>127;q[s]=x?1e20:0,J[s]=x?0:1e20}ne(q,a,t),ne(J,a,t);const te=64,Q=3;let z=0;const Z=new Float32Array(a*t),k=new Float32Array(a*t);for(let s=0;s<a*t;s++)Z[s]=q[s]-J[s],z=Math.max(z,q[s]),k[s]=Math.max(0,Math.min(1,(Z[s]+Q)/1.5));let M=k.slice(),re=new Float32Array(a*t);const B=Math.max(2,Math.round(z*.45)),ae=(s,x,P)=>{const U=P?a:t,W=P?t:a;for(let L=0;L<W;L++){let X=0;const V=g=>P?L*a+g:g*a+L;for(let g=-B;g<=B;g++)X+=s[V(Math.max(0,Math.min(U-1,g)))];for(let g=0;g<U;g++)x[V(g)]=X/(2*B+1),X+=s[V(Math.min(U-1,g+B+1))]-s[V(Math.max(0,g-B))]}};for(let s=0;s<6;s++){ae(M,re,!0),ae(re,M,!1);for(let x=0;x<M.length;x++)M[x]=Math.max(M[x],0)*k[x]+(s<5?k[x]*.35:0)}let ee=0;for(let s=0;s<M.length;s++)ee=Math.max(ee,M[s]);const N=new Uint8Array(a*t*4);for(let s=0;s<t;s++)for(let x=0;x<a;x++){const P=s*a+x,U=((t-1-s)*a+x)*4,W=Math.sqrt(Math.max(0,M[P]/ee))*65535/65536,L=Math.floor(W*255),X=Math.round((W*255-L)*255);N[U]=L,N[U+1]=X,N[U+2]=k[P]>.5?255:0,N[U+3]=Math.max(0,Math.min(255,Math.round((Z[P]/te*.5+.5)*255)))}F||(F=e.createTexture()),e.bindTexture(e.TEXTURE_2D,F),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MIN_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_MAG_FILTER,e.LINEAR),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_S,e.CLAMP_TO_EDGE),e.texParameteri(e.TEXTURE_2D,e.TEXTURE_WRAP_T,e.CLAMP_TO_EDGE),e.texImage2D(e.TEXTURE_2D,0,e.RGBA,a,t,0,e.RGBA,e.UNSIGNED_BYTE,N),b={sdfScale:te*2/d,radius:(z+Q)/d,inflate:Q/d,depth:.045*d*3,capTop:1-(K-H)/t,capBottom:1-K/t}}function I(){const n=Math.min(window.devicePixelRatio||1,1.5),i=Math.max(2,Math.floor(l.clientWidth*n)),a=Math.max(2,Math.floor(l.clientHeight*n));if(i===l.width&&a===l.height&&R)return!1;l.width=i,l.height=a;const d=Math.max(64,Math.round(i/2)),t=Math.max(64,Math.round(a/2));return R=w(d,t,r),S=w(d,t,r),C(),!0}I();function $(n,i,a,d,t,p){o.brush.use(),e.uniform1i(o.brush.u.uMask,y(0,R.tex)),e.uniform2f(o.brush.u.point,n[0],n[1]),e.uniform2f(o.brush.u.prev,i[0],i[1]),e.uniform1f(o.brush.u.radius,a/l.height),e.uniform1f(o.brush.u.aspect,l.width/l.height),e.uniform1f(o.brush.u.material,d/8),e.uniform1f(o.brush.u.decay,t),e.uniform1f(o.brush.u.active,p?1:0),e.uniform1f(o.brush.u.seed,j),A(S),[R,S]=[S,R]}function G(n){o.display.use(),e.uniform1i(o.display.u.uMask,y(0,R.tex)),e.uniform1i(o.display.u.uSdf,y(1,F)),e.uniform2f(o.display.u.res,l.width,l.height),e.uniform1f(o.display.u.sdfScale,b.sdfScale),e.uniform1f(o.display.u.inflate,b.inflate),e.uniform1f(o.display.u.radius,b.radius),e.uniform1f(o.display.u.time,n),e.uniform1f(o.display.u.seed,j),e.uniform1f(o.display.u.depth,b.depth),A(null)}return{resize:I,brush:$,render:G,materials:ue,get layout(){return b},rebake:C,destroy(){const n=e.getExtension("WEBGL_lose_context");n&&n.loseContext()}}}const de=()=>window.matchMedia("(prefers-reduced-motion: reduce)").matches;function pe({github:l}){const m=Y.useRef(null),[E,e]=Y.useState(!1);return Y.useEffect(()=>{const h=m.current;let r,c=0,T=!1,v=performance.now(),o=0,w=-1,A=null,y=null,R=!0;const S=1.1,F=.32,b={lastPaint:-1e9};(async()=>{try{await document.fonts.load('900 100px "Inter Variable"')}catch{}if(R){try{r=me(h)}catch(t){console.warn(t),r=null}if(!r){e(!0);return}r.render(0),de()||setTimeout(n,700)}})();const j=(t,p)=>{const f=h.getBoundingClientRect();return[(t-f.left)/f.width,1-(p-f.top)/f.height]},C=()=>{const t=r.layout;return Math.max(18,(t.capTop-t.capBottom)*h.height*.45)};function I(t){const p=Math.min(.05,(t-v)/1e3);v=t,r.resize();const f=t/1e3,_=f-b.lastPaint>S?F*p:0;if(A?(r.brush(A,y||A,C(),w,_,!0),y=A,A=null):r.brush([0,0],[0,0],1,w,_,!1),r.render(f),f-b.lastPaint>S+1/F+.5){T=!1;return}c=requestAnimationFrame(I)}function $(){!T&&r&&(T=!0,v=performance.now(),c=requestAnimationFrame(I))}function G(t){const p=performance.now()/1e3;p-o>.45&&(w=(w+1)%r.materials,y=null),o=p,b.lastPaint=p,A=t,$()}function n(){if(!r||!R)return;const t=r.layout,p=(t.capTop+t.capBottom)/2;let f=0;const _=46,D=()=>{if(!R)return;const O=.12+f/_*.7,H=p+Math.sin(f/_*Math.PI*1.6)*.06;G([O,H]),f+=1,f<=_&&setTimeout(D,22)};D()}const i=t=>{r&&G(j(t.clientX,t.clientY))},a=()=>{y=null},d=()=>{r&&(r.resize(),r.render(performance.now()/1e3))};return h.addEventListener("pointermove",i),h.addEventListener("pointerdown",i),h.addEventListener("pointerleave",a),window.addEventListener("resize",d),()=>{R=!1,cancelAnimationFrame(c),h.removeEventListener("pointermove",i),h.removeEventListener("pointerdown",i),h.removeEventListener("pointerleave",a),window.removeEventListener("resize",d),r==null||r.destroy()}},[]),u.jsxs("section",{className:"hero","aria-label":"TRUSTA",children:[u.jsx("canvas",{ref:m,className:"hero-canvas","aria-hidden":"true"}),E&&u.jsx("div",{className:"hero-word","aria-hidden":"true",children:"TRUSTA"}),u.jsx("h1",{className:"visually-hidden",children:"TRUSTA 银行助手"}),u.jsxs("div",{className:"corner tl",children:[u.jsxs("p",{children:["AI 提议，你授权。",u.jsx("br",{}),"运行在你自己电脑上的银行助手。"]}),u.jsx("a",{className:"pill",href:"/",children:"打开 TRUSTA"})]}),u.jsxs("nav",{className:"corner tr","aria-label":"页面导航",children:[u.jsx("a",{href:"#scenes",children:"能做什么"}),u.jsx("a",{href:"#trust",children:"为什么可信"}),u.jsx("a",{href:"#install",children:"安装"})]}),u.jsx("div",{className:"corner bl",children:"金融科技大赛参赛作品"}),u.jsxs("div",{className:"corner br",children:[u.jsx("a",{href:l,children:"GitHub"}),u.jsx("span",{children:"/"}),u.jsx("a",{href:"#install",children:"安装"})]}),u.jsx("div",{className:"hint","aria-hidden":"true",children:"移动鼠标，划过这个词"})]})}const ve="https://github.com/piggyz666/Trusta";le(document.getElementById("root")).render(u.jsx(Y.StrictMode,{children:u.jsx(pe,{github:ve})}));
