import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from app.prompts import site_instructions
from app.tool_schemas import tool_schema
from app.providers.openai_compatible import TOOLS


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
