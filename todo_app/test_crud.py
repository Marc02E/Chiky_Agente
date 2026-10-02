"""Tests for the in-memory CRUD todo app."""

import store


def test_add_list_delete():
    # Add
    item = store.add("Buy milk")
    assert item == {"id": 1, "title": "Buy milk"}

    # List
    items = store.list_all()
    assert len(items) == 1
    assert items[0]["title"] == "Buy milk"

    # Get
    found = store.get(item["id"])
    assert found == {"id": 1, "title": "Buy milk"}

    # Delete
    assert store.delete(item["id"]) is True
    assert store.list_all() == []

    # Delete non-existent
    assert store.delete(999) is False


def test_multiple_items():
    a = store.add("Task A")
    b = store.add("Task B")
    items = store.list_all()
    assert len(items) >= 2
    store.delete(a["id"])
    store.delete(b["id"])


if __name__ == "__main__":
    test_add_list_delete()
    test_multiple_items()
    print("All tests passed!")
