// "Hover ink" hero: a huge black TRUSTA wordmark; wherever the pointer passes the ink tears open and reveals the same
// word as inflated foil balloons underneath. Each stroke picks the next material; strokes heal back to ink by themselves.
// Everything is real-time WebGL: the word's signed distance field gives the balloon height, the shading is a studio
// environment reflected off a metallic foil.

const VS = `
attribute vec2 aPosition; varying vec2 vUv;
void main () { vUv = aPosition * .5 + .5; gl_Position = vec4(aPosition, 0., 1.); }`;

// mask: R = how open the ink is (0..1), G = material of the stroke that opened it
const BRUSH_FS = `
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
}`;

const DISPLAY_FS = `
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
}`;

const MATERIALS = 5;

// exact Euclidean distance transform (Felzenszwalb & Huttenlocher), squared distances
function edt1d(f, n, d, v, z) {
  let k = 0; v[0] = 0; z[0] = -Infinity; z[1] = Infinity;
  for (let q = 1; q < n; q++) {
    let s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]);
    while (s <= z[k]) { k--; s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k]); }
    k++; v[k] = q; z[k] = s; z[k + 1] = Infinity;
  }
  k = 0;
  for (let q = 0; q < n; q++) { while (z[k + 1] < q) k++; d[q] = (q - v[k]) * (q - v[k]) + f[v[k]]; }
}
function edt(grid, w, h) {
  const n = Math.max(w, h), f = new Float64Array(n), d = new Float64Array(n), v = new Int32Array(n), z = new Float64Array(n + 1);
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) f[y] = grid[y * w + x];
    edt1d(f, h, d, v, z);
    for (let y = 0; y < h; y++) grid[y * w + x] = d[y];
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) f[x] = grid[y * w + x];
    edt1d(f, w, d, v, z);
    for (let x = 0; x < w; x++) grid[y * w + x] = Math.sqrt(d[x]);
  }
}

export function createBalloonHero(canvas, { word = "TRUSTA", font = '900 100px "Inter Variable", Inter, Arial Black, sans-serif' } = {}) {
  const gl = canvas.getContext("webgl", { antialias: false, alpha: false, preserveDrawingBuffer: false });
  if (!gl) return null;
  const halfFloat = gl.getExtension("OES_texture_half_float");
  gl.getExtension("OES_texture_half_float_linear");
  const maskType = halfFloat ? halfFloat.HALF_FLOAT_OES : gl.UNSIGNED_BYTE;

  function compile(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const vs = compile(gl.VERTEX_SHADER, VS);
  function program(fs) {
    const p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs)); gl.bindAttribLocation(p, 0, "aPosition"); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {}; const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; u[name] = gl.getUniformLocation(p, name); }
    return { p, u, use() { gl.useProgram(p); } };
  }
  const P = { brush: program(BRUSH_FS), display: program(DISPLAY_FS) };
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(0);

  function target(w, h, type) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, type, null);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    return { tex, fbo, w, h };
  }
  const draw = (t) => { gl.bindFramebuffer(gl.FRAMEBUFFER, t ? t.fbo : null); gl.viewport(0, 0, t ? t.w : canvas.width, t ? t.h : canvas.height); gl.drawArrays(gl.TRIANGLES, 0, 3); };
  const bind = (unit, tex) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); return unit; };

  let A, B, sdfTex, layout = null;
  const seed = Math.random() * 50;

  // Lay the word out to fill the width, then bake its signed distance field (CPU, once per resize).
  function bake() {
    const W = canvas.width, H = canvas.height;
    const sw = Math.min(900, W), scale = sw / W, sh = Math.max(2, Math.round(H * scale));
    const c = document.createElement("canvas"); c.width = sw; c.height = sh;
    const g = c.getContext("2d");
    g.font = font; g.textBaseline = "alphabetic";
    try { g.letterSpacing = "-4px"; } catch { /* older browsers */ }
    const m = g.measureText(word);
    const size = 100 * (sw * 0.94) / m.width;
    g.font = font.replace("100px", `${size.toFixed(1)}px`);
    try { g.letterSpacing = `${(-0.012 * size).toFixed(1)}px`; } catch { /* ignore */ }
    const m2 = g.measureText(word);
    const capH = m2.actualBoundingBoxAscent;
    const x = (sw - m2.width) / 2, y = sh * 0.56 + capH / 2;
    g.fillStyle = "#000"; g.fillRect(0, 0, sw, sh);
    g.fillStyle = "#fff"; g.fillText(word, x, y);
    const img = g.getImageData(0, 0, sw, sh).data;
    const inside = new Float64Array(sw * sh), outside = new Float64Array(sw * sh);
    for (let i = 0; i < sw * sh; i++) { const on = img[i * 4] > 127; inside[i] = on ? 1e20 : 0; outside[i] = on ? 0 : 1e20; }
    edt(inside, sw, sh); edt(outside, sw, sh);
    const range = 64, inflatePx = 3;
    let maxIn = 0;
    const sd = new Float32Array(sw * sh), mask = new Float32Array(sw * sh);
    for (let i = 0; i < sw * sh; i++) {
      sd[i] = inside[i] - outside[i];
      maxIn = Math.max(maxIn, inside[i]);
      mask[i] = Math.max(0, Math.min(1, (sd[i] + inflatePx) / 1.5));
    }
    // inflate: repeated blur clamped to the (puffed) glyph gives an even pillow over thick and thin strokes alike
    let hgt = mask.slice(), tmp = new Float32Array(sw * sh);
    const r = Math.max(2, Math.round(maxIn * 0.45));
    const blur = (src, dst, horizontal) => {
      const len = horizontal ? sw : sh, lines = horizontal ? sh : sw;
      for (let l = 0; l < lines; l++) {
        let acc = 0;
        const at = (k) => horizontal ? l * sw + k : k * sw + l;
        for (let k = -r; k <= r; k++) acc += src[at(Math.max(0, Math.min(len - 1, k)))];
        for (let k = 0; k < len; k++) {
          dst[at(k)] = acc / (2 * r + 1);
          acc += src[at(Math.min(len - 1, k + r + 1))] - src[at(Math.max(0, k - r))];
        }
      }
    };
    for (let it = 0; it < 6; it++) {
      blur(hgt, tmp, true); blur(tmp, hgt, false);
      for (let i = 0; i < hgt.length; i++) hgt[i] = Math.max(hgt[i], 0) * mask[i] + (it < 5 ? mask[i] * 0.35 : 0);
    }
    let hmax = 0; for (let i = 0; i < hgt.length; i++) hmax = Math.max(hmax, hgt[i]);
    const bytes = new Uint8Array(sw * sh * 4);
    for (let yy = 0; yy < sh; yy++) for (let xx = 0; xx < sw; xx++) {
      const i = yy * sw + xx, j = ((sh - 1 - yy) * sw + xx) * 4;
      const hv = Math.sqrt(Math.max(0, hgt[i] / hmax)) * 65535 / 65536;      // dome profile, 16-bit across R/G
      const hi = Math.floor(hv * 255), lo = Math.round((hv * 255 - hi) * 255);
      bytes[j] = hi; bytes[j + 1] = lo; bytes[j + 2] = mask[i] > 0.5 ? 255 : 0;
      bytes[j + 3] = Math.max(0, Math.min(255, Math.round((sd[i] / range * 0.5 + 0.5) * 255)));
    }
    if (!sdfTex) sdfTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, sdfTex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, sw, sh, 0, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
    // values in screen pixels
    layout = { sdfScale: range * 2 / scale, radius: (maxIn + inflatePx) / scale, inflate: inflatePx / scale, depth: 0.045 * scale * 3, capTop: 1 - (y - capH) / sh, capBottom: 1 - y / sh };
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = Math.max(2, Math.floor(canvas.clientWidth * dpr)), h = Math.max(2, Math.floor(canvas.clientHeight * dpr));
    if (w === canvas.width && h === canvas.height && A) return false;
    canvas.width = w; canvas.height = h;
    const mw = Math.max(64, Math.round(w / 2)), mh = Math.max(64, Math.round(h / 2));
    A = target(mw, mh, maskType); B = target(mw, mh, maskType);
    bake();
    return true;
  }
  resize();

  function brush(point, prev, radiusPx, material, decay, active) {
    P.brush.use();
    gl.uniform1i(P.brush.u.uMask, bind(0, A.tex));
    gl.uniform2f(P.brush.u.point, point[0], point[1]);
    gl.uniform2f(P.brush.u.prev, prev[0], prev[1]);
    gl.uniform1f(P.brush.u.radius, radiusPx / canvas.height);
    gl.uniform1f(P.brush.u.aspect, canvas.width / canvas.height);
    gl.uniform1f(P.brush.u.material, material / 8);
    gl.uniform1f(P.brush.u.decay, decay);
    gl.uniform1f(P.brush.u.active, active ? 1 : 0);
    gl.uniform1f(P.brush.u.seed, seed);
    draw(B); [A, B] = [B, A];
  }
  function render(time) {
    P.display.use();
    gl.uniform1i(P.display.u.uMask, bind(0, A.tex));
    gl.uniform1i(P.display.u.uSdf, bind(1, sdfTex));
    gl.uniform2f(P.display.u.res, canvas.width, canvas.height);
    gl.uniform1f(P.display.u.sdfScale, layout.sdfScale);
    gl.uniform1f(P.display.u.inflate, layout.inflate);
    gl.uniform1f(P.display.u.radius, layout.radius);
    gl.uniform1f(P.display.u.time, time);
    gl.uniform1f(P.display.u.seed, seed);
    gl.uniform1f(P.display.u.depth, layout.depth);
    draw(null);
  }
  return {
    resize, brush, render, materials: MATERIALS,
    get layout() { return layout; },
    rebake: bake,
    destroy() { const e = gl.getExtension("WEBGL_lose_context"); e && e.loseContext(); },
  };
}
