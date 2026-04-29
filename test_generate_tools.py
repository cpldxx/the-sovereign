"""
Manual test for POST /domains/{name}/generate-tools

Run the server first:
  python main.py

Then:
  python test_generate_tools.py [domain_name]
"""

import asyncio
import sys
import httpx


async def test(domain_name: str = "quant"):
    base = "http://localhost:8080"

    print(f"Testing generate-tools for domain: {domain_name}")
    print("This may take several minutes while OpenHands generates the code...")
    print()

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{base}/domains/{domain_name}/generate-tools")
        print(f"Status: {resp.status_code}")
        data = resp.json()
        print(f"Response: {data}")

        if resp.status_code == 200:
            path = data["path"]
            print(f"\nGenerated {data['lines']} lines -> {path}")
            print("\n--- Generated tools.py ---")
            with open(path) as f:
                print(f.read())


if __name__ == "__main__":
    domain = sys.argv[1] if len(sys.argv) > 1 else "quant"
    asyncio.run(test(domain))
