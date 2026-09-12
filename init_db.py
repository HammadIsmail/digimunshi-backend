import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import get_settings
from app.models.models import Base


async def init_db():
    settings = get_settings()
    url = settings.DATABASE_URL
    # Remove ssl=require from URL - asyncpg handles it via connect_args
    if "?" in url:
        url = url.split("?")[0]
    
    engine = create_async_engine(
        url,
        echo=False,
        connect_args={"ssl": "require"}
    )

    async with engine.begin() as conn:
        # Create tables
        await conn.run_sync(Base.metadata.create_all)

        # Enable extensions
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # Enable Row-Level Security
        for table in ["customers", "ledger_entries", "pending_actions", "refresh_tokens"]:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))

            # Create tenant isolation policy
            await conn.execute(text(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation' AND tablename = '{table}'
                    ) THEN
                        CREATE POLICY tenant_isolation ON {table}
                            USING (shop_id = current_setting('app.current_shop_id', true)::uuid);
                    END IF;
                END $$;
            """))

    await engine.dispose()
    print("Database initialized successfully!")


if __name__ == "__main__":
    asyncio.run(init_db())
