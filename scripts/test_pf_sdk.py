#!/usr/bin/env python3
"""使用 predict-sdk 测试 Predict.fun 下单"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from predict_sdk import OrderBuilder, ChainId, ADDRESSES_BY_CHAIN_ID, generate_order_salt, Side
from predict_sdk.logger import get_logger
from eth_account import Account


async def main():
    private_key = os.getenv('PM_PRIVATE_KEY')
    api_key = os.getenv('PREDICT_FUN_API_KEY')
    predict_account = '0xe60eFb319e300c244Db089DEC83Bf3A12e9205e9'

    if not private_key or not api_key:
        print("错误: 缺少环境变量")
        return

    account = Account.from_key(private_key)
    print(f'Signer (Login Wallet): {account.address}')
    print(f'Predict account (资金账户): {predict_account}')
    print(f'API Key: {api_key[:8]}...{api_key[-4:]}')

    addresses = ADDRESSES_BY_CHAIN_ID[ChainId.BNB_MAINNET]
    logger = get_logger('INFO')

    builder = OrderBuilder(
        chain_id=ChainId.BNB_MAINNET,
        precision=4,
        addresses=addresses,
        generate_salt_fn=generate_order_salt,
        logger=logger,
        signer=account,
        predict_account=predict_account,
    )

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

        # 2. 使用 signer wallet 标准签名 (NOT predict account signing)
        print("\n" + "=" * 60)
        print("步骤 2: 使用 Signer Wallet 标准 EIP-191 签名")
        print("=" * 60)
        from eth_account.messages import encode_defunct
        msg = encode_defunct(text=message)
        signed = account.sign_message(msg)
        signature = '0x' + signed.signature.hex()
        print(f'签名: {signature[:40]}...')

        # 3. 获取 JWT - 使用 signer wallet address
        print("\n" + "=" * 60)
        print("步骤 3: 获取 JWT (使用 Signer Wallet Address)")
        print("=" * 60)
        auth_payload = {
            "message": message,
            "signature": signature,
            "walletAddress": account.address,  # 使用 signer wallet, 不是 predict account
        }
        print(f'请求体: walletAddress={account.address}')

        auth_resp = await client.post("/auth", json=auth_payload)
        print(f'响应状态: {auth_resp.status_code}')
        auth_data = auth_resp.json()
        print(f'响应: {auth_data}')

        if not auth_data.get("success"):
            print("\n认证失败!")
            return

        jwt = auth_data["data"]["token"]
        print(f'JWT: {jwt[:40]}...')

        # 添加 Authorization header
        client.headers["Authorization"] = f"Bearer {jwt}"

        # 4. 获取活跃市场
        print("\n" + "=" * 60)
        print("步骤 4: 获取市场信息")
        print("=" * 60)
        resp = await client.get("/markets", params={"limit": 5})
        markets_data = resp.json()
        markets = markets_data.get("data", [])
        active = [m for m in markets if m.get("status") == "REGISTERED"]

        if not active:
            print("未找到活跃市场")
            return

        market = active[0]
        market_id = market.get("id")
        title = market.get("title", "N/A")[:50]
        outcomes = market.get("outcomes", [])

        print(f'市场: {title}...')
        print(f'Market ID: {market_id}')
        print(f'Outcomes: {len(outcomes)}')

        for i, o in enumerate(outcomes):
            print(f'  [{i}] {o.get("name")}: {o.get("onChainId", "N/A")[:30]}...')

        # 5. 检查开放订单
        print("\n" + "=" * 60)
        print("步骤 5: 检查开放订单")
        print("=" * 60)
        orders_resp = await client.get("/orders")
        print(f'订单响应: {orders_resp.status_code}')
        if orders_resp.status_code == 200:
            orders = orders_resp.json().get("data", [])
            print(f'开放订单数: {len(orders)}')
        else:
            print(f'错误: {orders_resp.text[:200]}')

        print("\n" + "=" * 60)
        print("测试完成!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
