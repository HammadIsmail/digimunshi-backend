
import asyncio
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.database import get_engine, get_session_factory
from app.models.models import Base, Customer

async def test_all_features():
    print("=" * 60)
    print("DIGIMUNSHI AUTOMATED VERIFICATION SUITE")
    print("=" * 60)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        r = await client.get("/health")
        assert r.status_code == 200, f"Health check failed: {r.text}"
        print("[OK] 1. Health Check PASSED")

        # 2. Register & JWT Tokens
        import time
        phone = f"+92300{int(time.time()) % 10000000:07d}"
        reg_payload = {
            "owner_name": "عمران صاحب",
            "phone_number": phone,
            "pin": "1234"
        }
        r = await client.post("/auth/register", json=reg_payload)
        assert r.status_code == 201, f"Register failed: {r.text}"
        tokens = r.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        shop_id = tokens["shop_id"]
        assert access_token and refresh_token, "Tokens missing"
        print("[OK] 2. Registration & JWT Generation PASSED")

        # 3. JWT Persistence & /auth/me
        headers = {"Authorization": f"Bearer {access_token}"}
        r = await client.get("/auth/me", headers=headers)
        assert r.status_code == 200, f"/auth/me failed: {r.text}"
        shop_data = r.json()
        assert shop_data["owner_name"] == "عمران صاحب"
        print("[OK] 3. JWT Persistence & /auth/me PASSED")

        # 4. Token Refresh
        r = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert r.status_code == 200, f"Token refresh failed: {r.text}"
        new_tokens = r.json()
        new_access_token = new_tokens["access_token"]
        headers = {"Authorization": f"Bearer {new_access_token}"}
        r = await client.get("/auth/me", headers=headers)
        assert r.status_code == 200
        print("[OK] 4. JWT Refresh Token PASSED")

        # 5. Create customer via voice or direct db
        from uuid import UUID

        session_factory = get_session_factory()
        async with session_factory() as db:
            c = Customer(shop_id=UUID(shop_id), name="حماد", phone_number="+923009876543")
            db.add(c)
            await db.commit()
            await db.refresh(c)
            cust_id = str(c.id)

        # 6. Udhaar creation (1500 Rs)
        r = await client.post("/ledger/entries", headers=headers, json={
            "customer_id": cust_id,
            "amount": 1500.0,
            "entry_type": "udhaar",
            "description": "راشن سودا"
        })
        assert r.status_code == 200, f"Udhaar entry failed: {r.text}"
        udhaar_id = r.json()["id"]
        print("[OK] 5. Udhaar Entry Creation PASSED")

        # 7. Payment Confirmation Guardrail (> 1000 Rs without confirmation MUST fail)
        r = await client.post("/ledger/entries", headers=headers, json={
            "customer_id": cust_id,
            "amount": 1200.0,
            "entry_type": "wusool",
            "description": "کیش ادائیگی",
            "confirmed": False
        })
        assert r.status_code == 400, "Payment > 1000 without confirmation should have failed with 400!"
        assert "CONFIRMATION_REQUIRED" in r.text
        print("[OK] 6. Payment Confirmation Guardrail (> 1000 Rs blocked without confirmation) PASSED")

        # 8. Payment Confirmation Guardrail (> 1000 Rs with confirmation MUST succeed)
        r = await client.post("/ledger/entries", headers=headers, json={
            "customer_id": cust_id,
            "amount": 1200.0,
            "entry_type": "wusool",
            "description": "کیش ادائیگی",
            "confirmed": True
        })
        assert r.status_code == 200, f"Confirmed payment failed: {r.text}"
        wusool_id = r.json()["id"]
        print("[OK] 7. Payment Confirmation Guardrail (> 1000 Rs with confirmation) PASSED")

        # 9. Small payment (<= 1000 Rs) succeeds without requiring special confirmation
        r = await client.post("/ledger/entries", headers=headers, json={
            "customer_id": cust_id,
            "amount": 200.0,
            "entry_type": "wusool",
            "description": "بقایا کیش",
            "confirmed": False
        })
        assert r.status_code == 200
        print("[OK] 8. Small Payment (<= 1000 Rs without prompt) PASSED")

        # 10. Check Balance
        r = await client.get(f"/ledger/balance?customer_id={cust_id}", headers=headers)
        assert r.status_code == 200
        bal = r.json()["balance"]
        # 1500 - 1200 - 200 = 100
        assert bal == 100.0, f"Expected balance 100.0, got {bal}"
        print("[OK] 9. Balance Calculation PASSED (1500 - 1200 - 200 = Rs. 100)")

        # 11. Delete Guardrail 1: Single Entry Cancellation without confirmation MUST fail
        r = await client.post(f"/entries/{wusool_id}/cancel", headers=headers, json={"confirmed": False})
        assert r.status_code == 400, "Cancel entry without confirmation should fail!"
        print("[OK] 10. Single Entry Cancellation without confirmation blocked PASSED")

        # 12. Delete Guardrail 1: Single Entry Cancellation with confirmation MUST succeed
        r = await client.post(f"/entries/{wusool_id}/cancel", headers=headers, json={"confirmed": True})
        assert r.status_code == 200, f"Cancel entry with confirmation failed: {r.text}"
        print("[OK] 11. Single Entry Cancellation with confirmation PASSED")

        # 13. Delete Guardrail 2: Clear Khata without confirmation MUST fail
        r = await client.post(f"/customers/{cust_id}/clear", headers=headers, json={"confirmed": False})
        assert r.status_code == 400, "Clear khata without confirmation should fail!"
        print("[OK] 12. Clear Khata without confirmation blocked PASSED")

        # 14. Delete Guardrail 2: Clear Khata with confirmation MUST soft delete
        r = await client.post(f"/customers/{cust_id}/clear", headers=headers, json={"confirmed": True})
        assert r.status_code == 200, f"Clear khata with confirmation failed: {r.text}"
        print("[OK] 13. Clear Khata with confirmation PASSED")

        # 15. Verify Khata is cleared (balance 0)
        r = await client.get(f"/ledger/balance?customer_id={cust_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["balance"] == 0.0
        print("[OK] 14. Customer Khata Cleared Verified (Balance 0.0)")

    print("=" * 60)
    print("ALL 14 BACKEND & GUARDRAIL ASSERTIONS PASSED PERFECTLY!")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_all_features())
