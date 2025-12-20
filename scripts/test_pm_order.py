#!/usr/bin/env python3
"""测试 Polymarket 下单功能"""

import os
from dotenv import load_dotenv

load_dotenv()


def main():
    print("=" * 60)
    print("Polymarket 下单测试")
    print("=" * 60)

    private_key = os.getenv("PM_PRIVATE_KEY", "")
    proxy_address = os.getenv("PM_PROXY_ADDRESS", "")
    api_key = os.getenv("PM_API_KEY", "")
    api_secret = os.getenv("PM_API_SECRET", "")
    api_passphrase = os.getenv("PM_API_PASSPHRASE", "")

    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置")
        return

    print(f"Private Key: {private_key[:10]}...{private_key[-6:]}")
    print(f"Proxy Address: {proxy_address}")
    print(f"API Key: {api_key[:20]}...")

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        # 尝试不同的 signature_type
        for sig_type in [2, 1, 0]:
            sig_name = {0: "EOA", 1: "POLY_PROXY", 2: "POLY_GNOSIS_SAFE"}[sig_type]
            print(f"\n{'='*60}")
            print(f"尝试 signature_type={sig_type} ({sig_name})")
            print("=" * 60)

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=sig_type,
                funder=proxy_address if sig_type > 0 else None,
            )

            # 使用已有凭据
            creds = ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
            client.set_api_creds(creds)

            # 获取活跃市场
            print("获取活跃市场...")
            try:
                markets = client.get_sampling_markets()
                data = markets.get("data", []) if isinstance(markets, dict) else markets
            except Exception as e:
                print(f"获取市场失败: {e}")
                continue

            token_id = None
            market_name = None
            for m in data[:10]:
                if m.get("closed"):
                    continue
                tokens = m.get("tokens", [])
                if tokens:
                    token_id = tokens[0].get("token_id", "")
                    market_name = m.get("question", "N/A")[:40]
                    if token_id:
                        break

            if not token_id:
                print("未找到活跃市场")
                continue

            print(f"市场: {market_name}...")
            print(f"Token: {token_id[:40]}...")

            # 下单测试 (最小 size=5)
            print("\n下单 (BUY @ 0.01, size=5)...")
            order_args = OrderArgs(
                price=0.01,
                size=5.0,
                side=BUY,
                token_id=token_id,
            )

            try:
                signed_order = client.create_order(order_args)
                print("订单已签名")

                resp = client.post_order(signed_order, OrderType.GTC)
                print(f"下单成功! 响应: {resp}")

                order_id = resp.get("orderID", "")
                if order_id:
                    print(f"\n取消订单 {order_id}...")
                    try:
                        cancel = client.cancel(order_id)
                        print(f"取消成功: {cancel}")
                    except Exception as e:
                        print(f"取消失败: {e}")

                print(f"\n成功! signature_type={sig_type} ({sig_name}) 可用")
                return

            except Exception as e:
                error_msg = str(e)
                if "invalid signature" in error_msg.lower():
                    print(f"签名验证失败")
                elif "not enough balance" in error_msg.lower():
                    print(f"余额/授权不足")
                else:
                    print(f"下单失败: {e}")

        print("\n" + "=" * 60)
        print("所有 signature_type 都失败了")
        print("可能需要在 Polymarket 网站上检查账户设置")
        print("=" * 60)

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
