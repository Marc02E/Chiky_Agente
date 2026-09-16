# AB8-CRUD-MARKER-7765
from ab8_crud_store import TodoStore

def main():
    store = TodoStore()

    item1 = store.add("Buy groceries")
    item2 = store.add("Walk the dog")
    print(f"Added: {item1}")
    print(f"Added: {item2}")

    print(f"\nAll items: {store.list_all()}")

    deleted = store.delete(item1["id"])
    print(f"\nDeleted: {deleted}")
    print(f"All items after delete: {store.list_all()}")

if __name__ == "__main__":
    main()
