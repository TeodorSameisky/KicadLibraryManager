"""End-to-end: clone a real git repository, index it, and serve it.

These use an actual git repository on disk rather than stubbing the sync, so
the clone/fetch path in sources.py is covered by the same tests.
"""

import shutil
import subprocess
import textwrap

import pytest

from app.kicad.sources import Source, SourceError, load_sources, sync
from app.library_service import LibraryService, State

from tests.test_library_index import footprint, symbol

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def origin(tmp_path):
    """A git repository laid out like a company library."""
    repo = tmp_path / "origin"
    (repo / "symbols" / "Passives.kicad_symdir").mkdir(parents=True)
    (repo / "footprints" / "Passives.pretty").mkdir(parents=True)
    (repo / "parts" / "1102").mkdir(parents=True)
    (repo / "mpns").mkdir(parents=True)

    def write(rel, text):
        (repo / rel).write_text(textwrap.dedent(text), encoding="utf-8")

    write("symbols/Passives.kicad_symdir/R.kicad_sym", symbol("R", units=2))
    write("symbols/Passives.kicad_symdir/R_0603.kicad_sym",
          symbol("R_0603", extends="R", footprint="Passives:R_0603_1608Metric"))
    write("footprints/Passives.pretty/R_0603_1608Metric.kicad_mod",
          footprint("R_0603_1608Metric"))
    write("categories.yaml", '- code: "1102"\n  name: Resistors\n')
    write("mpns/RC0603FR-0710KL.yaml",
          "mpn: RC0603FR-0710KL\nmanufacturer: Yageo\nlifecycle: active\n")
    write("parts/1102/1102-0001.yaml", """\
        ipn: 1102-0001
        description: Resistor 10k 1% 0603
        status: approved
        symbol: Passives:R_0603
        footprint: Passives:R_0603_1608Metric
        fields:
          Value: 10k
        mpns:
          - mpn: RC0603FR-0710KL
            preferred: true
        """)

    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    return repo


@pytest.fixture()
def service(origin, tmp_path):
    source = Source(id="company", name="Company", url=str(origin), ref="main")
    return LibraryService(sources=[source], workdir=tmp_path / "work")


async def test_indexes_a_cloned_repository(service):
    assert service.state is State.SYNCING

    snap = await service.refresh()

    assert service.state is State.READY
    assert snap.part_count() == 1
    assert snap.catalog.symbol_count() == 2
    assert snap.errors == [], [str(e) for e in snap.errors]
    assert snap.sources[0].commit


async def test_search_matches_description_and_mpn(service):
    """IPNs carry no meaning, so these are what people actually search by."""
    await service.refresh()

    assert [p.ipn for p in service.search("10k")] == ["1102-0001"]
    assert [p.ipn for p in service.search("yageo")] == ["1102-0001"]
    assert [p.ipn for p in service.search("RC0603")] == ["1102-0001"]
    assert [p.ipn for p in service.search("1102")] == ["1102-0001"]
    assert service.search("nonexistent") == []
    assert len(service.search("")) == 1


async def test_refresh_picks_up_new_commits(service, origin):
    await service.refresh()
    first = service.snapshot.sources[0].commit

    (origin / "parts" / "1102" / "1102-0002.yaml").write_text(
        "ipn: 1102-0002\ndescription: Resistor 4k7\nsymbol: Passives:R_0603\n",
        encoding="utf-8")
    _git("add", "-A", cwd=origin)
    _git("commit", "-m", "add a part", cwd=origin)

    snap = await service.refresh()

    assert snap.sources[0].commit != first
    assert snap.part_count() == 2


async def test_a_failing_source_keeps_the_previous_index(service):
    """Losing the git host must not empty the catalog people are using."""
    await service.refresh()
    assert service.snapshot.part_count() == 1

    service._sources = [Source(id="company", name="Company",
                               url="/nonexistent/repo.git", ref="main")]
    await service.refresh()

    assert service.state is State.READY
    assert service.snapshot.part_count() == 1, "still serving the last good index"
    assert service.error


async def test_no_sources_reports_empty(tmp_path):
    svc = LibraryService(sources=[], workdir=tmp_path)

    await svc.refresh()

    assert svc.state is State.EMPTY
    assert svc.snapshot.part_count() == 0


def test_sync_recovers_from_an_interrupted_clone(origin, tmp_path):
    """A half-written directory must not wedge the platform permanently."""
    source = Source(id="company", name="Company", url=str(origin), ref="main")
    workdir = tmp_path / "work"

    broken = workdir / "company"
    broken.mkdir(parents=True)
    (broken / "junk.txt").write_text("left over from a failed clone")

    result = sync(source, workdir)

    assert (result.path / "categories.yaml").is_file()
    assert not (result.path / "junk.txt").exists()


def test_source_config_parsing():
    sources = load_sources('[{"id":"a","url":"https://example.com/a.git"}]')

    assert sources[0].ref == "main", "defaults to main"
    assert sources[0].name == "a", "falls back to the id"


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("{}", "must be a list"),
        ("not json", "invalid json"),
        ('[{"url":"x"}]', "missing id"),
        ('[{"id":"a"}]', "missing url"),
        ('[{"id":"a/b","url":"x"}]', "id is used in paths"),
        ('[{"id":"a","url":"x"},{"id":"a","url":"y"}]', "duplicate id"),
    ],
)
def test_bad_source_config_is_rejected(raw, reason):
    with pytest.raises(SourceError):
        load_sources(raw)


def test_changing_the_url_repoints_an_existing_clone(origin, tmp_path):
    """Otherwise a fetch silently keeps pulling from the old repository."""
    workdir = tmp_path / "work"
    first = Source(id="company", name="Company", url=str(origin), ref="main")
    sync(first, workdir)

    moved = tmp_path / "moved"
    shutil.move(str(origin), str(moved))
    relocated = Source(id="company", name="Company", url=str(moved), ref="main")

    result = sync(relocated, workdir)
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=str(result.path), capture_output=True, text=True, check=True,
    ).stdout.strip()

    assert remote == str(moved)
