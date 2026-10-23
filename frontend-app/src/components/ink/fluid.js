// Real-time ink in water: a GPU stable-fluids solver (semi-Lagrangian advection, vorticity confinement, Jacobi pressure
// projection) whose dye is stirred by the pointer, by typing in the composer and by app events. Nothing loops or drifts on
// a timer: when you stop, the ink slowly settles and fades into the dark.

const BASE_VS = `
precision highp float;
attribute vec2 aPosition;
varying vec2 vUv, vL, vR, vT, vB;
uniform vec2 texelSize;
void main () {
  vUv = aPosition * .5 + .5;
  vL = vUv - vec2(texelSize.x, 0.); vR = vUv + vec2(texelSize.x, 0.);
  vT = vUv + vec2(0., texelSize.y); vB = vUv - vec2(0., texelSize.y);
  gl_Position = vec4(aPosition, 0., 1.);
}`;

const SPLAT_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uTarget; uniform float aspectRatio; uniform vec3 color; uniform vec2 point; uniform float radius;
void main () {
  vec2 p = vUv - point.xy; p.x *= aspectRatio;
  vec3 splat = exp(-dot(p, p) / radius) * color;
  gl_FragColor = vec4(texture2D(uTarget, vUv).xyz + splat, 1.);
}`;

const ADVECT_FS = `
precision highp float;
varying vec2 vUv;
uniform sampler2D uVelocity, uSource; uniform vec2 texelSize, dyeTexelSize; uniform float dt, dissipation;
vec4 bilerp (sampler2D sam, vec2 uv, vec2 tsize) {
  vec2 st = uv / tsize - .5; vec2 iuv = floor(st); vec2 fuv = fract(st);
  vec4 a = texture2D(sam, (iuv + vec2(.5, .5)) * tsize), b = texture2D(sam, (iuv + vec2(1.5, .5)) * tsize);
  vec4 c = texture2D(sam, (iuv + vec2(.5, 1.5)) * tsize), d = texture2D(sam, (iuv + vec2(1.5, 1.5)) * tsize);
  return mix(mix(a, b, fuv.x), mix(c, d, fuv.x), fuv.y);
}
void main () {
#ifdef MANUAL_FILTERING
  vec2 coord = vUv - dt * bilerp(uVelocity, vUv, texelSize).xy * texelSize;
  vec4 result = bilerp(uSource, coord, dyeTexelSize);
#else
  vec2 coord = vUv - dt * texture2D(uVelocity, vUv).xy * texelSize;
  vec4 result = texture2D(uSource, coord);
#endif
  gl_FragColor = result / (1. + dissipation * dt);
}`;

const DIVERGENCE_FS = `
precision mediump float;
varying highp vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uVelocity;
void main () {
  float L = texture2D(uVelocity, vL).x, R = texture2D(uVelocity, vR).x, T = texture2D(uVelocity, vT).y, B = texture2D(uVelocity, vB).y;
  vec2 C = texture2D(uVelocity, vUv).xy;
  if (vL.x < 0.) L = -C.x; if (vR.x > 1.) R = -C.x; if (vT.y > 1.) T = -C.y; if (vB.y < 0.) B = -C.y;
  gl_FragColor = vec4(.5 * (R - L + T - B), 0., 0., 1.);
}`;

const CURL_FS = `
precision mediump float;
varying highp vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uVelocity;
void main () {
  float L = texture2D(uVelocity, vL).y, R = texture2D(uVelocity, vR).y, T = texture2D(uVelocity, vT).x, B = texture2D(uVelocity, vB).x;
  gl_FragColor = vec4(.5 * (R - L - T + B), 0., 0., 1.);
}`;

const VORTICITY_FS = `
precision highp float;
varying vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uVelocity, uCurl; uniform float curl, dt;
void main () {
  float L = texture2D(uCurl, vL).x, R = texture2D(uCurl, vR).x, T = texture2D(uCurl, vT).x, B = texture2D(uCurl, vB).x, C = texture2D(uCurl, vUv).x;
  vec2 force = .5 * vec2(abs(T) - abs(B), abs(R) - abs(L));
  force /= length(force) + .0001; force *= curl * C; force.y *= -1.;
  vec2 v = texture2D(uVelocity, vUv).xy + force * dt;
  gl_FragColor = vec4(clamp(v, -1000., 1000.), 0., 1.);
}`;

const PRESSURE_FS = `
precision mediump float;
varying highp vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uPressure, uDivergence;
void main () {
  float L = texture2D(uPressure, vL).x, R = texture2D(uPressure, vR).x, T = texture2D(uPressure, vT).x, B = texture2D(uPressure, vB).x;
  gl_FragColor = vec4((L + R + B + T - texture2D(uDivergence, vUv).x) * .25, 0., 0., 1.);
}`;

const GRADIENT_FS = `
precision mediump float;
varying highp vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uPressure, uVelocity;
void main () {
  float L = texture2D(uPressure, vL).x, R = texture2D(uPressure, vR).x, T = texture2D(uPressure, vT).x, B = texture2D(uPressure, vB).x;
  vec2 v = texture2D(uVelocity, vUv).xy - vec2(R - L, T - B);
  gl_FragColor = vec4(v, 0., 1.);
}`;

const CLEAR_FS = `
precision mediump float;
varying highp vec2 vUv;
uniform sampler2D uTexture; uniform float value;
void main () { gl_FragColor = value * texture2D(uTexture, vUv); }`;

// Ink shading: soft density → luminous colour with a filmic shoulder, a faint rim where the plume thins (the
// "ink edge" of 水墨), fine grain to avoid banding, and a vignette so the corners stay deep.
const DISPLAY_FS = `
precision highp float;
varying vec2 vUv, vL, vR, vT, vB;
uniform sampler2D uTexture; uniform float time, light; uniform vec3 base;
float hash (vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
void main () {
  vec3 c = texture2D(uTexture, vUv).rgb;
  vec3 l = texture2D(uTexture, vL).rgb, r = texture2D(uTexture, vR).rgb, t = texture2D(uTexture, vT).rgb, b = texture2D(uTexture, vB).rgb;
  float d = dot(c, vec3(.33));
  float edge = clamp(length(vec2(dot(r - l, vec3(.33)), dot(t - b, vec3(.33)))) * 3.5, 0., 1.);
  vec3 ink = (1. - exp(-c * 1.25)) * .78;
  ink += edge * .14 * normalize(c + .001);
  vec2 q = vUv - .5;
  float vig = 1. - .55 * dot(q, q) * 2.2;
  vec3 col = mix(base + ink * vig, base - (1. - exp(-c * 1.2)) * .5 * vec3(.9, .85, .6), light);
  col += (hash(vUv * 1024. + time) - .5) / 255. * 2.;
  gl_FragColor = vec4(col, 1.);
}`;

const PALETTE = [[0.30, 0.32, 1.0], [0.58, 0.40, 1.0], [0.55, 0.80, 1.0], [0.18, 0.62, 0.62]];

export function createInk(canvas, options = {}) {
  const config = {
    simRes: 128, dyeRes: options.mobile ? 512 : 1024, densityDissipation: 0.9, velocityDissipation: 0.6, pressure: 0.8,
    pressureIterations: 20, curl: 24, splatRadius: 0.0022, splatForce: 5200, ...options,
  };
  const params = { alpha: false, depth: false, stencil: false, antialias: false, preserveDrawingBuffer: false, powerPreference: "low-power" };
  let gl = canvas.getContext("webgl2", params);
  const isWebGL2 = !!gl;
  if (!gl) gl = canvas.getContext("webgl", params) || canvas.getContext("experimental-webgl", params);
  if (!gl) return null;

  let halfFloat, supportLinear;
  if (isWebGL2) { gl.getExtension("EXT_color_buffer_float"); supportLinear = gl.getExtension("OES_texture_float_linear"); }
  else { halfFloat = gl.getExtension("OES_texture_half_float"); supportLinear = gl.getExtension("OES_texture_half_float_linear"); }
  const halfType = isWebGL2 ? gl.HALF_FLOAT : halfFloat && halfFloat.HALF_FLOAT_OES;
  if (!halfType) return null;
  gl.clearColor(0, 0, 0, 1);

  function supported(internal, format) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, 4, 4, 0, format, halfType, null);
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.deleteFramebuffer(fbo); gl.deleteTexture(tex);
    return ok;
  }
  function format(internal, fmt) {
    if (!isWebGL2) return supported(gl.RGBA, gl.RGBA) ? { internal: gl.RGBA, format: gl.RGBA } : null;
    if (supported(internal, fmt)) return { internal, format: fmt };
    if (internal === gl.R16F) return format(gl.RG16F, gl.RG);
    if (internal === gl.RG16F) return format(gl.RGBA16F, gl.RGBA);
    return null;
  }
  const rgba = isWebGL2 ? format(gl.RGBA16F, gl.RGBA) : format();
  const rg = isWebGL2 ? format(gl.RG16F, gl.RG) : rgba;
  const r = isWebGL2 ? format(gl.R16F, gl.RED) : rgba;
  if (!rgba || !rg || !r) return null;

  function compile(type, source, keywords = []) {
    const s = gl.createShader(type);
    gl.shaderSource(s, keywords.map((k) => `#define ${k}\n`).join("") + source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const vs = compile(gl.VERTEX_SHADER, BASE_VS);
  function program(fs, keywords) {
    const p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs, keywords));
    gl.bindAttribLocation(p, 0, "aPosition");
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const uniforms = {};
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (let i = 0; i < n; i++) { const name = gl.getActiveUniform(p, i).name; uniforms[name] = gl.getUniformLocation(p, name); }
    return { p, u: uniforms, bind() { gl.useProgram(p); } };
  }
  const P = {
    splat: program(SPLAT_FS), advect: program(ADVECT_FS, supportLinear ? [] : ["MANUAL_FILTERING"]), divergence: program(DIVERGENCE_FS),
    curl: program(CURL_FS), vorticity: program(VORTICITY_FS), pressure: program(PRESSURE_FS), gradient: program(GRADIENT_FS),
    clear: program(CLEAR_FS), display: program(DISPLAY_FS),
  };

  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, -1, 1, 1, 1, 1, -1]), gl.STATIC_DRAW);
  const index = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 0, 2, 3]), gl.STATIC_DRAW);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(0);
  function blit(target) {
    if (target) { gl.viewport(0, 0, target.w, target.h); gl.bindFramebuffer(gl.FRAMEBUFFER, target.fbo); }
    else { gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight); gl.bindFramebuffer(gl.FRAMEBUFFER, null); }
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
  }

  function fbo(w, h, fmt, filter) {
    gl.activeTexture(gl.TEXTURE0);
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, fmt.internal, w, h, 0, fmt.format, halfType, null);
    const f = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, w, h); gl.clear(gl.COLOR_BUFFER_BIT);
    return { tex, fbo: f, w, h, tx: 1 / w, ty: 1 / h, attach(id) { gl.activeTexture(gl.TEXTURE0 + id); gl.bindTexture(gl.TEXTURE_2D, tex); return id; } };
  }
  function double(w, h, fmt, filter) {
    let a = fbo(w, h, fmt, filter), b = fbo(w, h, fmt, filter);
    return { w, h, tx: a.tx, ty: a.ty, get read() { return a; }, get write() { return b; }, swap() { [a, b] = [b, a]; } };
  }
  function resolution(res) {
    let aspect = gl.drawingBufferWidth / gl.drawingBufferHeight;
    if (aspect < 1) aspect = 1 / aspect;
    const min = Math.round(res), max = Math.round(res * aspect);
    return gl.drawingBufferWidth > gl.drawingBufferHeight ? { w: max, h: min } : { w: min, h: max };
  }

  let dye, velocity, divergence, curlFbo, pressure;
  function initFramebuffers() {
    const sim = resolution(config.simRes), dyeRes = resolution(config.dyeRes);
    const filter = supportLinear ? gl.LINEAR : gl.NEAREST;
    dye = double(dyeRes.w, dyeRes.h, rgba, filter);
    velocity = double(sim.w, sim.h, rg, filter);
    divergence = fbo(sim.w, sim.h, r, gl.NEAREST);
    curlFbo = fbo(sim.w, sim.h, r, gl.NEAREST);
    pressure = double(sim.w, sim.h, r, gl.NEAREST);
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = Math.floor(canvas.clientWidth * dpr), h = Math.floor(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; initFramebuffers(); return true; }
    return false;
  }
  resize();
  if (!dye) initFramebuffers();

  function splat(x, y, dx, dy, color, radiusScale = 1) {
    const aspect = canvas.width / canvas.height;
    P.splat.bind();
    gl.uniform1i(P.splat.u.uTarget, velocity.read.attach(0));
    gl.uniform1f(P.splat.u.aspectRatio, aspect);
    gl.uniform2f(P.splat.u.point, x, y);
    gl.uniform3f(P.splat.u.color, dx, dy, 0);
    const radius = config.splatRadius * radiusScale * (aspect > 1 ? aspect : 1);
    gl.uniform1f(P.splat.u.radius, radius);
    blit(velocity.write); velocity.swap();
    gl.uniform1i(P.splat.u.uTarget, dye.read.attach(0));
    gl.uniform3f(P.splat.u.color, color[0], color[1], color[2]);
    blit(dye.write); dye.swap();
  }

  function step(dt) {
    gl.disable(gl.BLEND);
    P.curl.bind();
    gl.uniform2f(P.curl.u.texelSize, velocity.tx, velocity.ty);
    gl.uniform1i(P.curl.u.uVelocity, velocity.read.attach(0));
    blit(curlFbo);

    P.vorticity.bind();
    gl.uniform2f(P.vorticity.u.texelSize, velocity.tx, velocity.ty);
    gl.uniform1i(P.vorticity.u.uVelocity, velocity.read.attach(0));
    gl.uniform1i(P.vorticity.u.uCurl, curlFbo.attach(1));
    gl.uniform1f(P.vorticity.u.curl, config.curl);
    gl.uniform1f(P.vorticity.u.dt, dt);
    blit(velocity.write); velocity.swap();

    P.divergence.bind();
    gl.uniform2f(P.divergence.u.texelSize, velocity.tx, velocity.ty);
    gl.uniform1i(P.divergence.u.uVelocity, velocity.read.attach(0));
    blit(divergence);

    P.clear.bind();
    gl.uniform1i(P.clear.u.uTexture, pressure.read.attach(0));
    gl.uniform1f(P.clear.u.value, config.pressure);
    blit(pressure.write); pressure.swap();

    P.pressure.bind();
    gl.uniform2f(P.pressure.u.texelSize, velocity.tx, velocity.ty);
    gl.uniform1i(P.pressure.u.uDivergence, divergence.attach(0));
    for (let i = 0; i < config.pressureIterations; i++) {
      gl.uniform1i(P.pressure.u.uPressure, pressure.read.attach(1));
      blit(pressure.write); pressure.swap();
    }

    P.gradient.bind();
    gl.uniform2f(P.gradient.u.texelSize, velocity.tx, velocity.ty);
    gl.uniform1i(P.gradient.u.uPressure, pressure.read.attach(0));
    gl.uniform1i(P.gradient.u.uVelocity, velocity.read.attach(1));
    blit(velocity.write); velocity.swap();

    P.advect.bind();
    gl.uniform2f(P.advect.u.texelSize, velocity.tx, velocity.ty);
    if (!supportLinear) gl.uniform2f(P.advect.u.dyeTexelSize, velocity.tx, velocity.ty);
    const vid = velocity.read.attach(0);
    gl.uniform1i(P.advect.u.uVelocity, vid);
    gl.uniform1i(P.advect.u.uSource, vid);
    gl.uniform1f(P.advect.u.dt, dt);
    gl.uniform1f(P.advect.u.dissipation, config.velocityDissipation);
    blit(velocity.write); velocity.swap();

    if (!supportLinear) gl.uniform2f(P.advect.u.dyeTexelSize, dye.tx, dye.ty);
    gl.uniform1i(P.advect.u.uVelocity, velocity.read.attach(0));
    gl.uniform1i(P.advect.u.uSource, dye.read.attach(1));
    gl.uniform1f(P.advect.u.dissipation, config.densityDissipation);
    blit(dye.write); dye.swap();
  }

  let base = [0.008, 0.010, 0.026], light = 0;
  function render(time) {
    P.display.bind();
    gl.uniform2f(P.display.u.texelSize, 1 / canvas.width, 1 / canvas.height);
    gl.uniform1i(P.display.u.uTexture, dye.read.attach(0));
    gl.uniform1f(P.display.u.time, time % 100);
    gl.uniform3f(P.display.u.base, base[0], base[1], base[2]);
    gl.uniform1f(P.display.u.light, light);
    blit(null);
  }

  // ---- colour: a slow walk through the palette so consecutive strokes blend into each other -----------------------
  let hue = Math.random() * PALETTE.length;
  function nextColor(strength = 1) {
    hue = (hue + 0.035) % PALETTE.length;
    const i = Math.floor(hue), f = hue - i, a = PALETTE[i], b = PALETTE[(i + 1) % PALETTE.length];
    const k = 0.055 * strength;
    return [(a[0] + (b[0] - a[0]) * f) * k, (a[1] + (b[1] - a[1]) * f) * k, (a[2] + (b[2] - a[2]) * f) * k];
  }

  return {
    get width() { return canvas.width; },
    get height() { return canvas.height; },
    resize, step, render, splat, nextColor,
    setTheme(isLight) { light = isLight ? 1 : 0; base = isLight ? [0.93, 0.945, 0.975] : [0.008, 0.010, 0.026]; },
    destroy() { const ext = gl.getExtension("WEBGL_lose_context"); ext && ext.loseContext(); },
  };
}
