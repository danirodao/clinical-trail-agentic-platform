import asyncio
import os
import json
import asyncpg
from auth.reconciliation_service import ReconciliationService

async def main():
    dsn = os.environ.get("DATABASE_URL")
    
    async def init_connection(conn):
        await conn.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog"
        )
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog"
        )
    
    pool = await asyncpg.create_pool(dsn, init=init_connection)
    
    svc = ReconciliationService(pool)
    print("Running reconciliation...")
    results = await svc.reconcile_all()
    print(f"Results: {results}")
    
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
