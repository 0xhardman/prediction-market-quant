#!/usr/bin/env python3
"""测试 Predict.fun 连接"""

import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import httpx

load_dotenv()


async def main():
    api_key = os.getenv("PREDICT_FUN_API_KEY", "")

    if not api_key:
        print("错误: 未设置 PREDICT_FUN_API_KEY 环境变量")
        print("请在 .env 文件中添加: PREDICT_FUN_API_KEY=your_api_key")
        return

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print()

    base_url = "https://api.predict.fun/v1"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        # 1. 获取市场列表
        print("=== 获取市场列表 ===")
        try:
            resp = await client.get("/markets", params={"limit": 5})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    markets = data.get("data", [])
                    print(f"找到 {len(markets)} 个市场")
                    print()

                    # 找活跃市场 (REGISTERED)
                    active_markets = [m for m in markets if m.get("status") == "REGISTERED"]
                    print(f"其中活跃市场: {len(active_markets)} 个")
                    print()

                    for m in active_markets[:3]:
                        print(f"  ID: {m.get('id')}")
                        print(f"  标题: {m.get('title', 'N/A')[:50]}...")
                        print(f"  状态: {m.get('status')}")
                        outcomes = m.get("outcomes", [])
                        for o in outcomes:
                            print(f"    - {o.get('name')}: {o.get('onChainId', 'N/A')[:20]}...")
                        print()

                    # 2. 尝试获取订单簿 (用 market_id)
                    if active_markets:
                        first_market = active_markets[0]
                        market_id = first_market.get("id")
                        print("=== 获取订单簿 ===")
                        print(f"Market ID: {market_id}")
                        print(f"Market: {first_market.get('title', 'N/A')}")

                        ob_resp = await client.get(f"/markets/{market_id}/orderbook")
                        if ob_resp.status_code == 200:
                            ob_data = ob_resp.json()
                            if ob_data.get("success"):
                                book = ob_data.get("data", {})
                                bids = book.get("bids", [])
                                asks = book.get("asks", [])
                                print(f"Bids: {len(bids)} 档")
                                print(f"Asks: {len(asks)} 档")
                                if bids:
                                    # Sort bids by price desc
                                    sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)
                                    print(f"Best Bid: price={sorted_bids[0][0]}, size={sorted_bids[0][1]}")
                                if asks:
                                    # Sort asks by price asc
                                    sorted_asks = sorted(asks, key=lambda x: x[0])
                                    print(f"Best Ask: price={sorted_asks[0][0]}, size={sorted_asks[0][1]}")
                            else:
                                print(f"订单簿 API 错误: {ob_data}")
                        else:
                            print(f"订单簿请求失败: {ob_resp.status_code}")
                            print(ob_resp.text[:200])
                else:
                    print(f"API 错误: {data}")
            else:
                print(f"请求失败: {resp.status_code}")
                print(resp.text[:200])
        except Exception as e:
            print(f"错误: {e}")

    print()
    print("=== 连接测试完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
