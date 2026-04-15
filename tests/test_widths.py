from update_readme import pad_to_width, wcswidth


def test_wcswidth_basic():
    assert wcswidth("abc") == 3
    assert wcswidth("") == 0


def test_pad_left():
    assert pad_to_width("hi", 5, "left") == "hi   "


def test_pad_right():
    assert pad_to_width("hi", 5, "right") == "   hi"


def test_pad_center_even():
    assert pad_to_width("hi", 6, "center") == "  hi  "


def test_pad_center_odd():
    # Leftover space goes to the right.
    assert pad_to_width("hi", 5, "center") == " hi  "


def test_pad_truncates_with_ellipsis():
    out = pad_to_width("abcdef", 4, "left")
    assert out.endswith("…")
    assert wcswidth(out) == 4


def test_pad_noop_when_exact():
    assert pad_to_width("abcd", 4, "left") == "abcd"


def test_pad_zero_width():
    assert pad_to_width("abc", 0, "left") == ""
