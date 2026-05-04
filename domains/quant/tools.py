"""Domain tools for quant: Cryptocurrency trading and analysis
import json
import httpx
from pydantic_ai import RunContext


async def collect(ctx: RunContext, query: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get('https://api.kraken.com/0/public/Ticker?pair=XBTUSD')
        return json.dumps(response.json())


async def analyze(ctx: RunContext, data: str) -> str:
    data_dict = json.loads(data)
    result = {
        'trading': {
            'last_trade_price': data_dict['result']['XBTUSD']['c'][0],
            'volume_24h': data_dict['result']['XBTUSD']['v'][1]
        },
        'bitcoin': {
            'ask': data_dict['result']['XBTUSD']['a'][0],
            'bid': data_dict['result']['XBTUSD']['b'][0]
        }
    }
    return json.dumps(result)


async def summarize(ctx: RunContext, data: str) -> str:
    analysis = json.loads(data)
    summary = {
        'title': 'Bitcoin Trading Analysis',
        'key_facts': [
            f'Last Trade Price: {analysis['trading']['last_trade_price']}',
            f'24h Volume: {analysis['trading']['volume_24h']}',
            f'Ask Price: {analysis['bitcoin']['ask']}',
            f'Bid Price: {analysis['bitcoin']['bid']}'
        ],
        'domain': 'quant'
    }
    return json.dumps(summary)

"""