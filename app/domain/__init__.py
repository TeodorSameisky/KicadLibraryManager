"""What the library is about, with nothing in it about where it is kept.

Parts, the manufacturer parts behind them, the templates their categories
set, what is asked of each, and who may change them. Nothing here reads a
file, runs git, opens a database or answers a request: everything is given
what it needs and says what it found. That is what lets the rest of the app
be rearranged around it, and what lets these rules be tested without a
repository on disk.

    refs       how a part names a symbol or a footprint
    parts      internal parts, manufacturer parts, and the words for their states
    templates  what a category expects every part in it to carry
    rules      what is asked of a part, said once for the library and the forms
    policy     who may change what
"""
