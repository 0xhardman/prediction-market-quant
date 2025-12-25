#!/usr/bin/env python3
"""
Predict.fun 市场查询工具

支持三种模式：
1. 交互式浏览 - 按 category 分组选择市场
2. URL 解析 - 从链接提取信息，通过 API 或网页获取市场详情
3. Market ID - 直接通过 market_id 查询
"""

import asyncio
import json
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
        """获取单个市场详情（通过API）"""
        client = await self._get_client()
        try:
            resp = await client.get(f"/markets/{market_id}")
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception as e:
            print(f"API 获取市场 {market_id} 失败: {e}")
            return {}

    async def fetch_market_from_web(self, url: str) -> dict | None:
        """从网页抓取市场信息（fallback方案）"""
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as web_client:
                resp = await web_client.get(url)
                resp.raise_for_status()
                html = resp.text

                # 方法1: 从HTML中提取JSON数据（Next.js __NEXT_DATA__）
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        market_data = data.get('props', {}).get('pageProps', {}).get('market', {})
                        if market_data and market_data.get('id'):
                            return market_data
                    except json.JSONDecodeError:
                        pass

                # 方法2: 从Next.js streaming data中提取（新版Next.js App Router）
                # 查找 self.__next_f.push 中的市场数据
                market_id_match = re.search(r'"id["\s:,]+(\d+)', html)
                if market_id_match:
                    market_id = int(market_id_match.group(1))
                    # 尝试提取问题
                    question_match = re.search(r'"question["\s:,]+"([^"]+gold[^"]+)"', html, re.IGNORECASE)
                    question = question_match.group(1) if question_match else ""

                    if market_id:
                        return {"id": market_id, "question": question}

                return None
        except Exception as e:
            print(f"从网页抓取失败: {e}")
            return None

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
    """从 URL 解析模式（支持 API 搜索 + 网页 fallback）"""
    # 提取 slug 或 market_id
    # URL 格式: https://predict.fun/market/what-price-will-btc-hit-in-2025
    match = re.search(r"predict\.fun/market/([^/?]+)", url)
    if not match:
        print(f"无法从 URL 提取 slug: {url}")
        print("支持的格式: https://predict.fun/market/<slug>")
        return

    slug = match.group(1)
    print()
    print(f"从 URL 提取 slug: {slug}")

    # 步骤1: 尝试从API市场列表搜索
    print()
    print("步骤 1/3: 正在搜索API市场列表...")
    markets = await lookup.fetch_markets()

    # 从 slug 提取关键词进行模糊匹配
    stop_words = ["will", "the", "and", "for", "above", "below", "close", "reach", "2024", "2025", "2026", "in"]
    keywords = [w for w in slug.lower().split("-") if len(w) > 2 and w not in stop_words]
    print(f"   搜索关键词: {keywords}")

    # 先尝试精确匹配 categorySlug
    matching_markets = [m for m in markets if m.get("categorySlug") == slug]

    if not matching_markets and keywords:
        # 尝试模糊匹配：所有关键词都出现在 title 或 question 中
        matching_markets = [
            m for m in markets
            if all(kw in m.get("title", "").lower() or kw in m.get("question", "").lower() for kw in keywords)
        ]

    if matching_markets:
        print(f"   ✓ 在API中找到 {len(matching_markets)} 个匹配的市场")
        market = matching_markets[0]
        market_id = market.get("id")
        print()
        print("正在获取市场详情...")
        market = await lookup.fetch_market(market_id)
        bid, ask = await lookup.get_market_price(market_id)
        lookup.print_market_details(market, bid, ask)
        return

    # 步骤2: API搜索失败，尝试从网页抓取
    print(f"   ✗ API中未找到匹配市场（共搜索 {len(markets)} 个市场）")
    print()
    print("步骤 2/3: 尝试从网页抓取市场信息...")

    web_data = await lookup.fetch_market_from_web(url)
    if web_data and web_data.get("id"):
        market_id = web_data.get("id")
        print(f"   ✓ 从网页获取到 Market ID: {market_id}")

        # 步骤3: 使用market_id从API获取完整数据
        print()
        print("步骤 3/3: 通过 Market ID 从 API 获取详情...")
        market = await lookup.fetch_market(market_id)
        if market:
            print("   ✓ 成功获取市场详情")
            bid, ask = await lookup.get_market_price(market_id)
            lookup.print_market_details(market, bid, ask)
            return
        else:
            # API获取失败，使用网页数据
            print("   ⚠ API 获取失败，使用网页数据")
            bid, ask = await lookup.get_market_price(market_id)
            lookup.print_market_details(web_data, bid, ask)
            return

    # 完全失败
    print("   ✗ 从网页抓取失败")
    print()
    print(f"❌ 无法找到市场: {slug}")
    print()
    print("可能的原因:")
    print("  1. 市场已被删除")
    print("  2. URL 格式不正确")
    print("  3. 网页结构已更改")
    print()
    print("提示: 如果您知道 Market ID，可以直接运行:")
    print(f"  uv run python {sys.argv[0]} <market_id>")


async def market_id_mode(lookup: PFLookup, market_id: int):
    """直接通过 Market ID 查询模式"""
    print()
    print(f"正在查询 Market ID: {market_id}")
    print()

    market = await lookup.fetch_market(market_id)
    if market and market.get("id"):
        print("✓ 成功获取市场详情")
        bid, ask = await lookup.get_market_price(market_id)
        lookup.print_market_details(market, bid, ask)
    else:
        print(f"❌ 未找到 Market ID {market_id}")
        print()
        print("请检查:")
        print("  1. Market ID 是否正确")
        print("  2. 市场是否存在")


async def main():
    lookup = PFLookup()
    lookup.load_env()

    try:
        if len(sys.argv) > 1:
            arg = sys.argv[1]

            # 判断是 URL 还是 Market ID
            if arg.isdigit():
                # 纯数字，当作 Market ID
                await market_id_mode(lookup, int(arg))
            elif "predict.fun" in arg:
                # URL 模式
                await url_mode(lookup, arg)
            else:
                print("错误: 参数必须是 Market ID (数字) 或 Predict.fun URL")
                print()
                print("用法:")
                print(f"  {sys.argv[0]} <market_id>")
                print(f"  {sys.argv[0]} <predict.fun_url>")
                print()
                print("示例:")
                print(f"  {sys.argv[0]} 538")
                print(f"  {sys.argv[0]} https://predict.fun/market/will-btc-reach-100k")
        else:
            await interactive_mode(lookup)
    finally:
        await lookup.close()


if __name__ == "__main__":
    asyncio.run(main())
