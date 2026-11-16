from app.presentation import RAW_PAYLOAD_FALLBACK, sanitize_assistant_content


def test_plain_conversation_is_preserved():
    text = "Creé la feria «Feria Tecnológica» como borrador."
    assert sanitize_assistant_content(text) == text


def test_raw_json_object_is_replaced():
    payload = '{"id": "fair-1", "name": "Feria", "created_at": "2026-11-15T10:00:00.000Z"}'
    assert sanitize_assistant_content(payload) == RAW_PAYLOAD_FALLBACK


def test_raw_json_array_is_replaced():
    payload = '[{"id": "fair-1"}, {"id": "fair-2"}]'
    assert sanitize_assistant_content(payload) == RAW_PAYLOAD_FALLBACK


def test_fenced_json_block_is_removed():
    text = 'Listo.\n```json\n{"id": "fair-1", "status": "DRAFT"}\n```'
    assert sanitize_assistant_content(text) == "Listo."


def test_fenced_json_without_context_uses_fallback():
    text = '```json\n{"id": "fair-1"}\n```'
    assert sanitize_assistant_content(text) == RAW_PAYLOAD_FALLBACK


def test_object_marker_is_replaced():
    text = "El sitio es [object Object]."
    assert sanitize_assistant_content(text) == RAW_PAYLOAD_FALLBACK


def test_empty_content_is_returned_unchanged():
    assert sanitize_assistant_content("") == ""
    assert sanitize_assistant_content(None) is None


def test_braces_that_are_not_json_are_preserved():
    text = "La rúbrica usa {criterios} definidos por la feria."
    assert sanitize_assistant_content(text) == text
