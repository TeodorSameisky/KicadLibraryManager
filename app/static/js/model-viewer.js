/* Shows a part's STEP model.
 *
 * STEP describes surfaces, not triangles, so a CAD kernel has to tessellate it
 * before anything can draw it. occt-import-js is OpenCascade compiled to
 * WebAssembly and does that here in the browser, which keeps a 400 MB kernel
 * out of the server image and its CPU out of every page view.
 *
 * The kernel is 7.6 MB, so nothing below is fetched until the viewer is asked
 * for.
 */
(() => {
  "use strict";

  const mount = document.getElementById("model-viewer");
  const loadButton = document.getElementById("model-load");
  const statusLine = document.getElementById("model-status");
  if (!mount || !loadButton) return;

  const base = document.body.dataset.basePath || "";
  const vendor = `${base}/static/vendor`;
  const modelUrl = `${base}/api/v1/parts/${encodeURIComponent(mount.dataset.ipn)}/model.step`;

  const setStatus = (text) => { statusLine.textContent = text; };

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error("the viewer could not be loaded"));
      document.head.append(tag);
    });
  }

  async function fetchModel() {
    const response = await fetch(modelUrl, { credentials: "same-origin" });
    if (!response.ok) throw new Error("the model is not available");
    return new Uint8Array(await response.arrayBuffer());
  }

  // ------------------------------------------------------------- geometry

  function material(THREE, rgb) {
    const colour = new THREE.Color();
    // The kernel reports linear values. Saying so keeps a black body black
    // instead of washing it out to grey.
    const [r, g, b] = rgb || [0.68, 0.7, 0.74];
    colour.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
    return new THREE.MeshStandardMaterial({
      color: colour,
      metalness: 0.2,
      roughness: 0.55,
      side: THREE.DoubleSide,
    });
  }

  function toMesh(THREE, source) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(source.attributes.position.array, 3)
    );
    if (source.index) geometry.setIndex(Array.from(source.index.array));
    if (source.attributes.normal) {
      geometry.setAttribute(
        "normal",
        new THREE.Float32BufferAttribute(source.attributes.normal.array, 3)
      );
    } else {
      geometry.computeVertexNormals();
    }

    /* A KiCad model has no single colour. Each face carries its own, which is
       what tells a resistor's black body from its terminations. So the faces
       become groups of the geometry, with one material per distinct colour. */
    const faces = source.brep_faces || [];
    if (!faces.length) return new THREE.Mesh(geometry, material(THREE, source.color));

    const materials = [];
    const indexOf = new Map();
    for (const face of faces) {
      const key = face.color ? face.color.join(",") : "";
      if (!indexOf.has(key)) {
        indexOf.set(key, materials.length);
        materials.push(material(THREE, face.color));
      }
      // first and last count triangles; a group counts indices, three each.
      geometry.addGroup(face.first * 3, (face.last - face.first + 1) * 3, indexOf.get(key));
    }
    return new THREE.Mesh(geometry, materials);
  }

  function buildScene(THREE, meshes) {
    const model = new THREE.Group();
    for (const mesh of meshes) model.add(toMesh(THREE, mesh));

    // Framed on the model itself, wherever it sits relative to the origin.
    const box = new THREE.Box3().setFromObject(model);
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.001);

    const scene = new THREE.Scene();
    scene.add(model);

    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    scene.add(new THREE.HemisphereLight(0xffffff, dark ? 0x202430 : 0xb0b4bc, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1, 2, 1.5);
    const fill = new THREE.DirectionalLight(0xffffff, 0.7);
    fill.position.set(-1.5, 0.5, -1);
    scene.add(key, fill);

    return { scene, centre, radius };
  }

  // -------------------------------------------------------------- control

  /* Drag to turn, wheel to zoom. Written here because the alternative is a
     further dependency for thirty lines of arithmetic. */
  function orbit(camera, canvas, centre, radius, onChange) {
    let theta = Math.PI * 0.25;
    let phi = Math.PI * 0.3;
    let distance = radius * 3.2;
    let dragFrom = null;

    function apply() {
      phi = Math.max(0.05, Math.min(Math.PI - 0.05, phi));
      camera.position.set(
        centre.x + distance * Math.sin(phi) * Math.sin(theta),
        centre.y + distance * Math.cos(phi),
        centre.z + distance * Math.sin(phi) * Math.cos(theta)
      );
      camera.lookAt(centre);
      onChange();
    }

    canvas.addEventListener("pointerdown", (event) => {
      dragFrom = { x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!dragFrom) return;
      theta -= (event.clientX - dragFrom.x) * 0.01;
      phi -= (event.clientY - dragFrom.y) * 0.01;
      dragFrom = { x: event.clientX, y: event.clientY };
      apply();
    });
    for (const name of ["pointerup", "pointercancel"]) {
      canvas.addEventListener(name, () => { dragFrom = null; });
    }
    canvas.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault(); // zoom the model, not the page
        const scaled = distance * (1 + event.deltaY * 0.001);
        distance = Math.max(radius * 1.2, Math.min(radius * 12, scaled));
        apply();
      },
      { passive: false }
    );

    return apply;
  }

  // ----------------------------------------------------------------- show

  async function show() {
    setStatus("Fetching the model");
    // three.js is split in two files; the module below imports the other.
    const [THREE, bytes] = await Promise.all([
      import(`${vendor}/three.module.min.js`),
      fetchModel(),
      loadScript(`${vendor}/occt-import-js.js`),
    ]);

    setStatus("Tessellating");
    const occt = await window.occtimportjs({ locateFile: (name) => `${vendor}/${name}` });
    const result = occt.ReadStepFile(bytes, null);
    if (!result || !result.success || !result.meshes.length) {
      throw new Error("the model could not be read");
    }

    const { scene, centre, radius } = buildScene(THREE, result.meshes);
    const size = () => [mount.clientWidth || 320, mount.clientHeight || 240];
    const [width, height] = size();

    const camera = new THREE.PerspectiveCamera(40, width / height, radius / 100, radius * 100);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    mount.replaceChildren(renderer.domElement);

    /* The model does not move by itself, so it is drawn when something
       changes and not on every frame: an idle animation loop spends a
       laptop's battery for as long as the page stays open. Draws are folded
       into the next frame, so a drag that fires several pointer events in
       one still renders once. */
    let queued = false;
    function draw() {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        renderer.render(scene, camera);
      });
    }

    const update = orbit(camera, renderer.domElement, centre, radius, draw);
    update();

    window.addEventListener("resize", () => {
      const [w, h] = size();
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      draw();
    });

    setStatus("Drag to turn, scroll to zoom");
  }

  loadButton.addEventListener("click", async () => {
    loadButton.hidden = true;
    try {
      await show();
    } catch (error) {
      loadButton.hidden = false;
      setStatus(`The model cannot be shown: ${error.message}`);
    }
  });
})();
