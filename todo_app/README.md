# Todo App (In-Memory CRUD)

Minimal Python todo app with no framework dependencies.

## Files
- `store.py` — in-memory dict-based store with add/list/get/delete
- `app.py` — HTTP server exposing the store as a REST API
- `test_crud.py` — unit tests for the store

## Run the server
```bash
python app.py
```

## Run tests
```bash
python test_crud.py
```

## API
| Method | Endpoint     | Description       |
|--------|-------------|-------------------|
| GET    | `/`          | List all todos    |
| POST   | `/`          | Add a todo        |
| DELETE | `/{id}`      | Delete a todo     |
