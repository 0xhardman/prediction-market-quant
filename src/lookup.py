"""市场查询模块 - 抽象自 pm_market_lookup.py 和 pf_market_lookup.py"""

from dataclasses import dataclass

import httpx


@dataclass
class MarketInfo:
    """市场详情"""
    platform: str
    market_id: str
    question: str
    slug: str
    description: str  # 结算条件
    outcomes: list[str]
    active: bool
    yes_token_id: str = ""
    no_token_id: str = ""


# ============ Polymarket ============

PM_CLOB_HOST = "https://clob.polymarket.com"
PM_GAMMA_HOST = "https://gamma-api.polymarket.com"


def pm_lookup_by_slug(slug: str) -> dict | None:
    """用 slug 查询 Polymarket 市场信息"""
    resp = httpx.get(f"{PM_GAMMA_HOST}/markets", params={"slug": slug}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        if data:
            return data[0]
    return None


def pm_lookup_by_condition_id(condition_id: str) -> dict | None:
    """用 condition_id 查询 Polymarket 市场信息"""
    resp = httpx.get(f"{PM_CLOB_HOST}/markets/{condition_id}", timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


def pm_lookup_by_token_id(token_id: str) -> dict | None:
    """用 token_id 查询 Polymarket 市场信息"""
    # 方法1: 搜索 sampling-markets
    try:
        resp = httpx.get(f"{PM_CLOB_HOST}/sampling-markets", timeout=30)
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

    # 方法2: 搜索 gamma API (用 clob_token_ids 参数)
    try:
        resp = httpx.get(
            f"{PM_GAMMA_HOST}/markets",
            params={"clob_token_ids": token_id},
            timeout=30,
        )
        if resp.status_code == 200:
            markets = resp.json()
            if markets:
                return markets[0]
    except Exception:
        pass

    return None


def pm_get_tokens(condition_id: str) -> list[dict]:
    """从 CLOB API 获取 token 详情"""
    resp = httpx.get(f"{PM_CLOB_HOST}/markets/{condition_id}", timeout=10)
    if resp.status_code == 200:
        return resp.json().get("tokens", [])
    return []


def pm_to_market_info(data: dict) -> MarketInfo | None:
    """将 Polymarket API 响应转换为 MarketInfo"""
    if not data:
        return None

    # 提取 tokens
    tokens = data.get("tokens", [])
    clob_ids = data.get("clobTokenIds", [])
    outcomes = data.get("outcomes", [])

    yes_token = ""
    no_token = ""

    if tokens:
        for t in tokens:
            if t.get("outcome") == "Yes":
                yes_token = t.get("token_id", "")
            elif t.get("outcome") == "No":
                no_token = t.get("token_id", "")
    elif clob_ids and len(clob_ids) >= 2:
        if outcomes and "Yes" in outcomes:
            yes_idx = outcomes.index("Yes")
            no_idx = outcomes.index("No") if "No" in outcomes else 1 - yes_idx
            yes_token = clob_ids[yes_idx]
            no_token = clob_ids[no_idx]
        else:
            yes_token = clob_ids[0]
            no_token = clob_ids[1]

    return MarketInfo(
        platform="Polymarket",
        market_id=data.get("conditionId") or data.get("condition_id", ""),
        question=data.get("question", "N/A"),
        slug=data.get("slug") or data.get("market_slug", ""),
        description=data.get("description", ""),
        outcomes=outcomes if outcomes else [t.get("outcome", "") for t in tokens],
        active=data.get("active", False),
        yes_token_id=yes_token,
        no_token_id=no_token,
    )


# ============ Predict.fun ============

PF_API_HOST = "https://api.predict.fun/v1"


def pf_lookup_by_market_id(market_id: int, api_key: str = None) -> dict | None:
    """用 market_id 查询 Predict.fun 市场信息"""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        resp = httpx.get(
            f"{PF_API_HOST}/markets/{market_id}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {})
    except Exception:
        pass

    return None


def pf_to_market_info(data: dict) -> MarketInfo | None:
    """将 Predict.fun API 响应转换为 MarketInfo"""
    if not data:
        return None

    outcomes = data.get("outcomes", [])
    yes_token = ""
    no_token = ""

    for o in outcomes:
        if o.get("name") == "Yes":
            yes_token = o.get("onChainId", "")
        elif o.get("name") == "No":
            no_token = o.get("onChainId", "")

    # 判断是否活跃：closedAt 和 resolvedAt 都为空
    # status 可能是 REGISTERED, ACTIVE, CLOSED, RESOLVED 等
    closed_at = data.get("closedAt")
    resolved_at = data.get("resolvedAt")
    is_active = not closed_at and not resolved_at

    return MarketInfo(
        platform="Predict.fun",
        market_id=str(data.get("id", "")),
        question=data.get("title") or data.get("question", "N/A"),
        slug=data.get("slug", ""),
        description=data.get("rules") or data.get("description", ""),
        outcomes=[o.get("name", "") for o in outcomes],
        active=is_active,
        yes_token_id=yes_token,
        no_token_id=no_token,
    )


# ============ 统一接口 ============

def lookup_pm_market(token_id: str) -> MarketInfo | None:
    """查询 Polymarket 市场信息（通过 token_id）"""
    data = pm_lookup_by_token_id(token_id)
    if data:
        # 补充 tokens 信息
        condition_id = data.get("conditionId") or data.get("condition_id", "")
        if condition_id and not data.get("tokens"):
            tokens = pm_get_tokens(condition_id)
            if tokens:
                data["tokens"] = tokens
        return pm_to_market_info(data)
    return None


def lookup_pf_market(market_id: int, api_key: str = None) -> MarketInfo | None:
    """查询 Predict.fun 市场信息（通过 market_id）"""
    data = pf_lookup_by_market_id(market_id, api_key)
    return pf_to_market_info(data)
