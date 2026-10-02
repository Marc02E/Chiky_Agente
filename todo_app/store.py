"""In-memory todo store."""

_store: dict[int, str] = {}
_next_id: int = 1


def add(title: str) -> dict:
    global _next_id
    item = {"id": _next_id, "title": title}
    _store[_next_id] = title
    _next_id += 1
    return item


def list_all() -> list[dict]:
    return [{"id": k, "title": v} for k, v in _store.items()]


def delete(item_id: int) -> bool:
    if item_id in _store:
        del _store[item_id]
        return True
    return False


def get(item_id: int) -> dict | None:
    if item_id in _store:
        return {"id": item_id, "title": _store[item_id]}
    return None
