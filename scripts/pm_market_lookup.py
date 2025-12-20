#!/usr/bin/env python3
"""
Polymarket 市场查询工具

支持双向查询：
1. 从网站 URL/slug → condition_id, token_ids
2. 从 condition_id → 网站 URL, token_ids
3. 从 token_id → 网站 URL, condition_id
"""

import sys
import re
import httpx

CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


def extract_slug_from_url(url: str) -> str | None:
    """从 Polymarket URL 提取 market slug"""
    # https://polymarket.com/event/xxx/market-slug-123?tid=xxx
    # 处理 shell 转义字符 (如 \? 变成 ?)
    url = url.replace("\\?", "?").replace("\\=", "=")
    match = re.search(r'polymarket\.com/event/[^/]+/([^?]+)', url)
    if match:
        slug = match.group(1).rstrip("\\")  # 移除可能残留的反斜杠
        return slug
    return None


def lookup_by_slug(slug: str) -> dict | None:
    """用 slug 查询市场信息"""
    resp = httpx.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        if data:
            return data[0]
    return None


def lookup_by_condition_id(condition_id: str) -> dict | None:
    """用 condition_id 查询市场信息"""
    resp = httpx.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


def lookup_by_token_id(token_id: str) -> dict | None:
    """用 token_id 查询市场信息

    注意：CLOB API 不支持直接按 token_id 查询市场。
    这里通过获取订单簿来验证 token 有效性，然后搜索活跃市场。
    """
    # 方法1: 先验证 token 是否有效（通过获取订单簿）
    book_resp = httpx.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=10)
    if book_resp.status_code != 200:
        return None

    # 方法2: 搜索活跃市场找匹配的 token
    # 获取采样市场列表搜索
    try:
        resp = httpx.get(f"{CLOB_API}/sampling-markets", timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            markets = data.get("data", []) if isinstance(data, dict) else data
            for m in markets:
                tokens = m.get("tokens", [])
                for t in tokens:
                    if t.get("token_id") == token_id:
                        return m
    except Exception:
        pass

    # 方法3: 返回基本信息（仅 token 验证通过）
    return {
        "token_id_valid": True,
        "token_id": token_id,
        "question": "(Token 有效，但未找到对应市场详情)",
        "condition_id": "N/A",
        "market_slug": "N/A",
        "note": "建议使用 URL 或 condition_id 查询获取完整信息"
    }


def get_tokens_from_clob(condition_id: str) -> list[dict]:
    """从 CLOB API 获取 token 详情"""
    resp = httpx.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        tokens = data.get("tokens", [])
        return tokens
    return []


def print_market_info(market: dict, source: str = ""):
    """打印市场信息"""
    print(f"\n{'=' * 60}")
    print(f"查询结果 (来源: {source})")
    print("=" * 60)

    # 特殊情况：仅 token 验证通过
    if market.get("token_id_valid"):
        print(f"\n✓ Token ID 有效，可用于交易")
        print(f"\nToken ID: {market.get('token_id', 'N/A')}")
        print(f"\n⚠ 未找到对应市场详情")
        print(f"  建议: 使用网站 URL 或 condition_id 查询获取完整信息")
        return

    # 从不同 API 来源提取字段
    question = market.get("question", "N/A")
    condition_id = market.get("conditionId") or market.get("condition_id", "N/A")
    slug = market.get("slug") or market.get("market_slug", "N/A")

    print(f"\n问题: {question}")
    print(f"\nCondition ID:\n  {condition_id}")
    print(f"\nSlug:\n  {slug}")
    print(f"\n网站链接:\n  https://polymarket.com/event/_/{slug}")

    # Token IDs
    tokens = market.get("tokens", [])
    clob_token_ids = market.get("clobTokenIds", [])
    outcomes = market.get("outcomes", [])

    print("\nToken IDs:")
    if tokens:
        for t in tokens:
            outcome = t.get("outcome", "?")
            token_id = t.get("token_id", "N/A")
            print(f"  [{outcome}]: {token_id}")
    elif clob_token_ids:
        for i, tid in enumerate(clob_token_ids):
            outcome = outcomes[i] if i < len(outcomes) else f"Token {i}"
            print(f"  [{outcome}]: {tid}")

    # 额外信息
    print("\n其他信息:")
    print(f"  活跃: {market.get('active', 'N/A')}")
    print(f"  已关闭: {market.get('closed', 'N/A')}")

    # 结算条件
    description = market.get("description", "")
    if description:
        print("\n" + "-" * 60)
        print("结算条件:")
        print("-" * 60)
        print(description)

    # 配置片段
    print("\n" + "-" * 60)
    print("config.yaml 配置片段:")
    print("-" * 60)

    yes_token = ""
    no_token = ""
    if tokens:
        for t in tokens:
            if t.get("outcome") == "Yes":
                yes_token = t.get("token_id", "")
            elif t.get("outcome") == "No":
                no_token = t.get("token_id", "")
    elif clob_token_ids and len(clob_token_ids) >= 2:
        if outcomes and "Yes" in outcomes:
            yes_idx = outcomes.index("Yes")
            no_idx = outcomes.index("No") if "No" in outcomes else 1 - yes_idx
            yes_token = clob_token_ids[yes_idx]
            no_token = clob_token_ids[no_idx]
        else:
            yes_token = clob_token_ids[0]
            no_token = clob_token_ids[1]

    print(f"""
  - name: "{question[:50]}..."
    polymarket:
      condition_id: "{condition_id}"
      yes_token_id: "{yes_token}"
      no_token_id: "{no_token}"
    opinion:
      market_id: ""  # 需要手动填写
      yes_token_id: ""
      no_token_id: ""
""")


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python pm_market_lookup.py <url>")
        print("  python pm_market_lookup.py <slug>")
        print("  python pm_market_lookup.py <condition_id>")
        print("  python pm_market_lookup.py <token_id>")
        print()
        print("示例:")
        print("  python pm_market_lookup.py https://polymarket.com/event/xxx/market-slug")
        print("  python pm_market_lookup.py will-trump-release-the-epstein-files-by-december-19-771")
        print("  python pm_market_lookup.py 0xac9c6628a5398bb...")
        print("  python pm_market_lookup.py 97631444429136963...")
        return

    query = sys.argv[1].strip()

    # 判断输入类型
    if "polymarket.com" in query:
        # URL
        slug = extract_slug_from_url(query)
        if not slug:
            print("无法从 URL 提取 slug")
            return
        print(f"从 URL 提取 slug: {slug}")
        market = lookup_by_slug(slug)
        if market:
            # 补充 tokens 信息
            condition_id = market.get("conditionId", "")
            if condition_id:
                tokens = get_tokens_from_clob(condition_id)
                if tokens:
                    market["tokens"] = tokens
            print_market_info(market, "Gamma API (by slug)")
        else:
            print("未找到市场")

    elif query.startswith("0x"):
        # Condition ID
        print(f"查询 condition_id: {query}")
        market = lookup_by_condition_id(query)
        if market:
            print_market_info(market, "CLOB API (by condition_id)")
        else:
            print("未找到市场")

    elif query.isdigit() or (len(query) > 40 and query[0].isdigit()):
        # Token ID (纯数字，很长)
        print(f"查询 token_id: {query[:40]}...")
        market = lookup_by_token_id(query)
        if market:
            print_market_info(market, "CLOB API (by token_id)")
        else:
            print("未找到市场")

    else:
        # 当作 slug
        print(f"查询 slug: {query}")
        market = lookup_by_slug(query)
        if market:
            condition_id = market.get("conditionId", "")
            if condition_id:
                tokens = get_tokens_from_clob(condition_id)
                if tokens:
                    market["tokens"] = tokens
            print_market_info(market, "Gamma API (by slug)")
        else:
            print("未找到市场")


if __name__ == "__main__":
    main()
