/* AccessSlice - the sliced preview: toolpaths layer by layer (Phase V).
 *
 * The picture is a mirror of the facts on this page, never more: every
 * number it shows (layers, heights, what each layer is made of) is
 * spoken by the page first, and every control here is a real one -
 * the layer slider and number field, the travel checkbox, one
 * checkbox per feature, the view buttons. Changing the layer updates
 * the spoken layer facts too, so a screen-reader user and a sighted
 * user read the same thing.
 *
 * Data: /api/toolpath?plate=N is one binary blob (layers of x0,y0,x1,y1
 * segments with a feature kind byte each); /api/toolpath-facts?plate=N
 * is the JSON the page was rendered from (colours, layer facts).
 *
 * Needs three.min.js. The canvas is aria-hidden; without WebGL the
 * status line says so and the facts remain.
 */

(function () {
  "use strict";

  var stage = document.getElementById("preview-stage");
  if (!stage || typeof THREE === "undefined") return;

  var panel = document.getElementById("preview-panel");
  var plate = panel.getAttribute("data-plate") || "";
  var statusEl = document.getElementById("preview-status");
  var slider = document.getElementById("layer-slider");
  var number = document.getElementById("layer-number");
  var travelBox = document.getElementById("show-travel");
  var layerFacts = document.getElementById("layer-facts");
  var kindBoxes = Array.prototype.slice.call(
    document.querySelectorAll("input.kind-toggle"));

  function say(text) { if (statusEl) statusEl.textContent = text; }

  function cssColour(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    var probe = document.createElement("span");
    probe.style.color = (v && v.trim()) || fallback;
    document.body.appendChild(probe);
    var rgb = getComputedStyle(probe).color;
    document.body.removeChild(probe);
    return new THREE.Color(rgb || fallback);
  }

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true });
  } catch (err) {
    say("The preview picture could not start in this browser (no WebGL). " +
        "The layer facts above still tell the whole story.");
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  stage.appendChild(renderer.domElement);
  var canvas = renderer.domElement;
  canvas.setAttribute("tabindex", "-1");
  canvas.setAttribute("aria-hidden", "true");

  var scene = new THREE.Scene();
  scene.background = cssColour("--surface-2", "#e9ebee");
  var camera = new THREE.PerspectiveCamera(40, 1, 1, 20000);
  camera.up.set(0, 0, 1);

  var dirty = true;
  function render() { dirty = true; }
  renderer.setAnimationLoop(function () {
    if (!dirty) return;
    dirty = false;
    renderer.render(scene, camera);
  });
  function resize() {
    var w = stage.clientWidth || 1, h = stage.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    render();
  }
  if (window.ResizeObserver) new ResizeObserver(resize).observe(stage);
  window.addEventListener("resize", resize);

  /* -------------------------------------------------------- camera */

  var view = { theta: -Math.PI / 2 + 0.55, phi: 0.95, dist: 300,
               target: new THREE.Vector3(0, 0, 0) };
  function applyCamera() {
    var t = view.target;
    camera.position.set(
      t.x + view.dist * Math.sin(view.phi) * Math.cos(view.theta),
      t.y + view.dist * Math.sin(view.phi) * Math.sin(view.theta),
      t.z + view.dist * Math.cos(view.phi));
    camera.lookAt(t);
    render();
  }

  /* --------------------------------------------------------- data */

  var facts = null;
  var layers = [];        // {z, height, n, xy: Float32Array, kinds: Uint8Array, mesh}
  var kindIndex = {};     // kind key -> index
  var kindColours = [];   // index -> THREE.Color
  var shownKinds = {};    // index -> true/false
  var topLayer = 0;       // 1-based

  function buildPlate() {
    if (!facts || !facts.plate_size) return;
    var PW = facts.plate_size[0], PD = facts.plate_size[1];
    var face = new THREE.Mesh(new THREE.PlaneGeometry(PW, PD),
      new THREE.MeshBasicMaterial({ color: cssColour("--plate", "#cfd5dd") }));
    face.position.set(PW / 2, PD / 2, -0.2);
    scene.add(face);
    var pts = [];
    for (var x = 0; x <= PW + 0.01; x += 10) pts.push(x, 0, 0, x, PD, 0);
    for (var y = 0; y <= PD + 0.01; y += 10) pts.push(0, y, 0, PW, y, 0);
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    scene.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({
      color: cssColour("--plate-grid", "#aab2bd"), transparent: true, opacity: 0.6 })));
  }

  function decode(buf) {
    var dv = new DataView(buf);
    var magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
    if (magic !== "ASTP") throw new Error("not a toolpath blob");
    var count = dv.getUint32(4, true);
    var pos = 8;
    var out = [];
    for (var i = 0; i < count; i++) {
      var z = dv.getFloat32(pos, true), h = dv.getFloat32(pos + 4, true);
      var n = dv.getUint32(pos + 8, true);
      pos += 12;
      var xy = new Float32Array(buf, pos, n * 4);
      pos += n * 16;
      var kinds = new Uint8Array(buf, pos, n);
      pos += n + ((4 - (n % 4)) % 4);
      out.push({ z: z, height: h, n: n, xy: xy, kinds: kinds, mesh: null });
    }
    return out;
  }

  function buildLayer(layer) {
    // Positions and colours for every segment; an index picks the
    // segments whose kind is shown (rebuilt when a checkbox changes).
    var n = layer.n;
    var pos = new Float32Array(n * 6);
    var col = new Float32Array(n * 6);
    var z = layer.z;
    for (var i = 0; i < n; i++) {
      pos[i * 6] = layer.xy[i * 4]; pos[i * 6 + 1] = layer.xy[i * 4 + 1]; pos[i * 6 + 2] = z;
      pos[i * 6 + 3] = layer.xy[i * 4 + 2]; pos[i * 6 + 4] = layer.xy[i * 4 + 3]; pos[i * 6 + 5] = z;
      var c = kindColours[layer.kinds[i]] || kindColours[0];
      col[i * 6] = c.r; col[i * 6 + 1] = c.g; col[i * 6 + 2] = c.b;
      col[i * 6 + 3] = c.r; col[i * 6 + 4] = c.g; col[i * 6 + 5] = c.b;
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    var mesh = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true }));
    mesh.frustumCulled = false;
    layer.mesh = mesh;
    scene.add(mesh);
    applyKinds(layer);
  }

  function applyKinds(layer) {
    var idx = [];
    for (var i = 0; i < layer.n; i++) {
      if (shownKinds[layer.kinds[i]]) idx.push(i * 2, i * 2 + 1);
    }
    layer.mesh.geometry.setIndex(idx);
    layer.mesh.geometry.index.needsUpdate = true;
  }

  function applyTop() {
    for (var i = 0; i < layers.length; i++) {
      if (layers[i].mesh) layers[i].mesh.visible = (i < topLayer);
    }
    render();
  }

  function readKinds() {
    shownKinds = {};
    kindBoxes.forEach(function (b) {
      var k = kindIndex[b.value];
      if (k !== undefined) shownKinds[k] = b.checked;
    });
    if (travelBox) {
      shownKinds[kindIndex.travel] = travelBox.checked;
      shownKinds[kindIndex.wipe] = travelBox.checked;
    }
  }

  function fit() {
    if (!facts) return;
    var b = facts.bbox;
    if (b) {
      view.target.set((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, facts.top_z / 2);
      view.dist = Math.max(b[2] - b[0], b[3] - b[1], facts.top_z, 20) * 2.0;
    } else if (facts.plate_size) {
      view.target.set(facts.plate_size[0] / 2, facts.plate_size[1] / 2, 0);
      view.dist = Math.max(facts.plate_size[0], facts.plate_size[1]) * 1.5;
    }
    applyCamera();
  }

  /* ----------------------------------------------------- the facts */

  function minutesWords(min) {
    if (min === null || min === undefined) return "";
    if (min < 60) return min + " minute" + (min === 1 ? "" : "s");
    var h = Math.floor(min / 60), m = min % 60;
    return h + " hour" + (h === 1 ? "" : "s") + (m ? " " + m + " minute" + (m === 1 ? "" : "s") : "");
  }

  function mmWords(mm) {
    return mm >= 1000 ? (mm / 1000).toFixed(1) + " m" : Math.round(mm) + " mm";
  }

  function layerSentence(n) {
    var L = facts.layers[n - 1];
    if (!L) return "";
    var s = "Layer " + n + " of " + facts.layer_count + ", top at " + L.z +
      " mm, " + L.height + " mm thick.";
    if (L.start_min !== null && L.start_min !== undefined) {
      s += " Starts about " + (L.start_min === 0 ? "at the beginning" : minutesWords(L.start_min) + " into the print") + ".";
    }
    s += " " + L.segments + " moves: " + mmWords(L.extrusion_mm) + " of extrusion";
    if (L.features.length) {
      s += " - " + L.features.map(function (f) { return f.name.toLowerCase() + " " + mmWords(f.mm); }).join(", ");
    }
    s += "; " + mmWords(L.travel_mm) + " of travel.";
    return s;
  }

  function setLayer(n, announce) {
    n = Math.max(1, Math.min(facts.layer_count, n | 0));
    topLayer = n;
    if (slider && parseInt(slider.value, 10) !== n) slider.value = String(n);
    if (number && parseInt(number.value, 10) !== n) number.value = String(n);
    applyTop();
    if (announce && layerFacts) layerFacts.textContent = layerSentence(n);
  }

  /* ---------------------------------------------------- interaction */

  var drag = null;
  canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  canvas.addEventListener("pointerdown", function (e) {
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("dragging");
    var base = { x: e.clientX, y: e.clientY };
    if (e.button === 0 && !e.shiftKey) {
      drag = { mode: "orbit", base: base, theta: view.theta, phi: view.phi };
    } else {
      drag = { mode: "pan", base: base, target: view.target.clone() };
    }
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.base.x, dy = e.clientY - drag.base.y;
    if (drag.mode === "orbit") {
      view.theta = drag.theta - dx * 0.008;
      view.phi = Math.min(Math.max(drag.phi - dy * 0.008, 0.05), Math.PI / 2 - 0.02);
    } else {
      var right = new THREE.Vector3(), up = new THREE.Vector3(0, 0, 1);
      camera.getWorldDirection(right);
      right.cross(up).normalize();
      var forward = up.clone().cross(right).normalize();
      var k = view.dist * 0.0018;
      view.target.copy(drag.target).addScaledVector(right, -dx * k).addScaledVector(forward, -dy * k);
    }
    applyCamera();
  });
  function endDrag(e) {
    drag = null;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* fine */ }
  }
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    view.dist = Math.min(Math.max(view.dist * (1 + e.deltaY * 0.0012), 5), 15000);
    applyCamera();
  }, { passive: false });

  function on(id, fn) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }
  on("preview-fit", function () { fit(); say("View fitted to the print."); });
  on("preview-top", function () {
    view.theta = -Math.PI / 2; view.phi = 0.02; applyCamera(); say("Viewing from above.");
  });
  on("preview-front", function () {
    view.theta = -Math.PI / 2; view.phi = Math.PI / 2 - 0.12; applyCamera(); say("Viewing from the front.");
  });
  on("preview-angle", function () {
    view.theta = -Math.PI / 2 + 0.55; view.phi = 0.95; applyCamera(); say("Viewing from the front left, above.");
  });
  on("preview-zoom-in", function () { view.dist *= 0.75; applyCamera(); say("Zoomed in."); });
  on("preview-zoom-out", function () { view.dist *= 1.33; applyCamera(); say("Zoomed out."); });

  if (slider) {
    slider.addEventListener("input", function () { setLayer(parseInt(slider.value, 10), false); });
    slider.addEventListener("change", function () { setLayer(parseInt(slider.value, 10), true); });
  }
  if (number) {
    number.addEventListener("change", function () { setLayer(parseInt(number.value, 10), true); });
  }
  function kindsChanged() {
    readKinds();
    layers.forEach(function (L) { if (L.mesh) applyKinds(L); });
    render();
  }
  kindBoxes.forEach(function (b) { b.addEventListener("change", kindsChanged); });
  if (travelBox) travelBox.addEventListener("change", kindsChanged);
  // The layer form's Show button is not needed once the script runs:
  // the number field updates the facts by itself.
  var showBtn = document.getElementById("btn-show-layer");
  if (showBtn) {
    showBtn.addEventListener("click", function (e) {
      e.preventDefault();
      setLayer(parseInt(number.value, 10), true);
    });
  }

  /* ------------------------------------------------------- loading */

  var q = "?plate=" + encodeURIComponent(plate);
  say("Loading the preview.");
  fetch("/api/toolpath-facts" + q).then(function (r) { return r.json(); }).then(function (f) {
    if (f.error) { say(f.error); return; }
    facts = f;
    facts.kinds.forEach(function (k, i) {
      kindIndex[k.kind] = i;
      kindColours[i] = new THREE.Color(k.colour);
    });
    readKinds();
    buildPlate();
    fit();
    resize();
    return fetch("/api/toolpath" + q).then(function (r) {
      if (!r.ok) throw new Error("no data");
      return r.arrayBuffer();
    }).then(function (buf) {
      layers = decode(buf);
      var initial = parseInt((number && number.value) || slider.value, 10) || layers.length;
      topLayer = Math.max(1, Math.min(layers.length, initial));
      // Build in chunks so the page stays responsive on big plates.
      var i = 0;
      function step() {
        var t0 = performance.now();
        while (i < layers.length && performance.now() - t0 < 40) {
          buildLayer(layers[i]);
          i += 1;
        }
        if (i < layers.length) {
          say("Drawing layers: " + i + " of " + layers.length + ".");
          setTimeout(step, 0);
        } else {
          applyTop();
          say("Preview ready: " + layers.length + " layers drawn" +
              (facts.travel_drawn ? "" : "; travel moves left out on this big print") + ".");
        }
      }
      step();
    });
  }).catch(function () {
    say("The preview could not load its data - is AccessSlice still running?");
  });
})();
