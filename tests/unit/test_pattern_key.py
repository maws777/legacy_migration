import pytest

from rules.pattern_key import extend_key, group_key, make_key, parse_key


@pytest.mark.parametrize("detail", [
    None, "", "Lecture référence logiciel <ECU>", "a|b", "x#row=3", "100%", "%7C literal", "%25|#%",
])
def test_round_trip_with_special_characters(detail):
    key = make_key("ecuCode", "UNREADABLE", detail)
    parts = parse_key(key)
    assert (parts.field, parts.reason, parts.detail, parts.source_row_id) == ("ecuCode", "UNREADABLE", detail, None)
    assert parts.group_key == key
    ext = extend_key(key, 42)
    p2 = parse_key(ext)
    assert (p2.detail, p2.source_row_id, p2.group_key) == (detail, 42, key)
    assert group_key(ext) == key


def test_encoding_is_visible_in_key():
    assert make_key("Vehmodel", "UNKNOWN_VARIANT", "A|B#C%") == "Vehmodel|UNKNOWN_VARIANT|A%7CB%23C%25"
    assert make_key("ecuCode", "FORMAT2_NO_DID") == "ecuCode|FORMAT2_NO_DID"


def test_invalid_components_and_double_extension():
    with pytest.raises(ValueError):
        make_key("a|b", "R")
    with pytest.raises(ValueError):
        extend_key(extend_key("f|r", 1), 2)
    with pytest.raises(ValueError):
        parse_key("no-separator")
