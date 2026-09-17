import os

from logstats import count_errors

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.log")


def test_counts_only_error_level_lines():
    assert count_errors(LOG) == 19


def test_small_sample(tmp_path):
    p = tmp_path / "x.log"
    p.write_text(
        "t [INFO] no error here\n"
        "t [ERROR] real failure\n"
        "t [WARN] error budget ok\n"
    )
    assert count_errors(str(p)) == 1


def test_empty_file(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("")
    assert count_errors(str(p)) == 0
