"""Making a change to the library's repository.

The source is brought up to date, the files are changed, and the result is
committed in someone's name and pushed. If somebody else pushed in between,
it is done again on top of theirs -- which is also what keeps two parts
requested at the same moment from being given the same number.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.library.indexer import Fetch
from app.library.publish import Identity, Publisher, PublishError, PushRejected
from app.library.sources import Source, SourceError

# A write to a checkout: given its root, change the files and return the
# commit message. May raise to refuse.
Write = Callable[[Path], str]

_PUSH_ATTEMPTS = 3


class Writer:
    def __init__(self, publisher: Publisher, fetch: Fetch) -> None:
        self._publisher = publisher
        self._fetch = fetch

    def change(self, source: Source, write: Write, author: Identity) -> None:
        """Change `source` and publish the change.

        Blocking: runs git. The caller sees to it that nothing else touches
        the checkout meanwhile. Raises `PublishError` if the repository cannot
        be reached or will not take the change; whatever `write` raises to
        refuse is passed on, with nothing left behind in the checkout.
        """
        for _ in range(_PUSH_ATTEMPTS):
            try:
                checkout = self._fetch(source)
            except SourceError as exc:
                raise PublishError(f"the library's repository cannot be reached: {exc}") from exc
            try:
                message = write(checkout.path)
                self._publisher.publish(checkout.path, source, message, author)
                return
            except PushRejected:
                continue  # somebody pushed first; start again from what they pushed
            except BaseException:
                # Whatever stopped it, leave no half-written files behind for
                # the index being served to trip over.
                self._publisher.discard(checkout.path)
                raise
        raise PublishError("the library kept changing while this was being saved; try again")
