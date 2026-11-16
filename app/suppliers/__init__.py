"""The suppliers the app can ask about a manufacturer part.

    base     what a supplier has to be able to do: the one interface
    store    what was asked and answered, kept, and how often each was asked
    reading  what reading any supplier's answer comes to: which product is
             the one meant, and counts, prices and links out of its fields
    digikey  DigiKey, through its Product Information API
    mouser   Mouser, through its Search API
    farnell  Farnell, Newark and element14, through the Product Search API

A supplier here does two things and nothing else. It *fetches*: sends one
question over the network and returns what came back, as it came. And it
*reads*: takes such an answer and says what it means, as an `Offer`. The two
are apart on purpose. What is kept is the answer as the supplier gave it, so
that a better reader can be put to answers already paid for; and deciding
whether to ask at all -- whether the answer is already here, whether today's
calls are used up, whether it has been called too often this minute -- is not
a supplier's business but `app.services.supply`'s,
which does it the same way for all of them.

To add a supplier: a module here with a class that satisfies `Supplier`, and a
line in `app.application` that builds it from the settings.
"""
