# PM Arbitrage

多平台预测市场套利系统，支持 Polymarket、Opinion、Predict.fun 三方互套。

## 支持平台

| 平台 | 链 | 数据获取 | Taker Fee |
|------|-----|---------|-----------|
| Polymarket | Polygon | WebSocket | 0% |
| Opinion | BNB Chain | REST | 1% |
| Predict.fun | BNB Chain | REST | 2% |

## 功能特点

- 实时监控三个平台的订单簿
- 自动检测互补 outcome 套利机会 (Yes+No < 1)
- 支持任意两个平台配对套利
- 混合执行策略: Polymarket FOK + 其他平台激进限价单
- 风险控制: 敞口限制、价格新鲜度检查、超时取消

## 安装

```bash
# 使用 uv (推荐)
uv sync

# 或使用 pip
pip install -e .
```

## 配置

1. 复制环境变量模板:
```bash
cp .env.example .env
```

2. 编辑 `.env` 填入你的 API 凭据

3. 编辑 `config.yaml` 配置市场映射表

## 使用

```bash
# 运行套利系统
uv run python -m src.main

# 或
uv run arbitrage
```

## 工具脚本

### 生成 Polymarket 凭据

```bash
uv run python scripts/generate_pm_creds.py
```

输入私钥后自动生成 API Key/Secret/Passphrase，可选择自动写入 `.env`。

### 市场查询工具

双向查询 Polymarket 市场信息，支持多种输入格式：

```bash
# 从网站 URL 查询
uv run python scripts/pm_market_lookup.py "https://polymarket.com/event/xxx/market-slug"

# 从 slug 查询
uv run python scripts/pm_market_lookup.py will-trump-release-the-epstein-files-by-december-19-771

# 从 condition_id 反查
uv run python scripts/pm_market_lookup.py 0xac9c6628a5398bb2a06f566854270a9fbc7f2badec4329d3b5fdc1407291c35b

# 验证 token_id 是否有效
uv run python scripts/pm_market_lookup.py 97631444429136963410558776454705646247419477447963422218240880878426855760467
```

输出包含 condition_id、token_ids、网站链接，以及可直接复制的 `config.yaml` 配置片段。

| 输入类型 | 示例 | 支持度 |
|----------|------|--------|
| URL | `https://polymarket.com/event/.../slug` | 完整信息 |
| slug | `market-slug-123` | 完整信息 |
| condition_id | `0xabc123...` | 完整信息 |
| token_id | `12345678...` | 仅验证有效性 |

### 连接测试

```bash
# 测试 Polymarket 连接
uv run python scripts/test_pm_connection.py

# 测试 Opinion 连接
uv run python scripts/test_opinion_connection.py

# 测试 Predict.fun 连接
uv run python scripts/test_pf_connection.py
```

## 项目结构

```
pm-quant/dashboard/
├── pyproject.toml          # 项目配置
├── config.yaml             # 运行时配置
├── .env.example            # 环境变量模板
├── scripts/
│   ├── generate_pm_creds.py    # PM 凭据生成
│   ├── pm_market_lookup.py     # 市场查询工具
│   ├── test_pm_connection.py   # PM 连接测试
│   ├── test_opinion_connection.py  # Opinion 连接测试
│   └── test_pf_connection.py   # Predict.fun 连接测试
├── src/
│   ├── main.py             # 入口
│   ├── config.py           # 配置加载
│   ├── models.py           # 数据模型
│   ├── clients/
│   │   ├── polymarket.py   # Polymarket 客户端
│   │   ├── opinion.py      # Opinion 客户端
│   │   └── predictfun.py   # Predict.fun 客户端
│   ├── engine/
│   │   ├── arbitrage.py    # 套利检测
│   │   └── executor.py     # 订单执行
│   └── utils/
│       └── logger.py       # 日志
└── tests/
    └── test_arbitrage.py   # 测试
```

## 风险提示

1. **单边成交风险**: 套利执行非原子操作，可能出现敞口
2. **费率不确定**: Opinion 实际费率需运行后确认
3. **地区限制**: Opinion API 限制部分地区访问，需使用代理
4. **资金风险**: 请使用可承受损失的资金进行测试
