"""Bills of materials: what each project is built from.

A BOM is exported from KiCad, uploaded here and kept. Kept, it answers two
questions the library alone cannot: what a project needs, with every line
looked up in the library as it stands today; and where a part is used, which
is what decides whether it can be changed or retired.

The library lives in git because it is authored and reviewed. BOMs are
records of what was built, arrive by upload, and are queried by part -- so
they live in a database, next to the library's clones on the data volume.

    parse    a KiCad BOM export, read into lines
    store    BOMs kept in the database, and looked up by part
    report   a stored BOM set against the library
"""
