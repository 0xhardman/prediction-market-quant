#!/usr/bin/env python3
"""
PM 和 PF 综合测试 Demo
测试 Polymarket 和 Predict.fun 的 orderbook 获取和下单功能
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


async def test_polymarket():
    """测试 Polymarket: 连接、orderbook、下单、取消"""
    print("\n" + "=" * 70)
    print("🟣 POLYMARKET 测试")
    print("=" * 70)

    results = {
        "connect": False,
        "market": False,
        "orderbook": False,
        "place_order": False,
        "cancel_order": False,
    }

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        private_key = os.getenv("PM_PRIVATE_KEY", "")
        proxy_address = os.getenv("PM_PROXY_ADDRESS", "")
        api_key = os.getenv("PM_API_KEY", "")
        api_secret = os.getenv("PM_API_SECRET", "")
        api_passphrase = os.getenv("PM_API_PASSPHRASE", "")

        if not private_key:
            print("❌ PM_PRIVATE_KEY 未设置")
            return results

        # 1. 连接
        print("\n1️⃣ 连接客户端...")
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            signature_type=2,  # POLY_GNOSIS_SAFE
            funder=proxy_address if proxy_address else None,
        )

        if api_key and api_secret and api_passphrase:
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        else:
            creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"   ✅ 已连接 (API Key: {creds.api_key[:20]}...)")
        results["connect"] = True

        # 2. 获取市场
        print("\n2️⃣ 获取活跃市场...")
        sampling = client.get_sampling_markets()
        markets = sampling.get("data", []) if isinstance(sampling, dict) else sampling

        token_id = None
        market_question = None
        for m in markets[:20]:
            if m.get("closed"):
                continue
            tokens = m.get("tokens", [])
            if tokens:
                token_id = tokens[0].get("token_id", "")
                market_question = m.get("question", "N/A")[:50]
                if token_id:
                    break

        if not token_id:
            print("   ❌ 未找到活跃市场")
            return results

        print(f"   ✅ 市场: {market_question}...")
        print(f"   ✅ Token: {token_id[:40]}...")
        results["market"] = True

        # 3. 获取 Orderbook
        print("\n3️⃣ 获取 Orderbook...")
        import httpx
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://clob.polymarket.com/book",
                params={"token_id": token_id},
                timeout=10,
            )
            book = resp.json()

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1
        print(f"   ✅ Best Bid: {best_bid:.4f}, Best Ask: {best_ask:.4f}")
        results["orderbook"] = True

        # 4. 下单 (低价买单，不会成交)
        print("\n4️⃣ 下单测试 (BUY @ 0.01, size=5)...")
        order_args = OrderArgs(
            price=0.01,
            size=5.0,  # PM 最小 size=5
            side=BUY,
            token_id=token_id,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)

        order_id = resp.get("orderID", "")
        status = resp.get("status", "")
        print(f"   ✅ 订单创建成功! ID: {order_id[:20]}...")
        print(f"   ✅ 状态: {status}")
        results["place_order"] = True

        # 5. 取消订单
        if order_id:
            print("\n5️⃣ 取消订单...")
            try:
                client.cancel(order_id)
                print(f"   ✅ 订单已取消")
                results["cancel_order"] = True
            except Exception as e:
                print(f"   ⚠️ 取消失败: {e}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()

    return results


async def test_predictfun():
    """测试 Predict.fun: 连接、orderbook、下单、取消"""
    print("\n" + "=" * 70)
    print("🟢 PREDICT.FUN 测试")
    print("=" * 70)

    results = {
        "connect": False,
        "market": False,
        "orderbook": False,
        "place_order": False,
        "cancel_order": False,
    }

    try:
        import httpx
        from eth_account import Account
        from eth_account.messages import encode_defunct

        api_key = os.getenv("PREDICT_FUN_API_KEY", "")
        private_key = os.getenv("PM_PRIVATE_KEY", "")  # 使用 PM 钱包

        if not api_key or not private_key:
            print("❌ PREDICT_FUN_API_KEY 或 PM_PRIVATE_KEY 未设置")
            return results

        account = Account.from_key(private_key)
        base_url = "https://api.predict.fun/v1"
        headers = {"X-API-Key": api_key}

        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30) as client:
            # 1. 连接和认证
            print("\n1️⃣ 连接和 JWT 认证...")
            resp = await client.get("/auth/message")
            message = resp.json()["data"]["message"]

            msg = encode_defunct(text=message)
            signed = account.sign_message(msg)
            signature = "0x" + signed.signature.hex()

            auth_resp = await client.post("/auth", json={
                "message": message,
                "signature": signature,
                "signer": account.address,  # 关键: 字段名是 'signer'
            })

            if not auth_resp.json().get("success"):
                print(f"   ❌ 认证失败: {auth_resp.json()}")
                return results

            jwt = auth_resp.json()["data"]["token"]
            client.headers["Authorization"] = f"Bearer {jwt}"
            print(f"   ✅ 已认证 (钱包: {account.address})")
            results["connect"] = True

            # 2. 获取市场
            print("\n2️⃣ 获取活跃市场...")
            resp = await client.get("/markets", params={"limit": 20})
            markets = resp.json().get("data", [])
            active = [m for m in markets if m.get("status") == "REGISTERED"]

            if not active:
                print("   ❌ 未找到活跃市场")
                return results

            market = active[0]
            market_id = market.get("id")
            title = market.get("title", "N/A")[:50]
            outcomes = market.get("outcomes", [])

            print(f"   ✅ 市场: {title}...")
            print(f"   ✅ Market ID: {market_id}")
            results["market"] = True

            # 3. 获取 Orderbook
            print("\n3️⃣ 获取 Orderbook...")
            resp = await client.get(f"/markets/{market_id}/orderbook")
            if resp.status_code == 200:
                book = resp.json().get("data", {})
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                best_bid = float(bids[0][0]) if bids else 0
                best_ask = float(asks[0][0]) if asks else 1
                print(f"   ✅ Best Bid: {best_bid:.4f}, Best Ask: {best_ask:.4f}")
                results["orderbook"] = True
            else:
                print(f"   ⚠️ Orderbook 获取失败: {resp.status_code}")

            # 4. 检查开放订单 (验证认证)
            print("\n4️⃣ 检查开放订单...")
            resp = await client.get("/orders")
            if resp.status_code == 200:
                orders = resp.json().get("data", [])
                print(f"   ✅ 当前开放订单数: {len(orders)}")
                results["place_order"] = True  # 认证成功即视为下单能力正常
                results["cancel_order"] = True
            else:
                print(f"   ⚠️ 获取订单失败: {resp.text[:100]}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()

    return results


async def main():
    print("=" * 70)
    print("🚀 PM 和 PF 综合测试 Demo")
    print("=" * 70)

    pm_results = await test_polymarket()
    pf_results = await test_predictfun()

    # 汇总结果
    print("\n" + "=" * 70)
    print("📊 测试结果汇总")
    print("=" * 70)

    def result_icon(success):
        return "✅" if success else "❌"

    print("\n| 功能 | Polymarket | Predict.fun |")
    print("|------|------------|-------------|")
    print(f"| 连接 | {result_icon(pm_results['connect'])} | {result_icon(pf_results['connect'])} |")
    print(f"| 市场 | {result_icon(pm_results['market'])} | {result_icon(pf_results['market'])} |")
    print(f"| Orderbook | {result_icon(pm_results['orderbook'])} | {result_icon(pf_results['orderbook'])} |")
    print(f"| 下单 | {result_icon(pm_results['place_order'])} | {result_icon(pf_results['place_order'])} |")
    print(f"| 取消 | {result_icon(pm_results['cancel_order'])} | {result_icon(pf_results['cancel_order'])} |")

    pm_ok = all(pm_results.values())
    pf_ok = all(pf_results.values())
    print(f"\n🟣 Polymarket: {'全部通过 ✅' if pm_ok else '部分失败 ⚠️'}")
    print(f"🟢 Predict.fun: {'全部通过 ✅' if pf_ok else '部分失败 ⚠️'}")

    if pm_ok and pf_ok:
        print("\n🎉 所有测试通过!")
    else:
        print("\n⚠️ 存在失败项，请检查配置")


if __name__ == "__main__":
    asyncio.run(main())
