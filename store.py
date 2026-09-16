class TodoStore:
    def __init__(self):
        self._store = {}
        self._next_id = 1

    def add(self, title):
        todo = {"id": self._next_id, "title": title}
        self._store[self._next_id] = todo
        self._next_id += 1
        return todo

    def list(self):
        return list(self._store.values())

    def delete(self, todo_id):
        if todo_id in self._store:
            del self._store[todo_id]
            return True
        return False

    def get(self, todo_id):
        return self._store.get(todo_id)
