class TodoStore:
    def __init__(self):
        self._store = {}
        self._next_id = 1

    def add(self, title):
        item_id = self._next_id
        self._store[item_id] = {"id": item_id, "title": title}
        self._next_id += 1
        return self._store[item_id]

    def get(self, item_id):
        return self._store.get(item_id)

    def list_all(self):
        return list(self._store.values())

    def delete(self, item_id):
        return self._store.pop(item_id, None)
