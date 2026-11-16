"""What can be done with the library, one function for each thing.

Requesting a part, approving it, changing a template, uploading a BOM. Each
function is the whole of one such action: who may do it, whether what was
asked makes sense, the change itself, and what came of it. It is given who is
asking and what they asked for, and either returns the outcome or raises one
of the errors in `app.errors`.

Nothing here knows about HTTP. A route turns a request into a call and the
answer into a response, and a script, a test or another way in can make the
same call and be held to the same rules.

    changes    what every change to the library's repository has in common
    parts      requesting a part, correcting it, approving and retiring it
    templates  changing what a category asks of its parts
    boms       keeping BOMs, and setting them against the products
"""
