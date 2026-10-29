import pytest

from pdum.ship.versions import ZERO, Version, VersionError, expected_dev_marker, split_dev


def test_parse_and_str():
    assert str(Version.parse("1.2.3")) == "1.2.3"
    assert Version.parse(" 0.7.0 ") == Version(0, 7, 0)


@pytest.mark.parametrize("bad", ["1.2", "1.2.3+dev", "v1.2.3", "1.2.3-alpha", "a.b.c"])
def test_parse_rejects_non_release(bad):
    with pytest.raises(VersionError):
        Version.parse(bad)


def test_bump():
    v = Version(0, 7, 3)
    assert v.bump("patch") == Version(0, 7, 4)
    assert v.bump("minor") == Version(0, 8, 0)
    assert v.bump("major") == Version(1, 0, 0)


def test_dev_marker():
    assert Version(0, 7, 0).dev == "0.7.0+dev"
    assert expected_dev_marker(None) == "0.0.0+dev"
    assert expected_dev_marker(ZERO.bump("minor")) == "0.1.0+dev"


def test_split_dev():
    assert split_dev("0.7.0+dev") == ("0.7.0", True)
    assert split_dev("0.7.0") == ("0.7.0", False)
    assert split_dev("0.7.0-alpha") == ("0.7.0-alpha", False)


def test_ordering():
    assert Version(0, 9, 9) < Version(0, 10, 0) < Version(1, 0, 0)
