"""The API's contract: every JSON endpoint says what it answers.

What each one answers is in `app.web.schemas`, and is what `/docs` shows. An
endpoint added without saying so would answer whatever its code happened to
build, with nothing to hold it to that; this is what notices.
"""

from fastapi.routing import APIRoute

from tests.conftest import OPEN

# What answers with something other than JSON: a file, a redirect, or
# nothing. Everything else has to name a model.
NOT_JSON = {
    ("GET", "/api/v1/parts/{ipn}/symbol.svg"),
    ("GET", "/api/v1/parts/{ipn}/footprint.svg"),
    ("GET", "/api/v1/parts/{ipn}/model.step"),
    ("GET", "/api/v1/boms/{bom_id}/export.csv"),
    ("GET", "/api/v1/boms/{bom_id}/source.csv"),
    ("GET", "/api/v1/backup"),
    ("DELETE", "/api/v1/boms/{bom_id}"),  # 204
    ("POST", "/api/v1/session/logout"),  # 204
    ("GET", "/session/consume"),  # a redirect, with a cookie
    ("GET", "/auth/login"),
    ("GET", "/auth/callback"),
}


def documented(app):
    for route in app.routes:
        if isinstance(route, APIRoute) and route.include_in_schema:
            for method in route.methods:
                yield (method, route.path), route


def test_every_json_endpoint_names_what_it_answers(client):
    without = {key for key, route in documented(client.app) if route.response_model is None}

    assert without == NOT_JSON, "a new endpoint answers with a model, or is listed above"


def test_the_reference_describes_each_answer_field_by_field(make_client):
    schema = make_client(**OPEN).get("/openapi.json").json()
    models = schema["components"]["schemas"]

    part = models["PartDetail"]
    assert set(part["required"]) == set(part["properties"]), "nothing is left to chance"
    assert part["properties"]["mpns"]["items"] == {"$ref": "#/components/schemas/PartSource"}

    answers = schema["paths"]["/api/v1/parts/{ipn}"]["get"]["responses"]["200"]
    assert answers["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PartDetail"
    }
    # An upload that would replace a BOM says which one, and says so here.
    conflict = schema["paths"]["/api/v1/boms"]["post"]["responses"]["409"]
    assert conflict["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BomConflict"
    }


def test_no_answer_is_described_as_anything_at_all(make_client):
    """An answer typed as a bare object is one nobody wrote down."""
    schema = make_client(**OPEN).get("/openapi.json").json()

    bare = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        for answer in operation["responses"].values()
        for content in answer.get("content", {}).values()
        if content.get("schema") in ({}, {"type": "object"})
        # What answers with a file or a redirect has no JSON to describe.
        and (method.upper(), path) not in NOT_JSON
    ]

    assert bare == []
