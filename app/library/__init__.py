"""The part library: where it comes from, what is in it, what gets placed.

sources    git repositories, cloned and kept current
files      where each kind of library file is, and how it is written down
assets     one repository's symbols, footprints and 3D models
parts      the parts in a repository's files, read and held to what they must say
products   the product families, read the same way
catalog    every source's assets together, since references cross them
snapshot   one finished index of all of the above, and the questions
           asked of it
placement  what KiCad is sent when a part is placed
previews   the same part, drawn

editing    what an accepted part form does to the files
template_editing   and what an accepted template form does
publish    committing a change in someone's name, and pushing it
history    what was changed in a file, put into words

service    keeps a current snapshot available while the app runs, with
indexer    reading the sources into a snapshot, and
writer     changing the repository, done for it by these two
"""
