/* CoFoundr dashboard: progressive enhancement only. Everything works (and Saint can read it) without this file. */
(() => {
  const doc = document.documentElement;
  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  /* ---- Hero: slow espresso + crema swirl (WebGL), paused when hidden; CSS gradient is the fallback ---- */
  function crema(canvas) {
    const gl = canvas.getContext("webgl", { antialias: false, premultipliedAlpha: false, powerPreference: "low-power" });
    if (!gl) return;
    const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const fs = `precision mediump float;uniform vec2 r;uniform float t;uniform vec2 m;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p),u=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}
      float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*n(p);p=p*2.03+vec2(1.7,9.2);a*=.5;}return v;}
      void main(){vec2 uv=gl_FragCoord.xy/r;vec2 p=uv*vec2(r.x/r.y,1.)*1.5;float s=t*.035;
        vec2 q=vec2(fbm(p+s),fbm(p+vec2(5.2,1.3)-s));vec2 pull=(m-uv)*.5;
        vec2 w=vec2(fbm(p+3.*q+vec2(1.7,9.2)+s*1.4+pull),fbm(p+3.*q+vec2(8.3,2.8)-s));float f=fbm(p+2.4*w);
        vec3 c=mix(vec3(.09,.055,.035),vec3(.34,.19,.1),smoothstep(.2,.6,f));
        c=mix(c,vec3(.82,.53,.31),smoothstep(.42,.8,f*length(q)*1.3));
        c=mix(c,vec3(.98,.88,.73),smoothstep(.7,.98,f*w.x*1.45)*.65);
        c*=mix(.62,1.15,smoothstep(1.2,.1,length(uv-vec2(.74,.6))));
        gl_FragColor=vec4(c,1.);}`;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(prog, "r"), uT = gl.getUniformLocation(prog, "t"), uM = gl.getUniformLocation(prog, "m");
    const mouse = { x: .7, y: .6, tx: .7, ty: .6 };
    let visible = true, raf = 0, t0 = performance.now() - 40000 * Math.random();
    const size = () => {
      const scale = Math.min(devicePixelRatio || 1, 1.5) * .5;  // half resolution: soft by design, cheap to draw
      canvas.width = Math.max(1, canvas.clientWidth * scale); canvas.height = Math.max(1, canvas.clientHeight * scale);
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    const draw = (now) => {
      mouse.x += (mouse.tx - mouse.x) * .04; mouse.y += (mouse.ty - mouse.y) * .04;
      gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, (now - t0) / 1000); gl.uniform2f(uM, mouse.x, mouse.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!calm && visible && !document.hidden) raf = requestAnimationFrame(draw);
    };
    const start = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); };
    size(); start(); canvas.classList.add("is-live");
    addEventListener("resize", () => { size(); if (calm) start(); }, { passive: true });
    canvas.parentElement.addEventListener("pointermove", (e) => {
      const b = canvas.getBoundingClientRect(); mouse.tx = (e.clientX - b.left) / b.width; mouse.ty = 1 - (e.clientY - b.top) / b.height;
    }, { passive: true });
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) start(); }).observe(canvas);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) start(); });
  }

  /* ---- Count-up numbers ---- */
  function countUp(el) {
    const target = parseFloat(el.dataset.count), prefix = el.dataset.prefix || "", dur = 1100, t0 = performance.now();
    if (!isFinite(target) || calm) return;
    const fmt = (v) => prefix + Math.round(v).toLocaleString("en-GB");
    const tick = (now) => { const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 4); el.textContent = fmt(target * e); if (k < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }

  /* ---- Reveal on scroll ---- */
  function reveal() {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px", threshold: .12 });
    $$(".reveal, .week").forEach((el) => io.observe(el));
  }

  /* ---- Forms: busy state, no double submits ---- */
  function forms() {
    $$("form").forEach((form) => form.addEventListener("submit", (e) => {
      if (form.dataset.sent) { e.preventDefault(); return; }
      form.dataset.sent = "1";
      const btn = e.submitter || $("button[type=submit], button:not([type])", form);
      if (btn && btn.dataset.busy) btn.classList.add("is-busy");
      // disable the others after the browser has captured the clicked button's name/value
      setTimeout(() => $$("button", form).forEach((b) => { if (b !== btn) b.disabled = true; }), 0);
    }));
    $$(".ask").forEach((card) => {
      const area = $("textarea", card);
      if (!area) return;
      const original = area.value;
      area.addEventListener("input", () => card.classList.toggle("is-edited", area.value.trim() !== original.trim()));
      card.addEventListener("pointermove", (e) => {
        const b = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${e.clientX - b.left}px`); card.style.setProperty("--my", `${e.clientY - b.top}px`);
      }, { passive: true });
    });
  }

  /* ---- Drop zone ---- */
  function dropzone() {
    const zone = $(".drop"); if (!zone) return;
    const input = $("input[type=file]", zone), name = $(".drop__file", zone);
    const show = () => { name.textContent = input.files[0] ? input.files[0].name : ""; zone.classList.toggle("has-file", !!input.files[0]); };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("is-over"); }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
  }

  /* ---- Copy the inbox address ---- */
  function copy() {
    $$("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(btn.dataset.copy); } catch { return; }
      const was = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => (btn.textContent = was), 1600);
    }));
  }

  /* ---- Toast ---- */
  function toast() {
    const el = $(".toast"); if (!el) return;
    const close = () => { el.classList.add("is-leaving"); setTimeout(() => el.remove(), 400); };
    $("button", el).addEventListener("click", close);
    setTimeout(close, 7000);
    history.replaceState(null, "", location.pathname + location.hash);  // a refresh shouldn't replay the message
  }

  /* ---- Dock: appears after the hero, follows the section you're reading ---- */
  let observer;
  function dockSetup() {
    const bar = $(".dock"), hero = $(".hero"); if (!bar || !hero) return;
    new IntersectionObserver(([e]) => bar.classList.toggle("is-shown", !e.isIntersecting), { threshold: .25 }).observe(hero);
    const links = $$("a", bar), byId = Object.fromEntries(links.map((a) => [a.hash.slice(1), a]));
    observer = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting && byId[e.target.id]) { links.forEach((a) => a.classList.remove("is-active")); byId[e.target.id].classList.add("is-active"); }
    }), { rootMargin: "-45% 0px -50% 0px" });
    $$("section[id]").forEach((s) => observer.observe(s));
  }

  window.__cofoundr = true;  // tells the <head> fallback the enhancements loaded
  doc.classList.add("js");
  const canvas = $(".hero__canvas"); if (canvas) crema(canvas);
  $$("[data-count]").forEach(countUp);
  reveal(); forms(); dropzone(); copy(); toast(); dockSetup();
})();
