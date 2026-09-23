import pytest
from pydantic import ValidationError

from l2_agent.geometry import ClientRect, NormalizedPoint


@pytest.mark.parametrize("origin", [(0, 0), (-1920, -100), (300, 200)])
@pytest.mark.parametrize("size", [(1, 1), (1920, 1080), (801, 601)])
@pytest.mark.parametrize("value", [0, 0.25, 0.5, 0.99, 1])
def test_mapping_stays_inside_client(origin, size, value):
    x, y = origin
    width, height = size
    rect = ClientRect(left=x, top=y, right=x + width, bottom=y + height)
    point = rect.to_screen(NormalizedPoint(x=value, y=value))
    assert rect.contains(*point)
    if value == 1:
        assert point == (rect.right - 1, rect.bottom - 1)


@pytest.mark.parametrize("value", [-0.001, 1.001, float("nan"), float("inf")])
@pytest.mark.parametrize("axis", ["x", "y"])
def test_invalid_coordinates_rejected(value, axis):
    values = {"x": 0.5, "y": 0.5, axis: value}
    with pytest.raises(ValidationError):
        NormalizedPoint(**values)


@pytest.mark.parametrize("right,bottom", [(0, 10), (10, 0), (-1, 10)])
def test_empty_rect_rejected(right, bottom):
    with pytest.raises(ValidationError):
        ClientRect(left=0, top=0, right=right, bottom=bottom)


def test_mapping_changes_after_resize():
    point = NormalizedPoint(x=1, y=1)
    before = ClientRect(left=10, top=20, right=110, bottom=120)
    after = ClientRect(left=-500, top=30, right=500, bottom=830)
    assert before.to_screen(point) == (109, 119)
    assert after.to_screen(point) == (499, 829)
