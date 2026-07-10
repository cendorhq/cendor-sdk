from cendor_init.semver import clean_version, compare_versions, range_blocks_latest


def test_clean_version():
    assert clean_version("^0.1.0") == "0.1.0"
    assert clean_version(">=1.3,<2") == "1.3"
    assert clean_version("~1.2.3") == "1.2.3"
    assert clean_version("*") is None


def test_compare_versions():
    assert compare_versions("1.2.0", "1.2") == 0
    assert compare_versions("0.1.0", "0.5.0") == -1
    assert compare_versions("1.5.0", "1.4.1") == 1


def test_range_blocks_latest_is_honest():
    # caret on 0.x locks the minor
    assert range_blocks_latest("^0.1.0", "0.5.0") is True
    assert range_blocks_latest("^0.5.0", "0.5.0") is False
    # open-ended >= never blocks (latest reachable) — the key honesty case
    assert range_blocks_latest(">=1.3,<2", "1.5.0") is False
    assert range_blocks_latest(">=1.0", "9.9.9") is False
    # upper bound / exact pin below latest do block
    assert range_blocks_latest(">=1.0,<1.4", "1.5.0") is True
    assert range_blocks_latest("==1.0.0", "1.5.0") is True
    assert range_blocks_latest("1.5.0", "1.5.0") is False
