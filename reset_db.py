import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import get_settings

async def clear_database():
    settings = get_settings()
    url = settings.DATABASE_URL
    if "?" in url:
        url = url.split("?")[0]
    
    engine = create_async_engine(
        url,
        echo=False,
        connect_args={"ssl": "require"}
    )

    async with engine.begin() as conn:
        print("Clearing all data from database...")
        # Order matters due to foreign keys if not CASCADE, but CASCADE handles it.
        # Truncate with CASCADE removes all rows cleanly and resets identities.
        await conn.execute(text("TRUNCATE TABLE ledger_entries, pending_actions, refresh_tokens, customers, shops CASCADE;"))
        print("Successfully truncated ledger_entries, pending_actions, refresh_tokens, customers, shops!")
        
        # Verify counts
        shop_count = await conn.execute(text("SELECT count(*) FROM shops;"))
        cust_count = await conn.execute(text("SELECT count(*) FROM customers;"))
        entry_count = await conn.execute(text("SELECT count(*) FROM ledger_entries;"))
        
        print(f"Current count -> Shops: {shop_count.scalar()}, Customers: {cust_count.scalar()}, Ledger Entries: {entry_count.scalar()}")

    await engine.dispose()
    print("Database reset completed successfully! Ready for fresh testing.")

if __name__ == "__main__":
    asyncio.run(clear_database())
