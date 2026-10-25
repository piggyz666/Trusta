// Interactive water surface. A 2-D wave equation runs on a small height-field texture (cheap: one pass per step);
// the screen shows a painted pool floor seen through that surface — refraction, caustic light where ripples focus,
// and a specular glint. The water is still until you touch it, and the loop stops once it has calmed down again.

const VS = `
precision highp float;
attribute vec2 aPosition;
varying vec2 vUv;
void main () { vUv = aPosition * .5 + .5; gl_Position = vec4(aPosition, 0., 1.); }`;

// wave step: R = height now, G = height one step ago
const STEP_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uState; uniform vec2 texel; uniform float damping;
void main () {
  vec4 s = texture2D(uState, vUv);
  float n = texture2D(uState, vUv + vec2(texel.x, 0.)).r + texture2D(uState, vUv - vec2(texel.x, 0.)).r
          + texture2D(uState, vUv + vec2(0., texel.y)).r + texture2D(uState, vUv - vec2(0., texel.y)).r;
  float next = (n * .5 - s.g) * damping;
  gl_FragColor = vec4(next, s.r, 0., 1.);
}`;

const DROP_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uState; uniform vec2 center; uniform float radius, strength, aspect;
void main () {
  vec4 s = texture2D(uState, vUv);
  vec2 d = vUv - center; d.x *= aspect;
  float r = length(d) / radius;
  float drop = r < 1. ? (cos(r * 3.14159265) * .5 + .5) : 0.;
  s.r += drop * strength;
  gl_FragColor = s;
}`;

// The pool floor is painted once: a deep-to-shallow gradient with the net of sunlight caustics you see on the bottom
// of a pool. It never moves by itself — only the ripples bend it.
const FLOOR_FS = `
precision highp float;
varying vec2 vUv;
uniform vec2 res; uniform float light, seed;
float caustic (vec2 uv, float t) {
  vec2 p = mod(uv * 6.28318, 6.28318) - 250.;
  vec2 i = p; float c = 1.; float inten = .005;
  for (int n = 0; n < 5; n++) {
    float tt = t * (1. - (3.5 / float(n + 1)));
    i = p + vec2(cos(tt - i.x) + sin(tt + i.y), sin(tt - i.y) + cos(tt + i.x));
    c += 1. / length(vec2(p.x / (sin(i.x + tt) / inten), p.y / (cos(i.y + tt) / inten)));
  }
  c /= 5.; c = 1.17 - pow(c, 1.4);
  return clamp(pow(abs(c), 8.), 0., 1.);
}
float hash (vec2 p) { return fract(sin(dot(p, vec2(41.3, 289.1)) + seed) * 43758.5453); }
float noise (vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3. - 2. * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
void main () {
  float aspect = res.x / res.y;
  vec2 p = vUv * vec2(aspect, 1.);
  vec2 c = vUv - vec2(.5, .56); c.x *= aspect;
  float pool = exp(-dot(c, c) * 1.6);                 // light falls into the middle of the pool
  float soft = noise(p * 2.2) * .5 + noise(p * 5.) * .25;
  // two layers of caustics: a fine sharp net and a broad soft one for depth
  float fine = caustic(p * 1.7 + seed, seed * 3.7);
  float broad = caustic(p * .75 + seed * .3, seed * 1.3 + 2.);
  vec3 col;
  if (light > .5) {
    vec3 edge = vec3(.70, .86, .89), centre = vec3(.90, .975, .975);
    col = mix(edge, centre, smoothstep(.0, 1., pool + (soft - .4) * .2));
    col += vec3(1.) * fine * (.28 + .32 * pool);
    col += vec3(.9, 1., 1.) * broad * .10;
    col -= vec3(.06, .03, .02) * (1. - pool) * soft;
  } else {
    vec3 edge = vec3(.008, .028, .038), mid = vec3(.02, .10, .12), centre = vec3(.05, .22, .24);
    col = mix(edge, mid, smoothstep(.0, .55, pool + (soft - .4) * .25));
    col = mix(col, centre, smoothstep(.45, 1., pool) * .9);
    col += vec3(.35, .75, .78) * fine * (.10 + .32 * pool);
    col += vec3(.25, .55, .6) * broad * .07 * pool;
  }
  vec2 q = vUv - .5; col *= 1. - (light > .5 ? .08 : .35) * dot(q, q) * 2.2;
  gl_FragColor = vec4(col, 1.);
}`;

const DISPLAY_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uState, uFloor; uniform vec2 texel; uniform float light, time, frost;
void main () {
  float h = texture2D(uState, vUv).r;
  float hx = texture2D(uState, vUv + vec2(texel.x, 0.)).r - texture2D(uState, vUv - vec2(texel.x, 0.)).r;
  float hy = texture2D(uState, vUv + vec2(0., texel.y)).r - texture2D(uState, vUv - vec2(0., texel.y)).r;
  vec3 n = normalize(vec3(-hx, -hy, .06));
  // refraction of the floor
  // behind content pages the floor is frosted here, once, instead of every glass card blurring it itself
  vec3 col = texture2D(uFloor, vUv + n.xy * .016 * (1. - frost * .5), frost * 5.).rgb;
  // caustics: light gathers where the surface curves inward
  float lap = texture2D(uState, vUv + vec2(texel.x, 0.)).r + texture2D(uState, vUv - vec2(texel.x, 0.)).r
            + texture2D(uState, vUv + vec2(0., texel.y)).r + texture2D(uState, vUv - vec2(0., texel.y)).r - 4. * h;
  float caustic = clamp(-lap * 16., 0., 1.);
  col += caustic * (light > .5 ? vec3(.10, .14, .12) : vec3(.22, .42, .36));
  // shading of the wave slopes from a light at the upper left, and a sharp glint
  vec3 L = normalize(vec3(-.45, .6, .65));
  float diffuse = dot(n, L) - dot(vec3(0., 0., 1.), L);
  col *= 1. + diffuse * (light > .5 ? .7 : 1.3);
  vec3 H = normalize(L + vec3(0., 0., 1.));
  float spec = pow(max(dot(n, H), 0.), 220.) * smoothstep(.002, .02, length(vec2(hx, hy)));
  col += spec * (light > .5 ? .55 : .9) * (1. - frost * .7);
  vec3 scrim = light > .5 ? vec3(.93, .975, .975) : vec3(.012, .035, .04);
  col = mix(col, scrim, frost * (light > .5 ? .38 : .48));
  gl_FragColor = vec4(col, 1.);
}`;

export function createWater(canvas, options = {}) {
  const config = { scale: options.mobile ? 0.3 : 0.25, damping: 0.986, ...options };
  const params = { alpha: false, depth: false, stencil: false, antialias: false, powerPreference: "low-power" };
  let gl = canvas.getContext("webgl2", params);
  const isWebGL2 = !!gl;
  if (!gl) gl = canvas.getContext("webgl", params);
  if (!gl) return null;
  let type, internal, linear;
  if (isWebGL2) {
    if (!gl.getExtension("EXT_color_buffer_float")) return null;
    type = gl.HALF_FLOAT; internal = gl.RGBA16F; linear = true;
  } else {
    const ext = gl.getExtension("OES_texture_half_float");
    if (!ext) return null;
    type = ext.HALF_FLOAT_OES; internal = gl.RGBA; linear = !!gl.getExtension("OES_texture_half_float_linear");
  }

  function compile(t, src) {
    const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
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
  const P = { step: program(STEP_FS), drop: program(DROP_FS), floor: program(FLOOR_FS), display: program(DISPLAY_FS) };

  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(0);

  function target(w, h, float) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    const filter = float && !linear ? gl.NEAREST : gl.LINEAR;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    if (float) gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, gl.RGBA, type, null);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, w, h); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    return { tex, fbo, w, h };
  }
  function draw(t) {
    if (t) { gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo); gl.viewport(0, 0, t.w, t.h); }
    else { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, canvas.width, canvas.height); }
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  const bind = (unit, tex) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); return unit; };

  let a, b, floor, sw, sh, light = 0, frost = 0;
  const seed = Math.random() * 100;
  function mip() {
    if (!isWebGL2) return;
    gl.bindTexture(gl.TEXTURE_2D, floor.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
  }
  function paintFloor() {
    // the floor is procedural; no photo is shipped (requesting one only produced a 404 on every load)
    P.floor.use();
    gl.uniform2f(P.floor.u.res, canvas.width, canvas.height);
    gl.uniform1f(P.floor.u.light, light);
    gl.uniform1f(P.floor.u.seed, seed);
    draw(floor);
    mip();
  }
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1);
    const w = Math.max(2, Math.floor(canvas.clientWidth * dpr)), h = Math.max(2, Math.floor(canvas.clientHeight * dpr));
    if (canvas.width === w && canvas.height === h && a) return false;
    canvas.width = w; canvas.height = h;
    sw = Math.max(64, Math.round(w * config.scale)); sh = Math.max(64, Math.round(h * config.scale));
    a = target(sw, sh, true); b = target(sw, sh, true); floor = target(w, h, false);
    paintFloor();
    return true;
  }
  resize();

  function step(n = 1) {
    P.step.use();
    gl.uniform2f(P.step.u.texel, 1 / sw, 1 / sh);
    gl.uniform1f(P.step.u.damping, config.damping);
    for (let i = 0; i < n; i++) {
      gl.uniform1i(P.step.u.uState, bind(0, a.tex));
      draw(b); [a, b] = [b, a];
    }
  }
  function drop(x, y, radius, strength) {
    P.drop.use();
    gl.uniform1i(P.drop.u.uState, bind(0, a.tex));
    gl.uniform2f(P.drop.u.center, x, y);
    gl.uniform1f(P.drop.u.radius, radius);
    gl.uniform1f(P.drop.u.strength, strength);
    gl.uniform1f(P.drop.u.aspect, canvas.width / canvas.height);
    draw(b); [a, b] = [b, a];
  }
  function render(time) {
    P.display.use();
    gl.uniform1i(P.display.u.uState, bind(0, a.tex));
    gl.uniform1i(P.display.u.uFloor, bind(1, floor.tex));
    gl.uniform2f(P.display.u.texel, 1 / sw, 1 / sh);
    gl.uniform1f(P.display.u.light, light);
    gl.uniform1f(P.display.u.time, time % 100);
    gl.uniform1f(P.display.u.frost, frost);
    draw(null);
  }
  return {
    resize, step, drop, render,
    setTheme(isLight) { light = isLight ? 1 : 0; paintFloor(); },
    setFrost(value) { frost = value; },
    destroy() { const ext = gl.getExtension("WEBGL_lose_context"); ext && ext.loseContext(); },
  };
}
