"""KiCad Library Manager.

Serves an internal part library to KiCad's Remote Symbols panel.

The packages are layered, and each one only reaches downwards:

    web      HTTP: routes, pages, request-scoped dependencies
    auth     identity: the OIDC provider and the sessions it leads to
    library  the domain: sources, assets, parts, and what gets placed
    kicad    the file formats: reading, rewriting and drawing them
"""

__version__ = "0.2.0"
