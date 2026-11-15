"""A person's own words on a change to the library.

The subject of a commit is written by the app -- "Request 1102-0002: ...". What
the person adds goes under it, so the history says why as well as what.
"""

import re

import pytest

from app.library.editing import MAX_NOTE, with_note
from app.library.history import read_history
from tests.conftest import sign_in_librarian

# The fixtures that build a repository and an app over it.
from tests.test_editing import REQUEST, git, repository, serve, web  # noqa: F401
from tests.test_template_editing import BODY

SUBJECT = "Request 1102-0002: Resistor 330R 1% 0.1W 0603"


def message(repository):  # noqa: F811
    """The whole message of the newest commit, subject and body."""
    return git("log", "-1", "--format=%B", "main", cwd=repository.path).strip()


# -- the message ----------------------------------------------------------------


def test_without_a_note_the_message_is_the_subject_alone():
    assert with_note(SUBJECT) == SUBJECT
    assert with_note(SUBJECT, "") == SUBJECT
    assert with_note(SUBJECT, "  \n \n ") == SUBJECT, "blank is no note"


def test_a_note_follows_the_subject_after_a_blank_line():
    assert (
        with_note(SUBJECT, "Needed for the new board") == f"{SUBJECT}\n\nNeeded for the new board"
    )


def test_the_note_is_kept_as_written_but_tidied():
    note = "  First line   \r\nSecond line\r\n\r\n\r\n\r\nThird  \n"

    assert with_note("S", note) == "S\n\nFirst line\nSecond line\n\nThird"


def test_what_cannot_be_printed_is_dropped_and_other_scripts_are_kept():
    assert with_note("S", "bad\x00 \x1b[31mtext\x07 — Ω 抵抗") == "S\n\nbad [31mtext — Ω 抵抗"


def test_a_note_that_looks_like_a_header_is_still_only_a_note():
    """The message body is free text; nothing in it can end up in the subject."""
    assert with_note("S", "Author: someone else\nSigned-off-by: x") == (
        "S\n\nAuthor: someone else\nSigned-off-by: x"
    )


# -- on parts -------------------------------------------------------------------


def test_a_request_can_carry_a_note(web, repository):  # noqa: F811
    web.post("/api/v1/parts", json={**REQUEST, "note": "Needed for the amplifier board"})

    assert message(repository) == f"{SUBJECT}\n\nNeeded for the amplifier board"
    assert repository.last("%s") == SUBJECT, "the subject is still the app's"
    assert repository.last("%an") == "Ada Lovelace"


def test_a_request_without_one_is_as_before(web, repository):  # noqa: F811
    web.post("/api/v1/parts", json=REQUEST)

    assert message(repository) == SUBJECT


def test_an_edit_carries_a_note(web, repository):  # noqa: F811
    web.post("/api/v1/parts", json=REQUEST)

    web.put(
        "/api/v1/parts/1102-0002",
        json={**REQUEST, "description": "Resistor 330R 5%", "note": "5% is enough here"},
    )

    assert message(repository) == "Edit 1102-0002: Resistor 330R 5%\n\n5% is enough here"


def test_approving_and_retiring_carry_a_note(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.post("/api/v1/parts", json=REQUEST)

    librarian.post(
        "/api/v1/parts/1102-0002/status",
        json={"status": "approved", "note": "Checked the datasheet"},
    )
    assert message(repository).endswith("\n\nChecked the datasheet")
    assert message(repository).startswith("Approve 1102-0002")

    librarian.post("/api/v1/parts/1102-0002/status", json={"status": "obsolete"})
    assert "\n" not in message(repository), "no note, no body"


def test_a_note_that_is_too_long_is_refused_and_nothing_is_committed(web, repository):  # noqa: F811
    before = repository.commits()

    response = web.post("/api/v1/parts", json={**REQUEST, "note": "x" * (MAX_NOTE + 1)})

    assert response.status_code == 422
    assert repository.commits() == before


def test_a_note_of_the_longest_allowed_size_is_kept_whole(web, repository):  # noqa: F811
    web.post("/api/v1/parts", json={**REQUEST, "note": "x" * MAX_NOTE})

    assert message(repository).endswith("x" * MAX_NOTE)


# -- on templates ---------------------------------------------------------------


def test_a_template_change_and_its_removal_carry_a_note(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())

    librarian.put("/api/v1/templates/1102", json={**BODY, "note": "Resistors need a tolerance"})
    assert message(repository) == (
        "Add the template for 1102: Resistors, fixed\n\nResistors need a tolerance"
    )

    librarian.request(
        "DELETE", "/api/v1/templates/1102", json={"note": "Replaced by per-value rules"}
    )
    assert message(repository) == (
        "Remove the template for 1102: Resistors, fixed\n\nReplaced by per-value rules"
    )


def test_removing_a_template_still_works_with_no_body_at_all(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.put("/api/v1/templates/1102", json=BODY)

    assert librarian.delete("/api/v1/templates/1102").status_code == 200
    assert message(repository) == "Remove the template for 1102: Resistors, fixed"


# -- in the history ---------------------------------------------------------------


def test_the_note_is_shown_with_the_change_in_the_part_s_history(web, repository):  # noqa: F811
    web.post("/api/v1/parts", json={**REQUEST, "note": "Needed for the amplifier\nboard"})
    web.put("/api/v1/parts/1102-0002", json={**REQUEST, "description": "Resistor 330R 5%"})

    changes = web.get("/api/v1/parts/1102-0002/history").json()["changes"]
    page = web.get("/ipn/1102-0002/history").text

    assert [c["note"] for c in changes] == ["", "Needed for the amplifier\nboard"]
    assert [c["subject"] for c in changes] == [
        "Edit 1102-0002: Resistor 330R 5%",
        SUBJECT,
    ], "the body does not leak into the subject, nor the what-changed lines"
    assert changes[1]["what"][0] == "Created as draft."
    assert '<p class="history__note">Needed for the amplifier\nboard</p>' in page
    assert page.count("history__note") == 1, "a change with no note has no note line"


def test_a_message_written_by_hand_in_git_has_its_body_read_as_a_note(repository):  # noqa: F811
    """Commits made outside the app are part of the same history."""
    repository.push_from_elsewhere(
        "parts/1102/1102-0001.yaml", "ipn: 1102-0001\ndescription: Changed\nstatus: approved\n"
    )
    git("commit", "--amend", "-q", "-m", "Tidy the description\n\nThe old one said 10k twice.\n\nSecond paragraph.",
        cwd=repository._scratch)  # fmt: skip
    git("push", "-q", "--force", cwd=repository._scratch)

    history = read_history(repository._scratch, "parts/1102/1102-0001.yaml")

    assert history.changes[0].subject == "Tidy the description"
    assert history.changes[0].note == "The old one said 10k twice.\n\nSecond paragraph."


# -- on the pages -------------------------------------------------------------------


def test_the_part_form_asks_for_a_note(web):  # noqa: F811
    request = web.get("/parts/new").text
    web.post("/api/v1/parts", json=REQUEST)
    edit = web.get("/ipn/1102-0002/edit").text

    assert 'id="part-note"' in request and "Anything the librarian should know?" in request
    assert 'id="part-note"' in edit and "What did you change, and why?" in edit
    assert 'maxlength="1000"' in request
    assert "(optional)" in request and "in the library's history" in request


def test_the_template_form_asks_for_a_note(serve):  # noqa: F811
    html = sign_in_librarian(serve()).get("/templates/1102/edit").text

    assert 'id="template-note"' in html and "What did you change, and why?" in html


@pytest.mark.parametrize(
    "script,needle",
    [("part-form.js", "note: dom.note.value"), ("template-form.js", "note: dom.note.value")],
)
def test_the_scripts_send_the_note(web, script, needle):  # noqa: F811
    assert needle in web.get(f"/static/js/{script}").text


def test_approving_asks_for_a_reason_first(web):  # noqa: F811
    script = web.get("/static/js/part-form.js").text

    assert "Say why, if you like." in script
    assert "note: note.value" in script


# -- returning a part to draft ------------------------------------------------------


def draft_button(html):
    return 'data-set-status="draft"' in html


def test_a_librarian_can_return_an_approved_or_retired_part_to_draft(serve, repository):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.post("/api/v1/parts", json=REQUEST)
    librarian.post("/api/v1/parts/1102-0002/status", json={"status": "approved"})

    back = librarian.post(
        "/api/v1/parts/1102-0002/status", json={"status": "draft", "note": "Wrong footprint"}
    )

    assert back.json() == {"ipn": "1102-0002", "status": "draft"}
    assert message(repository) == (
        "Return to draft 1102-0002: Resistor 330R 1% 0.1W 0603\n\nWrong footprint"
    )

    librarian.post("/api/v1/parts/1102-0002/status", json={"status": "obsolete"})
    assert (
        librarian.post("/api/v1/parts/1102-0002/status", json={"status": "draft"}).status_code
        == 200
    )


def test_the_part_page_offers_return_to_draft_to_a_librarian_when_it_is_not_one(serve):  # noqa: F811
    librarian = sign_in_librarian(serve())
    librarian.post("/api/v1/parts", json=REQUEST)

    assert not draft_button(librarian.get("/ipn/1102-0002").text), "it is a draft already"

    librarian.post("/api/v1/parts/1102-0002/status", json={"status": "approved"})
    approved = librarian.get("/ipn/1102-0002").text
    assert draft_button(approved) and "Return to draft" in approved
    assert 'data-set-status="obsolete"' in approved, "alongside Retire"

    librarian.post("/api/v1/parts/1102-0002/status", json={"status": "obsolete"})
    retired = librarian.get("/ipn/1102-0002").text
    assert draft_button(retired) and 'data-set-status="approved"' in retired, "with Reinstate"


def test_a_designer_is_not_offered_it(web):  # noqa: F811
    web.post("/api/v1/parts", json=REQUEST)

    assert not draft_button(web.get("/ipn/1102-0002").text)
    assert not draft_button(web.get("/ipn/1102-0001").text), "not on an approved part either"
    refused = web.post("/api/v1/parts/1102-0001/status", json={"status": "draft"})
    assert refused.status_code == 403


def test_the_button_is_for_a_browser_not_the_panel(serve):  # noqa: F811
    librarian = sign_in_librarian(serve())
    html = librarian.get("/ipn/1102-0001").text

    (button,) = [t for t in re.findall(r"<button\b[^>]*>", html) if 'data-set-status="draft"' in t]
    assert "web-only" in button
