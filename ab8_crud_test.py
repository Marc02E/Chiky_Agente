from ab8_crud_store import TodoStore

def test_crud():
    store = TodoStore()

    # Add an item
    item = store.add("Test todo")
    assert item["title"] == "Test todo"
    assert item["id"] == 1
    print(f"PASS: Added item {item}")

    # List items
    items = store.list_all()
    assert len(items) == 1
    assert items[0]["title"] == "Test todo"
    print(f"PASS: Listed items: {items}")

    # Get item
    fetched = store.get(item["id"])
    assert fetched["title"] == "Test todo"
    print(f"PASS: Got item: {fetched}")

    # Delete item
    deleted = store.delete(item["id"])
    assert deleted["title"] == "Test todo"
    assert store.list_all() == []
    print(f"PASS: Deleted item: {deleted}")

    print("\nAll CRUD tests passed!")

if __name__ == "__main__":
    test_crud()
