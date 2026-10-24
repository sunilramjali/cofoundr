/* CoFoundr dashboard: progressive enhancement only. Every page works (and Saint can read it) without this file. */
(() => {
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* Living backdrop: a slow, light gradient flow (WebGL). Clock = wall time, so it continues across pages. */
  (function backdrop() {
    const canvas = $(".backdrop canvas"); if (!canvas) return;
    const gl = canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
    if (!gl) return;
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision mediump float;uniform vec2 r;uniform float t;uniform vec2 m;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p),u=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}
      float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.02+vec2(3.1,1.7);a*=.5;}return v;}
      void main(){
        vec2 uv=gl_FragCoord.xy/r; vec2 p=uv*vec2(r.x/r.y,1.)*1.15; float s=t*.022;
        vec2 q=vec2(fbm(p+vec2(0.,s)),fbm(p+vec2(5.2,1.3)-s));
        vec2 w=vec2(fbm(p+2.6*q+vec2(1.7,9.2)+s*1.3),fbm(p+2.6*q+vec2(8.3,2.8)-s*.9));
        float f=fbm(p+2.2*w);
        vec3 cream=vec3(.957,.945,.922), sand=vec3(.92,.84,.74), apricot=vec3(.96,.74,.56),
             blush=vec3(.92,.76,.76), sage=vec3(.76,.86,.76), sky=vec3(.78,.86,.95);
        vec3 c=mix(cream,sand,smoothstep(.3,.72,f));
        c=mix(c,apricot,smoothstep(.42,.82,w.x)*.8);
        c=mix(c,sage,smoothstep(.46,.86,q.y)*.75);
        c=mix(c,sky,smoothstep(.5,.92,w.y*f*1.7)*.7);
        c=mix(c,blush,smoothstep(.55,.92,q.x*w.y*1.9)*.55);
        c+=.05*smoothstep(.62,.95,f);
        vec2 d=(uv-m)*vec2(r.x/r.y,1.); c=mix(c,vec3(1.),.16*exp(-6.*dot(d,d)));
        gl_FragColor=vec4(c,1.);}`;
    const sh = (type, src) => { const x = gl.createShader(type); gl.shaderSource(x, src); gl.compileShader(x); return x; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t"), uM = gl.getUniformLocation(prog, "m");
    const mouse = { x: .5, y: .6, tx: .5, ty: .6 };
    let raf = 0;
    const size = () => {
      const k = Math.min(devicePixelRatio || 1, 2) * .35;  // soft by design: low resolution, cheap to draw
      canvas.width = Math.max(1, Math.round(innerWidth * k)); canvas.height = Math.max(1, Math.round(innerHeight * k));
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    const draw = () => {
      mouse.x += (mouse.tx - mouse.x) * .035; mouse.y += (mouse.ty - mouse.y) * .035;
      gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, (Date.now() / 1000) % 100000); gl.uniform2f(uM, mouse.x, mouse.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!calm && !document.hidden) raf = requestAnimationFrame(draw);
    };
    size(); draw(); canvas.classList.add("is-live");
    addEventListener("resize", () => { size(); if (calm) draw(); }, { passive: true });
    addEventListener("pointermove", (e) => { mouse.tx = e.clientX / innerWidth; mouse.ty = 1 - e.clientY / innerHeight; }, { passive: true });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { cancelAnimationFrame(raf); draw(); } });
  })();

  /* Count-up numbers: data-count="1193" data-prefix="£" */
  $$("[data-count]").forEach((el) => {
    const target = parseFloat(el.dataset.count), prefix = el.dataset.prefix || "", t0 = performance.now(), dur = 900;
    if (!isFinite(target) || calm) return;
    const tick = (now) => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 4);
      el.textContent = prefix + Math.round(target * e).toLocaleString("en-GB");
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  /* Soft light that follows the cursor on modules and approval cards */
  $$(".mod, .ask").forEach((card) => card.addEventListener("pointermove", (e) => {
    const b = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - b.left}px`); card.style.setProperty("--my", `${e.clientY - b.top}px`);
  }, { passive: true }));

  /* Forms: busy label on the clicked button, no double submits */
  $$("form").forEach((form) => form.addEventListener("submit", (e) => {
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    const btn = e.submitter || $("button", form);
    if (btn && btn.dataset.busy) btn.classList.add("is-busy");
    // disable the others only after the browser has captured the clicked button's name/value
    setTimeout(() => $$("button", form).forEach((b) => { if (b !== btn) b.disabled = true; }), 0);
  }));

  /* "Send my edited version" appears only once the draft changes */
  $$(".ask").forEach((card) => {
    const area = $("textarea", card); if (!area) return;
    const original = area.value.trim();
    area.addEventListener("input", () => card.classList.toggle("is-edited", area.value.trim() !== original));
  });

  /* Drop zone */
  const zone = $(".drop");
  if (zone) {
    const input = $("input[type=file]", zone), name = $(".drop__file", zone);
    const show = () => { name.textContent = input.files[0] ? input.files[0].name : ""; };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
  }

  /* Copy the inbox address */
  $$("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { return; }
    const was = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => (btn.textContent = was), 1600);
  }));

  /* Toast: auto-dismiss, and a refresh shouldn't replay it */
  const toast = $(".toast");
  if (toast) {
    const close = () => { toast.classList.add("is-leaving"); setTimeout(() => toast.remove(), 350); };
    $("button", toast).addEventListener("click", close);
    setTimeout(close, 6500);
    const url = new URL(location.href); url.searchParams.delete("msg"); history.replaceState(null, "", url);
  }

  window.__cofoundr = true;
})();
