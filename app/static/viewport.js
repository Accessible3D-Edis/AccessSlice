/* AccessSlice - the 3D view of the build plate (Phase IV).
 *
 * The one rule: the view is a MIRROR, never the master. Every
 * operation on this page is a real, labelled control (the X/Y fields,
 * the rotate and size fields, the Arrange and Auto-orient buttons, the
 * "Show ... in the 3D view" toggles). The canvas shows the same state
 * and adds two mouse conveniences:
 *
 *   - click an object to select it (its fieldset is highlighted and
 *     scrolled into view; the toggle button shows as pressed),
 *   - drag an object across the plate to move it - on release the
 *     new position is written into that object's X and Y fields and
 *     the Placement form is submitted, so the move is staged exactly
 *     like a typed one: reviewable, discardable, saved on Save.
 *
 * Screen readers never meet the canvas (the stage is aria-hidden and
 * nothing in it is focusable). What they hear is the summary sentence,
 * the toolbar buttons, the plate chooser and the status line - all
 * real HTML rendered by the server.
 *
 * Meshes come from /api/mesh/<id> as one binary blob each, in the
 * object's own coordinates; the build-item transform from /api/scene
 * is applied here, so a staged move never re-sends a mesh. Only the
 * shown plate's meshes are loaded, which is what keeps a 36-plate
 * life-sized project usable.
 *
 * Needs three.min.js (loaded before this file). Without WebGL the
 * status line says so and the rest of the page works as before.
 */

(function () {
  "use strict";

  var stage = document.getElementById("view-stage");
  if (!stage || typeof THREE === "undefined") return;

  var panel = document.getElementById("view-panel");
  var statusEl = document.getElementById("view-status");
  var summaryEl = document.getElementById("view-summary");
  var legendEl = document.getElementById("view-legend");
  var plateSelect = document.getElementById("view-plate");
  var sceneUrl = panel.getAttribute("data-scene-url") || "/api/scene";
  var storageKey = "accessslice-view:" + (panel.getAttribute("data-project") || "");

  function say(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function cssColour(name, fallback) {
    // Read a CSS token through a probe element, so any CSS colour
    // syntax works, then hand Three.js an rgb() string.
    var probe = document.createElement("span");
    probe.style.color = cssVar(name, fallback);
    document.body.appendChild(probe);
    var rgb = getComputedStyle(probe).color;
    document.body.removeChild(probe);
    return new THREE.Color(rgb || fallback);
  }

  /* ------------------------------------------------------ renderer */

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  } catch (err) {
    say("The 3D view could not start in this browser (no WebGL). " +
        "Everything on this page still works without it.");
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  stage.appendChild(renderer.domElement);
  var canvas = renderer.domElement;
  canvas.setAttribute("tabindex", "-1");
  canvas.setAttribute("aria-hidden", "true");

  var scene3 = new THREE.Scene();
  scene3.background = cssColour("--surface-2", "#e9ebee");
  var camera = new THREE.PerspectiveCamera(40, 1, 1, 20000);
  camera.up.set(0, 0, 1);
  scene3.add(new THREE.AmbientLight(0xffffff, 0.75));
  var sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(-300, -500, 800);
  scene3.add(sun);
  var fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(500, 300, 200);
  scene3.add(fill);

  var dirty = true;
  function render() { dirty = true; }
  renderer.setAnimationLoop(function () {
    if (!dirty) return;
    dirty = false;
    renderer.render(scene3, camera);
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

  var view = { theta: -Math.PI / 2 + 0.55, phi: 0.95, dist: 600,
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

  function saveView() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        theta: view.theta, phi: view.phi, dist: view.dist,
        target: [view.target.x, view.target.y, view.target.z],
        plate: plateSelect ? plateSelect.value : "" }));
    } catch (err) { /* fine */ }
  }

  function restoreView() {
    try {
      var raw = sessionStorage.getItem(storageKey);
      if (!raw) return null;
      var v = JSON.parse(raw);
      if (typeof v.theta !== "number") return null;
      return v;
    } catch (err) { return null; }
  }

  /* --------------------------------------------------------- state */

  var data = null;          // the last /api/scene answer
  var meshes = {};          // objectid -> THREE.Mesh
  var plateGroup = new THREE.Group();
  var meshGroup = new THREE.Group();
  scene3.add(plateGroup);
  scene3.add(meshGroup);
  var selected = null;      // objectid or null
  var loadToken = 0;

  var accent = cssColour("--accent", "#0b5ea8");
  var offColour = cssColour("--danger", "#b3261e");

  function plateOrigin(pid) {
    if (!data) return [0, 0];
    for (var i = 0; i < data.plates.length; i++) {
      if (data.plates[i].id === pid) return data.plates[i].origin || [0, 0];
    }
    return [0, 0];
  }

  function shownOrigin() {
    if (!data) return [0, 0];
    if (data.all_plates && data.plates.length > 1) return null;
    return plateOrigin(data.shown_plate);
  }

  /* --------------------------------------------------------- plates */

  function labelSprite(text) {
    var c = document.createElement("canvas");
    c.width = 128; c.height = 64;
    var ctx = c.getContext("2d");
    ctx.font = "bold 40px Segoe UI, system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillStyle = "#" + cssColour("--muted", "#4b5563").getHexString();
    ctx.fillText(text, 64, 32);
    var tex = new THREE.CanvasTexture(c);
    var sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex,
      transparent: true, depthTest: false }));
    return sprite;
  }

  function buildPlates() {
    while (plateGroup.children.length) {
      var ch = plateGroup.children.pop();
      if (ch.geometry) ch.geometry.dispose();
      if (ch.material) ch.material.dispose();
    }
    if (!data || !data.plate_size) return;
    var PW = data.plate_size[0], PD = data.plate_size[1];
    var surface = cssColour("--plate", "#cfd5dd");
    var border = cssColour("--border-strong", "#8b929c");
    var faint = cssColour("--plate-grid", "#aab2bd");
    var plates = data.plates.length ? data.plates : [{ id: "", origin: [0, 0] }];
    var many = data.plates.length > 1;
    plates.forEach(function (p) {
      var o = p.origin || [0, 0];
      if (!p.origin && many) return;   // unreliable layout: only draw known ones
      var isShown = !data.all_plates && p.id === data.shown_plate;
      var face = new THREE.Mesh(new THREE.PlaneGeometry(PW, PD),
        new THREE.MeshBasicMaterial({ color: surface }));
      face.position.set(o[0] + PW / 2, o[1] + PD / 2, -0.15);
      face.userData.plate = true;
      plateGroup.add(face);

      // Grid every 10 mm, stronger every 50 mm.
      var pts = [], strong = [];
      for (var x = 0; x <= PW + 0.01; x += 10) {
        (x % 50 === 0 ? strong : pts).push(o[0] + x, o[1], 0, o[0] + x, o[1] + PD, 0);
      }
      for (var y = 0; y <= PD + 0.01; y += 10) {
        (y % 50 === 0 ? strong : pts).push(o[0], o[1] + y, 0, o[0] + PW, o[1] + y, 0);
      }
      [[pts, faint, 0.6], [strong, border, 0.9]].forEach(function (spec) {
        if (!spec[0].length) return;
        var g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(spec[0], 3));
        plateGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({
          color: spec[1], transparent: true, opacity: spec[2] })));
      });
      // The plate outline: accent for the shown plate.
      var edge = new THREE.BufferGeometry();
      edge.setAttribute("position", new THREE.Float32BufferAttribute([
        o[0], o[1], 0.05, o[0] + PW, o[1], 0.05,
        o[0] + PW, o[1], 0.05, o[0] + PW, o[1] + PD, 0.05,
        o[0] + PW, o[1] + PD, 0.05, o[0], o[1] + PD, 0.05,
        o[0], o[1] + PD, 0.05, o[0], o[1], 0.05], 3));
      plateGroup.add(new THREE.LineSegments(edge, new THREE.LineBasicMaterial({
        color: isShown || !many ? accent : border, linewidth: 2 })));
      // The printer's keep-out corner at the front left.
      if (data.keepout) {
        var kw = data.keepout[0], kd = data.keepout[1];
        var ko = new THREE.Mesh(new THREE.PlaneGeometry(kw, kd),
          new THREE.MeshBasicMaterial({ color: offColour, transparent: true,
                                        opacity: 0.25 }));
        ko.position.set(o[0] + kw / 2, o[1] + kd / 2, 0.1);
        plateGroup.add(ko);
      }
      if (many && p.id) {
        var s = labelSprite(String(p.id));
        s.scale.set(PW * 0.25, PW * 0.125, 1);
        s.position.set(o[0] + PW * 0.15, o[1] + PD * 0.92, 2);
        plateGroup.add(s);
      }
    });
  }

  /* --------------------------------------------------------- meshes */

  function matrixFor(nums) {
    // 3MF item transforms are row-vector: world = p x M + t. Three.js
    // wants column-vector, so the 3x3 part is transposed into set().
    var m = new THREE.Matrix4();
    m.set(nums[0], nums[3], nums[6], nums[9],
          nums[1], nums[4], nums[7], nums[10],
          nums[2], nums[5], nums[8], nums[11],
          0, 0, 0, 1);
    return m;
  }

  function materialFor(obj, isSelected) {
    var colour = new THREE.Color(obj.colour || "#4f8fd6");
    var mat = new THREE.MeshStandardMaterial({
      color: colour, metalness: 0.05, roughness: 0.6 });
    if (obj.off_plate) {
      mat.color.lerp(offColour, 0.55);
    }
    if (isSelected) {
      mat.emissive = accent.clone();
      mat.emissiveIntensity = 0.45;
    }
    return mat;
  }

  function clearMeshes() {
    Object.keys(meshes).forEach(function (id) {
      var m = meshes[id];
      meshGroup.remove(m);
      m.geometry.dispose();
      m.material.dispose();
    });
    meshes = {};
  }

  function decodeMesh(buf) {
    var view8 = new Uint8Array(buf, 0, 4);
    if (String.fromCharCode(view8[0], view8[1], view8[2], view8[3]) !== "ASM1") {
      throw new Error("not a mesh blob");
    }
    var dv = new DataView(buf);
    var vc = dv.getUint32(4, true), tc = dv.getUint32(8, true);
    var verts = new Float32Array(buf, 12, vc * 3);
    var tris = new Uint32Array(buf, 12 + vc * 12, tc * 3);
    var g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(verts, 3));
    g.setIndex(new THREE.BufferAttribute(tris, 1));
    g.computeVertexNormals();
    return g;
  }

  function loadMeshes(token) {
    var todo = data.objects.filter(function (o) { return o.has_mesh; });
    var done = 0, total = todo.length;
    if (!total) {
      say(data.objects.length ? "No readable shapes to draw." : "");
      return;
    }
    say("Loading shapes for the 3D view: 0 of " + total + ".");
    var queue = todo.slice();
    function next() {
      if (token !== loadToken) return;
      var obj = queue.shift();
      if (!obj) return;
      fetch("/api/mesh/" + encodeURIComponent(obj.id))
        .then(function (r) {
          if (!r.ok || (r.headers.get("Content-Type") || "").indexOf("json") >= 0) {
            throw new Error("no mesh");
          }
          return r.arrayBuffer();
        })
        .then(function (buf) {
          if (token !== loadToken) return;
          var g = decodeMesh(buf);
          var mesh = new THREE.Mesh(g, materialFor(obj, obj.id === selected));
          mesh.matrixAutoUpdate = false;
          mesh.matrix.copy(matrixFor(obj.nums));
          mesh.userData.objectid = obj.id;
          meshes[obj.id] = mesh;
          meshGroup.add(mesh);
          render();
        })
        .catch(function () { /* an unreadable mesh is simply not drawn */ })
        .then(function () {
          done += 1;
          if (token !== loadToken) return;
          if (done < total) {
            say("Loading shapes for the 3D view: " + done + " of " + total + ".");
            next();
          } else {
            say(loadedSentence());
          }
        });
    }
    // Three at a time keeps big plates responsive.
    next(); next(); next();
  }

  function loadedSentence() {
    var over = data.objects.filter(function (o) { return o.off_plate; });
    var text = "3D view ready: " + data.objects.length + " object" +
      (data.objects.length === 1 ? "" : "s") + " drawn.";
    if (over.length) {
      text += " Hanging over the plate edge: " +
        over.map(function (o) { return o.name; }).join(", ") + ".";
    }
    return text;
  }

  /* ---------------------------------------------------- page sync */

  function toggleButtons() {
    return Array.prototype.slice.call(document.querySelectorAll("button.view-select"));
  }

  function fieldsetOf(id) {
    return document.querySelector('fieldset[data-objectid="' + id + '"]');
  }

  function objectById(id) {
    if (!data) return null;
    for (var i = 0; i < data.objects.length; i++) {
      if (data.objects[i].id === id) return data.objects[i];
    }
    return null;
  }

  function select(id, from) {
    // from: "view" (a click in the canvas), "button" (the toggle), or
    // null (clear).
    var previous = selected;
    selected = (id && id !== selected) ? id : null;
    Object.keys(meshes).forEach(function (mid) {
      var obj = objectById(mid);
      if (!obj) return;
      meshes[mid].material.dispose();
      meshes[mid].material = materialFor(obj, mid === selected);
    });
    toggleButtons().forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-objectid") === selected));
    });
    document.querySelectorAll("fieldset.selected").forEach(function (f) {
      f.classList.remove("selected");
    });
    var obj = selected ? objectById(selected) : null;
    if (obj) {
      var fs = fieldsetOf(selected);
      if (fs) {
        fs.classList.add("selected");
        if (from === "view") fs.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      if (from === "button") frameObject(obj);
      say(obj.name + " selected" + (from === "view" ? " from the 3D view" : "") +
          (obj.off_plate ? ". It hangs over the plate edge." : "."));
    } else if (previous) {
      say("Nothing selected.");
    }
    render();
  }

  function frameObject(obj) {
    if (!obj.bbox) return;
    var b = obj.bbox;
    view.target.set((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
    var size = Math.max(b[3] - b[0], b[4] - b[1], b[5] - b[2], 10);
    view.dist = size * 2.2;
    applyCamera();
    saveView();
  }

  function fitPlate() {
    if (!data || !data.plate_size) { applyCamera(); return; }
    var PW = data.plate_size[0], PD = data.plate_size[1];
    var o = shownOrigin();
    if (o === null) {
      // All plates: frame the whole layout.
      var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
      data.plates.forEach(function (p) {
        var po = p.origin || [0, 0];
        minx = Math.min(minx, po[0]); miny = Math.min(miny, po[1]);
        maxx = Math.max(maxx, po[0] + PW); maxy = Math.max(maxy, po[1] + PD);
      });
      view.target.set((minx + maxx) / 2, (miny + maxy) / 2, 0);
      view.dist = Math.max(maxx - minx, maxy - miny) * 1.35;
    } else {
      view.target.set(o[0] + PW / 2, o[1] + PD / 2, 0);
      view.dist = Math.max(PW, PD) * 1.55;
    }
    applyCamera();
    saveView();
  }

  function fillLegend() {
    if (!legendEl || !data) return;
    legendEl.textContent = "";
    var seen = {};
    data.objects.forEach(function (o) {
      var key = o.colour || "";
      if (seen[key]) return;
      seen[key] = true;
      var li = document.createElement("li");
      var sw = document.createElement("span");
      sw.className = "swatch"; sw.style.background = key; sw.setAttribute("aria-hidden", "true");
      li.appendChild(sw);
      li.appendChild(document.createTextNode(
        "Filament colour " + key + (Object.keys(seen).length === 1 && data.objects.length === 1
          ? " (" + o.name + ")" : "")));
      legendEl.appendChild(li);
    });
    var extra = [["Selected object", "#" + accent.getHexString()],
                 ["Hangs over the plate edge", "#" + offColour.getHexString()]];
    if (data.keepout) extra.push(["Keep-out corner of the printer", "#" + offColour.getHexString()]);
    extra.forEach(function (e) {
      var li = document.createElement("li");
      var sw = document.createElement("span");
      sw.className = "swatch"; sw.style.background = e[1]; sw.setAttribute("aria-hidden", "true");
      li.appendChild(sw);
      li.appendChild(document.createTextNode(e[0]));
      legendEl.appendChild(li);
    });
  }

  function fillNotes() {
    document.querySelectorAll("[data-view-note]").forEach(function (el) {
      el.textContent = "";
    });
    if (!data) return;
    data.objects.forEach(function (o) {
      var el = document.querySelector('[data-view-note="' + o.id + '"]');
      if (!el) return;
      if (o.off_plate) {
        el.textContent = "In the 3D view this object hangs over the plate edge.";
      } else if (!o.has_mesh) {
        el.textContent = "Its shape could not be read, so it is not drawn.";
      }
    });
    if (summaryEl) {
      var over = data.objects.filter(function (o) { return o.off_plate; });
      if (over.length) {
        summaryEl.textContent = summaryEl.textContent.replace(/\s*Hanging over.*$/, "") +
          " Hanging over the plate edge: " +
          over.map(function (o) { return o.name; }).join(", ") + ".";
      }
    }
  }

  /* ------------------------------------------------------- loading */

  function loadScene(keepCamera) {
    var token = ++loadToken;
    var url = sceneUrl;
    if (plateSelect) {
      url += (plateSelect.value === "all") ? "?all=1"
           : "?plate=" + encodeURIComponent(plateSelect.value);
    }
    say("Loading the 3D view.");
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (token !== loadToken) return;
      if (d.error) { say(d.error); return; }
      data = d;
      clearMeshes();
      buildPlates();
      fillLegend();
      fillNotes();
      var saved = keepCamera ? restoreView() : null;
      if (saved) {
        view.theta = saved.theta; view.phi = saved.phi; view.dist = saved.dist;
        view.target.set(saved.target[0], saved.target[1], saved.target[2]);
        applyCamera();
      } else {
        fitPlate();
      }
      toggleButtons().forEach(function (b) { b.hidden = false; });
      loadMeshes(token);
    }).catch(function () {
      say("The 3D view could not load its data - is AccessSlice still running?");
    });
  }

  /* --------------------------------------------------- interaction */

  var ray = new THREE.Raycaster();
  var ptr = new THREE.Vector2();
  var plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  var drag = null;   // {mode: "orbit"|"pan"|"move", ...}

  function pointerToNdc(e) {
    var r = canvas.getBoundingClientRect();
    ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    ray.setFromCamera(ptr, camera);
  }

  function hitObject(e) {
    pointerToNdc(e);
    var hits = ray.intersectObjects(meshGroup.children, false);
    return hits.length ? hits[0].object : null;
  }

  function planePoint(e) {
    pointerToNdc(e);
    var p = new THREE.Vector3();
    return ray.ray.intersectPlane(plane, p) ? p : null;
  }

  canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });

  canvas.addEventListener("pointerdown", function (e) {
    if (!data) return;
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("dragging");
    var base = { x: e.clientX, y: e.clientY, moved: false };
    if (e.button === 0 && !e.shiftKey) {
      var hit = hitObject(e);
      if (hit) {
        var start = planePoint(e);
        var m = hit.matrix.elements;
        drag = { mode: "move", mesh: hit, start: start,
                 tx: m[12], ty: m[13], base: base };
        return;
      }
      drag = { mode: "orbit", base: base, theta: view.theta, phi: view.phi };
    } else {
      drag = { mode: "pan", base: base, target: view.target.clone() };
    }
  });

  canvas.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.base.x, dy = e.clientY - drag.base.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.base.moved = true;
    if (drag.mode === "orbit") {
      view.theta = drag.theta - dx * 0.008;
      view.phi = Math.min(Math.max(drag.phi - dy * 0.008, 0.05), Math.PI / 2 - 0.02);
      applyCamera();
    } else if (drag.mode === "pan") {
      var right = new THREE.Vector3(), up = new THREE.Vector3(0, 0, 1);
      camera.getWorldDirection(right);
      right.cross(up).normalize();
      var forward = up.clone().cross(right).normalize();
      var k = view.dist * 0.0018;
      view.target.copy(drag.target)
        .addScaledVector(right, -dx * k)
        .addScaledVector(forward, -dy * k);
      applyCamera();
    } else if (drag.mode === "move" && drag.start) {
      var p = planePoint(e);
      if (!p) return;
      var el = drag.mesh.matrix.elements;
      el[12] = drag.tx + (p.x - drag.start.x);
      el[13] = drag.ty + (p.y - drag.start.y);
      drag.mesh.matrixWorldNeedsUpdate = true;
      render();
    }
  });

  function endDrag(e) {
    if (!drag) return;
    var d = drag;
    drag = null;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* fine */ }
    if (d.mode === "move") {
      var id = d.mesh.userData.objectid;
      var el = d.mesh.matrix.elements;
      var movedMm = Math.hypot(el[12] - d.tx, el[13] - d.ty);
      if (!d.base.moved || movedMm < 0.5) {
        // A click, not a drag.
        el[12] = d.tx; el[13] = d.ty; d.mesh.matrixWorldNeedsUpdate = true;
        select(id, "view");
        return;
      }
      commitMove(id, el[12], el[13]);
      return;
    }
    if (!d.base.moved && d.mode === "orbit") {
      if (selected) select(null, null);
    }
    saveView();
  }
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    zoomBy(1 + e.deltaY * 0.0012);
  }, { passive: false });

  function zoomBy(factor) {
    view.dist = Math.min(Math.max(view.dist * factor, 20), 15000);
    applyCamera();
    saveView();
  }

  function commitMove(id, wx, wy) {
    // Write the plate-local centre into the object's own X and Y
    // fields and submit the Placement form - the very same staged
    // move a typed value produces.
    var obj = objectById(id);
    var o = obj ? plateOrigin(obj.plate) : [0, 0];
    var fx = document.getElementById("f-x__" + id);
    var fy = document.getElementById("f-y__" + id);
    var form = document.querySelector('form[action="/project/placement"]');
    if (!fx || !fy || !form) {
      say("The move could not be staged: the position fields were not found.");
      return;
    }
    fx.value = (Math.round((wx - o[0]) * 10) / 10).toString();
    fy.value = (Math.round((wy - o[1]) * 10) / 10).toString();
    say("Moving " + (obj ? obj.name : "the object") + " to X " + fx.value +
        ", Y " + fy.value + " - staging the change.");
    saveView();
    try { sessionStorage.setItem("accessslice-focus", fx.id); } catch (err) { /* fine */ }
    var apply = document.getElementById("btn-apply-placement");
    if (apply && form.requestSubmit) form.requestSubmit(apply); else form.submit();
  }

  /* ------------------------------------------------------ controls */

  function on(id, fn) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  }
  on("view-fit", function () { fitPlate(); say("View fitted to the plate."); });
  on("view-top", function () {
    view.theta = -Math.PI / 2; view.phi = 0.02; applyCamera(); saveView();
    say("Viewing from above.");
  });
  on("view-front", function () {
    view.theta = -Math.PI / 2; view.phi = Math.PI / 2 - 0.12; applyCamera(); saveView();
    say("Viewing from the front.");
  });
  on("view-angle", function () {
    view.theta = -Math.PI / 2 + 0.55; view.phi = 0.95; applyCamera(); saveView();
    say("Viewing from the front left, above.");
  });
  on("view-zoom-in", function () { zoomBy(0.75); say("Zoomed in."); });
  on("view-zoom-out", function () { zoomBy(1.33); say("Zoomed out."); });

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button.view-select");
    if (!b) return;
    e.preventDefault();
    select(b.getAttribute("data-objectid"), "button");
  });

  if (plateSelect) {
    var saved = restoreView();
    if (saved && saved.plate) {
      var has = Array.prototype.some.call(plateSelect.options, function (opt) {
        return opt.value === saved.plate;
      });
      if (has) plateSelect.value = saved.plate;
    }
    plateSelect.addEventListener("change", function () {
      selected = null;
      loadScene(false);
      saveView();
    });
  }

  resize();
  loadScene(true);
})();
