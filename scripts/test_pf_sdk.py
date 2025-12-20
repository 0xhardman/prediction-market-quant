#!/usr/bin/env python3
"""使用 predict-sdk 测试 Predict.fun 认证和市场查询"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct


async def main():
    private_key = os.getenv('PM_PRIVATE_KEY')  # 使用 PM 钱包
    api_key = os.getenv('PREDICT_FUN_API_KEY')

    if not private_key or not api_key:
        print("错误: 缺少环境变量 PM_PRIVATE_KEY 或 PREDICT_FUN_API_KEY")
        return

    account = Account.from_key(private_key)
    print(f'钱包: {account.address}')
    print(f'API Key: {api_key[:8]}...{api_key[-4:]}')

    base_url = "https://api.predict.fun/v1"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        # 1. 获取签名消息
        print("\n" + "=" * 60)
        print("步骤 1: 获取认证消息")
        print("=" * 60)
        resp = await client.get("/auth/message")
        data = resp.json()
        message = data["data"]["message"]
        print(f'消息: {message[:60]}...')

        # 2. 签名
        print("\n" + "=" * 60)
        print("步骤 2: 签名消息")
        print("=" * 60)
        msg = encode_defunct(text=message)
        signed = account.sign_message(msg)
        signature = '0x' + signed.signature.hex()
        print(f'签名: {signature[:50]}...')

        # 3. 获取 JWT - 字段名是 'signer' 不是 'walletAddress'
        print("\n" + "=" * 60)
        print("步骤 3: 获取 JWT")
        print("=" * 60)
        auth_payload = {
            "message": message,
            "signature": signature,
            "signer": account.address,  # 关键: 字段名是 'signer'
        }
        print(f'请求: signer={account.address}')

        auth_resp = await client.post("/auth", json=auth_payload)
        auth_data = auth_resp.json()

        if not auth_data.get("success"):
            print(f"认证失败: {auth_data}")
            return

        jwt = auth_data["data"]["token"]
        print(f'JWT: {jwt[:50]}...')

        # 添加 Authorization header
        client.headers["Authorization"] = f"Bearer {jwt}"

        # 4. 获取活跃市场
        print("\n" + "=" * 60)
        print("步骤 4: 获取市场信息")
        print("=" * 60)
        resp = await client.get("/markets", params={"limit": 20})
        markets_data = resp.json()
        markets = markets_data.get("data", [])
        active = [m for m in markets if m.get("status") == "REGISTERED"]

        print(f'活跃市场数: {len(active)}')
        for m in active[:5]:
            title = m.get("title", "N/A")[:40]
            market_id = m.get("id")
            print(f'  [{market_id}] {title}...')

        # 5. 检查开放订单
        print("\n" + "=" * 60)
        print("步骤 5: 检查开放订单")
        print("=" * 60)
        orders_resp = await client.get("/orders")
        if orders_resp.status_code == 200:
            orders = orders_resp.json().get("data", [])
            print(f'开放订单数: {len(orders)}')
        else:
            print(f'错误: {orders_resp.text[:200]}')

        print("\n" + "=" * 60)
        print("✅ 测试完成!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
