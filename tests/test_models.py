from apppilot.models import Action, Point, Step


def test_action_defaults() -> None:
    a = Action(type="click", x=10, y=20)
    assert a.type == "click"
    assert a.x == 10
    assert a.y == 20
    assert a.selector is None


def test_extract_action() -> None:
    schema = {"title": "h1", "count": {"selector": ".item", "attribute": "count"}}
    a = Action(type="extract", extract_schema=schema)
    assert a.type == "extract"
    assert a.extract_schema == schema


def test_step_extract_result() -> None:
    s = Step(number=1, action=Action(type="extract", extract_schema={"title": "h1"}))
    assert s.extract_result is None
    s.extract_result = {"title": "Hello"}
    assert s.extract_result == {"title": "Hello"}


def test_point_dict() -> None:
    p = Point(x=1.5, y=2.5)
    assert p.__dict__ == {"x": 1.5, "y": 2.5}
