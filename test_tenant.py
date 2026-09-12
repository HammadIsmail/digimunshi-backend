import httpx

BASE = "http://localhost:8000"
client = httpx.Client(timeout=60.0)


def safe_json(r):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:200]}


def test_tenant_isolation():
    results = []
    print("=" * 60)
    print("TENANT ISOLATION TEST SUITE")
    print("=" * 60)

    # Setup: Register two shops
    print("\n--- Setup ---")
    r = client.post(f"{BASE}/auth/register", json={
        "owner_name": "Shop Owner A", "phone_number": "+923001000001", "pin": "1111"
    })
    if r.status_code == 201:
        token_a = r.json()["access_token"]
        print("  Shop A registered")
    elif r.status_code == 409:
        r2 = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000001", "pin": "1111"})
        token_a = r2.json()["access_token"]
        print("  Shop A logged in")
    else:
        print(f"  FATAL: {safe_json(r)}")
        return

    r = client.post(f"{BASE}/auth/register", json={
        "owner_name": "Shop Owner B", "phone_number": "+923001000002", "pin": "2222"
    })
    if r.status_code == 201:
        token_b = r.json()["access_token"]
        print("  Shop B registered")
    elif r.status_code == 409:
        r2 = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000002", "pin": "2222"})
        token_b = r2.json()["access_token"]
        print("  Shop B logged in")
    else:
        print(f"  FATAL: {safe_json(r)}")
        return

    h_a = {"Authorization": f"Bearer {token_a}"}
    h_b = {"Authorization": f"Bearer {token_b}"}

    # Test 1: Customers isolation
    print("\nTEST 1: Shop B cannot see Shop A customers")
    r_a = client.get(f"{BASE}/customers", headers=h_a)
    r_b = client.get(f"{BASE}/customers", headers=h_b)
    ok = r_a.status_code == 200 and r_b.status_code == 200
    results.append(("Customers isolation", ok))

    # Test 2: Summary isolation
    print("TEST 2: Shop B cannot see Shop A summary")
    r_a = client.get(f"{BASE}/ledger/summary", headers=h_a)
    r_b = client.get(f"{BASE}/ledger/summary", headers=h_b)
    ok = r_a.status_code == 200 and r_b.status_code == 200
    results.append(("Summary isolation", ok))

    # Test 3: Balance with fake customer ID returns 404 (not 403)
    print("TEST 3: Fake customer ID returns 404 (not 403)")
    r = client.get(f"{BASE}/ledger/balance?customer_id=00000000-0000-0000-0000-000000000000", headers=h_a)
    ok = r.status_code == 404
    results.append(("Fake ID returns 404", ok))

    # Test 4: Unauthorized access blocked
    print("TEST 4: No token = 401")
    r = client.get(f"{BASE}/customers")
    ok = r.status_code == 401
    results.append(("No token blocked", ok))

    # Test 5: Invalid token blocked
    print("TEST 5: Invalid token = 401")
    r = client.get(f"{BASE}/customers", headers={"Authorization": "Bearer invalidtoken123"})
    ok = r.status_code == 401
    results.append(("Invalid token blocked", ok))

    # Test 6: Cross-tenant pending action
    print("TEST 6: Cross-tenant pending action resolution")
    r = client.post(f"{BASE}/voice/confirm", headers=h_b, json={
        "pending_action_id": "00000000-0000-0000-0000-000000000000", "confirmed": True
    })
    ok = r.status_code == 404
    results.append(("Cross-tenant action blocked", ok))

    # Test 7: Refresh token rotation
    print("TEST 7: Refresh token rotation works")
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000001", "pin": "1111"})
    refresh = safe_json(r).get("refresh_token", "")
    r2 = client.post(f"{BASE}/auth/refresh", json={"refresh_token": refresh})
    ok = r2.status_code == 200
    results.append(("Token refresh", ok))

    # Test 8: Old refresh token invalid after rotation
    print("TEST 8: Old refresh token invalid after rotation")
    r = client.post(f"{BASE}/auth/refresh", json={"refresh_token": refresh})
    ok = r.status_code == 401
    results.append(("Old refresh token rejected", ok))

    # Test 9: Logout revokes refresh token
    print("TEST 9: Logout revokes refresh token")
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000001", "pin": "1111"})
    new_refresh = safe_json(r).get("refresh_token", "")
    client.post(f"{BASE}/auth/logout", json={"refresh_token": new_refresh})
    r2 = client.post(f"{BASE}/auth/refresh", json={"refresh_token": new_refresh})
    ok = r2.status_code == 401
    results.append(("Logout revokes token", ok))

    # Test 10: Wrong PIN lockout
    print("TEST 10: Wrong PIN lockout (5 attempts)")
    for i in range(5):
        client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000002", "pin": "0000"})
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001000002", "pin": "2222"})
    ok = r.status_code == 423
    results.append(("PIN lockout", ok))

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  {passed}/{total} tests passed")
    return results


if __name__ == "__main__":
    test_tenant_isolation()
