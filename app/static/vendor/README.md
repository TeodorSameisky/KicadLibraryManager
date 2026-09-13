# Vendored libraries

Committed rather than fetched at build time, so the image builds without
network access and the exact bytes that were tested are the ones that ship.

| File | Version | Licence | Source |
| --- | --- | --- | --- |
| `occt-import-js.js` | 0.0.23 | LGPL-2.1 | https://github.com/kovacsv/occt-import-js |
| `occt-import-js.wasm` | 0.0.23 | LGPL-2.1 | built from the above |
| `three.module.min.js` | 0.171.0 | MIT | https://github.com/mrdoob/three.js |
| `three.core.min.js` | 0.171.0 | MIT | imported by the above; three.js splits its build |

`occt-import-js` is OpenCascade compiled to WebAssembly. It is here because
STEP is a boundary-representation format: turning one into triangles a browser
can draw needs a CAD kernel, and there is no lighter way to do it. KiCad 10
ships STEP only -- the VRML meshes earlier versions carried are gone.

It is loaded as a separate, unmodified file and is replaceable by dropping in
another build, which is what LGPL-2.1 asks of a project that uses it this way.
Both files come from the published release and are not patched.
