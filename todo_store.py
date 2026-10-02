class TodoStore:
    def __init__(self):
        self.items = {}
        self.next_id = 1

    def add(self, title):
        item = {"id": self.next_id, "title": title, "done": False}
        self.items[self.next_id] = item
        self.next_id += 1
        return item

    def list(self):
        return list(self.items.values())

    def delete(self, item_id):
        if item_id in self.items:
            return self.items.pop(item_id)
        return None

    def get(self, item_id):
        return self.items.get(item_id)

    def update(self, item_id, title=None, done=None):
        if item_id not in self.items:
            return None
        item = self.items[item_id]
        if title is not None:
            item["title"] = title
        if done is not None:
            item["done"] = done
        return item