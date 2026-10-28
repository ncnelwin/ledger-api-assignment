import pytest
from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
    })
    with app.test_client() as client:
        yield client


def create_account(client):
    response = client.post("/api/accounts", json={"name": "Alice"})
    assert response.status_code == 201
    return response.get_json()["id"]


def txn(client, aid, kind, amount, ref):
    return client.post(
        f"/api/accounts/{aid}/transactions",
        json={
            "type": kind,
            "amount": amount,
            "description": "Test transaction",
            "reference_id": ref,
        },
    )


def balance(client, aid):
    return client.get(f"/api/accounts/{aid}/balance").get_json()["balance"]


def test_account_starts_at_zero(client):
    aid = create_account(client)
    assert balance(client, aid) == "0.00"


def test_client_cannot_set_balance(client):
    response = client.post(
        "/api/accounts", json={"name": "Alice", "balance": 100}
    )
    assert response.status_code == 400


def test_credit_debit(client):
    aid = create_account(client)
    assert txn(client, aid, "credit", "100.00", "R1").status_code == 201
    assert txn(client, aid, "debit", "30.00", "R2").status_code == 201
    assert balance(client, aid) == "70.00"


def test_insufficient_funds(client):
    aid = create_account(client)
    response = txn(client, aid, "debit", "10", "R1")
    assert response.status_code == 422
    assert balance(client, aid) == "0.00"


def test_duplicate_reference(client):
    aid = create_account(client)
    assert txn(client, aid, "credit", "100", "R1").status_code == 201
    assert txn(client, aid, "credit", "200", "R1").status_code == 409
    assert balance(client, aid) == "100.00"


def test_reference_can_be_reused_on_another_account(client):
    first, second = create_account(client), create_account(client)
    assert txn(client, first, "credit", "10", "R1").status_code == 201
    assert txn(client, second, "credit", "10", "R1").status_code == 201


def test_filter_pagination(client):
    aid = create_account(client)
    txn(client, aid, "credit", "100", "R1")
    txn(client, aid, "debit", "20", "R2")
    response = client.get(
        f"/api/accounts/{aid}/transactions?type=credit&page=1&limit=1"
    )
    data = response.get_json()
    assert data["pagination"]["total_records"] == 1
    assert data["data"][0]["reference_id"] == "R1"


def test_missing_account(client):
    assert client.get("/api/accounts/missing/balance").status_code == 404


def test_invalid_amount(client):
    aid = create_account(client)
    assert txn(client, aid, "credit", "-1", "R1").status_code == 400


def test_final_balance(client):
    aid = create_account(client)
    for kind, amount, ref in [
        ("credit", "100", "1"),
        ("credit", "50", "2"),
        ("debit", "30", "3"),
        ("debit", "20", "4"),
    ]:
        assert txn(client, aid, kind, amount, ref).status_code == 201
    assert balance(client, aid) == "100.00"
