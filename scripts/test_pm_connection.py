#!/usr/bin/env python3
"""测试 Polymarket 连接"""

import os
from dotenv import load_dotenv

load_dotenv()

def main():
    print("=" * 50)
    print("测试 Polymarket 连接")
    print("=" * 50)

    private_key = os.getenv("PM_PRIVATE_KEY", "")

    if not private_key:
        print("错误: PM_PRIVATE_KEY 未设置")
        return

    print(f"私钥: {private_key[:10]}...{private_key[-6:]}")

    try:
        from py_clob_client.client import ClobClient

        print("\n正在连接 Polymarket...")

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )

        print("正在派生 API 凭据...")
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"API Key: {creds.api_key[:20]}...")

        # 使用 SDK 获取有流动性的市场
        print("\n正在获取市场...")

        sampling = client.get_sampling_markets()
        print(f"get_sampling_markets 返回类型: {type(sampling)}")

        # 解析市场列表
        if isinstance(sampling, dict):
            markets = sampling.get('data', [])
        elif isinstance(sampling, list):
            markets = sampling
        else:
            markets = []

        print(f"找到 {len(markets)} 个市场")

        # 打印第一个市场的结构看看
        if markets:
            print(f"\n第一个市场的 keys: {markets[0].keys()}")
            m0 = markets[0]
            print(f"  condition_id: {m0.get('condition_id', 'N/A')}")
            print(f"  question: {m0.get('question', 'N/A')[:50] if m0.get('question') else 'N/A'}...")
            print(f"  closed: {m0.get('closed', 'N/A')}")
            print(f"  active: {m0.get('active', 'N/A')}")
            print(f"  tokens: {m0.get('tokens', 'N/A')}")

        # 找一个活跃的有订单簿的市场
        found = False
        for idx, m in enumerate(markets[:20]):
            condition_id = m.get('condition_id', '')
            question = m.get('question', 'N/A')
            tokens = m.get('tokens', [])
            closed = m.get('closed', False)

            print(f"\n[{idx}] 检查市场: {question[:40] if question else 'N/A'}...")
            print(f"    closed={closed}, tokens数量={len(tokens) if tokens else 0}")

            if closed:
                print(f"    -> 跳过: 已关闭")
                continue

            if not tokens:
                print(f"    -> 跳过: 无tokens")
                continue

            token_id = tokens[0].get('token_id', '')
            if not token_id:
                print(f"    -> 跳过: token_id为空")
                continue

            print(f"    token_id: {token_id[:40]}...")

            # 尝试获取订单簿
            try:
                book = client.get_order_book(token_id)

                # OrderBookSummary 是对象，用属性访问
                if hasattr(book, 'bids'):
                    bids = book.bids or []
                    asks = book.asks or []
                elif isinstance(book, dict):
                    bids = book.get('bids', [])
                    asks = book.get('asks', [])
                else:
                    print(f"    book类型: {type(book)}")
                    print(f"    book内容: {book}")
                    bids = []
                    asks = []

                print(f"    订单簿: bids={len(bids)}, asks={len(asks)}")

                if not bids or not asks:
                    print(f"    -> 跳过: 无买卖盘")
                    continue

                # 解析 bid/ask (可能是对象或字典)
                if hasattr(bids[0], 'price'):
                    best_bid_price = bids[0].price
                    best_bid_size = bids[0].size
                    best_ask_price = asks[0].price
                    best_ask_size = asks[0].size
                else:
                    best_bid_price = bids[0].get('price', 0)
                    best_bid_size = bids[0].get('size', 0)
                    best_ask_price = asks[0].get('price', 0)
                    best_ask_size = asks[0].get('size', 0)

                # 找到活跃市场！
                found = True
                print(f"\n{'='*60}")
                print(f"成功! 找到活跃市场!")
                print(f"{'='*60}")
                print(f"\nQuestion: {question}")
                print(f"Condition ID: {condition_id}")

                print(f"\nTokens:")
                for i, t in enumerate(tokens):
                    tid = t.get('token_id', '')
                    outcome = t.get('outcome', f'Token {i}')
                    print(f"  [{outcome}]: {tid}")

                print(f"\n订单簿:")
                print(f"  Best Bid: {best_bid_price} @ {best_bid_size}")
                print(f"  Best Ask: {best_ask_price} @ {best_ask_size}")

                spread = float(best_ask_price) - float(best_bid_price)
                print(f"  Spread: {spread:.4f}")
                break

            except Exception as e:
                print(f"    -> 错误: {e}")
                import traceback
                traceback.print_exc()
                continue

        if not found:
            print("\n未找到活跃市场，可能是API返回的都是旧市场")

        print("\n" + "=" * 50)
        print("Polymarket 连接测试完成!")
        print("=" * 50)

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
