#!/usr/bin/env python3
"""黄金市场跨平台套利检测脚本 - Polymarket (7个价格区间) + Predict.fun (1个NO)"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
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

from src.clients import PolymarketClient, PredictFunClient
from src.models import Side, Order
from src.exceptions import InsufficientBalanceError, OrderRejectedError
from src.lookup import MarketInfo, lookup_pm_market, lookup_pf_market, pm_get_tokens

# 加载 .env 文件
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# 配置常量
REFRESH_INTERVAL = 5  # 刷新间隔（秒）
PROFIT_THRESHOLD = 0.01  # 利润阈值（1%）
PM_FEE = 0.0  # Polymarket 费率
PF_FEE = 0.02  # Predict.fun 费率（2%）

# 数据库路径
DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"

# API 端点
PM_CLOB_HOST = "https://clob.polymarket.com"
PM_GAMMA_HOST = "https://gamma-api.polymarket.com"
PF_API_HOST = "https://api.predict.fun/v1"

# Telegram 配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8023765575:AAFKn2Nn5TNxFqQ1nYQ3y2A5IUqowpzvGAs")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "-5088762482")


# ============ 数据结构 ============

@dataclass
class Orderbook:
    """Orderbook 数据结构"""
    bids: list[tuple[float, float]]  # [(price, size), ...] 降序
    asks: list[tuple[float, float]]  # [(price, size), ...] 升序
    timestamp: float


@dataclass
class GoldArbResult:
    """金价套利分析结果"""
    strategy: str  # "PF买NO + PM买全部范围(>=4400)"

    # PF 端
    pf_no_price: float
    pf_no_cost: float  # 含2%费率

    # PM 端（7个市场）
    pm_markets: list[tuple[str, str]]  # [(token_id, "4400-4500"), ...]
    pm_yes_prices: list[float]  # 7个价格
    pm_total_cost: float  # sum(prices)

    # 汇总
    total_cost: float
    profit_pct: float
    best_amount: float
    expected_profit: float
    shares_per_market: float  # 每个市场买多少份


@dataclass
class GoldDepthAnalysis:
    """深度分析"""
    amount: float
    pf_avg_price: float
    pf_slippage: float
    pf_total_cost: float
    pm_avg_prices: list[float]  # 7个
    pm_worst_slippage: float  # max(7个滑点)
    pm_total_cost: float
    total_cost: float
    profit_pct: float
    expected_profit: float


# ============ 数据库 ============

def init_db():
    """初始化数据库"""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS gold_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            -- 市场信息
            pf_market INTEGER NOT NULL,
            pm_markets TEXT NOT NULL,  -- JSON: [{"token": "...", "range": "4400-4500"}, ...]
            strategy TEXT,

            -- 价格信息
            pf_no_price REAL,
            pm_yes_prices TEXT,  -- JSON: [0.12, 0.10, ...]
            total_cost REAL,
            profit_pct REAL,

            -- 下单信息
            trade_amount REAL,
            shares REAL,
            pf_order_id TEXT,
            pm_order_ids TEXT,  -- JSON: ["order1", "order2", ...]

            -- 状态
            success BOOLEAN,
            error TEXT,
            partial_success BOOLEAN,
            succeeded_count INTEGER
        )
    ''')
    conn.commit()
    conn.close()


def record_trade(
    pf_market: int,
    pm_markets: list[tuple[str, str]],
    arb_result: GoldArbResult,
    trade_amount: float,
    pf_order: Order | None,
    pm_orders: list[Order | None],
    success: bool,
    error: str | None = None,
    partial_success: bool = False,
    succeeded_count: int = 0,
):
    """记录交易到数据库"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            INSERT INTO gold_trades (
                pf_market, pm_markets, strategy,
                pf_no_price, pm_yes_prices, total_cost, profit_pct,
                trade_amount, shares, pf_order_id, pm_order_ids,
                success, error, partial_success, succeeded_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            pf_market,
            json.dumps([{"token": t, "range": r} for t, r in pm_markets]),
            arb_result.strategy,
            arb_result.pf_no_price,
            json.dumps(arb_result.pm_yes_prices),
            arb_result.total_cost,
            arb_result.profit_pct,
            trade_amount,
            arb_result.shares_per_market,
            pf_order.id if pf_order else None,
            json.dumps([o.id if o else None for o in pm_orders]) if pm_orders else None,
            success,
            error,
            partial_success,
            succeeded_count,
        ))
        conn.commit()
        conn.close()
        print(f"  [DB] 交易已记录")
    except Exception as e:
        print(f"  [DB] 记录失败: {e}")


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


async def fetch_pf_orderbook(http: httpx.AsyncClient, market_id: int, api_key: str = None, outcome: str = "NO") -> Orderbook:
    """获取 Predict.fun orderbook

    Args:
        http: HTTP client
        market_id: Market ID
        api_key: API key
        outcome: "YES" or "NO" (default: "NO")
    """
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    # 先获取市场信息，找到对应 outcome 的 token ID
    market_resp = await http.get(f"{PF_API_HOST}/markets/{market_id}", headers=headers)
    market_resp.raise_for_status()
    market_data = market_resp.json().get("data", {})

    # 找到指定 outcome 的 token ID
    token_id = None
    for o in market_data.get("outcomes", []):
        if o.get("name") == outcome:
            token_id = o.get("onChainId")
            break

    if not token_id:
        raise ValueError(f"No {outcome} outcome found for market {market_id}")

    # 获取该 outcome 的 orderbook
    params = {"tokenId": token_id}
    resp = await http.get(
        f"{PF_API_HOST}/markets/{market_id}/orderbook",
        headers=headers,
        params=params
    )
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


# ============ 辅助函数 ============

def retry_request(func, max_retries=3, delay=2, url_hint=""):
    """重试 HTTP 请求"""
    import time
    last_error = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_error = e
            error_type = type(e).__name__
            error_msg = str(e)

            if attempt < max_retries - 1:
                print(f"  [重试 {attempt + 1}/{max_retries}] {error_type}: {error_msg}")
                if url_hint:
                    print(f"  URL: {url_hint}")
                print(f"  等待 {delay} 秒后重试...")
                time.sleep(delay)
            else:
                print(f"  [失败] 重试 {max_retries} 次后仍失败")
                print(f"  错误类型: {error_type}")
                print(f"  错误信息: {error_msg}")
                if url_hint:
                    print(f"  请求URL: {url_hint}")
                raise


# ============ 市场解析 ============

def parse_gold_pm_event(event_url: str) -> list[tuple[str, str]]:
    """从PM事件URL提取所有7个>=4400的市场

    实现:
    1. 提取event slug
    2. 查询Gamma API获取所有markets
    3. 解析question提取价格区间
    4. 过滤>=4400的市场
    5. 提取YES token_id
    6. 验证恰好7个市场

    返回: [(token_id, "4400-4500"), ...]
    """
    # 提取 slug
    match = re.search(r'polymarket\.com/event/([^/]+)', event_url)
    if not match:
        raise ValueError(f"Invalid PM event URL: {event_url}")

    slug = match.group(1).rstrip('\\')

    # 查询 Gamma API（带重试）
    api_url = f"{PM_GAMMA_HOST}/events?slug={slug}"
    def _fetch():
        resp = httpx.get(
            f"{PM_GAMMA_HOST}/events",
            params={"slug": slug},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=10
        )
        resp.raise_for_status()
        return resp

    resp = retry_request(_fetch, url_hint=api_url)

    events = resp.json()
    if not events:
        raise ValueError(f"No event found for slug: {slug}")

    event = events[0]
    markets = event.get("markets", [])

    # 解析和过滤市场
    gold_markets = []

    for market in markets:
        question = market.get("question", "").lower()

        # 尝试提取范围
        range_match = re.search(r'between.*?(\d+).*?and.*?(\d+)', question)
        above_match = re.search(r'above.*?(\d+)', question)

        if range_match:
            lower = int(range_match.group(1))
            upper = int(range_match.group(2))
            if lower >= 4400:
                range_label = f"{lower}-{upper}"
                # 获取 YES token
                condition_id = market.get("conditionId")
                tokens = pm_get_tokens(condition_id)
                yes_token = next(
                    (t["token_id"] for t in tokens if t.get("outcome") == "Yes"),
                    None
                )
                if yes_token:
                    gold_markets.append((yes_token, range_label, lower))

        elif above_match:
            threshold = int(above_match.group(1))
            if threshold >= 4400:
                range_label = f">{threshold}"
                condition_id = market.get("conditionId")
                tokens = pm_get_tokens(condition_id)
                yes_token = next(
                    (t["token_id"] for t in tokens if t.get("outcome") == "Yes"),
                    None
                )
                if yes_token:
                    gold_markets.append((yes_token, range_label, 99999))  # Sort last

    # 按下限排序
    gold_markets.sort(key=lambda x: x[2])

    # 验证数量
    if len(gold_markets) != 7:
        raise ValueError(
            f"Expected 7 gold markets (>=4400), found {len(gold_markets)}. "
            f"Markets: {[m[1] for m in gold_markets]}"
        )

    # 返回不带排序键
    return [(token, label) for token, label, _ in gold_markets]


def parse_pf_market(market_url: str, api_key: str = None) -> int:
    """解析PF市场URL并提取market_id

    Args:
        market_url: PF URL like "https://predict.fun/market/will-gold-close-above-4400-in-2025"

    Returns:
        market_id (int)
    """
    # 如果是纯数字，直接返回
    if market_url.isdigit():
        return int(market_url)

    # 提取 slug
    match = re.search(r'predict\.fun/market/([^/?]+)', market_url)
    if not match:
        raise ValueError(f"Invalid PF market URL: {market_url}")

    slug = match.group(1)

    # 抓取页面（带重试）
    page_url = f"https://predict.fun/market/{slug}"
    def _fetch():
        resp = httpx.get(
            page_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=30,
            follow_redirects=True
        )
        resp.raise_for_status()
        return resp

    resp = retry_request(_fetch, url_hint=page_url)

    # 提取 market ID
    market_ids = re.findall(r'marketId=(\d+)', resp.text)
    if not market_ids:
        market_ids = re.findall(r'"market","(\d+)"', resp.text)

    if not market_ids:
        raise ValueError(f"Could not find market_id in {market_url}")

    market_id = int(market_ids[0])
    return market_id


# ============ 套利分析 ============

def analyze_gold_arb_opportunity(
    pm_books: list[tuple[Orderbook, str]],  # [(book, range_label), ...]
    pf_book: Orderbook,
    pm_tokens: list[tuple[str, str]],  # [(token_id, range_label), ...]
    amounts: list[float] = None,
) -> tuple[GoldArbResult | None, list[GoldDepthAnalysis]]:
    """分析金价套利机会

    逻辑:
    1. 提取价格:
       pf_no_price = 1 - pf_book.bids[0][0]
       pm_yes_prices = [book.asks[0][0] for book, _ in pm_books]

    2. 计算成本:
       total_cost = pf_no_price × 1.02 + sum(pm_yes_prices)
       profit_pct = (1.0 - total_cost) × 100

    3. 深度分析（每个amount）:
       shares = amount / total_cost
       计算每个市场的avg_price和slippage

    4. 返回最优金额的结果
    """
    if amounts is None:
        amounts = [100, 500, 1000, 2000, 5000]

    # 验证输入
    if len(pm_books) != 7:
        raise ValueError(f"Expected 7 PM orderbooks, got {len(pm_books)}")

    # 提取基础价格 - 直接使用 NO ask（因为我们要买 NO）
    pf_no_price = pf_book.asks[0][0] if pf_book.asks else 0.0

    pm_yes_prices = []
    for book, _ in pm_books:
        price = book.asks[0][0] if book.asks else 1.0
        pm_yes_prices.append(price)

    # 计算基础成本
    pf_no_cost = pf_no_price * (1 + PF_FEE)
    pm_total_cost = sum(pm_yes_prices)
    total_cost = pf_no_cost + pm_total_cost
    base_profit_pct = (1.0 - total_cost) * 100

    # 深度分析
    depth_results = []
    best_result = None
    best_profit = -float("inf")
    best_amount = 0

    for amount in amounts:
        # 计算份额
        shares = amount / total_cost

        # PF 端 - 买 NO 直接吃 NO asks
        pf_avg, _ = calc_fill_price(pf_book.asks, shares)
        pf_cost = shares * pf_avg * (1 + PF_FEE) if pf_avg > 0 else pf_no_cost * shares
        pf_slippage = (pf_avg - pf_no_price) / pf_no_price * 100 if pf_no_price > 0 else 0

        # PM 端 - 买每个 YES 市场
        pm_avg_prices = []
        pm_costs = []
        pm_slippages = []

        for i, (book, _) in enumerate(pm_books):
            avg_price, _ = calc_fill_price(book.asks, shares)
            if avg_price <= 0:
                avg_price = pm_yes_prices[i]
            pm_avg_prices.append(avg_price)
            pm_costs.append(shares * avg_price)

            slippage = (avg_price - pm_yes_prices[i]) / pm_yes_prices[i] * 100 if pm_yes_prices[i] > 0 else 0
            pm_slippages.append(slippage)

        pm_total = sum(pm_costs)
        pm_worst_slippage = max(pm_slippages) if pm_slippages else 0

        total = pf_cost + pm_total
        profit_pct = (amount - total) / amount * 100
        expected_profit = amount * profit_pct / 100

        depth_results.append(GoldDepthAnalysis(
            amount=amount,
            pf_avg_price=pf_avg if pf_avg > 0 else pf_no_price,
            pf_slippage=pf_slippage,
            pf_total_cost=pf_cost,
            pm_avg_prices=pm_avg_prices,
            pm_worst_slippage=pm_worst_slippage,
            pm_total_cost=pm_total,
            total_cost=total,
            profit_pct=profit_pct,
            expected_profit=expected_profit,
        ))

        if expected_profit > best_profit:
            best_profit = expected_profit
            best_amount = amount

    # 创建结果如果有利可图
    if base_profit_pct > 0:
        best_result = GoldArbResult(
            strategy="PF买NO + PM买全部范围(>=4400)",
            pf_no_price=pf_no_price,
            pf_no_cost=pf_no_cost,
            pm_markets=pm_tokens,
            pm_yes_prices=pm_yes_prices,
            pm_total_cost=pm_total_cost,
            total_cost=total_cost,
            profit_pct=base_profit_pct,
            best_amount=best_amount,
            expected_profit=best_amount * base_profit_pct / 100,
            shares_per_market=best_amount / total_cost,
        )

    return best_result, depth_results


# ============ 报告生成 ============

def print_gold_report(
    pm_markets: list[tuple[str, str]],  # [(token_id, label), ...]
    pf_market: int,
    pm_books: list[Orderbook],  # 7 orderbooks
    pf_book: Orderbook,
    arb_result: GoldArbResult | None,
    depth_analysis: list[GoldDepthAnalysis],
):
    """打印详细金价套利报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 70)
    print("                   金价跨平台套利分析报告")
    print("=" * 70)
    print(f"时间: {now}")
    print()

    # PM Markets
    print("【Polymarket - 7个价格范围市场】")
    pm_total_cost = 0
    for (token_id, label), book in zip(pm_markets, pm_books):
        ask = book.asks[0][0] if book.asks else None
        if ask:
            pm_total_cost += ask
            print(f"  {label:>12}: Ask={ask:.4f} (token: {token_id[:10]}...)")
        else:
            print(f"  {label:>12}: No orderbook")
    print()
    print(f"  PM 总成本: {pm_total_cost:.4f}")
    print()

    # PF Market
    print("【Predict.fun - 金价>4400】")
    print(f"  Market ID: {pf_market}")

    pf_no_ask = pf_book.asks[0][0] if pf_book.asks else None
    if pf_no_ask:
        pf_no_cost = pf_no_ask * (1 + PF_FEE)
        print(f"  NO Ask:  {pf_no_ask:.4f}")
        print(f"  含费成本: {pf_no_cost:.4f} ({PF_FEE*100:.0f}%费率)")
    else:
        print("  No orderbook")
    print()

    # 套利机会
    print("【套利机会】")
    if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
        print("存在套利机会!")
        print()
        print(f"策略: {arb_result.strategy}")
        print()
        print("成本明细:")
        print(f"  PF NO:         ${arb_result.pf_no_cost:.4f}")
        print(f"  PM YES (7个):  ${arb_result.pm_total_cost:.4f}")
        print("  " + "-" * 30)
        print(f"  总成本:        ${arb_result.total_cost:.4f}")
        print()
        print("利润分析:")
        print(f"  总成本:   {arb_result.total_cost:.4f}")
        print(f"  回报:     1.0000 (保证)")
        print(f"  净利润:   {arb_result.profit_pct:+.2f}%")
        print()
        print(f"  最优金额: ${arb_result.best_amount:.0f}")
        print(f"  预期收益: ${arb_result.expected_profit:.2f}")
    else:
        print("无套利机会")
        if arb_result:
            print(f"  利润率: {arb_result.profit_pct:.2f}% (低于阈值 {PROFIT_THRESHOLD*100:.1f}%)")
    print()

    print("=" * 70)


# ============ 辅助函数 ============

def play_alert():
    """播放提示音（macOS）"""
    try:
        subprocess.run(
            ["afplay", "/System/Library/Sounds/Glass.aiff"],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass


def send_telegram_alert(arb_result: GoldArbResult):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    message = f"""🚨 *发现金价套利机会!*

*策略*: {arb_result.strategy}

💰 *总成本*: {arb_result.total_cost:.4f}
📈 *利润率*: {arb_result.profit_pct:.2f}%
💵 *最优金额*: ${arb_result.best_amount:.0f}
🎯 *预期收益*: ${arb_result.expected_profit:.2f}

*PF NO*: {arb_result.pf_no_price:.4f} (含费: {arb_result.pf_no_cost:.4f})
*PM YES总计*: {arb_result.pm_total_cost:.4f}
"""

    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Telegram] 发送失败: {e}")


# ============ 主循环 ============

async def single_check_gold(
    pm_markets: list[tuple[str, str]],
    pf_market: int,
    pf_api_key: str = None,
):
    """单次检查"""
    async with httpx.AsyncClient(timeout=30) as http:
        # 获取所有 orderbook
        fetch_tasks = [
            fetch_pm_orderbook(http, token_id)
            for token_id, _ in pm_markets
        ]
        fetch_tasks.append(
            fetch_pf_orderbook(http, pf_market, pf_api_key)
        )

        books = await asyncio.gather(*fetch_tasks)
        pm_books = books[:-1]
        pf_book = books[-1]

        # 分析
        arb_result, depth_analysis = analyze_gold_arb_opportunity(
            list(zip(pm_books, [label for _, label in pm_markets])),
            pf_book,
            pm_markets,
        )

        # 打印报告
        print_gold_report(
            pm_markets, pf_market,
            pm_books, pf_book,
            arb_result, depth_analysis
        )


# ============ 监控循环 ============

async def monitor_loop_gold(
    pm_markets: list[tuple[str, str]],
    pf_market: int,
    pf_api_key: str = None,
    auto_trade: bool = False,
    trade_amount: float = None,
    dry_run: bool = False,
):
    """持续监控金价套利机会"""
    async with httpx.AsyncClient(timeout=30) as http:
        print(f"开始监控金价套利机会...")
        print(f"  PM: 7个价格区间市场 (>=4400)")
        print(f"  PF: Market {pf_market}")
        print(f"  刷新间隔: {REFRESH_INTERVAL}秒")
        print(f"  利润阈值: {PROFIT_THRESHOLD*100:.1f}%")
        print(f"  自动交易: {'启用' if auto_trade else '禁用'}")
        if auto_trade:
            if trade_amount:
                print(f"  交易金额: ${trade_amount:.2f}")
            if dry_run:
                print(f"  模式: DRY-RUN（模拟）")
        print()
        print("按 Ctrl+C 停止监控")
        print()

        last_alert = 0

        while True:
            try:
                # 获取所有 orderbook
                fetch_tasks = [
                    fetch_pm_orderbook(http, token_id)
                    for token_id, _ in pm_markets
                ]
                fetch_tasks.append(
                    fetch_pf_orderbook(http, pf_market, pf_api_key)
                )

                books = await asyncio.gather(*fetch_tasks)
                pm_books = books[:-1]
                pf_book = books[-1]

                # 分析
                arb_result, depth_analysis = analyze_gold_arb_opportunity(
                    list(zip(pm_books, [label for _, label in pm_markets])),
                    pf_book,
                    pm_markets,
                )

                # 打印报告
                print_gold_report(
                    pm_markets, pf_market,
                    pm_books, pf_book,
                    arb_result, depth_analysis
                )

                # 发现机会
                if arb_result and arb_result.profit_pct >= PROFIT_THRESHOLD * 100:
                    current_time = time()
                    if current_time - last_alert > 60:  # 限制频率：60秒
                        play_alert()
                        send_telegram_alert(arb_result)
                        last_alert = current_time

                        # 自动交易
                        if auto_trade:
                            print("\n[自动交易] 发现套利机会，准备执行...")
                            result = await execute_gold_arb_trade(
                                arb_result,
                                pm_markets,
                                pf_market,
                                trade_amount,
                                dry_run
                            )

                            if result["success"]:
                                if result.get("dry_run"):
                                    print("[自动交易] DRY-RUN 模拟成功")
                                else:
                                    print("[自动交易] 交易成功！暂停30分钟")
                                    await asyncio.sleep(1800)  # 冷却30分钟
                            else:
                                print(f"[自动交易] 交易失败: {result.get('error', '未知错误')}")

                await asyncio.sleep(REFRESH_INTERVAL)

            except KeyboardInterrupt:
                print("\n监控已停止")
                break
            except Exception as e:
                print(f"[错误] {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(REFRESH_INTERVAL)


# ============ 交易执行 ============

def send_telegram_trade_result(
    success: bool,
    arb_result: GoldArbResult,
    pm_orders: list[Order | None] = None,
    pf_order: Order | None = None,
    error: str | None = None,
):
    """发送交易执行结果通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    if success:
        message = f"""✅ *金价套利交易成功!*

*策略*: {arb_result.strategy}
*总投入*: ${arb_result.best_amount:.0f}
*预期收益*: ${arb_result.expected_profit:.2f}

*PF订单*: {pf_order.price:.4f} x {pf_order.size:.2f} (ID: {pf_order.id[:10]}...)
*PM订单*: 7个市场全部成功
"""
    else:
        message = f"""❌ *金价套利交易失败!*

*策略*: {arb_result.strategy}
*错误*: {error or '未知错误'}
"""

    try:
        httpx.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Telegram] 发送交易结果失败: {e}")


async def execute_gold_arb_trade(
    arb_result: GoldArbResult,
    pm_tokens: list[tuple[str, str]],  # 7个
    pf_market: int,
    trade_amount: float | None = None,
    dry_run: bool = False,
) -> dict:
    """执行金价套利交易（8个市场并行）

    Returns:
        {
            "success": bool,
            "pm_orders": list[Order | None],
            "pf_order": Order | None,
            "error": str | None,
            "dry_run": bool,
        }
    """
    total_amount = trade_amount if trade_amount else arb_result.best_amount
    shares = total_amount / arb_result.total_cost

    print(f"\n[交易] 开始执行金价套利交易...")
    print(f"  策略: {arb_result.strategy}")
    print(f"  总金额: ${total_amount:.2f}")
    print(f"  每市场份额: {shares:.4f}")
    print(f"  PF NO: ${shares * arb_result.pf_no_price * (1 + PF_FEE):.2f}")
    print(f"  PM YES (7市场): ${shares * arb_result.pm_total_cost:.2f}")

    # Dry-run 模式
    if dry_run:
        print(f"\n[DRY-RUN] 模拟交易信息:")
        print(f"  PF Market {pf_market}: 买入 {shares:.4f} 份 NO @ {arb_result.pf_no_price:.4f}")
        for i, (token_id, label) in enumerate(pm_tokens):
            print(f"  PM {label}: 买入 {shares:.4f} 份 YES @ {arb_result.pm_yes_prices[i]:.4f}")
        print(f"\n[DRY-RUN] 模拟交易完成（未实际下单）")
        return {"success": True, "dry_run": True}

    pm_clients = []
    pf_client = None

    try:
        # 初始化客户端
        print(f"  初始化客户端...")
        pf_client = PredictFunClient(market_id=pf_market)
        pm_clients = [
            PolymarketClient(token_id=token_id)
            for token_id, _ in pm_tokens
        ]

        # 并行连接
        print(f"  连接客户端...")
        await asyncio.gather(
            pf_client.connect(),
            *[client.connect() for client in pm_clients]
        )
        print(f"  已连接 1 PF + 7 PM 客户端")

        # 并行下单
        print(f"  正在下单...")
        pf_amount = shares * arb_result.pf_no_price * (1 + PF_FEE)

        results = await asyncio.gather(
            pf_client.place_market_order(side=Side.BUY, value=pf_amount),
            *[
                client.place_market_order(side=Side.BUY, size=shares)
                for client in pm_clients
            ],
            return_exceptions=True
        )

        # 解析结果
        pf_result = results[0]
        pm_results = results[1:]

        pf_success = isinstance(pf_result, Order)
        pm_successes = [isinstance(r, Order) for r in pm_results]
        succeeded_count = sum(pm_successes) + (1 if pf_success else 0)

        # 打印状态
        if pf_success:
            print(f"  ✓ PF NO: {pf_result.id[:20]}... @ {pf_result.price:.4f} x {pf_result.size:.2f}")
        else:
            print(f"  ✗ PF NO: {pf_result}")

        for i, (success, (_, label)) in enumerate(zip(pm_successes, pm_tokens)):
            if success:
                order = pm_results[i]
                print(f"  ✓ PM {label}: {order.id[:20]}... @ {order.price:.4f} x {order.size:.2f}")
            else:
                print(f"  ✗ PM {label}: {pm_results[i]}")

        # 处理结果
        if succeeded_count == 8:
            # 完全成功
            print(f"  🎉 金价套利交易完成! (8/8)")
            send_telegram_trade_result(True, arb_result, pm_results, pf_result)
            record_trade(pf_market, pm_tokens, arb_result, total_amount, pf_result, pm_results, True)
            return {
                "success": True,
                "pm_orders": pm_results,
                "pf_order": pf_result,
                "error": None,
            }

        elif succeeded_count > 0:
            # 部分成功 - 回滚
            print(f"  [警告] 部分成功 ({succeeded_count}/8)，开始回滚...")

            rollback_tasks = []

            # 回滚 PF
            if pf_success:
                async def rollback_pf():
                    try:
                        position = await pf_client.get_position()
                        if position > 0:
                            sell_order = await pf_client.place_market_order(
                                side=Side.SELL,
                                size=position
                            )
                            print(f"  [回滚] PF 卖出 {sell_order.size:.2f} @ {sell_order.price:.4f}")
                    except Exception as e:
                        print(f"  [回滚失败] PF: {e}")
                        with open("CRITICAL_ERRORS.log", "a") as f:
                            f.write(f"{datetime.now()}: PF rollback failed: {e}\n")

                rollback_tasks.append(rollback_pf())

            # 回滚 PM
            for i, (success, client, (_, label)) in enumerate(zip(pm_successes, pm_clients, pm_tokens)):
                if success:
                    async def rollback_pm(c=client, lbl=label):
                        try:
                            position = await c.get_position()
                            if position > 0:
                                sell_order = await c.place_market_order(
                                    side=Side.SELL,
                                    size=position
                                )
                                print(f"  [回滚] PM {lbl} 卖出 {sell_order.size:.2f} @ {sell_order.price:.4f}")
                        except Exception as e:
                            print(f"  [回滚失败] PM {lbl}: {e}")
                            with open("CRITICAL_ERRORS.log", "a") as f:
                                f.write(f"{datetime.now()}: PM {lbl} rollback failed: {e}\n")

                    rollback_tasks.append(rollback_pm())

            # 执行回滚
            await asyncio.gather(*rollback_tasks, return_exceptions=True)

            error_msg = f"部分执行 ({succeeded_count}/8 成功)，已回滚"
            send_telegram_trade_result(False, arb_result, None, None, error_msg)
            record_trade(pf_market, pm_tokens, arb_result, total_amount, pf_result if pf_success else None, pm_results, False, error_msg, True, succeeded_count)

            return {
                "success": False,
                "partial_success": True,
                "succeeded_count": succeeded_count,
                "pm_orders": pm_results,
                "pf_order": pf_result,
                "error": error_msg,
            }

        else:
            # 全部失败
            error_msg = "所有订单失败 (0/8)"
            print(f"  ✗ {error_msg}")
            send_telegram_trade_result(False, arb_result, None, None, error_msg)
            record_trade(pf_market, pm_tokens, arb_result, total_amount, None, None, False, error_msg)

            return {
                "success": False,
                "pm_orders": None,
                "pf_order": None,
                "error": error_msg,
            }

    except Exception as e:
        error_msg = f"交易执行异常: {e}"
        print(f"  [错误] {error_msg}")
        import traceback
        traceback.print_exc()
        send_telegram_trade_result(False, arb_result, None, None, error_msg)
        record_trade(pf_market, pm_tokens, arb_result, total_amount, None, None, False, error_msg)

        return {
            "success": False,
            "error": error_msg,
        }

    finally:
        # 关闭所有客户端
        if pf_client:
            try:
                await pf_client.close()
            except Exception:
                pass
        for client in pm_clients:
            try:
                await client.close()
            except Exception:
                pass


# ============ CLI ============

def main():
    """主入口"""
    global REFRESH_INTERVAL, PROFIT_THRESHOLD

    parser = argparse.ArgumentParser(
        description="金价跨平台套利检测 (1 PF + 7 PM)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单次检查
  uv run python scripts/gold_arb_checker.py \\
    --pm-event "https://polymarket.com/event/what-price-will-gold..." \\
    --pf-market "https://predict.fun/market/will-gold-close..." \\
    --check

  # 持续监控
  uv run python scripts/gold_arb_checker.py \\
    --pm-event "..." \\
    --pf-market "..." \\
    --monitor
""",
    )

    parser.add_argument("--pm-event", required=True, help="PM event URL")
    parser.add_argument("--pf-market", required=True, help="PF market URL or ID")
    parser.add_argument("--check", action="store_true", help="单次检查")
    parser.add_argument("--monitor", action="store_true", help="持续监控")
    parser.add_argument("--auto-trade", action="store_true", help="启用自动交易")
    parser.add_argument("--trade-amount", type=float, help="交易金额（美元）")
    parser.add_argument("--dry-run", action="store_true", help="模拟交易（不实际下单）")
    parser.add_argument("--interval", type=int, default=5, help="刷新间隔（秒）")
    parser.add_argument("--threshold", type=float, default=1.0, help="利润阈值（%）")

    args = parser.parse_args()

    # 初始化数据库
    init_db()

    # 启动日志
    print("=" * 60)
    print(f"🚀 金价套利监控启动")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   PM Event: {args.pm_event}")
    print(f"   PF Market: {args.pf_market}")
    print(f"   刷新间隔: {args.interval}秒")
    print(f"   利润阈值: {args.threshold}%")
    print("=" * 60)
    print()

    # 更新全局配置
    REFRESH_INTERVAL = args.interval
    PROFIT_THRESHOLD = args.threshold / 100

    # 连接测试
    print("[0/3] 测试网络连接...")
    try:
        test_resp = httpx.get("https://www.google.com", timeout=5)
        print("  ✓ 基础网络连接正常")
    except Exception as e:
        print(f"  ✗ 基础网络连接失败: {e}")
        print("  提示: 请检查网络连接或代理设置")
    print()

    # 解析市场
    try:
        print("[1/3] 解析 PM 事件...")
        pm_markets = parse_gold_pm_event(args.pm_event)
        print(f"  ✓ 找到 {len(pm_markets)} 个价格区间市场:")
        for token_id, label in pm_markets:
            print(f"    {label:>12}: {token_id[:20]}...")
        print()

        print("[2/3] 解析 PF 市场...")
        pf_api_key = os.environ.get("PREDICT_FUN_API_KEY")
        pf_market_id = parse_pf_market(args.pf_market, pf_api_key)
        print(f"  ✓ PF Market ID: {pf_market_id}")
        print()

        print("[3/3] 测试 API 连接...")
        # 测试 Polymarket API
        try:
            test_pm = httpx.get(
                f"{PM_GAMMA_HOST}/markets",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=5
            )
            if test_pm.status_code == 200:
                print(f"  ✓ Polymarket API 连接正常")
            else:
                print(f"  ⚠ Polymarket API 返回 {test_pm.status_code}")
        except Exception as e:
            print(f"  ✗ Polymarket API 连接失败: {type(e).__name__}: {e}")

        # 测试 Predict.fun API
        try:
            test_pf = httpx.get(
                f"{PF_API_HOST}/markets",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=5
            )
            if test_pf.status_code == 200:
                print(f"  ✓ Predict.fun API 连接正常")
            else:
                print(f"  ⚠ Predict.fun API 返回 {test_pf.status_code}")
        except Exception as e:
            print(f"  ✗ Predict.fun API 连接失败: {type(e).__name__}: {e}")
        print()

    except Exception as e:
        print(f"[错误] 市场解析失败: {e}")
        import traceback
        print("\n详细错误信息:")
        traceback.print_exc()
        return 1

    # 执行
    try:
        if args.check:
            asyncio.run(single_check_gold(pm_markets, pf_market_id, pf_api_key))
        elif args.monitor:
            asyncio.run(monitor_loop_gold(
                pm_markets,
                pf_market_id,
                pf_api_key,
                auto_trade=args.auto_trade,
                trade_amount=args.trade_amount,
                dry_run=args.dry_run,
            ))
        else:
            print("请指定 --check 或 --monitor")
            return 1
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
