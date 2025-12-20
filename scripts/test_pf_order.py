#!/usr/bin/env python3
"""测试 Predict.fun 下单功能"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

load_dotenv()


async def get_jwt(client: httpx.AsyncClient, private_key: str) -> str:
    """获取 Predict.fun JWT token"""

    # 1. 获取签名消息
    resp = await client.get("/auth/message")
    if resp.status_code != 200:
        print(f"获取认证消息失败: {resp.status_code}")
        return ""

    data = resp.json()
    if not data.get("success"):
        print(f"API 错误: {data}")
        return ""

    message = data["data"]["message"]
    print(f"签名消息: {message[:60]}...")

    # 2. 签名消息
    account = Account.from_key(private_key)
    msg = encode_defunct(text=message)
    signed = account.sign_message(msg)
    signature = "0x" + signed.signature.hex()

    print(f"签名钱包: {account.address}")
    print(f"签名: {signature[:40]}...")

    # 3. 获取 JWT - 字段名是 'signer' 不是 'walletAddress'
    auth_payload = {
        "message": message,
        "signature": signature,
        "signer": account.address,  # 关键: 字段名是 'signer'
    }

    auth_resp = await client.post("/auth", json=auth_payload)
    if auth_resp.status_code != 200:
        print(f"认证失败: {auth_resp.status_code} - {auth_resp.text[:100]}")
        return ""

    auth_data = auth_resp.json()
    if not auth_data.get("success"):
        print(f"认证 API 错误: {auth_data}")
        return ""

    jwt = auth_data["data"].get("token", "")
    print(f"JWT Token: {jwt[:40]}..." if jwt else "JWT Token: 获取失败")
    return jwt


async def main():
    print("=" * 60)
    print("Predict.fun 下单测试")
    print("=" * 60)

    api_key = os.getenv("PREDICT_FUN_API_KEY", "")
    private_key = os.getenv("PM_PRIVATE_KEY", "")  # 使用 PM 私钥

    if not api_key:
        print("错误: PREDICT_FUN_API_KEY 未设置")
        return

    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置")
        return

    account = Account.from_key(private_key)
    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print(f"钱包: {account.address}")

    base_url = "https://api.predict.fun/v1"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        # 获取 JWT
        print("\n" + "=" * 60)
        print("获取 JWT Token")
        print("=" * 60)

        jwt = await get_jwt(client, private_key)

        if not jwt:
            print("\n无法获取 JWT Token")
            return

        # 添加 Authorization header
        client.headers["Authorization"] = f"Bearer {jwt}"

        # 获取活跃市场
        print("\n" + "=" * 60)
        print("获取活跃市场")
        print("=" * 60)

        resp = await client.get("/markets", params={"limit": 20})
        if resp.status_code != 200:
            print(f"获取市场失败: {resp.status_code}")
            return

        data = resp.json()
        markets = data.get("data", [])
        active = [m for m in markets if m.get("status") == "REGISTERED"]

        if not active:
            print("未找到活跃市场")
            return

        market = active[0]
        print(f"市场: {market.get('title', 'N/A')[:50]}...")
        print(f"Market ID: {market.get('id')}")

        # 检查开放订单
        print("\n检查开放订单...")
        orders_resp = await client.get("/orders")
        if orders_resp.status_code == 200:
            orders_data = orders_resp.json()
            print(f"当前开放订单: {len(orders_data.get('data', []))}")
        else:
            print(f"获取订单失败: {orders_resp.text[:100]}")

        print("\n" + "=" * 60)
        print("✅ 认证测试完成!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
