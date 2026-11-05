from app.llm import TOOLS, site_instructions, tool_schema


def test_tool_schemas_do_not_accept_scope_identifiers_from_the_model():
    for name in TOOLS:
        parameters = tool_schema(name)["parameters"]
        assert "site_id" not in parameters["properties"]
        assert "site_id" not in parameters["required"]
        assert "organization_id" not in parameters["properties"]


def test_system_instructions_use_authorized_names_and_never_request_ids():
    instructions = site_instructions({
        "site_id": "site-b",
        "site_name": "Sede Norte",
    })

    assert "Sede Norte" in instructions
    assert "Sede activa: Sede Norte" in instructions
    assert "No solicites site_id" in instructions
    assert "site-b" not in instructions
