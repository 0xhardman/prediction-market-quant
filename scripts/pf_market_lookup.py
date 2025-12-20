#!/usr/bin/env python3
"""
Predict.fun 市场查询工具

支持两种模式：
1. 交互式浏览 - 按 category 分组选择市场
2. URL 解析 - 从链接提取 slug，列出该 event 下的市场
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct


class PFLookup:
    """Predict.fun API 客户端"""

    def __init__(self):
        self.base_url = "https://api.predict.fun/v1"
        self.api_key = ""
        self.private_key = ""
        self.jwt = None
        self._client = None

    def load_env(self):
        """从 .env 文件加载环境变量"""
        env_file = Path(__file__).parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

        self.api_key = os.getenv("PREDICT_FUN_API_KEY", "")
        self.private_key = os.getenv("PM_PRIVATE_KEY", "")

        if not self.api_key or not self.private_key:
            print("错误: 请设置 PREDICT_FUN_API_KEY 和 PM_PRIVATE_KEY 环境变量")
            sys.exit(1)

    async def authenticate(self):
        """获取 JWT token"""
        account = Account.from_key(self.private_key)

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key},
            timeout=30,
        ) as client:
            # 获取签名消息
            resp = await client.get("/auth/message")
            message = resp.json()["data"]["message"]

            # 签名
            msg = encode_defunct(text=message)
            signed = account.sign_message(msg)
            signature = "0x" + signed.signature.hex()

            # 认证
            auth_resp = await client.post(
                "/auth",
                json={
                    "message": message,
                    "signature": signature,
                    "signer": account.address,
                },
            )

            if not auth_resp.json().get("success"):
                print(f"认证失败: {auth_resp.json()}")
                sys.exit(1)

            self.jwt = auth_resp.json()["data"]["token"]

    async def _get_client(self) -> httpx.AsyncClient:
        """获取已认证的 HTTP 客户端"""
        if self._client is None:
            await self.authenticate()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "X-API-Key": self.api_key,
                    "Authorization": f"Bearer {self.jwt}",
                },
                timeout=30,
            )
        return self._client

    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_markets(self, limit: int = 200) -> list:
        """获取所有活跃市场（从 /markets 和 /categories 两个端点）"""
        client = await self._get_client()
        all_markets = []
        seen_ids = set()

        # 1. 从 /markets 端点获取
        offset = 0
        while True:
            resp = await client.get(
                "/markets",
                params={"limit": limit, "offset": offset},
            )
            data = resp.json().get("data", [])
            if not data:
                break
            for m in data:
                if m.get("id") not in seen_ids:
                    all_markets.append(m)
                    seen_ids.add(m.get("id"))
            if len(data) < limit:
                break
            offset += limit

        # 2. 从 /categories 端点获取（包含更多市场）
        try:
            resp = await client.get("/categories", params={"limit": 500})
            categories = resp.json().get("data", [])
            for cat in categories:
                for m in cat.get("markets", []):
                    if m.get("id") not in seen_ids:
                        all_markets.append(m)
                        seen_ids.add(m.get("id"))
        except Exception:
            pass

        return all_markets

    async def fetch_market(self, market_id: int) -> dict:
        """获取单个市场详情"""
        client = await self._get_client()
        resp = await client.get(f"/markets/{market_id}")
        return resp.json().get("data", {})

    async def fetch_orderbook(self, market_id: int) -> dict:
        """获取订单簿"""
        client = await self._get_client()
        resp = await client.get(f"/markets/{market_id}/orderbook")
        return resp.json().get("data", {})

    def group_by_category(self, markets: list) -> dict:
        """按 categorySlug 分组市场"""
        categories = {}
        for m in markets:
            slug = m.get("categorySlug", "other")
            if slug not in categories:
                categories[slug] = []
            categories[slug].append(m)
        return categories

    async def get_market_price(self, market_id: int) -> tuple:
        """获取市场当前价格 (bid, ask)"""
        try:
            book = await self.fetch_orderbook(market_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            bid = float(bids[0][0]) if bids else 0
            ask = float(asks[0][0]) if asks else 1
            return bid, ask
        except Exception:
            return 0, 1

    def print_market_details(self, market: dict, bid: float = 0, ask: float = 1):
        """打印市场详情和 config 片段"""
        outcomes = market.get("outcomes", [])
        yes_token = next(
            (o["onChainId"] for o in outcomes if o["name"] == "Yes"), ""
        )
        no_token = next(
            (o["onChainId"] for o in outcomes if o["name"] == "No"), ""
        )

        market_id = market.get("id")
        question = market.get("question", "N/A")
        description = market.get("description", "")

        print()
        print("=" * 60)
        print(f"Market {market_id}: {question}")
        print("=" * 60)
        print()
        print(f"Market ID: {market_id}")
        print(f"YES Token: {yes_token}")
        print(f"NO Token:  {no_token}")
        print()
        fee_bps = market.get("feeRateBps", 200)
        fee_pct = fee_bps / 100
        print(f"手续费: {fee_pct:.1f}% (Taker Fee)")
        print(f"当前价格: YES Bid {bid:.4f} | Ask {ask:.4f}")

        # 结算条件
        if description:
            print()
            print("-" * 60)
            print("结算条件:")
            print("-" * 60)
            print(description)

        print()
        print("-" * 60)
        print("config.yaml 配置片段:")
        print("-" * 60)
        print(f"""
  predict_fun:
    market_id: {market_id}
    yes_token_id: "{yes_token}"
    no_token_id: "{no_token}"
""")


async def interactive_mode(lookup: PFLookup):
    """交互式选择模式"""
    print()
    print("=" * 60)
    print("Predict.fun 市场查询")
    print("=" * 60)
    print()
    print("正在获取市场列表...")

    markets = await lookup.fetch_markets()
    categories = lookup.group_by_category(markets)

    # 按市场数量排序
    sorted_cats = sorted(categories.items(), key=lambda x: -len(x[1]))

    print()
    print("可用 Events (按市场数量排序):")
    print()

    cat_list = []
    for i, (slug, ms) in enumerate(sorted_cats, 1):
        cat_list.append((slug, ms))
        # 显示第一个市场的问题作为描述
        desc = ms[0].get("question", slug)[:50] if ms else slug
        print(f"  [{i}] {slug}")
        print(f"      {desc}... ({len(ms)} markets)")

    print()
    try:
        choice = input("请选择 Event (输入数字, q 退出): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice.lower() == "q":
        return

    try:
        cat_idx = int(choice) - 1
        if cat_idx < 0 or cat_idx >= len(cat_list):
            print("无效选择")
            return
    except ValueError:
        print("请输入数字")
        return

    slug, ms = cat_list[cat_idx]

    print()
    print(f"📊 Event: {slug}")
    print()
    print("Markets:")

    for m in ms:
        mid = m.get("id")
        title = m.get("title", "N/A")
        question = m.get("question", "")[:40]
        print(f"  [{mid}] {title} - {question}...")

    print()
    try:
        market_choice = input("请选择 Market ID (输入数字, q 退出): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if market_choice.lower() == "q":
        return

    try:
        market_id = int(market_choice)
    except ValueError:
        print("请输入数字")
        return

    # 获取详情
    print()
    print("正在获取市场详情...")
    market = await lookup.fetch_market(market_id)
    bid, ask = await lookup.get_market_price(market_id)
    lookup.print_market_details(market, bid, ask)


async def url_mode(lookup: PFLookup, url: str):
    """从 URL 解析模式"""
    # 提取 slug
    # URL 格式: https://predict.fun/market/what-price-will-btc-hit-in-2025
    match = re.search(r"predict\.fun/market/([^/?]+)", url)
    if not match:
        print(f"无法从 URL 提取 slug: {url}")
        print("支持的格式: https://predict.fun/market/<slug>")
        return

    slug = match.group(1)
    print()
    print(f"从 URL 提取 slug: {slug}")
    print()
    print("正在获取市场列表...")

    markets = await lookup.fetch_markets()

    # 从 slug 提取关键词进行模糊匹配
    # 例如 "will-gold-close-above-4400-in-2025" -> ["gold", "4400"]
    stop_words = ["will", "the", "and", "for", "above", "below", "close", "reach", "2024", "2025", "2026"]
    keywords = [w for w in slug.lower().split("-") if len(w) > 2 and w not in stop_words]

    print(f"搜索关键词: {keywords}")

    # 先尝试精确匹配 categorySlug
    matching_markets = [
        m for m in markets
        if m.get("categorySlug") == slug
    ]

    if not matching_markets:
        # 尝试模糊匹配：任意关键词出现在 title 或 question 中
        matching_markets = [
            m for m in markets
            if any(kw in m.get("title", "").lower() or kw in m.get("question", "").lower() for kw in keywords)
        ]

    if not matching_markets:
        print(f"未找到匹配的市场: {slug}")
        print()
        print(f"共获取到 {len(markets)} 个活跃市场，但没有包含关键词 {keywords} 的市场")
        print()
        print("这个市场可能:")
        print("  1. 已经结算/关闭")
        print("  2. 尚未创建")
        print("  3. 关键词不匹配")
        print()
        print("部分可用市场:")
        for m in markets[:10]:
            print(f"  [{m.get('id')}] {m.get('question', '')[:60]}...")
        return

    print()
    print(f"找到 {len(matching_markets)} 个匹配的市场:")
    print()

    for m in matching_markets:
        mid = m.get("id")
        question = m.get("question", "")[:60]
        status = m.get("status", "")
        print(f"  [{mid}] {question}... ({status})")

    if len(matching_markets) == 1:
        # 只有一个市场，直接显示详情
        market = matching_markets[0]
        market_id = market.get("id")
        print()
        print("正在获取市场详情...")
        market = await lookup.fetch_market(market_id)
        bid, ask = await lookup.get_market_price(market_id)
        lookup.print_market_details(market, bid, ask)
        return

    print()
    try:
        market_choice = input("请选择 Market ID (输入数字, q 退出): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if market_choice.lower() == "q":
        return

    try:
        market_id = int(market_choice)
    except ValueError:
        print("请输入数字")
        return

    # 获取详情
    print()
    print("正在获取市场详情...")
    market = await lookup.fetch_market(market_id)
    bid, ask = await lookup.get_market_price(market_id)
    lookup.print_market_details(market, bid, ask)


async def main():
    lookup = PFLookup()
    lookup.load_env()

    try:
        if len(sys.argv) > 1:
            url = sys.argv[1]
            await url_mode(lookup, url)
        else:
            await interactive_mode(lookup)
    finally:
        await lookup.close()


if __name__ == "__main__":
    asyncio.run(main())
