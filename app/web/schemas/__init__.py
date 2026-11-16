"""What the API takes and what it answers, written down.

Every JSON endpoint says here what it sends back, as a model with named,
typed fields, and what it expects to be sent. That is the contract the
panel's scripts, KiCad and anybody's own script are written against: it is
what `/docs` shows, and a field renamed or dropped by accident is a type
error here instead of a blank column in the panel.

A model is built from the app's own objects by its `of` method, so a route
says which answer it gives and not how the answer is put together.

    library   the index, parts, placing, the forms' choices, templates, history
    boms      BOMs, where a part is used, the product tree
    system    health, the session, KiCad's discovery document
"""
