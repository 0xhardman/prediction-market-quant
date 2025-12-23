#!/usr/bin/env python3
"""跨平台套利检测脚本 - Polymarket + Predict.fun"""

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import time
from typing import NamedTuple

import httpx
from dotenv import load_dotenv

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.lookup import MarketInfo, lookup_pm_market, lookup_pf_market

# 加载 .env 文件
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# 配置常量
REFRESH_INTERVAL = 5  # 刷新间隔（秒）
PROFIT_THRESHOLD = 0.01  # 利润阈值（1%）
PM_FEE = 0.0  # Polymarket 费率
PF_FEE = 0.02  # Predict.fun 费率（2%）

# API 端点
PM_CLOB_HOST = "https://clob.polymarket.com"
PF_API_HOST = "https://api.predict.fun/v1"


@dataclass
class Orderbook:
    """Orderbook 数据结构"""
    bids: list[tuple[float, float]]  # [(price, size), ...] 降序
    asks: list[tuple[float, float]]  # [(price, size), ...] 升序
    timestamp: float


class ArbResult(NamedTuple):
    """套利计算结果"""
    strategy: str  # 策略描述
    pm_side: str  # PM 买 Yes/No
    pf_side: str  # PF 买 Yes/No
    pm_price: float  # PM 价格
    pf_price: float  # PF 价格
    total_cost: float  # 总成本
    profit_pct: float  # 利润率
    best_amount: float  # 最优金额
    expected_profit: float  # 预期收益


class DepthAnalysis(NamedTuple):
    """深度分析结果"""
    amount: float
    pm_avg_price: float
    pf_avg_price: float
    pm_slippage: float
    pf_slippage: float
    total_cost: float
    profit_pct: float
    expected_profit: float  # 预期收益 = amount * profit_pct / 100


# ============ 输入解析 ============

def parse_pm_input(input_str: str) -> tuple[str, str, str]:
    """解析 Polymarket 输入，返回 (condition_id, yes_token_id, no_token_id)

    支持格式:
    - condition_id (0x开头): 自动查询 yes/no token
    - token_id (纯数字长串): 返回 ("", token_id, "")
    - URL: https://polymarket.com/event/xxx
    """
    from src.lookup import pm_lookup_by_condition_id, pm_lookup_by_token_id, pm_get_tokens

    input_str = input_str.strip()

    # 如果是 0x 开头，当作 condition_id
    if input_str.startswith("0x"):
        condition_id = input_str
        tokens = pm_get_tokens(condition_id)
        yes_token = ""
        no_token = ""
        for t in tokens:
            if t.get("outcome") == "Yes":
                yes_token = t.get("token_id", "")
            elif t.get("outcome") == "No":
                no_token = t.get("token_id", "")
        return condition_id, yes_token, no_token

    # 纯数字长串，当作 token_id
    if input_str.isdigit() or (len(input_str) > 40 and input_str[0].isdigit()):
        return "", input_str, ""

    # URL 解析 - 提取 slug
    if "polymarket.com" in input_str:
        import re
        from src.lookup import pm_lookup_by_slug

        slug = None

        # 格式1: /event/{event_slug}/{market_slug}
        match = re.search(r'polymarket\.com/event/[^/]+/([^?/\\]+)', input_str)
        if match:
            slug = match.group(1).rstrip("\\")

        # 格式2: /sports/{sport}/games/week/{n}/{slug}
        if not slug:
            match = re.search(r'polymarket\.com/sports/[^/]+/games/[^/]+/\d+/([^?/\\]+)', input_str)
            if match:
                slug = match.group(1).rstrip("\\")

        if slug:
            data = pm_lookup_by_slug(slug)
            if data:
                condition_id = data.get("conditionId", "")
                tokens = pm_get_tokens(condition_id) if condition_id else []
                # 提取所有 token（不仅仅是 Yes/No）
                token_map = {}
                for t in tokens:
                    outcome = t.get("outcome", "")
                    token_id = t.get("token_id", "")
                    if outcome and token_id:
                        token_map[outcome] = token_id
                # 返回第一个 token 作为主 token（用于 orderbook 查询）
                first_token = list(token_map.values())[0] if token_map else ""
                second_token = list(token_map.values())[1] if len(token_map) > 1 else ""
                return condition_id, first_token, second_token

    return "", input_str, ""


def fetch_pf_market_ids_from_page(slug: str, api_key: str = None) -> list[tuple[int, str]]:
    """从 predict.fun 获取 market_ids 和 outcome 名称

    尝试方法:
    1. 从页面 HTML 提取 market IDs
    2. 用 API 获取 category 下的 markets（需要 api_key）

    Returns: [(market_id, outcome_name), ...]
    """
    import re as regex

    results = []

    # 方法1: 从页面提取 market IDs
    try:
        resp = httpx.get(
            f"https://predict.fun/market/{slug}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text

        # 提取所有 marketId 参数
        market_ids = set(regex.findall(r'marketId=(\d+)', html))
        # 也查找 "market","XXX" 格式
        market_ids.update(regex.findall(r'"market","(\d+)"', html))

        if market_ids:
            print(f"  从页面提取到 market IDs: {market_ids}")

    except Exception as e:
        print(f"[警告] 无法从页面获取数据: {e}")
        market_ids = set()

    # 如果只找到一个 market ID，尝试推断另一个
    if len(market_ids) == 1:
        mid = int(list(market_ids)[0])
        # 通常相邻的 market ID 是同一个 event 的不同 outcome
        market_ids.add(str(mid - 1))
        market_ids.add(str(mid + 1))

    # 从 slug 提取城市/球队关键词用于过滤
    slug_keywords = set()
    for word in slug.replace('-', ' ').replace('_', ' ').split():
        if len(word) > 2:  # 忽略短词
            slug_keywords.add(word.lower())

    # 方法2: 用 API 获取每个 market 的详情
    if api_key:
        for mid in sorted(market_ids):
            try:
                resp = httpx.get(
                    f"https://api.predict.fun/v1/markets/{mid}",
                    headers={"X-API-Key": api_key},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    title = data.get("title") or data.get("name", f"Outcome {mid}")
                    # 检查 title 是否与 slug 相关
                    title_lower = title.lower()
                    is_related = any(kw in title_lower for kw in slug_keywords)
                    # 也检查是否是常见城市/球队名
                    if is_related or title_lower in ['yes', 'no']:
                        results.append((int(mid), title))
                        print(f"  Market {mid}: {title} (matched)")
                    else:
                        print(f"  Market {mid}: {title} (skipped - not related to '{slug}')")
            except Exception:
                pass
    else:
        # 没有 API key，只能用 ID 作为 outcome 名称
        for mid in sorted(market_ids):
            results.append((int(mid), f"Outcome {mid}"))

    # 去重
    seen = set()
    unique_results = []
    for item in results:
        if item[0] not in seen:
            seen.add(item[0])
            unique_results.append(item)

    return unique_results[:2]  # 只返回前两个


def parse_pf_input(input_str: str, api_key: str = None) -> tuple[int, str, list[tuple[int, str]]]:
    """解析 Predict.fun 输入，返回 (market_id, token_id, all_markets)

    支持格式:
    - market_id 直接传入: 12345
    - market_id:token_id: 12345:0x...
    - URL: https://predict.fun/market/xxx

    Returns:
        (market_id, token_id, all_markets)
        all_markets: [(market_id, outcome_name), ...] 用于多 outcome 市场
    """
    import re

    input_str = input_str.strip()

    # 如果包含冒号，分割为 market_id:token_id
    if ":" in input_str and not input_str.startswith("http"):
        parts = input_str.split(":", 1)
        return int(parts[0]), parts[1], []

    # 如果是纯数字，作为 market_id
    if input_str.isdigit():
        return int(input_str), "", []

    # URL 解析
    if "predict.fun/market/" in input_str:
        match = re.search(r'predict\.fun/market/([^?/\\]+)', input_str)
        if match:
            slug = match.group(1).rstrip("\\")
            # 从环境变量获取 API key（如果没有传入）
            if not api_key:
                api_key = os.environ.get("PREDICT_FUN_API_KEY")
            markets = fetch_pf_market_ids_from_page(slug, api_key)
            if markets:
                # 返回第一个 market_id，以及完整的 markets 列表
                return markets[0][0], "", markets

    # 尝试直接解析为整数
    try:
        return int(input_str), "", []
    except ValueError:
        return 0, "", []


# ============ Orderbook 获取 ============

async def fetch_pm_orderbook(http: httpx.AsyncClient, token_id: str) -> Orderbook:
    """获取 Polymarket orderbook"""
    resp = await http.get(
        f"{PM_CLOB_HOST}/book",
        params={"token_id": token_id},
    )
    resp.raise_for_status()
    book = resp.json()

    bids = [(float(b["price"]), float(b["size"])) for b in book.get("bids", [])]
    asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks", [])]

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    return Orderbook(bids=bids, asks=asks, timestamp=time())


async def fetch_pf_orderbook(http: httpx.AsyncClient, market_id: int, api_key: str = None) -> Orderbook:
    """获取 Predict.fun orderbook"""
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    resp = await http.get(f"{PF_API_HOST}/markets/{market_id}/orderbook", headers=headers)
    resp.raise_for_status()

    book = resp.json().get("data", {})
    bids = [(float(b[0]), float(b[1])) for b in book.get("bids", [])]
    asks = [(float(a[0]), float(a[1])) for a in book.get("asks", [])]

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    return Orderbook(bids=bids, asks=asks, timestamp=time())


# ============ 套利计算 ============

def calc_fill_price(orders: list[tuple[float, float]], amount: float) -> tuple[float, float]:
    """计算吃单成交均价和总成本

    Args:
        orders: [(price, size), ...] 订单列表
        amount: 想要成交的数量（美元）

    Returns:
        (avg_price, total_cost) 均价和总成本
    """
    if not orders or amount <= 0:
        return 0.0, 0.0

    filled = 0.0
    total_cost = 0.0

    for price, size in orders:
        # 计算这一档能成交多少美元
        available = price * size
        take = min(available, amount - filled)
        total_cost += take
        filled += take

        if filled >= amount:
            break

    if filled <= 0:
        return 0.0, 0.0

    # 计算获得的份额
    shares = 0.0
    remaining = amount
    for price, size in orders:
        available = price * size
        take = min(available, remaining)
        shares += take / price
        remaining -= take
        if remaining <= 0:
            break

    avg_price = total_cost / shares if shares > 0 else 0.0
    return avg_price, total_cost


def analyze_arb_opportunity(
    pm_book: Orderbook,
    pf_book: Orderbook,
    amounts: list[float] = None,
) -> tuple[ArbResult | None, list[DepthAnalysis]]:
    """分析套利机会

    策略1: PM买Yes + PF买No (当 PM_Yes_Ask + PF_No_Ask < 1.0)
    策略2: PM买No + PF买Yes (当 PM_No_Ask + PF_Yes_Ask < 1.0)

    注意: 买Yes用asks，买No也用asks（对手方是No的卖方）
    """
    if amounts is None:
        amounts = [100, 500, 1000, 2000, 5000]

    # 获取最优价格
    pm_yes_ask = pm_book.asks[0][0] if pm_book.asks else 1.0
    pm_no_ask = (1 - pm_book.bids[0][0]) if pm_book.bids else 1.0  # No价格 = 1 - Yes bid
    pf_yes_ask = pf_book.asks[0][0] if pf_book.asks else 1.0
    pf_no_ask = (1 - pf_book.bids[0][0]) if pf_book.bids else 1.0

    # 计算两种策略的基础成本
    # 策略1: PM买Yes + PF买No
    cost1_base = pm_yes_ask + pf_no_ask * (1 + PF_FEE)

    # 策略2: PM买No + PF买Yes
    cost2_base = pm_no_ask + pf_yes_ask * (1 + PF_FEE)

    # 选择更优策略
    if cost1_base < cost2_base:
        strategy = "PM买Yes + PF买No"
        pm_side, pf_side = "Yes", "No"
        pm_price, pf_price = pm_yes_ask, pf_no_ask
        pm_orders = pm_book.asks
        # PF买No: 用 PF 的 Yes bids 反推 No asks
        pf_orders = [(1 - p, s) for p, s in pf_book.bids]
    else:
        strategy = "PM买No + PF买Yes"
        pm_side, pf_side = "No", "Yes"
        pm_price, pf_price = pm_no_ask, pf_yes_ask
        # PM买No: 用 PM 的 Yes bids 反推 No asks
        pm_orders = [(1 - p, s) for p, s in pm_book.bids]
        pf_orders = pf_book.asks

    # 深度分析
    depth_results = []
    best_result = None
    best_profit = -float("inf")

    for amount in amounts:
        # 简化计算：假设两边各投入 amount/2
        half = amount / 2

        pm_avg, pm_cost = calc_fill_price(pm_orders, half) if pm_orders else (pm_price, half)
        pf_avg, pf_cost = calc_fill_price(pf_orders, half) if pf_orders else (pf_price, half)

        # 滑点
        pm_slippage = (pm_avg - pm_price) / pm_price * 100 if pm_price > 0 else 0
        pf_slippage = (pf_avg - pf_price) / pf_price * 100 if pf_price > 0 else 0

        # 总成本（含费用）
        total_cost = pm_avg + pf_avg * (1 + PF_FEE)
        profit_pct = (1.0 - total_cost) * 100
        expected_profit = amount * profit_pct / 100

        depth_results.append(DepthAnalysis(
            amount=amount,
            pm_avg_price=pm_avg if pm_avg > 0 else pm_price,
            pf_avg_price=pf_avg if pf_avg > 0 else pf_price,
            pm_slippage=pm_slippage,
            pf_slippage=pf_slippage,
            total_cost=total_cost,
            profit_pct=profit_pct,
            expected_profit=expected_profit,
        ))

        if profit_pct > best_profit:
            best_profit = profit_pct
            best_amount = amount

    # 构建结果
    if best_profit > 0:
        total_cost = pm_price + pf_price * (1 + PF_FEE)
        expected_profit = best_amount * (best_profit / 100)

        best_result = ArbResult(
            strategy=strategy,
            pm_side=pm_side,
            pf_side=pf_side,
            pm_price=pm_price,
            pf_price=pf_price,
            total_cost=total_cost,
            profit_pct=best_profit,
            best_amount=best_amount,
            expected_profit=expected_profit,
        )

    return best_result, depth_results


def analyze_team_arb_opportunity(
    pm_book1: Orderbook,  # PM Team1 (e.g., Bucks)
    pm_book2: Orderbook,  # PM Team2 (e.g., Timberwolves)
    pf_book1: Orderbook,  # PF Team1 (对应 PM Team1)
    pf_book2: Orderbook,  # PF Team2 (对应 PM Team2)
    team1_name: str = "Team1",
    team2_name: str = "Team2",
    amounts: list[float] = None,
) -> tuple[ArbResult | None, list[DepthAnalysis]]:
    """分析 Team vs Team 市场的套利机会

    策略1: PM买Team1 + PF买Team2 (当 PM_Team1_Ask + PF_Team2_Ask < 1.0)
    策略2: PM买Team2 + PF买Team1 (当 PM_Team2_Ask + PF_Team1_Ask < 1.0)
    """
    if amounts is None:
        amounts = [100, 500, 1000, 2000, 5000]

    # 获取各方最优价格
    pm_team1_ask = pm_book1.asks[0][0] if pm_book1.asks else 1.0
    pm_team2_ask = pm_book2.asks[0][0] if pm_book2.asks else 1.0
    pf_team1_ask = pf_book1.asks[0][0] if pf_book1.asks else 1.0
    pf_team2_ask = pf_book2.asks[0][0] if pf_book2.asks else 1.0

    # 策略1: PM买Team1 + PF买Team2
    cost1_base = pm_team1_ask + pf_team2_ask * (1 + PF_FEE)

    # 策略2: PM买Team2 + PF买Team1
    cost2_base = pm_team2_ask + pf_team1_ask * (1 + PF_FEE)

    # 选择更优策略
    if cost1_base < cost2_base:
        strategy = f"PM买{team1_name} + PF买{team2_name}"
        pm_side, pf_side = team1_name, team2_name
        pm_price, pf_price = pm_team1_ask, pf_team2_ask
        pm_orders = pm_book1.asks
        pf_orders = pf_book2.asks
    else:
        strategy = f"PM买{team2_name} + PF买{team1_name}"
        pm_side, pf_side = team2_name, team1_name
        pm_price, pf_price = pm_team2_ask, pf_team1_ask
        pm_orders = pm_book2.asks
        pf_orders = pf_book1.asks

    # 深度分析
    depth_results = []
    best_result = None
    best_profit = -float("inf")

    for amount in amounts:
        half = amount / 2

        pm_avg, _ = calc_fill_price(pm_orders, half) if pm_orders else (pm_price, half)
        pf_avg, _ = calc_fill_price(pf_orders, half) if pf_orders else (pf_price, half)

        pm_slippage = (pm_avg - pm_price) / pm_price * 100 if pm_price > 0 else 0
        pf_slippage = (pf_avg - pf_price) / pf_price * 100 if pf_price > 0 else 0

        total_cost = pm_avg + pf_avg * (1 + PF_FEE)
        profit_pct = (1.0 - total_cost) * 100
        expected_profit = amount * profit_pct / 100

        depth_results.append(DepthAnalysis(
            amount=amount,
            pm_avg_price=pm_avg if pm_avg > 0 else pm_price,
            pf_avg_price=pf_avg if pf_avg > 0 else pf_price,
            pm_slippage=pm_slippage,
            pf_slippage=pf_slippage,
            total_cost=total_cost,
            profit_pct=profit_pct,
            expected_profit=expected_profit,
        ))

        if profit_pct > best_profit:
            best_profit = profit_pct
            best_amount = amount

    if best_profit > 0:
        total_cost = pm_price + pf_price * (1 + PF_FEE)
        expected_profit = best_amount * (best_profit / 100)

        best_result = ArbResult(
            strategy=strategy,
            pm_side=pm_side,
            pf_side=pf_side,
            pm_price=pm_price,
            pf_price=pf_price,
            total_cost=total_cost,
            profit_pct=best_profit,
            best_amount=best_amount,
            expected_profit=expected_profit,
        )

    return best_result, depth_results


# ============ 报告生成 ============

def print_report(
    pm_token: str,
    pf_market: int,
    pm_book: Orderbook,
    pf_book: Orderbook,
    arb_result: ArbResult | None,
    depth_analysis: list[DepthAnalysis],
    pm_info: MarketInfo | None = None,
    pf_info: MarketInfo | None = None,
):
    """打印详细报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 70)
    print("                   跨平台套利分析报告")
    print("=" * 70)
    print(f"时间: {now}")
    print()

    # Polymarket 市场信息
    print("【Polymarket】")
    if pm_info:
        print(f"  问题: {pm_info.question}")
        print(f"  Slug: {pm_info.slug}")
        print(f"  状态: {'活跃' if pm_info.active else '已结束'}")
    print(f"  Token: {pm_token[:40]}..." if len(pm_token) > 40 else f"  Token: {pm_token}")

    pm_yes_ask = pm_book.asks[0][0] if pm_book.asks else None
    pm_yes_bid = pm_book.bids[0][0] if pm_book.bids else None
    if pm_yes_ask is not None and pm_yes_bid is not None:
        pm_no_bid = 1 - pm_yes_ask
        pm_no_ask = 1 - pm_yes_bid
        print(f"  Yes: Bid={pm_yes_bid:.4f} | Ask={pm_yes_ask:.4f}")
        print(f"  No:  Bid={pm_no_bid:.4f} | Ask={pm_no_ask:.4f}")
    else:
        print("  No orderbook data")
    print()

    # Predict.fun 市场信息
    print("【Predict.fun】")
    if pf_info:
        print(f"  问题: {pf_info.question}")
        print(f"  Slug: {pf_info.slug}")
        print(f"  状态: {'活跃' if pf_info.active else '已结束'}")
    print(f"  Market ID: {pf_market}")

    pf_yes_ask = pf_book.asks[0][0] if pf_book.asks else None
    pf_yes_bid = pf_book.bids[0][0] if pf_book.bids else None
    if pf_yes_ask is not None and pf_yes_bid is not None:
        pf_no_bid = 1 - pf_yes_ask
        pf_no_ask = 1 - pf_yes_bid
        print(f"  Yes: Bid={pf_yes_bid:.4f} | Ask={pf_yes_ask:.4f}")
        print(f"  No:  Bid={pf_no_bid:.4f} | Ask={pf_no_ask:.4f}")
    else:
        print("  No orderbook data")
    print()

    # 套利机会
    print("【套利机会】")
    if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
        print("存在套利机会!")
        print()
        print(f"最优策略: {arb_result.strategy}")
        print(f"  PM {arb_result.pm_side}价格: {arb_result.pm_price:.4f} (费率{PM_FEE*100:.1f}%)")
        print(f"  PF {arb_result.pf_side}价格: {arb_result.pf_price:.4f} (费率{PF_FEE*100:.1f}%)")
        print()
        print(f"  总成本: {arb_result.total_cost:.4f}")
        print(f"  净利润: {arb_result.profit_pct:.2f}%")
        print()
        print(f"  最优金额: ${arb_result.best_amount:.0f}")
        print(f"  预期收益: ${arb_result.expected_profit:.2f}")
    else:
        print("无套利机会")
        if arb_result:
            print(f"  最优利润率: {arb_result.profit_pct:.2f}% (低于阈值 {PROFIT_THRESHOLD*100:.1f}%)")
    print()

    # 深度分析
    print("【Orderbook深度分析】")
    print(f"{'金额':>8} | {'PM滑点':>7} | {'PF滑点':>7} | {'总成本':>8} | {'利润率':>7} | {'预期收益':>9}")
    print("-" * 70)

    # 找到收益最高的档位
    max_profit_idx = -1
    max_profit = -float("inf")
    for i, d in enumerate(depth_analysis):
        if d.expected_profit > max_profit:
            max_profit = d.expected_profit
            max_profit_idx = i

    for i, d in enumerate(depth_analysis):
        marker = " <- 收益最高" if i == max_profit_idx else ""
        print(f"${d.amount:>7.0f} | {d.pm_slippage:>6.2f}% | {d.pf_slippage:>6.2f}% | {d.total_cost:>8.4f} | {d.profit_pct:>6.2f}% | ${d.expected_profit:>8.2f}{marker}")

    # 结算条件
    if pm_info and pm_info.description:
        print()
        print("【Polymarket 结算条件】")
        print("-" * 70)
        # 截断过长的描述
        desc = pm_info.description
        if len(desc) > 500:
            desc = desc[:500] + "..."
        print(desc)

    if pf_info and pf_info.description:
        print()
        print("【Predict.fun 结算条件】")
        print("-" * 70)
        desc = pf_info.description
        if len(desc) > 500:
            desc = desc[:500] + "..."
        print(desc)

    print("=" * 70)


def play_alert():
    """播放提示音（macOS）"""
    try:
        subprocess.run(
            ["afplay", "/System/Library/Sounds/Glass.aiff"],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass  # 忽略错误


# Telegram 配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8023765575:AAFKn2Nn5TNxFqQ1nYQ3y2A5IUqowpzvGAs")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "-5088762482")


def send_telegram_alert(arb_result: ArbResult, pm_url: str = "", pf_url: str = ""):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    message = f"""🚨 *发现套利机会!*

*策略*: {arb_result.strategy}
*PM {arb_result.pm_side}*: {arb_result.pm_price:.4f}
*PF {arb_result.pf_side}*: {arb_result.pf_price:.4f}

💰 *总成本*: {arb_result.total_cost:.4f}
📈 *利润率*: {arb_result.profit_pct:.2f}%
💵 *最优金额*: ${arb_result.best_amount:.0f}
🎯 *预期收益*: ${arb_result.expected_profit:.2f}
"""
    if pm_url:
        message += f"\n[Polymarket]({pm_url})"
    if pf_url:
        message += f" | [Predict.fun]({pf_url})"

    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Telegram] 发送失败: {e}")


# ============ 主循环 ============

async def monitor_loop(pm_token: str, pf_market: int, pf_api_key: str = None):
    """持续监控循环"""
    async with httpx.AsyncClient(timeout=30) as http:
        print(f"开始监控套利机会...")
        print(f"  Polymarket: {pm_token[:20]}..." if len(pm_token) > 20 else f"  Polymarket: {pm_token}")
        print(f"  Predict.fun: {pf_market}")
        print(f"  刷新间隔: {REFRESH_INTERVAL}秒")

        # 获取市场详情（只需要一次，使用 lookup 模块）
        print("  正在获取市场详情...")
        pm_info = lookup_pm_market(pm_token)
        pf_info = lookup_pf_market(pf_market, pf_api_key)
        if pm_info:
            print(f"  PM: {pm_info.question[:50]}..." if len(pm_info.question) > 50 else f"  PM: {pm_info.question}")
        if pf_info:
            print(f"  PF: {pf_info.question[:50]}..." if len(pf_info.question) > 50 else f"  PF: {pf_info.question}")
        print(f"  利润阈值: {PROFIT_THRESHOLD*100:.1f}%")
        print()
        print("按 Ctrl+C 停止监控")

        last_alert = 0

        while True:
            try:
                # 获取 orderbook
                pm_book, pf_book = await asyncio.gather(
                    fetch_pm_orderbook(http, pm_token),
                    fetch_pf_orderbook(http, pf_market, pf_api_key),
                )

                # 分析套利
                arb_result, depth_analysis = analyze_arb_opportunity(pm_book, pf_book)

                # 打印报告
                print_report(pm_token, pf_market, pm_book, pf_book, arb_result, depth_analysis, pm_info, pf_info)

                # 发现套利机会时播放提示音和发送通知（限制频率）
                if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
                    if time() - last_alert > 30:  # 30秒内不重复提醒
                        play_alert()
                        send_telegram_alert(arb_result)
                        last_alert = time()

            except httpx.HTTPError as e:
                print(f"[错误] HTTP请求失败: {e}")
            except Exception as e:
                print(f"[错误] {e}")

            await asyncio.sleep(REFRESH_INTERVAL)


async def single_check(pm_token: str, pf_market: int, pf_api_key: str = None):
    """单次检查"""
    async with httpx.AsyncClient(timeout=30) as http:
        # 获取 orderbook
        pm_book, pf_book = await asyncio.gather(
            fetch_pm_orderbook(http, pm_token),
            fetch_pf_orderbook(http, pf_market, pf_api_key),
        )

        # 获取市场详情（使用 lookup 模块）
        pm_info = lookup_pm_market(pm_token)
        pf_info = lookup_pf_market(pf_market, pf_api_key)

        arb_result, depth_analysis = analyze_arb_opportunity(pm_book, pf_book)
        print_report(pm_token, pf_market, pm_book, pf_book, arb_result, depth_analysis, pm_info, pf_info)


async def single_check_teams(
    pm_tokens: list[tuple[str, str]],  # [(token_id, outcome_name), ...]
    pf_markets: list[tuple[int, str]],  # [(market_id, outcome_name), ...]
    pf_api_key: str = None,
):
    """单次检查 - Team vs Team 市场

    Args:
        pm_tokens: Polymarket tokens [(token_id, outcome), ...]
        pf_markets: Predict.fun markets [(market_id, outcome), ...]
    """
    if len(pm_tokens) < 2 or len(pf_markets) < 2:
        print("[错误] Team vs Team 市场需要至少两个 outcome")
        return

    async with httpx.AsyncClient(timeout=30) as http:
        # 获取所有 orderbook
        pm_book1 = await fetch_pm_orderbook(http, pm_tokens[0][0])
        pm_book2 = await fetch_pm_orderbook(http, pm_tokens[1][0])
        pf_book1 = await fetch_pf_orderbook(http, pf_markets[0][0], pf_api_key)
        pf_book2 = await fetch_pf_orderbook(http, pf_markets[1][0], pf_api_key)

        # 获取市场详情
        pm_info = lookup_pm_market(pm_tokens[0][0])

        # 分析套利
        team1_name = pm_tokens[0][1]  # e.g., "Bucks"
        team2_name = pm_tokens[1][1]  # e.g., "Timberwolves"

        arb_result, depth_analysis = analyze_team_arb_opportunity(
            pm_book1, pm_book2,
            pf_book1, pf_book2,
            team1_name=team1_name,
            team2_name=team2_name,
        )

        # 打印报告
        print_team_report(
            pm_tokens, pf_markets,
            pm_book1, pm_book2,
            pf_book1, pf_book2,
            arb_result, depth_analysis,
            pm_info,
        )


async def monitor_loop_teams(
    pm_tokens: list[tuple[str, str]],
    pf_markets: list[tuple[int, str]],
    pf_api_key: str = None,
):
    """持续监控 - Team vs Team 市场"""
    if len(pm_tokens) < 2 or len(pf_markets) < 2:
        print("[错误] Team vs Team 市场需要至少两个 outcome")
        return

    async with httpx.AsyncClient(timeout=30) as http:
        print(f"开始监控套利机会 (Team vs Team)...")
        print(f"  PM: {pm_tokens[0][1]} vs {pm_tokens[1][1]}")
        print(f"  PF: {pf_markets[0][1]} (ID {pf_markets[0][0]}) vs {pf_markets[1][1]} (ID {pf_markets[1][0]})")
        print(f"  刷新间隔: {REFRESH_INTERVAL}秒")
        print(f"  利润阈值: {PROFIT_THRESHOLD*100:.1f}%")
        print()
        print("按 Ctrl+C 停止监控")

        pm_info = lookup_pm_market(pm_tokens[0][0])
        last_alert = 0

        while True:
            try:
                # 获取所有 orderbook
                pm_book1 = await fetch_pm_orderbook(http, pm_tokens[0][0])
                pm_book2 = await fetch_pm_orderbook(http, pm_tokens[1][0])
                pf_book1 = await fetch_pf_orderbook(http, pf_markets[0][0], pf_api_key)
                pf_book2 = await fetch_pf_orderbook(http, pf_markets[1][0], pf_api_key)

                # 分析套利
                team1_name = pm_tokens[0][1]
                team2_name = pm_tokens[1][1]

                arb_result, depth_analysis = analyze_team_arb_opportunity(
                    pm_book1, pm_book2,
                    pf_book1, pf_book2,
                    team1_name=team1_name,
                    team2_name=team2_name,
                )

                # 打印报告
                print_team_report(
                    pm_tokens, pf_markets,
                    pm_book1, pm_book2,
                    pf_book1, pf_book2,
                    arb_result, depth_analysis,
                    pm_info,
                )

                # 发现套利机会时播放提示音和发送通知
                if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
                    if time() - last_alert > 30:
                        play_alert()
                        send_telegram_alert(arb_result)
                        last_alert = time()

            except httpx.HTTPError as e:
                print(f"[错误] HTTP请求失败: {e}")
            except Exception as e:
                print(f"[错误] {e}")

            await asyncio.sleep(REFRESH_INTERVAL)


def print_team_report(
    pm_tokens: list[tuple[str, str]],
    pf_markets: list[tuple[int, str]],
    pm_book1: Orderbook,
    pm_book2: Orderbook,
    pf_book1: Orderbook,
    pf_book2: Orderbook,
    arb_result: ArbResult | None,
    depth_analysis: list[DepthAnalysis],
    pm_info: MarketInfo | None = None,
):
    """打印 Team vs Team 市场报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 70)
    print("                   跨平台套利分析报告 (Team vs Team)")
    print("=" * 70)
    print(f"时间: {now}")
    print()

    # Polymarket 市场信息
    print("【Polymarket】")
    if pm_info:
        print(f"  问题: {pm_info.question}")
        print(f"  Slug: {pm_info.slug}")
        print(f"  状态: {'活跃' if pm_info.active else '已结束'}")

    for token_id, outcome in pm_tokens:
        short_id = f"{token_id[:20]}..." if len(token_id) > 20 else token_id
        print(f"  {outcome}: {short_id}")

    # PM Outcome 1
    pm1_ask = pm_book1.asks[0][0] if pm_book1.asks else None
    pm1_bid = pm_book1.bids[0][0] if pm_book1.bids else None
    if pm1_ask is not None and pm1_bid is not None:
        print(f"  {pm_tokens[0][1]}: Bid={pm1_bid:.4f} | Ask={pm1_ask:.4f}")

    # PM Outcome 2
    pm2_ask = pm_book2.asks[0][0] if pm_book2.asks else None
    pm2_bid = pm_book2.bids[0][0] if pm_book2.bids else None
    if pm2_ask is not None and pm2_bid is not None:
        print(f"  {pm_tokens[1][1]}: Bid={pm2_bid:.4f} | Ask={pm2_ask:.4f}")
    print()

    # Predict.fun 市场信息
    print("【Predict.fun】")
    for market_id, outcome in pf_markets:
        print(f"  {outcome}: Market ID {market_id}")

    # PF Outcome 1
    pf1_ask = pf_book1.asks[0][0] if pf_book1.asks else None
    pf1_bid = pf_book1.bids[0][0] if pf_book1.bids else None
    if pf1_ask is not None and pf1_bid is not None:
        print(f"  {pf_markets[0][1]}: Bid={pf1_bid:.4f} | Ask={pf1_ask:.4f}")

    # PF Outcome 2
    pf2_ask = pf_book2.asks[0][0] if pf_book2.asks else None
    pf2_bid = pf_book2.bids[0][0] if pf_book2.bids else None
    if pf2_ask is not None and pf2_bid is not None:
        print(f"  {pf_markets[1][1]}: Bid={pf2_bid:.4f} | Ask={pf2_ask:.4f}")
    print()

    # 套利机会
    print("【套利机会】")
    if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
        print("存在套利机会!")
        print()
        print(f"最优策略: {arb_result.strategy}")
        print(f"  PM {arb_result.pm_side}价格: {arb_result.pm_price:.4f} (费率{PM_FEE*100:.1f}%)")
        print(f"  PF {arb_result.pf_side}价格: {arb_result.pf_price:.4f} (费率{PF_FEE*100:.1f}%)")
        print()
        print(f"  总成本: {arb_result.total_cost:.4f}")
        print(f"  净利润: {arb_result.profit_pct:.2f}%")
        print()
        print(f"  最优金额: ${arb_result.best_amount:.0f}")
        print(f"  预期收益: ${arb_result.expected_profit:.2f}")
    else:
        print("无套利机会")
        if arb_result:
            print(f"  最优利润率: {arb_result.profit_pct:.2f}% (低于阈值 {PROFIT_THRESHOLD*100:.1f}%)")
    print()

    # 深度分析
    print("【Orderbook深度分析】")
    print(f"{'金额':>8} | {'PM滑点':>7} | {'PF滑点':>7} | {'总成本':>8} | {'利润率':>7} | {'预期收益':>9}")
    print("-" * 70)

    max_profit_idx = -1
    max_profit = -float("inf")
    for i, d in enumerate(depth_analysis):
        if d.expected_profit > max_profit:
            max_profit = d.expected_profit
            max_profit_idx = i

    for i, d in enumerate(depth_analysis):
        marker = " <- 收益最高" if i == max_profit_idx else ""
        print(f"${d.amount:>7.0f} | {d.pm_slippage:>6.2f}% | {d.pf_slippage:>6.2f}% | {d.total_cost:>8.4f} | {d.profit_pct:>6.2f}% | ${d.expected_profit:>8.2f}{marker}")

    print("=" * 70)


def match_outcomes(
    pm_outcomes: list[str],
    pf_outcomes: list[str],
) -> dict[str, str]:
    """匹配两个平台的 outcome 名称

    Returns: {pm_outcome: pf_outcome}
    """
    # 常见的球队名称映射
    team_aliases = {
        "Bucks": ["Milwaukee", "MIL"],
        "Timberwolves": ["Minnesota", "MIN"],
        "Lakers": ["Los Angeles Lakers", "LA Lakers", "LAL"],
        "Warriors": ["Golden State", "GSW"],
        "Celtics": ["Boston", "BOS"],
        "Heat": ["Miami", "MIA"],
        "Nets": ["Brooklyn", "BKN"],
        "Knicks": ["New York", "NYK"],
        # 添加更多映射...
    }

    # 构建反向映射
    alias_to_team = {}
    for team, aliases in team_aliases.items():
        alias_to_team[team.lower()] = team
        for alias in aliases:
            alias_to_team[alias.lower()] = team

    result = {}
    for pm in pm_outcomes:
        pm_lower = pm.lower()
        pm_team = alias_to_team.get(pm_lower, pm)

        for pf in pf_outcomes:
            pf_lower = pf.lower()
            pf_team = alias_to_team.get(pf_lower, pf)

            # 如果归一化后相同，或者其中一个包含另一个
            if pm_team.lower() == pf_team.lower():
                result[pm] = pf
                break
            elif pm_lower in pf_lower or pf_lower in pm_lower:
                result[pm] = pf
                break

    return result


def main():
    global REFRESH_INTERVAL, PROFIT_THRESHOLD

    parser = argparse.ArgumentParser(
        description="跨平台套利检测 - Polymarket + Predict.fun",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用 condition_id（推荐）
  uv run python scripts/arb_checker.py 0x1dc687... 538

  # 使用 token_id
  uv run python scripts/arb_checker.py 5354756... 538

  # 使用 URL（支持体育赛事）
  uv run python scripts/arb_checker.py "https://polymarket.com/sports/nba/games/week/3/nba-mil-min-2025-12-21" "https://predict.fun/market/milwaukee-vs-minnesota"

  # 单次检查
  uv run python scripts/arb_checker.py 0x1dc687... 538 --once
""",
    )
    parser.add_argument("pm_market", help="Polymarket condition_id (0x...) 或 token_id 或 URL")
    parser.add_argument("pf_market", help="Predict.fun market_id 或 URL")
    parser.add_argument("--once", action="store_true", help="只检查一次，不持续监控")
    parser.add_argument("--interval", type=int, default=5, help="刷新间隔（秒）")
    parser.add_argument("--threshold", type=float, default=1.0, help="利润阈值（%%）")

    args = parser.parse_args()

    # 更新全局配置
    REFRESH_INTERVAL = args.interval
    PROFIT_THRESHOLD = args.threshold / 100

    # 解析输入
    pm_condition_id, pm_token1, pm_token2 = parse_pm_input(args.pm_market)
    pf_market, _, pf_markets_list = parse_pf_input(args.pf_market)

    # 确保有 token
    if not pm_token1:
        print(f"[错误] 无法获取 Polymarket token，请检查输入: {args.pm_market}")
        sys.exit(1)

    # 获取 Predict.fun API key
    pf_api_key = os.environ.get("PREDICT_FUN_API_KEY")
    if not pf_api_key:
        print("[警告] 未设置 PREDICT_FUN_API_KEY 环境变量")

    # 判断市场类型：Yes/No 还是 Team vs Team
    is_team_market = False

    # 如果 PF 返回了多个 market（URL 解析的情况）
    if pf_markets_list and len(pf_markets_list) >= 2:
        is_team_market = True

    # 如果 PM 有两个 token，且从 gamma API 能获取 outcome 名称
    if pm_token2 and pm_condition_id:
        # 尝试获取 outcome 名称（带重试）
        from src.lookup import pm_get_tokens
        tokens = None
        for attempt in range(3):
            try:
                tokens = pm_get_tokens(pm_condition_id)
                break
            except Exception as e:
                if attempt < 2:
                    print(f"[重试 {attempt + 1}/3] 获取 PM tokens 失败: {e}")
                    import time as time_module
                    time_module.sleep(2)
                else:
                    print(f"[错误] 获取 PM tokens 失败: {e}")
                    tokens = []

        if tokens is None:
            tokens = []
        pm_outcomes = []
        pm_tokens_with_outcomes = []
        for t in tokens:
            outcome = t.get("outcome", "")
            token_id = t.get("token_id", "")
            if outcome and token_id:
                pm_outcomes.append(outcome)
                pm_tokens_with_outcomes.append((token_id, outcome))

        # 如果 outcome 不是 Yes/No，那就是 Team vs Team
        if pm_outcomes and "Yes" not in pm_outcomes and "No" not in pm_outcomes:
            is_team_market = True

            if is_team_market and pf_markets_list and len(pf_markets_list) >= 2:
                print("=" * 50)
                print("检测到 Team vs Team 市场")
                print("=" * 50)
                print()
                print("【Polymarket】")
                for token_id, outcome in pm_tokens_with_outcomes[:2]:
                    short_id = f"{token_id[:20]}..." if len(token_id) > 20 else token_id
                    print(f"  {outcome}: {short_id}")
                print()
                print("【Predict.fun】")
                for market_id, outcome in pf_markets_list[:2]:
                    print(f"  {outcome}: Market ID {market_id}")
                print()

                # 匹配 outcome
                pm_outcome_names = [o[1] for o in pm_tokens_with_outcomes[:2]]
                pf_outcome_names = [o[1] for o in pf_markets_list[:2]]
                outcome_mapping = match_outcomes(pm_outcome_names, pf_outcome_names)

                if len(outcome_mapping) < 2:
                    print("[警告] 无法自动匹配所有 outcome，尝试按顺序匹配")
                    # 按顺序匹配
                    outcome_mapping = {pm_outcome_names[0]: pf_outcome_names[0], pm_outcome_names[1]: pf_outcome_names[1]}

                print("【Outcome 匹配】")
                for pm, pf in outcome_mapping.items():
                    print(f"  PM {pm} <-> PF {pf}")
                print()

                # 构建 pf_markets 列表，按照 PM 的顺序排列
                pf_markets_ordered = []
                pf_market_dict = {o[1]: o for o in pf_markets_list}
                for pm_outcome in pm_outcome_names:
                    pf_outcome = outcome_mapping.get(pm_outcome)
                    if pf_outcome and pf_outcome in pf_market_dict:
                        pf_markets_ordered.append(pf_market_dict[pf_outcome])

                if len(pf_markets_ordered) < 2:
                    print("[错误] 无法匹配足够的 outcome")
                    sys.exit(1)

                try:
                    if args.once:
                        asyncio.run(single_check_teams(
                            pm_tokens_with_outcomes[:2],
                            pf_markets_ordered[:2],
                            pf_api_key,
                        ))
                    else:
                        asyncio.run(monitor_loop_teams(
                            pm_tokens_with_outcomes[:2],
                            pf_markets_ordered[:2],
                            pf_api_key,
                        ))
                except KeyboardInterrupt:
                    print("\n监控已停止")
                return

    # 标准 Yes/No 市场处理
    print(f"PM Condition: {pm_condition_id[:20]}..." if pm_condition_id and len(pm_condition_id) > 20 else f"PM Condition: {pm_condition_id or 'N/A'}")
    print(f"PM Token: {pm_token1[:20]}..." if len(pm_token1) > 20 else f"PM Token: {pm_token1}")
    print(f"PF Market ID: {pf_market}")
    print()

    try:
        if args.once:
            asyncio.run(single_check(pm_token1, pf_market, pf_api_key))
        else:
            asyncio.run(monitor_loop(pm_token1, pf_market, pf_api_key))
    except KeyboardInterrupt:
        print("\n监控已停止")


if __name__ == "__main__":
    main()
