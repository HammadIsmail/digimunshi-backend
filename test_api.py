import httpx

BASE = "http://localhost:8000"
client = httpx.Client(timeout=60.0)

def safe_json(r):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:200]}

def test_all():
    results = []

    print("=" * 60)
    print("TEST 1: Health check")
    r = client.get(f"{BASE}/health")
    ok = r.status_code == 200
    results.append(("Health check", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} | {safe_json(r)}")

    print("\nTEST 2: Register Shop A")
    r = client.post(f"{BASE}/auth/register", json={
        "owner_name": "Bilal Ahmed",
        "phone_number": "+923001111111",
        "pin": "1234"
    })
    resp = safe_json(r)
    print(f"  Status: {r.status_code} | {resp}")
    if r.status_code == 201:
        shop_a_token = resp["access_token"]
        results.append(("Register Shop A", True, r.status_code, "OK"))
    elif r.status_code == 409:
        print("  Already exists, logging in...")
        r2 = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001111111", "pin": "1234"})
        d2 = safe_json(r2)
        shop_a_token = d2.get("access_token", "")
        results.append(("Register Shop A", True, 409, "Already exists, logged in"))
    else:
        results.append(("Register Shop A", False, r.status_code, resp))
        print("  FATAL: Cannot proceed without Shop A")
        return results

    print("\nTEST 3: Register Shop B")
    r = client.post(f"{BASE}/auth/register", json={
        "owner_name": "Ahmed Khan",
        "phone_number": "+923002222222",
        "pin": "5678"
    })
    resp = safe_json(r)
    print(f"  Status: {r.status_code} | {resp}")
    if r.status_code == 201:
        shop_b_token = resp["access_token"]
        results.append(("Register Shop B", True, r.status_code, "OK"))
    elif r.status_code == 409:
        r2 = client.post(f"{BASE}/auth/login", json={"phone_number": "+923002222222", "pin": "5678"})
        d2 = safe_json(r2)
        shop_b_token = d2.get("access_token", "")
        results.append(("Register Shop B", True, 409, "Already exists, logged in"))
    else:
        results.append(("Register Shop B", False, r.status_code, resp))
        return results

    h_a = {"Authorization": f"Bearer {shop_a_token}"}
    h_b = {"Authorization": f"Bearer {shop_b_token}"}

    print("\nTEST 4: Login Shop A")
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001111111", "pin": "1234"})
    ok = r.status_code == 200
    results.append(("Login Shop A", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code}")

    print("\nTEST 5: Login wrong PIN")
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001111111", "pin": "9999"})
    ok = r.status_code == 401
    results.append(("Login wrong PIN", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} (expected 401)")

    print("\nTEST 6: Token refresh")
    r = client.post(f"{BASE}/auth/login", json={"phone_number": "+923001111111", "pin": "1234"})
    refresh = safe_json(r).get("refresh_token", "")
    r2 = client.post(f"{BASE}/auth/refresh", json={"refresh_token": refresh})
    ok = r2.status_code == 200
    results.append(("Token refresh", ok, r2.status_code, safe_json(r2)))
    print(f"  Status: {r2.status_code}")

    print("\nTEST 7: Get customers Shop A (empty)")
    r = client.get(f"{BASE}/customers", headers=h_a)
    ok = r.status_code == 200 and r.json() == []
    results.append(("Get customers A", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} | {safe_json(r)}")

    print("\nTEST 8: Get summary Shop A (zeros)")
    r = client.get(f"{BASE}/ledger/summary", headers=h_a)
    resp = safe_json(r)
    ok = r.status_code == 200 and resp.get("total_outstanding") == 0
    results.append(("Get summary A", ok, r.status_code, resp))
    print(f"  Status: {r.status_code} | {resp}")

    print("\nTEST 9: Unauthorized (no token)")
    r = client.get(f"{BASE}/customers")
    ok = r.status_code == 403
    results.append(("Unauthorized access", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} (expected 403)")

    print("\nTEST 10: Tenant isolation - Shop B sees only own data")
    r = client.get(f"{BASE}/customers", headers=h_b)
    ok = r.status_code == 200 and r.json() == []
    results.append(("Tenant isolation", ok, r.status_code, "Empty for Shop B"))
    print(f"  Status: {r.status_code} | {safe_json(r)}")

    print("\nTEST 11: Duplicate phone registration")
    r = client.post(f"{BASE}/auth/register", json={
        "owner_name": "Dupe", "phone_number": "+923001111111", "pin": "0000"
    })
    ok = r.status_code == 409
    results.append(("Duplicate registration", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} (expected 409)")

    print("\nTEST 12: Balance with fake customer ID")
    r = client.get(f"{BASE}/ledger/balance?customer_id=00000000-0000-0000-0000-000000000000", headers=h_a)
    ok = r.status_code == 404
    results.append(("Fake customer ID", ok, r.status_code, safe_json(r)))
    print(f"  Status: {r.status_code} (expected 404)")

    print("\n" + "=" * 60)
    print("RESULTS:")
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    for name, ok, status, detail in results:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name} (HTTP {status})")
    print(f"\n  {passed}/{total} tests passed")

    return results

if __name__ == "__main__":
    test_all()
