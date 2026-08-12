import asyncio
import httpx
from app.config import settings

async def test_tenant(tenant_id):
    client_id = settings.AZURE_CLIENT_ID
    client_secret = settings.AZURE_CLIENT_SECRET
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(token_url, data=payload)
            print(f"Tenant '{tenant_id}': status {res.status_code}")
            if res.status_code == 200:
                print(f"  SUCCESS! Token acquired for tenant '{tenant_id}'")
                return tenant_id
            else:
                print(f"  Response: {res.text[:200]}")
        except Exception as e:
            print(f"  Error testing tenant '{tenant_id}': {e}")
    return None

async def main():
    tenants_to_test = [
        settings.AZURE_TENANT_ID,
        "biztechnosys.com",
        "biztechnosys.onmicrosoft.com",
        "organizations",
        "common"
    ]
    for t in tenants_to_test:
        working = await test_tenant(t)
        if working:
            print(f"\nFound working tenant ID: {working}")
            break

if __name__ == "__main__":
    asyncio.run(main())
