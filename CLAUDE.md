# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

预测市场量化交易项目，支持 Polymarket、Opinion、Predict.fun 等平台。检测并执行跨平台套利机会。

## Project Structure

```
src/                 # 基础设施层 - 高度可复用的核心模块
  clients/           # 各平台 API 客户端 (PolymarketClient, PredictFunClient)
  models.py          # 数据模型 (Orderbook, Order, Trade, Side, OrderStatus)
  config.py          # 配置类 (PolymarketConfig, PredictFunConfig)
  exceptions.py      # 异常定义
  logging.py         # 日志工具
  lookup.py          # 市场查询工具

src/case/            # 套利案例层 - 具体的套利策略实现
  gold/              # 黄金相关套利案例

scripts/             # 辅助脚本 - 测试、调试、工具脚本
```

## Commands

```bash
uv sync                                    # 安装依赖
uv run pytest src/tests/ -v                # 运行测试
uv run pytest src/tests/test_xxx.py -v     # 运行单个测试
uv run pytest -m "not live"                # 跳过需要 API 连接的测试

# 平台连接测试
uv run python scripts/test_pm_connection.py
uv run python scripts/test_pf_connection.py

# 市场查询 (支持 URL/slug/condition_id/token_id)
uv run python scripts/pm_market_lookup.py "https://polymarket.com/event/..."
uv run python scripts/pf_market_lookup.py <market_id>
```

## Architecture

### Client Pattern
所有客户端继承 `BaseClient` 抽象类，提供统一接口:
- 异步上下文管理器: `async with Client(token_id) as client`
- 核心方法: `get_orderbook()`, `place_order()`, `cancel_order()`, `get_balance()`
- 配置通过 `XxxConfig.from_env()` 从环境变量加载

### Platform Details
| Platform | Chain | SDK | Fee |
|----------|-------|-----|-----|
| Polymarket | Polygon (137) | py_clob_client | 0% |
| Predict.fun | BNB (56) | predict_sdk | 2% |

### Key Concepts
- `token_id`: 市场中单个 outcome 的唯一标识 (用于下单)
- `market_id`: Predict.fun 的市场 ID (用于获取 orderbook)
- Orderbook: `bids` 按价格降序，`asks` 按价格升序

## Environment Variables

```
# Polymarket
PM_PRIVATE_KEY, PM_API_KEY, PM_API_SECRET, PM_API_PASSPHRASE, PM_PROXY_ADDRESS

# Predict.fun
PREDICT_FUN_API_KEY, PREDICT_FUN_PRIVATE_KEY, PREDICT_FUN_SMART_WALLET
```
