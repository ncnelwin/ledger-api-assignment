# Transaction Ledger API

Flask REST API with SQLite

## Run

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

API base URL: http://127.0.0.1:5000
Documentation URL: http://127.0.0.1:5000/apidocs
## Endpoints

- `POST /api/accounts` — create account
- `GET /api/accounts/` — retrieve balance
- `GET /api/accounts/<id>/balance` — retrieve balance
- `POST /api/accounts/<id>/transactions` — credit/debit
- `GET /api/accounts/<id>/transactions?page=1&limit=10&type=credit` — history
- `POST /api/transfers` — transfer funds between two accounts atomically, `JSON Parameters: source_account_id, destination_account_id, amount`
## Tests

```bash
pytest -v
```
