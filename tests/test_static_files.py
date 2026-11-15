"""Static files are checked for freshness, not kept on a browser's own guess."""

import pytest

SCRIPTS = ["js/app.js", "js/model-viewer.js", "css/app.css", "vendor/three.module.min.js"]


@pytest.mark.parametrize("path", SCRIPTS)
def test_a_browser_is_told_to_check_before_reusing_a_file(client, path):
    """With no instruction a browser decides for itself how long to keep a
    script, and one from before a deploy can run for hours after it, against
    pages that have changed."""
    response = client.get(f"/static/{path}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"], "what it asks with"


def test_an_unchanged_file_costs_a_round_trip_and_not_a_download(client):
    first = client.get("/static/vendor/occt-import-js.wasm")
    again = client.get(
        "/static/vendor/occt-import-js.wasm", headers={"If-None-Match": first.headers["etag"]}
    )

    assert len(first.content) > 1_000_000
    assert again.status_code == 304
    assert again.content == b"", "seven megabytes, not sent again"
    assert again.headers["cache-control"] == "no-cache"


def test_a_changed_file_is_sent_again(client):
    response = client.get("/static/js/app.js", headers={"If-None-Match": '"not-the-current-one"'})

    assert response.status_code == 200
    assert response.text.startswith("/* Shared by every page")
