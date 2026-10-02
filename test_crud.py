from store import TodoStore


def test_add_list_delete():
    s = TodoStore()
    item = s.add("Test item")
    assert item == {"id": 1, "title": "Test item"}
    assert s.list() == [item]
    assert s.get(1) == item
    assert s.delete(1) is True
    assert s.list() == []
    assert s.delete(1) is False


def test_multiple_items():
    s = TodoStore()
    a = s.add("First")
    b = s.add("Second")
    assert len(s.list()) == 2
    s.delete(a["id"])
    assert len(s.list()) == 1
    assert s.list()[0] == b
