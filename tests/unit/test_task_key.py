import pytest

from domain.routes.tasks import _KEY


def test_valid_key_is_split_into_project_key_and_number():
    match = _KEY.match("FLWN-12")
    assert (match.group(1), int(match.group(2))) == ("FLWN", 12)


@pytest.mark.parametrize(
    "key",
    ["flwn-12", "FLWN-0", "FLWN-012", "F-1", "TOOLONGKEYXX-1", "FLWN12", "FLWN-", "-1", "FLWN-1x"],
)
def test_malformed_key_is_rejected(key):
    assert _KEY.match(key) is None
