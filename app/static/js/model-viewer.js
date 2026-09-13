/* Renders a part's STEP model.
 *
 * STEP is a boundary representation, not a mesh, so it has to be tessellated
 * by a CAD kernel before anything can draw it. occt-import-js is OpenCascade
 * compiled to WebAssembly and does that in the browser, which keeps a 400MB
 * kernel out of the server image and its CPU out of every page view.
 *
 * That kernel is 7.6MB, so nothing here loads until the viewer is asked for.
 */
(function () {
  "use strict";

  var mount = document.getElementById("model-viewer");
  if (!mount) return;

  var BASE = document.body.dataset.basePath || "";
  var IPN = mount.dataset.ipn;
  var loaded = false;

  var button = document.getElementById("model-load");
  var statusEl = document.getElementById("model-status");

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = function () { reject(new Error("could not load " + src)); };
      document.head.appendChild(tag);
    });
  }

  /* A small orbit control: drag to rotate, wheel to zoom. Written here rather
     than pulled in, because the alternative is another dependency for thirty
     lines of arithmetic. */
  function orbit(camera, canvas, target, radius, onChange) {
    var theta = Math.PI * 0.25;
    var phi = Math.PI * 0.3;
    var distance = radius * 3.2;
    var dragging = false;
    var last = { x: 0, y: 0 };

    function apply() {
      phi = Math.max(0.05, Math.min(Math.PI - 0.05, phi));
      camera.position.set(
        target.x + distance * Math.sin(phi) * Math.sin(theta),
        target.y + distance * Math.cos(phi),
        target.z + distance * Math.sin(phi) * Math.cos(theta)
      );
      camera.lookAt(target);
      if (onChange) onChange();
    }

    canvas.addEventListener("pointerdown", function (e) {
      dragging = true;
      last = { x: e.clientX, y: e.clientY };
      canvas.setPointerCapture(e.pointerId);
    });

    canvas.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      theta -= (e.clientX - last.x) * 0.01;
      phi -= (e.clientY - last.y) * 0.01;
      last = { x: e.clientX, y: e.clientY };
      apply();
    });

    ["pointerup", "pointercancel"].forEach(function (name) {
      canvas.addEventListener(name, function () { dragging = false; });
    });

    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      distance = Math.max(radius * 1.2, Math.min(radius * 12, distance * (1 + e.deltaY * 0.001)));
      apply();
    }, { passive: false });

    apply();
    return apply;
  }

  function material(THREE, rgb) {
    var colour = new THREE.Color();
    // OCCT reports linear values; saying so keeps a black body from washing out.
    if (rgb) {
      colour.setRGB(rgb[0], rgb[1], rgb[2], THREE.LinearSRGBColorSpace);
    } else {
      colour.setRGB(0.68, 0.70, 0.74, THREE.LinearSRGBColorSpace);
    }
    return new THREE.MeshStandardMaterial({
      color: colour,
      metalness: 0.2,
      roughness: 0.55,
      side: THREE.DoubleSide,
    });
  }

  function meshFrom(THREE, mesh) {
    var geometry = new THREE.BufferGeometry();
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(mesh.attributes.position.array, 3)
    );
    if (mesh.attributes.normal) {
      geometry.setAttribute(
        "normal",
        new THREE.Float32BufferAttribute(mesh.attributes.normal.array, 3)
      );
    }
    if (mesh.index) geometry.setIndex(Array.from(mesh.index.array));
    if (!mesh.attributes.normal) geometry.computeVertexNormals();

    /* KiCad's models carry no single colour; each face has its own, which is
       what separates a resistor's black body from its terminations. The faces
       become geometry groups, one material per distinct colour. */
    var faces = mesh.brep_faces || [];
    if (!faces.length) {
      return new THREE.Mesh(geometry, material(THREE, mesh.color));
    }

    var materials = [];
    var seen = {};
    faces.forEach(function (face) {
      var key = face.color ? face.color.join(",") : "default";
      if (seen[key] === undefined) {
        seen[key] = materials.length;
        materials.push(material(THREE, face.color));
      }
      // first/last count triangles, so three indices each.
      geometry.addGroup(face.first * 3, (face.last - face.first + 1) * 3, seen[key]);
    });

    return new THREE.Mesh(geometry, materials);
  }

  function build(THREE, meshes) {
    var scene = new THREE.Scene();
    var group = new THREE.Group();

    meshes.forEach(function (mesh) { group.add(meshFrom(THREE, mesh)); });

    // Centre on the model rather than assuming it sits at the origin.
    var box = new THREE.Box3().setFromObject(group);
    var centre = box.getCenter(new THREE.Vector3());
    var radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.001);
    scene.add(group);

    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    scene.add(new THREE.HemisphereLight(0xffffff, dark ? 0x202430 : 0xb0b4bc, 1.6));
    var key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(1, 2, 1.5);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0xffffff, 0.7);
    fill.position.set(-1.5, 0.5, -1);
    scene.add(fill);

    return { scene: scene, centre: centre, radius: radius };
  }

  function render() {
    var width = mount.clientWidth || 320;
    var height = mount.clientHeight || 240;

    setStatus("Reading the model");

    return Promise.all([
      loadScript(BASE + "/static/vendor/occt-import-js.js"),
      import(BASE + "/static/vendor/three.module.min.js"),
      fetch(BASE + "/api/v1/parts/" + encodeURIComponent(IPN) + "/model.step")
        .then(function (r) {
          if (!r.ok) throw new Error("no model available");
          return r.arrayBuffer();
        }),
    ]).then(function (results) {
      var THREE = results[1];
      var buffer = results[2];

      setStatus("Tessellating");
      return window.occtimportjs({
        locateFile: function (name) { return BASE + "/static/vendor/" + name; },
      }).then(function (occt) {
        var result = occt.ReadStepFile(new Uint8Array(buffer), null);
        if (!result || !result.success || !result.meshes.length) {
          throw new Error("the model could not be read");
        }

        var built = build(THREE, result.meshes);
        var camera = new THREE.PerspectiveCamera(40, width / height, 0.01, 10000);

        var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(width, height);
        mount.replaceChildren(renderer.domElement);

        /* The model does not move on its own, so it is drawn when something
           changes rather than on every frame. An idle animation loop costs a
           laptop battery the whole time a part page is left open. Draws are
           coalesced into the next frame so a drag that fires several pointer
           events still renders once. */
        var pending = false;
        function draw() {
          if (pending) return;
          pending = true;
          requestAnimationFrame(function () {
            pending = false;
            renderer.render(built.scene, camera);
          });
        }

        var apply = orbit(camera, renderer.domElement, built.centre, built.radius, draw);

        var resize = function () {
          var w = mount.clientWidth || width;
          var h = mount.clientHeight || height;
          camera.aspect = w / h;
          camera.updateProjectionMatrix();
          renderer.setSize(w, h);
          apply();
        };
        window.addEventListener("resize", resize);

        setStatus("Drag to rotate, scroll to zoom");
      });
    });
  }

  button.addEventListener("click", function () {
    if (loaded) return;
    loaded = true;
    button.disabled = true;
    button.hidden = true;

    render().catch(function (err) {
      loaded = false;
      button.disabled = false;
      button.hidden = false;
      setStatus("Could not show the model: " + err.message);
    });
  });
})();
