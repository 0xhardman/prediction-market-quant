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
        match = re.search(r'polymarket\.com/event/[^/]+/([^?]+)', input_str)
        if match:
            slug = match.group(1).rstrip("\\")
            from src.lookup import pm_lookup_by_slug
            data = pm_lookup_by_slug(slug)
            if data:
                condition_id = data.get("conditionId", "")
                tokens = pm_get_tokens(condition_id) if condition_id else []
                yes_token = ""
                no_token = ""
                for t in tokens:
                    if t.get("outcome") == "Yes":
                        yes_token = t.get("token_id", "")
                    elif t.get("outcome") == "No":
                        no_token = t.get("token_id", "")
                return condition_id, yes_token, no_token

    return "", input_str, ""


def parse_pf_input(input_str: str) -> tuple[int, str]:
    """解析 Predict.fun 输入，返回 (market_id, token_id)

    支持格式:
    - market_id 直接传入: 12345
    - market_id:token_id: 12345:0x...
    - URL: https://predict.fun/market/xxx
    """
    input_str = input_str.strip()

    # 如果包含冒号，分割为 market_id:token_id
    if ":" in input_str and not input_str.startswith("http"):
        parts = input_str.split(":", 1)
        return int(parts[0]), parts[1]

    # 如果是纯数字，作为 market_id
    if input_str.isdigit():
        return int(input_str), ""

    # TODO: 支持 URL 解析
    return int(input_str), ""


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

                # 发现套利机会时播放提示音（限制频率）
                if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
                    if time() - last_alert > 30:  # 30秒内不重复提醒
                        play_alert()
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
    pm_condition_id, pm_yes_token, pm_no_token = parse_pm_input(args.pm_market)
    pf_market, _ = parse_pf_input(args.pf_market)

    # 确保有 yes_token
    if not pm_yes_token:
        print(f"[错误] 无法获取 Polymarket Yes token，请检查输入: {args.pm_market}")
        sys.exit(1)

    print(f"PM Condition: {pm_condition_id[:20]}..." if pm_condition_id and len(pm_condition_id) > 20 else f"PM Condition: {pm_condition_id or 'N/A'}")
    print(f"PM Yes Token: {pm_yes_token[:20]}..." if len(pm_yes_token) > 20 else f"PM Yes Token: {pm_yes_token}")
    print(f"PF Market ID: {pf_market}")
    print()

    # 获取 Predict.fun API key
    pf_api_key = os.environ.get("PREDICT_FUN_API_KEY")
    if not pf_api_key:
        print("[警告] 未设置 PREDICT_FUN_API_KEY 环境变量")

    try:
        if args.once:
            asyncio.run(single_check(pm_yes_token, pf_market, pf_api_key))
        else:
            asyncio.run(monitor_loop(pm_yes_token, pf_market, pf_api_key))
    except KeyboardInterrupt:
        print("\n监控已停止")


if __name__ == "__main__":
    main()
