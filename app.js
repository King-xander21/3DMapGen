/* maps3d demo — front-end controller
 * Left rail picks a place and parameters; the backend returns geometry we drop
 * into a three.js BufferGeometry. The elevation ramp doubles as the legend.
 */
(function () {
  "use strict";

  const PRESETS = [
    { name: "Grand Canyon", lat: 36.100, lon: -112.115, km: 12 },
    { name: "Mount Fuji",   lat: 35.3606, lon: 138.7274, km: 14 },
    { name: "Matterhorn",   lat: 45.9763, lon: 7.6586,   km: 8 },
    { name: "Santorini",    lat: 36.404, lon: 25.396,    km: 16 },
    { name: "Yosemite",     lat: 37.746, lon: -119.533,  km: 10 },
  ];

  const state = { lat: 36.100, lon: -112.115, km: 10, currentId: null };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const $ = (id) => document.getElementById(id);

  /* ---------------- map picker ---------------- */
  let map, marker, rect;

  function bbox() {
    const halfLat = state.km / 2 / 111.32;
    const halfLon = state.km / 2 / (111.32 * Math.cos(state.lat * Math.PI / 180));
    return [state.lon - halfLon, state.lat - halfLat,
            state.lon + halfLon, state.lat + halfLat];
  }

  function fmtCoord() {
    const ns = state.lat >= 0 ? "N" : "S";
    const ew = state.lon >= 0 ? "E" : "W";
    return `${Math.abs(state.lat).toFixed(4)}°${ns}  ${Math.abs(state.lon).toFixed(4)}°${ew}  ·  ${state.km} km`;
  }

  function drawFootprint() {
    const [w, s, e, n] = bbox();
    const bounds = [[s, w], [n, e]];
    if (rect) rect.setBounds(bounds);
    else rect = L.rectangle(bounds, { color: "#f0a24b", weight: 1.5, fillOpacity: 0.08 }).addTo(map);
    if (marker) marker.setLatLng([state.lat, state.lon]);
    else marker = L.circleMarker([state.lat, state.lon], { radius: 4, color: "#f0a24b", fillOpacity: 1 }).addTo(map);
    $("coord").textContent = fmtCoord();
  }

  function initMap() {
    map = L.map("map", { attributionControl: false, zoomControl: false })
      .setView([state.lat, state.lon], 9);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 17, crossOrigin: true,
    }).addTo(map);
    map.on("click", (ev) => {
      state.lat = ev.latlng.lat;
      state.lon = ev.latlng.lng;
      drawFootprint();
    });
    drawFootprint();
  }

  function initPresets() {
    const host = $("presets");
    PRESETS.forEach((p) => {
      const b = document.createElement("button");
      b.className = "preset";
      b.textContent = p.name;
      b.addEventListener("click", () => {
        state.lat = p.lat; state.lon = p.lon; state.km = p.km;
        $("km").value = p.km; $("km-out").textContent = `${p.km} km`;
        map.setView([p.lat, p.lon], 10);
        drawFootprint();
        generate();
      });
      host.appendChild(b);
    });
  }

  /* ---------------- place search (geocoding) ---------------- */
  function renderResults(items) {
    const ul = $("search-results");
    ul.innerHTML = "";
    if (!items.length) {
      ul.innerHTML = '<li class="sr-empty">No matches — try a different search.</li>';
      ul.hidden = false;
      return;
    }
    items.forEach((it) => {
      const li = document.createElement("li");
      li.className = "sr-item";
      li.textContent = it.name;
      li.addEventListener("click", () => selectResult(it));
      ul.appendChild(li);
    });
    ul.hidden = false;
  }

  function selectResult(it) {
    state.lat = it.lat;
    state.lon = it.lon;
    $("search-results").hidden = true;
    $("search-input").value = it.name.split(",")[0];
    if (it.bbox) {
      const [w, s, e, n] = it.bbox;
      map.fitBounds([[s, w], [n, e]], { maxZoom: 13 });
    } else {
      map.setView([it.lat, it.lon], 11);
    }
    drawFootprint();
    generate();
  }

  async function runSearch() {
    const q = $("search-input").value.trim();
    if (!q) return;
    const ul = $("search-results");
    ul.innerHTML = '<li class="sr-empty">Searching…</li>';
    ul.hidden = false;
    try {
      const res = await fetch("/api/search?q=" + encodeURIComponent(q));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "search failed");
      renderResults(data);
    } catch (err) {
      ul.innerHTML = '<li class="sr-empty">' + err.message + "</li>";
      ul.hidden = false;
    }
  }

  function initSearch() {
    $("search-btn").addEventListener("click", runSearch);
    $("search-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runSearch(); }
    });
  }

  /* ---------------- three.js viewer ---------------- */
  let renderer, scene, camera, controls, terrainMesh;

  function initViewer() {
    const el = $("viewer");
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(42, el.clientWidth / el.clientHeight, 0.1, 5000);
    camera.position.set(120, 150, 240);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = !reduceMotion;
    controls.autoRotateSpeed = 0.6;
    controls.addEventListener("start", () => { controls.autoRotate = false; });

    scene.add(new THREE.HemisphereLight(0xcfe0ea, 0x1a2730, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 0.85);
    key.position.set(-120, 200, 140);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xf0a24b, 0.18);
    fill.position.set(160, 80, -120);
    scene.add(fill);

    window.addEventListener("resize", onResize);
    animate();
  }

  function onResize() {
    const el = $("viewer");
    if (!el.clientWidth) return;
    camera.aspect = el.clientWidth / el.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(el.clientWidth, el.clientHeight);
  }

  function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }

  function setMesh(data) {
    if (terrainMesh) {
      scene.remove(terrainMesh);
      terrainMesh.geometry.dispose();
      terrainMesh.material.dispose();
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(data.positions, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(data.colors, 3));
    if (data.uvs) g.setAttribute("uv", new THREE.Float32BufferAttribute(data.uvs, 2));
    g.setIndex(data.indices);
    g.computeVertexNormals();

    const textured = data.stats && data.stats.has_texture;
    let mat;
    if (textured) {
      const tex = new THREE.TextureLoader().load(`/api/texture/${data.id}.png`, () => {
        renderer.render(scene, camera);
      });
      if ("sRGBEncoding" in THREE) tex.encoding = THREE.sRGBEncoding;
      tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
      mat = new THREE.MeshStandardMaterial({
        map: tex, roughness: 0.98, metalness: 0.0, flatShading: false,
        side: THREE.DoubleSide,
      });
    } else {
      mat = new THREE.MeshStandardMaterial({
        vertexColors: true, roughness: 0.95, metalness: 0.0, flatShading: true,
        side: THREE.DoubleSide,
      });
    }
    terrainMesh = new THREE.Mesh(g, mat);
    scene.add(terrainMesh);
    frameObject(g);
    if (!reduceMotion) controls.autoRotate = true;

    // Legend only makes sense for the elevation tint; attribution only for the map.
    $("legend").hidden = textured;
    $("attribution").hidden = !textured;
  }

  function frameObject(geometry) {
    geometry.computeBoundingSphere();
    const s = geometry.boundingSphere;
    controls.target.copy(s.center);
    const dist = s.radius / Math.sin((camera.fov * Math.PI / 180) / 2) * 1.15;
    const dir = new THREE.Vector3(0.35, 0.55, 1).normalize();
    camera.position.copy(s.center.clone().add(dir.multiplyScalar(dist)));
    camera.near = s.radius / 100;
    camera.far = s.radius * 12;
    camera.updateProjectionMatrix();
    controls.update();
  }

  /* ---------------- generate + export ---------------- */
  function params() {
    return {
      bbox: bbox(),
      resolution: parseInt($("res").value, 10),
      exaggeration: parseInt($("exag").value, 10) / 10,
      detail: parseInt($("detail").value, 10) / 100,
      mode: document.querySelector("#mode .seg-btn.is-active").dataset.mode,
      surface: document.querySelector("#surface .seg-btn.is-active").dataset.surface,
    };
  }

  async function generate() {
    const btn = $("generate");
    btn.disabled = true;
    $("empty").hidden = true;
    $("spinner").hidden = false;
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params()),
      });
      if (!res.ok) throw new Error((await res.json()).error || res.statusText);
      const data = await res.json();
      state.currentId = data.id;
      setMesh(data);
      showStats(data.stats);
      enableExports(true);
    } catch (err) {
      $("empty").hidden = false;
      $("empty").querySelector("p").textContent = "Couldn't build that model — " + err.message;
    } finally {
      $("spinner").hidden = true;
      btn.disabled = false;
    }
  }

  function showStats(s) {
    $("s-tris").textContent = s.triangles.toLocaleString();
    $("s-verts").textContent = s.vertices.toLocaleString();
    $("s-relief").textContent = s.relief_m + " m";
    const water = $("s-water");
    water.textContent = s.watertight ? "yes" : "surface";
    water.classList.toggle("good", s.watertight);
    $("stats").hidden = false;

    $("legend").hidden = !!s.has_texture;
    $("legend-hi").textContent = Math.round(s.max_elevation_m) + "m";
    $("legend-lo").textContent = Math.round(s.min_elevation_m) + "m";

    const badge = $("source-badge");
    badge.hidden = false;
    badge.textContent = s.source_label || s.source;
    badge.classList.toggle("synthetic", s.source === "synthetic");

    if (s.texture_note) $("surface-hint").textContent = s.texture_note;
  }

  function enableExports(on) {
    document.querySelectorAll(".export").forEach((b) => (b.disabled = !on));
  }

  function initExports() {
    document.querySelectorAll(".export").forEach((b) => {
      b.addEventListener("click", () => {
        if (!state.currentId) return;
        window.location = `/api/download/${state.currentId}.${b.dataset.fmt}`;
      });
    });
  }

  /* ---------------- control wiring ---------------- */
  function initControls() {
    $("km").addEventListener("input", (e) => {
      state.km = parseInt(e.target.value, 10);
      $("km-out").textContent = `${state.km} km`;
      drawFootprint();
    });
    $("res").addEventListener("input", (e) => { $("res-out").textContent = e.target.value; });
    $("exag").addEventListener("input", (e) => {
      $("exag-out").textContent = (parseInt(e.target.value, 10) / 10).toFixed(1) + "×";
    });
    $("detail").addEventListener("input", (e) => { $("detail-out").textContent = e.target.value + "%"; });

    const modeHints = {
      adaptive: "Lightweight triangulated surface — spends triangles only where the ground is rough.",
      solid: "Closed, watertight block with a flat base and walls — the version to slice for printing.",
    };
    document.querySelectorAll("#mode .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#mode .seg-btn").forEach((x) => x.classList.remove("is-active"));
        b.classList.add("is-active");
        $("mode-hint").textContent = modeHints[b.dataset.mode];
        $("detail-field").style.display = b.dataset.mode === "adaptive" ? "block" : "none";
      });
    });

    const surfaceHints = {
      elevation: "Coloured by height using the hypsometric ramp shown at right.",
      map: "Drapes the OpenStreetMap road map (roads, water, land use) over the terrain.",
    };
    document.querySelectorAll("#surface .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#surface .seg-btn").forEach((x) => x.classList.remove("is-active"));
        b.classList.add("is-active");
        $("surface-hint").textContent = surfaceHints[b.dataset.surface];
      });
    });

    $("generate").addEventListener("click", generate);
  }

  /* ---------------- boot ---------------- */
  window.addEventListener("DOMContentLoaded", () => {
    initMap();
    initSearch();
    initPresets();
    initViewer();
    initControls();
    initExports();
  });
})();