from store import TodoStore

store = TodoStore()

def add_todo(title):
    return store.add(title)

def list_todos():
    return store.list()

def delete_todo(todo_id):
    return store.delete(todo_id)

if __name__ == "__main__":
    t1 = add_todo("Buy groceries")
    t2 = add_todo("Write tests")
    print("Added:", t1, t2)
    print("List:", list_todos())
    delete_todo(t1["id"])
    print("After delete:", list_todos())
