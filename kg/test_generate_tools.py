"""
Manual test for POST /domains/{name}/generate-tools

Run the server first:
  uv run python main.py

Then:
  uv run python test_generate_tools.py [domain_name]
"""

import asyncio
import sys

import httpx

BASE = "http://localhost:8080"


async def test(domain_name: str = "quant"):
    print(f"Testing generate-tools for domain: {domain_name}")
    print("This may take several minutes while OpenHands generates the code...\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{BASE}/domains/{domain_name}/generate-tools")
        print(f"Status: {resp.status_code} {resp.json()}")
        resp.raise_for_status()
        job_id = resp.json()["job_id"]

        while True:
            await asyncio.sleep(10)
            job = (await client.get(f"{BASE}/domains/generate-tools/status/{job_id}")).json()
            print(f"  {job['status']}")
            if job["status"] != "generating":
                break

    print(f"\nResult: {job}")
    if job["status"] == "done":
        print(f"\n--- domains/{domain_name}/tools.py ---")
        with open(f"domains/{domain_name}/tools.py") as f:
            print(f.read())


if __name__ == "__main__":
    asyncio.run(test(sys.argv[1] if len(sys.argv) > 1 else "quant"))
