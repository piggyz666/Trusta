// Hover Ink, done as a real fluid: the pointer stirs a stable-fluids simulation (velocity + pressure),
// injects ink (4 dye slots, one material each), and drags a displacement field around.
//   · outside the ink: a flat black wordmark on paper, its edges warped by the displacement field
//   · inside the ink: black, with the balloon wordmark showing through — also warped, so it smears and drips
// Ink dissipates and the displacement relaxes, so the word heals back to clean black type.

const VS = `#version 300 es
in vec2 aPos; out vec2 vUv; out vec2 vL; out vec2 vR; out vec2 vT; out vec2 vB; uniform vec2 texel;
void main(){ vUv=aPos*.5+.5; vL=vUv-vec2(texel.x,0.); vR=vUv+vec2(texel.x,0.); vT=vUv+vec2(0.,texel.y); vB=vUv-vec2(0.,texel.y); gl_Position=vec4(aPos,0.,1.); }`;

const HEAD = `#version 300 es
precision highp float; precision highp sampler2D;
in vec2 vUv; in vec2 vL; in vec2 vR; in vec2 vT; in vec2 vB; out vec4 o;`;

const FS = {
  splat: `${HEAD}
    uniform sampler2D uTarget; uniform float aspect; uniform vec4 color; uniform vec2 point; uniform float radius; uniform float cap;
    void main(){ vec2 p=vUv-point; p.x*=aspect; vec4 b=texture(uTarget,vUv); o=min(b+exp(-dot(p,p)/radius)*color, vec4(cap)); }`,
  advect: `${HEAD}
    uniform sampler2D uVelocity; uniform sampler2D uSource; uniform vec2 simTexel; uniform float dt; uniform float dissipation;
    void main(){ vec2 c=vUv-dt*texture(uVelocity,vUv).xy*simTexel; o=texture(uSource,c)/(1.+dissipation*dt); }`,
  warp: `${HEAD}
    uniform sampler2D uVelocity; uniform sampler2D uWarp; uniform vec2 simTexel; uniform float dt; uniform float relax;
    void main(){ vec2 d=dt*texture(uVelocity,vUv).xy*simTexel; vec2 w=texture(uWarp,vUv-d).xy-d; o=vec4(w*exp(-relax*dt),0.,1.); }`,
  divergence: `${HEAD}
    uniform sampler2D uVelocity;
    void main(){ float L=texture(uVelocity,vL).x,R=texture(uVelocity,vR).x,T=texture(uVelocity,vT).y,B=texture(uVelocity,vB).y;
      vec2 C=texture(uVelocity,vUv).xy; if(vL.x<0.)L=-C.x; if(vR.x>1.)R=-C.x; if(vT.y>1.)T=-C.y; if(vB.y<0.)B=-C.y;
      o=vec4(.5*(R-L+T-B),0.,0.,1.); }`,
  curl: `${HEAD}
    uniform sampler2D uVelocity;
    void main(){ float L=texture(uVelocity,vL).y,R=texture(uVelocity,vR).y,T=texture(uVelocity,vT).x,B=texture(uVelocity,vB).x; o=vec4(.5*(R-L-T+B),0.,0.,1.); }`,
  vorticity: `${HEAD}
    uniform sampler2D uVelocity; uniform sampler2D uCurl; uniform float curl; uniform float dt;
    void main(){ float L=texture(uCurl,vL).x,R=texture(uCurl,vR).x,T=texture(uCurl,vT).x,B=texture(uCurl,vB).x,C=texture(uCurl,vUv).x;
      vec2 f=.5*vec2(abs(T)-abs(B),abs(R)-abs(L)); f/=length(f)+1e-4; f*=curl*C; f.y*=-1.;
      vec2 v=texture(uVelocity,vUv).xy+f*dt; o=vec4(clamp(v,-1000.,1000.),0.,1.); }`,
  pressure: `${HEAD}
    uniform sampler2D uPressure; uniform sampler2D uDivergence;
    void main(){ float L=texture(uPressure,vL).x,R=texture(uPressure,vR).x,T=texture(uPressure,vT).x,B=texture(uPressure,vB).x;
      o=vec4((L+R+B+T-texture(uDivergence,vUv).x)*.25,0.,0.,1.); }`,
  gradient: `${HEAD}
    uniform sampler2D uPressure; uniform sampler2D uVelocity;
    void main(){ float L=texture(uPressure,vL).x,R=texture(uPressure,vR).x,T=texture(uPressure,vT).x,B=texture(uPressure,vB).x;
      vec2 v=texture(uVelocity,vUv).xy-vec2(R-L,T-B); o=vec4(v,0.,1.); }`,
  clear: `${HEAD}
    uniform sampler2D uTexture; uniform float value; void main(){ o=value*texture(uTexture,vUv); }`,
  display: `${HEAD}
    uniform sampler2D uText; uniform sampler2D uDye; uniform sampler2D uWarp;
    uniform sampler2D uM0; uniform sampler2D uM1; uniform sampler2D uM2; uniform sampler2D uM3;
    uniform vec4 matRect; uniform vec3 paper; uniform vec2 px; uniform float uWarpGain; uniform float uMatWarp;
    vec4 mat(sampler2D s, vec2 q){ vec2 u=(q-matRect.xy)/matRect.zw; if(any(lessThan(u,vec2(0.)))||any(greaterThan(u,vec2(1.)))) return vec4(0.); return texture(s,u); }
    float hash(vec2 p){ return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453); }
    float vnoise(vec2 p){ vec2 i=floor(p),f=fract(p); f=f*f*(3.-2.*f);
      return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y); }
    void main(){
      vec2 raw=texture(uWarp,vUv).xy; vec2 w=raw*uWarpGain;
      // torn, fibrous edges: fine noise whose strength follows how far the ink has dragged this spot
      float drag=length(raw);
      vec2 aspectP=vUv*vec2(px.y/px.x,1.);
      vec2 fib=vec2(vnoise(aspectP*vec2(28.,70.))-.5, vnoise(aspectP*vec2(70.,28.)+7.)-.5)+.5*vec2(vnoise(aspectP*vec2(80.,160.)+3.)-.5, vnoise(aspectP*vec2(160.,80.)+11.)-.5);
      vec2 q=vUv+w+fib*min(drag,.06)*.42;
      float t=texture(uText,q).a;
      vec3 col=mix(paper, vec3(.035), t);
      vec4 d=max(texture(uDye,vUv),0.); float total=d.x+d.y+d.z+d.w;
      // crisp, slightly anti-aliased ink edge
      float aa=fwidth(total)*1.2+.004;
      float ink=smoothstep(.42-aa,.42+aa,total);
      if(total>.05){
        // balloons drift a little further than the type, which makes them read as liquid
        vec2 qm=vUv+raw*uMatWarp;
        vec4 m=(mat(uM0,qm)*d.x+mat(uM1,qm)*d.y+mat(uM2,qm)*d.z+mat(uM3,qm)*d.w)/max(total,1e-4);
        vec3 inside=m.rgb+vec3(.02)*(1.-m.a);           // premultiplied balloon over ink-black
        // thin wet highlight just inside the edge
        float rim=smoothstep(.42-aa,.42+aa,total)-smoothstep(.42+aa,.42+aa*4.,total);
        inside=mix(inside,vec3(.92),rim*.55);
        col=mix(col,inside,ink);
      }
      o=vec4(col,1.);
    }`,
};

function load(src) {
  return new Promise((ok, bad) => { const im = new Image(); im.decoding = "async"; im.onload = () => ok(im); im.onerror = bad; im.src = src; });
}

export async function createInkFluid(canvas, { materials, word = "TRUSTA", font = '"Inter Variable", system-ui, sans-serif' }) {
  const gl = canvas.getContext("webgl2", { alpha: false, antialias: false, premultipliedAlpha: false, preserveDrawingBuffer: false });
  if (!gl) throw new Error("webgl2 unavailable");
  if (!gl.getExtension("EXT_color_buffer_float") && !gl.getExtension("EXT_color_buffer_half_float")) throw new Error("no float render targets");
  gl.getExtension("OES_texture_float_linear");
  try { await document.fonts.load(`900 100px ${font}`); } catch { /* fall back face */ }
  const mats = await Promise.all(materials.map(load));

  // ---------- plumbing ----------
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  function compile(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const vs = compile(gl.VERTEX_SHADER, VS);
  const progs = {};
  for (const [k, src] of Object.entries(FS)) {
    const p = gl.createProgram(); gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, src));
    gl.bindAttribLocation(p, 0, "aPos"); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; u[name] = gl.getUniformLocation(p, name); }
    progs[k] = { p, u };
  }
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

  function target(w, h, internal, format) {
    const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.HALF_FLOAT, null);
    const fbo = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    return { tex, fbo, w, h, tx: [1 / w, 1 / h], del() { gl.deleteTexture(tex); gl.deleteFramebuffer(fbo); } };
  }
  function pair(w, h, i, f) {
    let a = target(w, h, i, f), b = target(w, h, i, f);
    return { get read() { return a; }, get write() { return b; }, swap() { [a, b] = [b, a]; }, w, h, tx: a.tx, del() { a.del(); b.del(); } };
  }
  function imageTex(src) {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }
  const matTex = mats.map(imageTex);
  const bind = (unit, tex) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); return unit; };
  function run(prog, out, uniforms) {
    gl.useProgram(prog.p);
    let unit = 0;
    for (const [k, v] of Object.entries(uniforms)) {
      const loc = prog.u[k]; if (loc == null) continue;
      if (v && v.tex !== undefined) gl.uniform1i(loc, bind(unit++, v.tex));
      else if (v instanceof WebGLTexture) gl.uniform1i(loc, bind(unit++, v));
      else if (typeof v === "number") gl.uniform1f(loc, v);
      else if (v.length === 2) gl.uniform2fv(loc, v); else if (v.length === 3) gl.uniform3fv(loc, v); else gl.uniform4fv(loc, v);
    }
    if (out) { gl.bindFramebuffer(gl.FRAMEBUFFER, out.fbo); gl.viewport(0, 0, out.w, out.h); }
    else { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, canvas.width, canvas.height); }
    gl.uniform2fv(prog.u.texel, out ? out.tx : [1 / canvas.width, 1 / canvas.height]);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  // ---------- sizing: sim fields scale with the canvas ----------
  let vel, dye, warp, div, curl, pres, textTex = null, matRect = [0, 0, 1, 1], wordBox = { x: 0, y: 0, w: 1, h: 1 }, dpr = 1;
  const textCanvas = document.createElement("canvas");
  function res(scale) {
    const a = canvas.width / canvas.height;
    return a >= 1 ? [Math.round(scale * a), scale] : [scale, Math.round(scale / a)];
  }
  function layout() {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const W = Math.max(2, Math.round(r.width * dpr)), H = Math.max(2, Math.round(r.height * dpr));
    if (W === canvas.width && H === canvas.height && vel) return false;
    canvas.width = W; canvas.height = H;
    for (const f of [vel, dye, warp, div, curl, pres]) f?.del();
    const [sw, sh] = res(Math.min(192, Math.round(H / 5)));
    const [dw, dh] = res(Math.min(720, Math.round(H / 1.4)));
    vel = pair(sw, sh, gl.RG16F, gl.RG); pres = pair(sw, sh, gl.R16F, gl.RED);
    div = target(sw, sh, gl.R16F, gl.RED); curl = target(sw, sh, gl.R16F, gl.RED);
    dye = pair(dw, dh, gl.RGBA16F, gl.RGBA); warp = pair(dw, dh, gl.RG16F, gl.RG);
    drawWord(W, H);
    return true;
  }
  function drawWord(W, H) {
    textCanvas.width = W; textCanvas.height = H;
    const c = textCanvas.getContext("2d");
    c.clearRect(0, 0, W, H);
    let size = 100; c.font = `900 ${size}px ${font}`;
    const m0 = c.measureText(word);
    const target = Math.min(W * (W < 720 * dpr ? 0.94 : 0.9), H * 0.34 * (m0.width / (m0.actualBoundingBoxAscent + m0.actualBoundingBoxDescent)));
    size = size * target / m0.width;
    c.font = `900 ${size}px ${font}`;
    try { c.letterSpacing = `${-0.02 * size}px`; } catch { /* older canvases */ }
    const m = c.measureText(word);
    const w = m.actualBoundingBoxLeft + m.actualBoundingBoxRight, h = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const x = (W - w) / 2 + m.actualBoundingBoxLeft, y = (H + h) / 2 - m.actualBoundingBoxDescent + H * 0.02;
    c.fillStyle = "#000"; c.fillText(word, x, y);
    wordBox = { x: (W - w) / 2, y: (H - h) / 2 + H * 0.02, w, h };
    if (textTex) gl.deleteTexture(textTex);
    textTex = imageTex(textCanvas);
    // balloons: the letter area of each image is inset (24px sides/top, 54px bottom at 1602×456); scale it a touch
    // larger than the type and centre it on the type, like the reference
    const im = mats[0], s = (w * 1.06) / (im.width - 48);
    const mw = im.width * s, mh = im.height * s;
    const cx = W / 2, cy = wordBox.y + h / 2 - h * 0.03;
    const left = cx - mw / 2, top = cy - ((24 + (im.height - 78) / 2) * s);
    matRect = [left / W, 1 - (top + mh) / H, mw / W, mh / H];
  }
  layout();

  // ---------- simulation ----------
  const P = { curl: 3, velDiss: 2.2, dyeDiss: 0.9, relax: 0.75, iters: 20, radius: 0.0075, force: 1500, warp: 0.6, matWarp: 0.22 };
  const slotMat = [0, 1, 2, 3];
  let slot = 3, lastMat = -1, pointer = null, lastInput = -1e9;

  function splat(x, y, dx, dy, amount) {
    const aspect = canvas.width / canvas.height;
    run(progs.splat, vel.write, { uTarget: vel.read, aspect, point: [x, y], radius: P.radius, color: [dx * P.force, dy * P.force, 0, 0], cap: 1e4 }); vel.swap();
    const col = [0, 0, 0, 0]; col[slot] = amount;
    run(progs.splat, dye.write, { uTarget: dye.read, aspect, point: [x, y], radius: P.radius * 1.15, color: col, cap: 1.6 }); dye.swap();
  }
  function newStroke() {
    let m; do { m = Math.floor(Math.random() * mats.length); } while (mats.length > 1 && m === lastMat);
    lastMat = m; slot = (slot + 1) % 4; slotMat[slot] = m;
    // the slot being reused may hold a faint remainder of an old stroke; wipe it so colours never jump
    const mask = [1, 1, 1, 1]; mask[slot] = 0;
    clearSlot(mask);
  }
  const maskProg = (() => {
    const src = `${HEAD} uniform sampler2D uTexture; uniform vec4 mask; void main(){ o=texture(uTexture,vUv)*mask; }`;
    const p = gl.createProgram(); gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, src)); gl.bindAttribLocation(p, 0, "aPos"); gl.linkProgram(p);
    return { p, u: { uTexture: gl.getUniformLocation(p, "uTexture"), mask: gl.getUniformLocation(p, "mask"), texel: gl.getUniformLocation(p, "texel") } };
  })();
  function clearSlot(mask) { run(maskProg, dye.write, { uTexture: dye.read, mask }); dye.swap(); }

  function input(clientX, clientY, now) {
    const r = canvas.getBoundingClientRect();
    const x = (clientX - r.left) / r.width, y = 1 - (clientY - r.top) / r.height;
    if (now - lastInput > 0.45 || !pointer) { newStroke(); pointer = null; }
    if (pointer) {
      const dx = x - pointer[0], dy = y - pointer[1];
      const n = Math.max(1, Math.ceil(Math.hypot(dx * canvas.width, dy * canvas.height) / (canvas.height * 0.025)));
      for (let i = 1; i <= n; i++) splat(pointer[0] + dx * i / n, pointer[1] + dy * i / n, dx / n * 1.6, dy / n * 1.6, 0.9 / Math.sqrt(n));
    } else splat(x, y, 0, 0, 1.1);
    pointer = [x, y]; lastInput = now;
  }
  function leave() { pointer = null; }

  function step(dt) {
    const st = vel.tx;
    run(progs.curl, curl, { uVelocity: vel.read });
    run(progs.vorticity, vel.write, { uVelocity: vel.read, uCurl: curl, curl: P.curl, dt }); vel.swap();
    run(progs.divergence, div, { uVelocity: vel.read });
    run(progs.clear, pres.write, { uTexture: pres.read, value: 0.8 }); pres.swap();
    for (let i = 0; i < P.iters; i++) { run(progs.pressure, pres.write, { uPressure: pres.read, uDivergence: div }); pres.swap(); }
    run(progs.gradient, vel.write, { uPressure: pres.read, uVelocity: vel.read }); vel.swap();
    run(progs.advect, vel.write, { uVelocity: vel.read, uSource: vel.read, simTexel: st, dt, dissipation: P.velDiss }); vel.swap();
    run(progs.warp, warp.write, { uVelocity: vel.read, uWarp: warp.read, simTexel: st, dt, relax: P.relax }); warp.swap();
    run(progs.advect, dye.write, { uVelocity: vel.read, uSource: dye.read, simTexel: st, dt, dissipation: P.dyeDiss }); dye.swap();
  }
  function draw() {
    const u = { uText: textTex, uDye: dye.read, uWarp: warp.read, matRect, uWarpGain: P.warp, uMatWarp: P.matWarp, px: [1 / canvas.width, 1 / canvas.height], paper: [0xed / 255, 0xec / 255, 0xe8 / 255] };
    for (let i = 0; i < 4; i++) u[`uM${i}`] = matTex[slotMat[i]];
    run(progs.display, null, u);
  }
  const busySince = () => lastInput;

  return {
    layout, step, draw, input, leave,
    idleFor: (now) => now - busySince(),
    get wordBox() { return { ...wordBox, dpr }; },
  };
}
