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


async def get_jwt(client: httpx.AsyncClient, private_key: str, predict_account: str = None) -> str:
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
    signature = signed.signature.hex()

    # 确保签名以 0x 开头
    if not signature.startswith("0x"):
        signature = "0x" + signature

    print(f"签名钱包: {account.address}")
    print(f"签名: {signature[:40]}...")

    # 3. 获取 JWT - 尝试不同的请求格式
    auth_payloads = [
        # 格式1: 使用 predict account 地址
        {
            "message": message,
            "signature": signature,
            "walletAddress": predict_account if predict_account else account.address,
        },
        # 格式2: 只用签名钱包地址
        {
            "message": message,
            "signature": signature,
            "walletAddress": account.address,
        },
        # 格式3: 添加 address 字段
        {
            "message": message,
            "signature": signature,
            "address": account.address,
        },
    ]

    for i, payload in enumerate(auth_payloads):
        print(f"\n尝试认证格式 {i+1}: {list(payload.keys())}")
        print(f"  walletAddress: {payload.get('walletAddress', payload.get('address', 'N/A'))}")

        auth_resp = await client.post("/auth", json=payload)
        print(f"  响应: {auth_resp.status_code}")

        if auth_resp.status_code == 200:
            auth_data = auth_resp.json()
            if auth_data.get("success"):
                jwt = auth_data["data"].get("token", "")
                print(f"  JWT 获取成功!")
                return jwt
            else:
                print(f"  API 错误: {auth_data.get('message', auth_data)}")
        else:
            print(f"  错误: {auth_resp.text[:100]}")

    return ""


async def main():
    print("=" * 60)
    print("Predict.fun 下单测试")
    print("=" * 60)

    api_key = os.getenv("PREDICT_FUN_API_KEY", "")
    private_key = os.getenv("PM_PRIVATE_KEY", "")  # 使用 PM 私钥

    # Predict 账户地址 (BNB 链上的资金地址)
    predict_account = "0xe60eFb319e300c244Db089DEC83Bf3A12e9205e9"

    if not api_key:
        print("错误: PREDICT_FUN_API_KEY 未设置")
        return

    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置")
        return

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print(f"Predict Account: {predict_account}")

    base_url = "https://api.predict.fun/v1"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        # 获取 JWT
        print("\n" + "=" * 60)
        print("获取 JWT Token")
        print("=" * 60)

        jwt = await get_jwt(client, private_key, predict_account)

        if not jwt:
            print("\n无法获取 JWT Token")
            print("\n可能的原因:")
            print("1. 签名钱包需要是 Predict.fun 的登录钱包")
            print("2. 需要使用 Predict Account 的私钥签名")
            print("3. 认证 API 参数格式不对")
            return

        # 添加 Authorization header
        client.headers["Authorization"] = f"Bearer {jwt}"

        # 获取活跃市场
        print("\n" + "=" * 60)
        print("获取活跃市场")
        print("=" * 60)

        resp = await client.get("/markets", params={"limit": 10})
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


if __name__ == "__main__":
    asyncio.run(main())
