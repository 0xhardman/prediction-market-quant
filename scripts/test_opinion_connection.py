#!/usr/bin/env python3
"""测试 Opinion 连接"""

import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

async def main():
    print("=" * 50)
    print("测试 Opinion 连接")
    print("=" * 50)

    # 检查环境变量
    api_key = os.getenv("OPINION_API_KEY", "")
    http_proxy = os.getenv("HTTP_PROXY", "")
    https_proxy = os.getenv("HTTPS_PROXY", "")

    print(f"API Key: {'已设置' if api_key else '未设置'}")
    print(f"代理: {https_proxy if https_proxy else '未设置'}")

    # 设置代理
    proxies = None
    if https_proxy:
        proxies = {"http://": http_proxy, "https://": https_proxy}

    headers = {}
    if api_key:
        headers["apikey"] = api_key

    print("\n正在连接 Opinion API...")

    try:
        async with httpx.AsyncClient(timeout=30, proxies=proxies) as client:
            # 测试市场列表接口
            url = "https://openapi.opinion.trade/openapi/market"
            params = {"limit": 5, "status": "activated"}

            print(f"请求: GET {url}")
            resp = await client.get(url, params=params, headers=headers)

            print(f"状态码: {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    result = data.get("result", {})
                    markets = result.get("list", [])
                    print(f"\n获取到 {len(markets)} 个市场:")

                    for m in markets[:3]:
                        print(f"\n  市场 ID: {m.get('marketId')}")
                        print(f"  标题: {m.get('marketTitle', 'N/A')[:40]}...")
                        print(f"  状态: {m.get('status')}")
                        print(f"  Yes Token: {m.get('yesTokenId', 'N/A')[:30]}...")
                        print(f"  No Token: {m.get('noTokenId', 'N/A')[:30]}...")

                    print("\n" + "=" * 50)
                    print("Opinion 连接成功!")
                    print("=" * 50)
                else:
                    print(f"API 错误: {data.get('msg', data)}")

            elif resp.status_code == 403:
                data = resp.json()
                if data.get("errno") == 10403:
                    print("\n地区限制! Opinion API 禁止从当前地区访问")
                    print("请配置代理后重试:")
                    print("  HTTP_PROXY=http://your-proxy:port")
                    print("  HTTPS_PROXY=http://your-proxy:port")
                else:
                    print(f"403 错误: {data}")

            else:
                print(f"HTTP 错误: {resp.status_code}")
                print(resp.text[:500])

    except httpx.ConnectError as e:
        print(f"连接错误: {e}")
        print("\n可能原因:")
        print("  1. 网络问题")
        print("  2. 需要代理访问")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
