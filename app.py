import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Flask, g, jsonify, request

BASE_DIR = Path(__file__).resolve().parent
SQLITE_MAX_INT = (1 << 63) - 1  # Maximum positive SQLite INTEGER

from flasgger import Swagger


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid amount")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero")
    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount must have at most two decimal places")
    cents = int(amount * 100)
    if cents > SQLITE_MAX_INT:
        raise ValueError("Amount exceeds the maximum supported value")
    return cents


def fmt(cents):
    return f"{Decimal(cents) / 100:.2f}"


def create_app(config=None):
    app = Flask(__name__)
    swagger = Swagger(app)
    app.config.update(
        DATABASE=str(BASE_DIR / "ledger.db"),
        TESTING=False,
    )
    if config:
        app.config.update(config)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(
                app.config["DATABASE"],
                timeout=10,
                isolation_level=None,
            )
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
            g.db.execute("PRAGMA busy_timeout = 10000")
        return g.db

    @app.teardown_appcontext
    def close_db(exc=None):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    with app.app_context():
        db().executescript(
            (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
        )

    def error(message, status):
        return jsonify({"error": message}), status

    def body():
        if not request.is_json:
            return None, error("Content-Type must be application/json", 415)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return None, error("Expected a JSON object", 400)
        return data, None

    def account(account_id):
        return db().execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()

    def transaction_json(row):
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "type": row["type"],
            "amount": fmt(row["amount_cents"]),
            "description": row["description"],
            "reference_id": row["reference_id"],
            "created_at": row["created_at"],
        }
    def accounts(limit, offset):
        return db().execute(
            """
            SELECT *
            FROM accounts
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset)
        ).fetchall()
    
    @app.post("/api/accounts")
    def create_account():
        """
        Create a new account
        ---
        tags:
          - Accounts
        consumes:
          - application/json
        produces:
          - application/json
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              required:
                - name
              properties:
                name:
                  type: string
                  example: Savings Account
        responses:
          201:
            description: Account created successfully
          400:
            description: Invalid request or initial balance supplied
          415:
            description: Content-Type must be application/json
        """
        data, failure = body()
        if failure:
            return failure
        if "balance" in data or "balance_cents" in data:
            return error("Initial balance cannot be supplied", 400)
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return error("name is required", 400)

        account_id = str(uuid.uuid4())
        print("Creating account", account_id)
        created = now()
        db().execute(
            "INSERT INTO accounts VALUES (?, ?, 0, ?)",
            (account_id, name.strip(), created),
        )
        return jsonify({
            "id": account_id,
            "name": name.strip(),
            "balance": "0.00",
            "created_at": created,
        }), 201



    def total_accounts():
        return db().execute(
            "SELECT COUNT(*) FROM accounts"
        ).fetchone()[0]


    @app.route("/api/accounts", methods=["GET"])
    def get_accounts():
        """
        Retrieve accounts with pagination
        ---
        tags:
          - Accounts
        parameters:
          - name: page
            in: query
            type: integer
            default: 1
            minimum: 1
          - name: limit
            in: query
            type: integer
            default: 10
            minimum: 1
            maximum: 100
        responses:
          200:
            description: Paginated account list
            schema:
              type: object
              properties:
                accounts:
                  type: array
                  items:
                    type: object
                    properties:
                      id:
                        type: string
                        format: uuid
                      name:
                        type: string
                      balance:
                        type: string
                        example: "100.00"
                      created_at:
                        type: string
                pagination:
                  type: object
                  properties:
                    page:
                      type: integer
                    limit:
                      type: integer
                    total_records:
                      type: integer
                    total_pages:
                      type: integer
          400:
            description: Invalid pagination parameters
        """
        page = request.args.get("page", default=1, type=int)
        limit = request.args.get("limit", default=10, type=int)

        if page < 1 or limit < 1:
            return jsonify({
                "error": "page and limit must be positive integers"
            }), 400

        limit = min(limit, 100)
        offset = (page - 1) * limit

        account_list = accounts(limit, offset)
        total = total_accounts()

        return jsonify({
            "accounts": [
                {
                    "id": account["id"],
                    "name": account["name"],
                    "balance": f'{account["balance_cents"] / 100:.2f}',
                    "created_at": account["created_at"]
                }
                for account in account_list
            ],
            "pagination": {
                "page": page,
                "limit": limit,
                "total_records": total,
                "total_pages": (total + limit - 1) // limit
            }
        }), 200
    @app.get("/api/accounts/<account_id>/balance")
    def balance(account_id):
        """
        Retrieve an account balance
        ---
        tags:
          - Accounts
        parameters:
          - name: account_id
            in: path
            required: true
            type: string
            format: uuid
        responses:
          200:
            description: Account balance retrieved
            schema:
              type: object
              properties:
                account_id:
                  type: string
                  format: uuid
                balance:
                  type: string
                  example: "250.00"
          404:
            description: Account not found
        """
        row = account(account_id)
        if row is None:
            return error("Account not found", 404)
        return jsonify({
            "account_id": account_id,
            "balance": fmt(row["balance_cents"]),
        })

    @app.post("/api/accounts/<account_id>/transactions")
    def add_transaction(account_id):
        """
        Create a credit or debit transaction
        ---
        tags:
          - Transactions
        consumes:
          - application/json
        parameters:
          - name: account_id
            in: path
            required: true
            type: string
            format: uuid
          - in: body
            name: body
            required: true
            schema:
              type: object
              required:
                - type
                - amount
                - description
                - reference_id
              properties:
                type:
                  type: string
                  enum:
                    - credit
                    - debit
                amount:
                  type: number
                  exclusiveMinimum: true
                  minimum: 0
                  example: 100.50
                description:
                  type: string
                  example: Initial deposit
                reference_id:
                  type: string
                  example: TXN-001
        responses:
          201:
            description: Transaction created successfully
          400:
            description: Invalid transaction data
          404:
            description: Account not found
          409:
            description: Duplicate transaction reference
          415:
            description: Content-Type must be application/json
          422:
            description: Insufficient balance or balance limit exceeded
          500:
            description: Database error
        """
        data, failure = body()
        if failure:
            return failure

        kind = data.get("type")
        if kind not in ("credit", "debit"):
            return error("type must be credit or debit", 400)
        try:
            amount = money(data.get("amount"))
        except ValueError as exc:
            return error(str(exc), 400)

        description = data.get("description")
        reference = data.get("reference_id")
        if not isinstance(description, str) or not description.strip():
            return error("description is required", 400)
        if not isinstance(reference, str) or not reference.strip():
            return error("reference_id is required", 400)
        description, reference = description.strip(), reference.strip()

        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            acc = account(account_id)
            if acc is None:
                conn.rollback()
                return error("Account not found", 404)

            existing = conn.execute(
                """SELECT * FROM transactions
                   WHERE account_id = ? AND reference_id = ?""",
                (account_id, reference),
            ).fetchone()
            if existing:
                conn.rollback()
                return jsonify({
                    "error": "Transaction already exists",
                    "transaction_id": existing["id"],
                    "reference_id": reference,
                }), 409

            current = acc["balance_cents"]
            if kind == "debit" and amount > current:
                conn.rollback()
                return error("Insufficient balance", 422)

            if kind == "credit" and amount > SQLITE_MAX_INT - current:
                conn.rollback()
                return error("Balance would exceed the maximum supported value", 422)

            updated = current + amount if kind == "credit" else current - amount
            txn_id, created = str(uuid.uuid4()), now()

            conn.execute(
                """INSERT INTO transactions
                   (id, account_id, type, amount_cents, description,
                    reference_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (txn_id, account_id, kind, amount, description, reference, created),
            )
            conn.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ?",
                (updated, account_id),
            )
            conn.commit()

            return jsonify({
                "id": txn_id,
                "account_id": account_id,
                "type": kind,
                "amount": fmt(amount),
                "description": description,
                "reference_id": reference,
                "created_at": created,
            }), 201

        except sqlite3.IntegrityError:
            conn.rollback()
            existing = conn.execute(
                """SELECT id FROM transactions
                   WHERE account_id = ? AND reference_id = ?""",
                (account_id, reference),
            ).fetchone()
            if existing:
                return jsonify({
                    "error": "Transaction already exists",
                    "transaction_id": existing["id"],
                }), 409
            app.logger.exception("Integrity error")
            return error("Transaction could not be created", 409)
        except sqlite3.Error:
            conn.rollback()
            app.logger.exception("Database error")
            return error("Unable to process transaction", 500)


    @app.post("/api/transfers")
    def transfer():
        """
        Transfer funds between two accounts atomically
        ---
        tags:
          - Transfers
        consumes:
          - application/json
        produces:
          - application/json
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              required:
                - source_account_id
                - destination_account_id
                - amount
                - description
                - reference_id
              properties:
                source_account_id:
                  type: string
                  format: uuid
                  example: 11111111-1111-4111-8111-111111111111
                destination_account_id:
                  type: string
                  format: uuid
                  example: 22222222-2222-4222-8222-222222222222
                amount:
                  type: number
                  minimum: 0
                  exclusiveMinimum: true
                  example: 30.00
                description:
                  type: string
                  example: Payment for services
                reference_id:
                  type: string
                  example: TRANSFER-001
        responses:
          201:
            description: Transfer completed successfully
            schema:
              type: object
              properties:
                reference_id:
                  type: string
                amount:
                  type: string
                  example: "30.00"
                description:
                  type: string
                created_at:
                  type: string
                source:
                  type: object
                  properties:
                    account_id:
                      type: string
                    balance:
                      type: string
                    transaction:
                      type: object
                destination:
                  type: object
                  properties:
                    account_id:
                      type: string
                    balance:
                      type: string
                    transaction:
                      type: object
          400:
            description: Invalid request or identical source and destination
          404:
            description: Source or destination account not found
          409:
            description: Transfer reference already used
          415:
            description: Content-Type must be application/json
          422:
            description: Insufficient source balance or destination balance limit exceeded
          500:
            description: Database error or unexpected transfer failure
        """
        data, failure = body()
        if failure:
            return failure

        source_id = data.get("source_account_id")
        destination_id = data.get("destination_account_id")
        if not isinstance(source_id, str) or not source_id.strip():
            return error("source_account_id is required", 400)
        if not isinstance(destination_id, str) or not destination_id.strip():
            return error("destination_account_id is required", 400)

        source_id = source_id.strip()
        destination_id = destination_id.strip()
        if source_id == destination_id:
            return error("Source and destination accounts must be different", 400)

        try:
            amount = money(data.get("amount"))
        except ValueError as exc:
            return error(str(exc), 400)

        description = data.get("description")
        reference = data.get("reference_id")
        if not isinstance(description, str) or not description.strip():
            return error("description is required", 400)
        if not isinstance(reference, str) or not reference.strip():
            return error("reference_id is required", 400)
        description = description.strip()
        reference = reference.strip()

        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")

            source = account(source_id)
            if source is None:
                conn.rollback()
                return error("Source account not found", 404)

            destination = account(destination_id)
            if destination is None:
                conn.rollback()
                return error("Destination account not found", 404)
            
            existing = conn.execute(
                """SELECT account_id FROM transactions
                   WHERE reference_id = ? AND account_id IN (?, ?)
                   LIMIT 1""",
                (reference, source_id, destination_id),
            ).fetchone()
            if existing:
                conn.rollback()
                return jsonify({
                    "error": "Transaction reference already used for a source or destination account",
                    "reference_id": reference,
                }), 409

            source_balance = source["balance_cents"]
            destination_balance = destination["balance_cents"]
            if amount > source_balance:
                conn.rollback()
                return error("Insufficient balance in source account", 422)

            if amount > SQLITE_MAX_INT - destination_balance:
                conn.rollback()
                return error("Destination balance would exceed the maximum supported value", 422)

            source_updated = source_balance - amount
            destination_updated = destination_balance + amount
            created = now()
            debit_id = str(uuid.uuid4())
            credit_id = str(uuid.uuid4())

            conn.execute(
                """INSERT INTO transactions
                   (id, account_id, type, amount_cents, description,
                    reference_id, created_at)
                   VALUES (?, ?, 'debit', ?, ?, ?, ?)""",
                (debit_id, source_id, amount, description, reference, created),
            )
            conn.execute(
                """INSERT INTO transactions
                   (id, account_id, type, amount_cents, description,
                    reference_id, created_at)
                   VALUES (?, ?, 'credit', ?, ?, ?, ?)""",
                (credit_id, destination_id, amount, description, reference, created),
            )
            conn.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ?",
                (source_updated, source_id),
            )
            conn.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ?",
                (destination_updated, destination_id),
            )
            conn.commit()

            return jsonify({
                "reference_id": reference,
                "amount": fmt(amount),
                "description": description,
                "created_at": created,
                "source": {
                    "account_id": source_id,
                    "balance": fmt(source_updated),
                    "transaction": {
                        "id": debit_id,
                        "account_id": source_id,
                        "type": "debit",
                        "amount": fmt(amount),
                        "description": description,
                        "reference_id": reference,
                        "created_at": created,
                    },
                },
                "destination": {
                    "account_id": destination_id,
                    "balance": fmt(destination_updated),
                    "transaction": {
                        "id": credit_id,
                        "account_id": destination_id,
                        "type": "credit",
                        "amount": fmt(amount),
                        "description": description,
                        "reference_id": reference,
                        "created_at": created,
                    },
                },
            }), 201

        except sqlite3.IntegrityError:
            conn.rollback()
            existing = conn.execute(
                """SELECT account_id FROM transactions
                   WHERE reference_id = ? AND account_id IN (?, ?)
                   LIMIT 1""",
                (reference, source_id, destination_id),
            ).fetchone()
            if existing:
                return jsonify({
                    "error": "Transaction reference already used for a source or destination account",
                    "reference_id": reference,
                }), 409
            app.logger.exception("Transfer integrity error")
            return error("Transfer could not be completed", 409)
        except sqlite3.Error:
            conn.rollback()
            app.logger.exception("Transfer database error")
            return error("Unable to process transfer", 500)
        except Exception:
            conn.rollback()
            app.logger.exception("Unexpected transfer error")
            return error("Unable to process transfer", 500)

    @app.get("/api/accounts/<account_id>/transactions")
    def history(account_id):
        """
        Retrieve paginated transaction history
        ---
        tags:
          - Transactions
        parameters:
          - name: account_id
            in: path
            required: true
            type: string
            format: uuid
          - name: page
            in: query
            type: integer
            default: 1
            minimum: 1
          - name: limit
            in: query
            type: integer
            default: 10
            minimum: 1
            maximum: 100
          - name: type
            in: query
            type: string
            enum:
              - credit
              - debit
            required: false
        responses:
          200:
            description: Transaction history retrieved
            schema:
              type: object
              properties:
                data:
                  type: array
                  items:
                    type: object
                    properties:
                      id:
                        type: string
                      account_id:
                        type: string
                      type:
                        type: string
                        enum:
                          - credit
                          - debit
                      amount:
                        type: string
                        example: "100.50"
                      description:
                        type: string
                      reference_id:
                        type: string
                      created_at:
                        type: string
                pagination:
                  type: object
                  properties:
                    page:
                      type: integer
                    limit:
                      type: integer
                    total_records:
                      type: integer
                    total_pages:
                      type: integer
          400:
            description: Invalid pagination or transaction type
          404:
            description: Account not found
        """
        if account(account_id) is None:
            return error("Account not found", 404)

        kind = request.args.get("type")
        if kind is not None and kind not in ("credit", "debit"):
            return error("type must be credit or debit", 400)
        try:
            page = int(request.args.get("page", "1"))
            limit = int(request.args.get("limit", "10"))
            if page < 1 or not 1 <= limit <= 100:
                raise ValueError
        except ValueError:
            return error("page must be >= 1; limit must be 1 to 100", 400)

        clause = "WHERE account_id = ?"
        params = [account_id]
        if kind:
            clause += " AND type = ?"
            params.append(kind)

        conn = db()
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions {clause}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT * FROM transactions {clause}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, (page - 1) * limit),
        ).fetchall()

        return jsonify({
            "data": [transaction_json(row) for row in rows],
            "pagination": {
                "page": page,
                "limit": limit,
                "total_records": total,
                "total_pages": (total + limit - 1) // limit,
            },
        })

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
