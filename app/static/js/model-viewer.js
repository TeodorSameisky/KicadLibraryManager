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
      // Little metal: with nothing around it to reflect, a metal is just dark.
      metalness: 0.05,
      roughness: 0.6,
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

    /* Shaded from the triangles themselves, not from the normals the kernel
       supplies. Those describe the CAD surface, and on a face the kernel
       built the other way round they point inwards while the triangles still
       face out. The material is two-sided, so three.js then takes the side
       you are looking at for a back face and turns the shading away from the
       light: from some angles a face goes dark whatever lights there are.
       Normals worked out from the triangles always agree with how they are
       wound, so the side facing the viewer is always the one that is lit.

       Smoothing stays within a face. The kernel gives each face vertices of
       its own, so an edge between two faces is still sharp. */
    geometry.computeVertexNormals();

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

    /* Lit from where it is looked at. Lights fixed in the scene light one
       side of the model, and turning it round -- or tipping it over -- shows
       the other, which none of them reach: a flat silhouette with its detail
       gone. So the two main lights ride on the camera and swing round with it,
       and what faces the viewer is always the lit side.

       A directional light shines from its position towards its target. Both
       are given in the camera's own terms and travel with it, so the angle
       between the light and the line of sight is the same at any distance and
       for any size of part. The camera's x is to the right, y up, and it looks
       down its negative z. */
    // Intensities chosen by measuring: across a sweep of viewpoints, this keeps
    // the darkest view well clear of black without washing the lightest
    // surfaces out to white, where their shading is lost.
    const key = new THREE.DirectionalLight(0xffffff, 2.0);
    key.position.set(-1, 1.5, 2); // above, to the left, and behind the viewer
    key.target.position.set(0, 0, -1);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
    fill.position.set(2, -1, 1.5); // from the other side and below, to lift the shadows
    fill.target.position.set(0, 0, -1);

    // The same in every theme, like the grey the model is drawn on.
    scene.add(new THREE.HemisphereLight(0xffffff, 0x6f737b, 0.9));
    scene.add(new THREE.AmbientLight(0xffffff, 0.4)); // so no face is ever black

    return { scene, centre, radius, headlights: [key, fill] };
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

    const { scene, centre, radius, headlights } = buildScene(THREE, result.meshes);
    const size = () => [mount.clientWidth || 320, mount.clientHeight || 240];
    const [width, height] = size();

    const camera = new THREE.PerspectiveCamera(40, width / height, radius / 100, radius * 100);
    // In the scene, so that what is attached to it is carried with it.
    scene.add(camera);
    for (const light of headlights) camera.add(light, light.target);
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
