#!/usr/bin/env python3
"""测试 Polymarket 和 Predict.fun 下单功能"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import httpx

load_dotenv()


async def test_polymarket_order():
    """测试 Polymarket 下单"""
    print("\n" + "=" * 60)
    print("测试 Polymarket 下单")
    print("=" * 60)

    private_key = os.getenv("PM_PRIVATE_KEY", "")
    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置")
        return False

    proxy_address = os.getenv("PM_PROXY_ADDRESS", "")

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.clob_types import OrderType, OrderArgs

        print("正在连接 Polymarket...")
        print(f"Proxy Address: {proxy_address}" if proxy_address else "Proxy Address: 未配置")

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=1,  # POLY_GNOSIS_SAFE
            funder=proxy_address if proxy_address else None,
        )

        # 使用 .env 中已有的 API credentials
        api_key = os.getenv("PM_API_KEY", "")
        api_secret = os.getenv("PM_API_SECRET", "")
        api_passphrase = os.getenv("PM_API_PASSPHRASE", "")

        if api_key and api_secret and api_passphrase:
            print("使用已有 API 凭据...")
            from py_clob_client.clob_types import ApiCreds
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        else:
            print("正在派生 API 凭据...")
            creds = client.create_or_derive_api_creds()

        client.set_api_creds(creds)
        print(f"API Key: {creds.api_key[:20]}...")

        # 获取一个活跃市场
        print("\n正在获取活跃市场...")
        sampling = client.get_sampling_markets()
        markets = sampling.get('data', []) if isinstance(sampling, dict) else sampling

        token_id = None
        for m in markets[:20]:
            if m.get('closed'):
                continue
            tokens = m.get('tokens', [])
            if tokens:
                token_id = tokens[0].get('token_id', '')
                if token_id:
                    print(f"使用市场: {m.get('question', 'N/A')[:50]}...")
                    print(f"Token ID: {token_id[:40]}...")
                    break

        if not token_id:
            print("错误: 未找到活跃市场")
            return False

        # 下一个低价买单 (不会成交)
        print("\n下单测试 (BUY @ 0.01)...")
        order_args = OrderArgs(
            price=0.01,
            size=1.0,
            side=BUY,
            token_id=token_id,
        )

        signed_order = client.create_order(order_args)
        print(f"订单已签名")

        # 使用 GTC (Good Till Cancel) 而不是 FOK，这样可以看到订单状态
        resp = client.post_order(signed_order, OrderType.GTC)
        print(f"下单响应: {resp}")

        order_id = resp.get("orderID", "")
        status = resp.get("status", "")
        print(f"订单 ID: {order_id}")
        print(f"订单状态: {status}")

        if order_id:
            # 取消订单
            print("\n取消订单...")
            try:
                cancel_resp = client.cancel(order_id)
                print(f"取消响应: {cancel_resp}")
            except Exception as e:
                print(f"取消失败 (可能已成交或过期): {e}")

        print("\nPolymarket 下单测试完成!")
        return True

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        return False


async def get_pf_jwt(client: httpx.AsyncClient, private_key: str) -> str:
    """获取 Predict.fun JWT token"""
    from eth_account import Account
    from eth_account.messages import encode_defunct

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

    print(f"钱包地址: {account.address}")
    print(f"签名: {signature[:40]}...")

    # 3. 获取 JWT
    auth_resp = await client.post("/auth", json={
        "message": message,
        "signature": signature,
        "walletAddress": account.address,
    })

    if auth_resp.status_code != 200:
        print(f"认证失败: {auth_resp.status_code} - {auth_resp.text[:200]}")
        return ""

    auth_data = auth_resp.json()
    if not auth_data.get("success"):
        print(f"认证 API 错误: {auth_data}")
        return ""

    jwt = auth_data["data"].get("token", "")
    print(f"JWT Token: {jwt[:40]}..." if jwt else "JWT Token: 获取失败")
    return jwt


async def test_predictfun_order():
    """测试 Predict.fun 下单"""
    print("\n" + "=" * 60)
    print("测试 Predict.fun 下单")
    print("=" * 60)

    api_key = os.getenv("PREDICT_FUN_API_KEY", "")
    if not api_key:
        print("错误: PREDICT_FUN_API_KEY 未设置")
        return False

    # 使用 PM 私钥 (假设同一个钱包)
    private_key = os.getenv("PM_PRIVATE_KEY", "")
    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置 (用于签名认证)")
        return False

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")

    base_url = "https://api.predict.fun/v1"
    headers = {"X-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
        # 获取 JWT
        print("\n正在获取 JWT Token...")
        jwt = await get_pf_jwt(client, private_key)
        if not jwt:
            print("错误: 无法获取 JWT Token")
            return False

        # 添加 Authorization header
        client.headers["Authorization"] = f"Bearer {jwt}"

        # 获取一个活跃市场
        print("\n正在获取活跃市场...")
        resp = await client.get("/markets", params={"limit": 20})
        if resp.status_code != 200:
            print(f"获取市场失败: {resp.status_code}")
            return False

        data = resp.json()
        if not data.get("success"):
            print(f"API 错误: {data}")
            return False

        markets = data.get("data", [])
        active_markets = [m for m in markets if m.get("status") == "REGISTERED"]

        if not active_markets:
            print("错误: 未找到活跃市场")
            return False

        market = active_markets[0]
        market_id = market.get("id")
        outcomes = market.get("outcomes", [])

        if not outcomes:
            print("错误: 市场没有 outcomes")
            return False

        # 获取第一个 outcome 的 onChainId
        token_id = outcomes[0].get("onChainId", "")
        print(f"使用市场: {market.get('title', 'N/A')[:50]}...")
        print(f"Market ID: {market_id}")
        print(f"Token ID (onChainId): {token_id[:40]}..." if token_id else "Token ID: N/A")

        # 查看开放订单 (测试认证是否生效)
        print("\n检查开放订单 (测试认证)...")
        orders_resp = await client.get("/orders")
        print(f"订单列表响应: {orders_resp.status_code}")
        if orders_resp.status_code == 200:
            orders_data = orders_resp.json()
            print(f"开放订单数: {len(orders_data.get('data', []))}")
        else:
            print(f"获取订单失败: {orders_resp.text[:200]}")

        # 下一个低价买单 (不会成交)
        # 注意: Predict.fun 下单可能需要链上签名，这里先测试 API 调用
        print("\n下单测试 (BUY @ 0.01)...")
        order_data = {
            "marketId": market_id,
            "outcomeIndex": 0,
            "side": "BUY",
            "price": "0.01",
            "size": "1",
            "type": "LIMIT",
        }

        print(f"请求体: {order_data}")
        order_resp = await client.post("/orders", json=order_data)
        print(f"下单响应状态: {order_resp.status_code}")
        print(f"下单响应: {order_resp.text[:500]}")

        if order_resp.status_code == 200:
            order_result = order_resp.json()
            if order_result.get("success"):
                order_info = order_result.get("data", {})
                order_id = order_info.get("id", order_info.get("orderId", ""))
                print(f"订单 ID: {order_id}")

                if order_id:
                    # 取消订单
                    print("\n取消订单...")
                    cancel_resp = await client.delete(f"/orders/{order_id}")
                    print(f"取消响应: {cancel_resp.status_code} - {cancel_resp.text[:200]}")

                print("\nPredict.fun 下单测试完成!")
                return True
            else:
                print(f"下单失败: {order_result}")
        else:
            print(f"下单请求失败: {order_resp.status_code}")

        return False


async def main():
    print("=" * 60)
    print("PM 和 Predict.fun 下单功能测试")
    print("=" * 60)
    print("\n注意: 使用极低价格 (0.01) 下单，确保不会实际成交")

    pm_ok = await test_polymarket_order()
    pf_ok = await test_predictfun_order()

    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    print(f"Polymarket: {'OK' if pm_ok else 'FAILED'}")
    print(f"Predict.fun: {'OK' if pf_ok else 'FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
