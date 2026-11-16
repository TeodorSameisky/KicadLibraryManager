"""The pieces the library service is made of, each on its own.

The files module, which knows where each kind of file is and how it is written
down; the indexer, which reads the sources into a snapshot; the writer, which
changes the repository; and the histories it remembers. None of these needs a
running app, and only the files need a disk.
"""

import pytest

from app.domain.templates import TemplateField
from app.library import files
from app.library import history as history_module
from app.library.history import Histories
from app.library.indexer import Indexer
from app.library.parts import load_parts
from app.library.products import load_products
from app.library.publish import Identity, PublishError, PushRejected
from app.library.snapshot import Snapshot
from app.library.sources import Checkout, Source, SourceError
from app.library.writer import Writer

ADA = Identity("Ada Lovelace", "ada@example.com")
PRIVATE = Source("company", "Company", "https://github.com/acme/library.git", private=True)
PUBLIC = Source("kicad", "KiCad", "https://gitlab.com/kicad/libraries.git")


# -- the files: what is written is what is read back ---------------------------


def test_a_part_and_its_manufacturer_part_are_read_back_as_written(tree):
    root = tree.standard().root
    data = {
        "ipn": "1102-0002",
        "description": "Resistor 0R 0603",
        "status": "draft",
        "symbol": "Passives:R_0603",
        "footprint": "Passives:R_0603_1608Metric",
        "fields": {"Value": "0805", "Tolerance": "1%"},
        "mpns": [{"mpn": "LM358/NOPB", "preferred": True}],
    }

    files.write(files.part_file(root, "1102", "1102-0002"), files.part_text(data))
    files.write(
        files.mpn_file(root, "LM358/NOPB"),
        files.mpn_text(mpn="LM358/NOPB", manufacturer="TI", lifecycle="active"),
    )

    index = load_parts(root)
    part = index.parts["1102-0002"]
    assert part.path == "parts/1102/1102-0002.yaml", "where the loader looks for it"
    assert part.fields == {"Value": "0805", "Tolerance": "1%"}, "0805 is text, not 805"
    assert part.preferred_mpn == "LM358/NOPB"
    assert index.mpns["LM358/NOPB"].manufacturer == "TI"
    assert not [issue for issue in index.issues if "1102-0002" in issue.message]
    assert not [issue for issue in index.issues if issue.path == "mpns/LM358_NOPB.yaml"]


def test_a_template_is_read_back_as_written(tree):
    root = tree.standard().root
    fields = (
        TemplateField("Resistance", required=True, example="10k"),
        TemplateField("Package", options=("0402", "0603", "1e3"), default="0603"),
    )

    files.write(
        files.template_file(root, "1102"), files.template_text("1102", "{Resistance}", fields)
    )

    template = load_parts(root).templates["1102"]
    assert template.fields == fields, "the options are still text, whichever parser reads them"
    assert template.value == "{Resistance}"
    text = files.template_file(root, "1102").read_text(encoding="utf-8")
    assert "options: ['0402', '0603', '1e3']" in text, "on one line, as a person would write them"


def test_what_a_hand_written_part_holds_besides_is_carried_along():
    text = files.part_text(
        {"ipn": "1102-0001", "status": "approved", "notes": "checked", "symbol": "Passives:R"}
    )

    assert files.parse(text) == {
        "ipn": "1102-0001",
        "status": "approved",
        "symbol": "Passives:R",
        "notes": "checked",
    }
    assert text.index("ipn:") < text.index("symbol:") < text.index("notes:"), "in the usual order"


# -- the files: where each kind is ----------------------------------------------


def test_a_part_is_found_wherever_under_parts_it_was_filed(tree):
    root = tree.standard().root
    tree.write("parts/misc/odd/1102-0009.yaml", "ipn: 1102-0009\n")

    assert files.find_part(root, "1102-0001") == root / "parts" / "1102" / "1102-0001.yaml"
    assert files.find_part(root, "1102-0009") == root / "parts" / "misc" / "odd" / "1102-0009.yaml"
    assert files.find_part(root, "9999-0001") is None
    assert len(files.part_files(root)) == 2


def test_a_manufacturer_part_s_file_cannot_leave_its_directory(tmp_path):
    path = files.mpn_file(tmp_path, "../../etc/passwd")

    assert path.parent == tmp_path / "mpns"
    assert path.name == ".._.._etc_passwd.yaml"


def test_a_repository_says_what_it_carries(tree, tmp_path):
    assert files.has_parts(tree.standard().root) is True
    assert files.has_parts(tmp_path) is False
    assert files.has_products(tree.root) is False

    tree.write("products/Zeta.yaml", "name: Zeta\n")
    tree.write("products/alpha.yaml", "name: Alpha\n")

    assert files.has_products(tree.root) is True
    assert [path.name for path in files.product_files(tree.root)] == ["alpha.yaml", "Zeta.yaml"]
    assert [family.name for family in load_products(tree.root)[0].families] == ["Alpha", "Zeta"]


def test_a_file_that_cannot_be_read_says_which_way(tmp_path):
    (tmp_path / "broken.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    (tmp_path / "binary.yaml").write_bytes(b"\xff\xfe\x00not text")

    with pytest.raises(files.Unreadable) as not_yaml:
        files.read(tmp_path / "broken.yaml")
    with pytest.raises(files.Unreadable) as not_text:
        files.read(tmp_path / "binary.yaml")
    with pytest.raises(files.Unreadable) as not_there:
        files.read(tmp_path / "missing.yaml")

    assert not_yaml.value.kind == "invalid-yaml"
    assert "\n" not in str(not_yaml.value), "what and where, without quoting the file"
    assert (not_text.value.kind, not_there.value.kind) == ("unreadable", "unreadable")
    assert files.parse("") is None, "an empty file says nothing, which is not an error here"


# -- the indexer ----------------------------------------------------------------


def test_sources_that_have_not_moved_are_not_read_again(tree):
    tree.standard()
    indexer = Indexer([tree.source], lambda source: tree.checkout)

    first = indexer.index(Snapshot())
    again = indexer.index(first.snapshot)

    assert first.snapshot.part_count == 1
    assert again.snapshot is first.snapshot, "the very same snapshot, not an equal one"
    assert again.checkouts == {"company": tree.root}


def test_a_source_that_cannot_be_reached_is_in_the_snapshot_as_one_that_failed(tree):
    tree.standard()

    def fetch(source):
        if source is PUBLIC:
            raise SourceError("the host did not answer")
        return tree.checkout

    indexed = Indexer([tree.source, PUBLIC], fetch).index(Snapshot())

    assert [(status.id, status.error) for status in indexed.snapshot.sources] == [
        ("company", None),
        ("kicad", "the host did not answer"),
    ]
    assert indexed.checkouts == {"company": tree.root}, "only what there is on disk"
    assert indexed.snapshot.failed is False, "one source still answered"

    # A failure is always worth another look, even if nothing else has moved.
    assert Indexer([tree.source, PUBLIC], fetch).index(indexed.snapshot).snapshot is not (
        indexed.snapshot
    )


# -- the writer -----------------------------------------------------------------


class FakePublisher:
    """Takes what it is given, or refuses it the first few times."""

    def __init__(self, rejections=0):
        self.rejections = rejections
        self.published = []
        self.discarded = 0

    def publish(self, checkout, source, message, author):
        if self.rejections:
            self.rejections -= 1
            raise PushRejected("somebody pushed first")
        self.published.append((source.id, message, author))

    def discard(self, checkout):
        self.discarded += 1


def checkout_of(tmp_path):
    return lambda source: Checkout(source, tmp_path, "abc")


def test_a_change_is_written_and_published_in_the_author_s_name(tmp_path):
    publisher = FakePublisher()
    seen = []

    Writer(publisher, checkout_of(tmp_path)).change(
        PRIVATE, lambda root: seen.append(root) or "Request 1102-0002", ADA
    )

    assert seen == [tmp_path], "given the checkout to change"
    assert publisher.published == [("company", "Request 1102-0002", ADA)]


def test_a_change_is_made_again_on_top_of_whoever_pushed_first(tmp_path):
    """Which is also what keeps two parts requested together from being given
    the same number: the number is taken again, from what they pushed."""
    publisher = FakePublisher(rejections=2)
    fetched, written = [], []

    def fetch(source):
        fetched.append(source.id)
        return Checkout(source, tmp_path, "abc")

    Writer(publisher, fetch).change(PRIVATE, lambda root: written.append(1) or "message", ADA)

    assert (len(fetched), len(written)) == (3, 3), "fetched and written afresh each time"
    assert len(publisher.published) == 1


def test_a_repository_that_keeps_changing_is_given_up_on(tmp_path):
    publisher = FakePublisher(rejections=99)

    with pytest.raises(PublishError, match="kept changing"):
        Writer(publisher, checkout_of(tmp_path)).change(PRIVATE, lambda root: "message", ADA)

    assert publisher.published == []


def test_a_change_that_is_refused_leaves_nothing_behind(tmp_path):
    publisher = FakePublisher()

    def refuse(root):
        (root / "half-written.yaml").write_text("ipn:", encoding="utf-8")
        raise ValueError("that makes no sense")

    with pytest.raises(ValueError, match="makes no sense"):
        Writer(publisher, checkout_of(tmp_path)).change(PRIVATE, refuse, ADA)

    assert publisher.discarded == 1, "what was uncommitted is thrown away"
    assert publisher.published == []


def test_a_repository_that_cannot_be_reached_is_said_as_a_change_not_saved(tmp_path):
    def unreachable(source):
        raise SourceError("the host did not answer")

    with pytest.raises(PublishError, match="cannot be reached: the host did not answer"):
        Writer(FakePublisher(), unreachable).change(PRIVATE, lambda root: "message", ADA)


# -- histories ------------------------------------------------------------------


@pytest.fixture()
def reads(monkeypatch):
    """Stands in for git: notes what was asked for, and with which credentials."""
    asked = []

    def read(repository, path, limit, env):
        asked.append({"path": path, "limit": limit, "env": dict(env)})
        return f"history of {path} #{len(asked)}"

    monkeypatch.setattr(history_module, "read_history", read)
    return asked


def test_a_history_is_read_once_until_the_library_moves_on(reads, tmp_path):
    histories = Histories()

    first = histories.read(tmp_path, PUBLIC, "commit-1", "parts/a.yaml")
    again = histories.read(tmp_path, PUBLIC, "commit-1", "parts/a.yaml")
    other = histories.read(tmp_path, PUBLIC, "commit-1", "parts/b.yaml")
    moved = histories.read(tmp_path, PUBLIC, "commit-2", "parts/a.yaml")

    assert again is first
    assert len(reads) == 3, "asked again for another file, and after a new commit"
    assert (other, moved) == ("history of parts/b.yaml #2", "history of parts/a.yaml #3")


def test_the_token_goes_to_a_private_source_and_to_no_other(reads, tmp_path):
    histories = Histories("s3cret")

    histories.read(tmp_path, PRIVATE, "abc", "parts/a.yaml")
    histories.read(tmp_path, PUBLIC, "abc", "parts/b.yaml")
    histories.read(tmp_path, None, "abc", "parts/c.yaml")

    assert "GIT_CONFIG_KEY_0" in reads[0]["env"]
    assert reads[1]["env"] == {} and reads[2]["env"] == {}


def test_what_is_remembered_is_bounded(reads, tmp_path):
    histories = Histories(room=2)

    for name in ("a", "b", "c"):
        histories.read(tmp_path, PUBLIC, "abc", f"parts/{name}.yaml")
    histories.read(tmp_path, PUBLIC, "abc", "parts/a.yaml")

    assert len(reads) == 4, "forgotten to make room, and read again: only a re-read is lost"
