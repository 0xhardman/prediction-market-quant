# 预测市场 API 调研报告

## 概述

本文档调研了四个主流预测市场平台的 API 能力，目标是实现一个统一的网页界面来查看和交易各平台的赌局。

| 平台 | 链 | API 完整度 | 公开文档 | 下单支持 |
|------|-----|-----------|---------|---------|
| Polymarket | Polygon | ⭐⭐⭐⭐⭐ | 完善 | ✅ |
| Opinion.trade | BNB Chain | ⭐⭐⭐⭐ | 较完善 | ✅ |
| Predict.fun | BNB Chain | ⭐⭐⭐ | 需申请 | ✅ |
| Worm.wtf | Solana | ⭐ | 无公开文档 | ❓ |

---

## 1. Polymarket

**官方文档**: https://docs.polymarket.com/

### 1.1 架构概述

Polymarket 采用混合架构：
- **链下**: CLOB (Central Limit Order Book) 负责订单匹配
- **链上**: Polygon 网络完成结算

### 1.2 API 类型

#### Gamma API (只读市场数据)
- **Base URL**: `https://gamma-api.polymarket.com`
- 用途：获取市场元数据、分类、成交量等

**主要端点**:
| 端点 | 说明 |
|------|------|
| `/markets` | 获取市场列表 |
| `/events` | 获取事件列表 |
| `/tags` | 获取标签分类 |
| `/series` | 获取系列 |

#### CLOB API (交易)
- **Base URL**: `https://clob.polymarket.com`

**主要端点**:
| 方法 | 端点 | 说明 | 认证 |
|------|------|------|------|
| GET | `/markets` | 获取市场列表 | 无需 |
| GET | `/midpoint?token_id=xxx` | 获取中间价 | 无需 |
| GET | `/price?token_id=xxx&side=BUY` | 获取买卖价 | 无需 |
| GET | `/order-book?token_id=xxx` | 获取订单簿 | 无需 |
| POST | `/order` | 下单 | L2 |
| GET | `/data/order/<order_hash>` | 查询订单 | L2 |
| GET | `/data/orders` | 获取活跃订单 | L2 |
| DELETE | `/order/<order_id>` | 取消订单 | L2 |

### 1.3 认证方式

三个级别：
- **L0 (无认证)**: 读取市场数据
- **L1 (钱包签名)**: 基础操作
- **L2 (API Key + HMAC-SHA256)**: 交易操作

**生成 API Key**:
1. 连接钱包到 Polymarket
2. Settings → API → Enable API Access
3. 生成密钥对

### 1.4 下单示例

```python
# 安装: pip install py-clob-client

from py_clob_client.client import ClobClient
from py_clob_client.order_builder.constants import BUY

# 初始化客户端
client = ClobClient(
    "https://clob.polymarket.com",
    key="<PRIVATE_KEY>",
    chain_id=137,
    signature_type=1,
    funder="<PROXY_ADDRESS>"
)
client.set_api_creds(client.create_or_derive_api_creds())

# 下单
order_args = OrderArgs(
    price=0.50,      # 价格 0.50 USDC
    size=10.0,       # 数量 10 份
    side=BUY,
    token_id="<token_id>"
)
signed_order = client.create_order(order_args)
resp = client.post_order(signed_order, OrderType.GTC)
```

### 1.5 订单类型

- **GTC** (Good Till Cancelled): 挂单直到成交或取消
- **FOK** (Fill Or Kill): 立即全部成交否则取消
- **GTD** (Good Till Date): 有效期至指定时间

---

## 2. Opinion.trade

**官方文档**: https://docs.opinion.trade/developer-guide/overview

### 2.1 架构概述

- **链**: BNB Chain
- **特点**: AI Oracle 自动创建市场和结算
- **融资**: YZi Labs (原 Binance Labs) 领投 500万美元

### 2.2 API 端点

**Base URL**: `https://openapi.opinion.trade`

#### Market API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/openapi/market` | 获取市场列表 |
| GET | `/openapi/market/{marketId}` | 获取二元市场详情 |
| GET | `/openapi/market/categorical/{marketId}` | 获取多选市场详情 |

**市场列表参数**:
```
page: 页码 (默认 1)
limit: 每页数量 (最大 20, 默认 10)
status: 状态 (activated / resolved)
marketType: 类型 (0=二元, 1=多选, 2=全部)
sortBy: 排序 (1=最新, 2=即将结束, 3-8=交易量)
chainId: 链ID
```

**响应示例**:
```json
{
  "code": 0,
  "msg": "success",
  "result": {
    "marketId": 123,
    "marketTitle": "Will BTC reach 100k?",
    "status": "activated",
    "yesTokenId": "0x...",
    "noTokenId": "0x...",
    "volume": 1500000,
    "chainId": 56
  }
}
```

### 2.3 认证方式

所有端点需要 API Key:
```
Header: apikey: <your-api-key>
```

### 2.4 SDK

```bash
pip install opinion-clob-sdk
```

```python
from opinion_clob_sdk import ClobClient

client = ClobClient(api_key="<API_KEY>")

# 获取所有市场
markets = client.get_markets()

# 获取订单簿
orderbook = client.get_orderbook(token_id='token_123')

# 下单
order = PlaceOrderDataInput(
    marketId="123",
    tokenId="0x...",
    side=OrderSide.BUY,
    orderType="LIMIT",
    price=0.65,
    makerAmountInQuoteToken=100
)
result = client.place_order(order)

# 查看持仓
positions = client.get_my_positions()
```

---

## 3. Predict.fun

**官方文档**: https://docs.predict.fun/
**API 参考**: https://api.predict.fun/docs

### 3.1 架构概述

- **链**: BNB Chain (原 Blast)
- **特点**: 订单簿交易系统，UMA Oracle 验证结果
- **API 状态**: 早期访问，需申请

### 3.2 API 端点

| 环境 | Base URL | 认证 |
|------|----------|------|
| Mainnet | `https://api.predict.fun/` | 需要 API Key |
| Testnet | `https://api-testnet.predict.fun/` | 不需要 |

**API 文档**: https://api.predict.fun/docs (Swagger UI)

### 3.3 限流

- 240 请求/分钟 (Mainnet 和 Testnet 相同)

### 3.4 获取 API Key

1. 加入 Discord: discord.gg/predictdotfun
2. 开 ticket 申请 API 访问权限
3. 获取 API Key 和 SDK 文档

### 3.5 SDK

官方提供 TypeScript 和 Python SDK（需申请后获取）

---

## 4. Worm.wtf

**官网**: https://www.worm.wtf/

### 4.1 架构概述

- **链**: Solana
- **特点**: AI 驱动，创作者激励，UMA Oracle
- **融资**: 450万美元 Pre-Seed (6MV, Alliance, Solana Ventures等)

### 4.2 API 状态

⚠️ **目前无公开 API 文档**

该平台专注于用户体验和创作者工具，暂未开放开发者 API。

### 4.3 可能的接入方式

1. **直接合约交互**: 研究其 Solana 合约
2. **联系团队**: 通过社交渠道申请 API 访问
3. **网页爬虫**: 最后手段，不推荐

---

## 5. 统一接口设计建议

### 5.1 数据模型统一

```typescript
interface Market {
  id: string;
  platform: 'polymarket' | 'opinion' | 'predict' | 'worm';
  title: string;
  description: string;
  status: 'active' | 'resolved' | 'pending';
  outcomes: Outcome[];
  volume: number;
  endDate: Date;
  chain: string;
}

interface Outcome {
  id: string;
  name: string;
  price: number;  // 0-1
  tokenId: string;
}

interface Order {
  marketId: string;
  platform: string;
  side: 'buy' | 'sell';
  outcomeId: string;
  price: number;
  size: number;
  type: 'limit' | 'market';
}
```

### 5.2 后端架构

```
┌─────────────────────────────────────────────┐
│              统一 API 层                      │
│         /api/markets, /api/orders           │
└─────────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Polymarket  │ │   Opinion    │ │  Predict.fun │
│   Adapter    │ │   Adapter    │ │   Adapter    │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 5.3 实现优先级

1. **Polymarket** - 文档最完善，交易量最大
2. **Opinion** - 文档较好，SDK 可用
3. **Predict.fun** - 需申请 API 后评估
4. **Worm.wtf** - 暂不支持，等待官方 API

---

## 6. 参考链接

### Polymarket
- 官方文档: https://docs.polymarket.com/
- Gamma API: https://docs.polymarket.com/developers/gamma-markets-api/overview
- CLOB 介绍: https://docs.polymarket.com/developers/CLOB/introduction
- Python SDK: https://github.com/Polymarket/py-clob-client
- 认证指南: https://docs.polymarket.com/developers/CLOB/authentication

### Opinion
- 开发者文档: https://docs.opinion.trade/developer-guide/overview
- Market API: https://docs.opinion.trade/developer-guide/opinion-open-api/market
- SDK: https://www.piwheels.org/project/opinion-clob-sdk/

### Predict.fun
- 官方文档: https://docs.predict.fun/
- API 参考: https://dev.predict.fun/
- Discord: discord.gg/predictdotfun

### Worm.wtf
- 官网: https://www.worm.wtf/
- Twitter: https://x.com/wormdotwtf

---

## 7. 逆向工程发现 (补充)

### 7.1 Predict.fun - 实际可用！

**发现**: Predict.fun 的 Testnet API 完全开放，无需认证。

**Testnet 端点**:
```
Base URL: https://api-testnet.predict.fun/v1
```

**已验证端点**:
| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/markets` | 获取市场列表 (无需认证) |

**响应结构**:
```json
{
  "success": true,
  "cursor": "NDE3",
  "data": [
    {
      "id": 393,
      "title": "Oklahoma City Thunder",
      "question": "Will the Oklahoma City Thunder win the 2026 NBA Finals?",
      "description": "...",
      "status": "REGISTERED",
      "imageUrl": "https://...",
      "categorySlug": "2026-nba-champion",
      "conditionId": "0x...",
      "decimalPrecision": 2,
      "feeRateBps": 200,
      "spreadThreshold": 0.06,
      "outcomes": [
        {"name": "Yes", "indexSet": 1, "onChainId": "..."},
        {"name": "No", "indexSet": 2, "onChainId": "..."}
      ]
    }
  ]
}
```

**Mainnet**: 需要 API Key (Discord 申请)
```
Base URL: https://api.predict.fun/v1
Header: Authorization: Bearer <API_KEY>
```

### 7.2 Worm.wtf - tRPC API (需进一步逆向)

**发现**: Worm.wtf 使用 tRPC 作为 API 层。

**技术栈**:
- Next.js + React
- tRPC for API
- TanStack Query (React Query) 状态管理
- Solana 钱包集成

**tRPC 端点**:
```
Base URL: https://www.worm.wtf/api/trpc
```

**可能的 Procedure**:
- `markets.list` - 获取市场列表
- `featured-markets` - 获取精选市场

**数据结构** (从页面 SSR 推断):
```typescript
interface Market {
  title: string;
  description: string;
  condition_id: string;
  logo: string;
  category: 'crypto' | 'sports' | 'politics' | 'tech';
  last_trade_price: number;
  liquidity: number;
  phase: 'first_phase' | 'waiting_to_trade';
  state: 'open' | 'draft';
  margin_enabled: boolean;
  outcomes: Outcome[];
  creator: {
    username: string;
    image: string;
  };
}
```

**逆向建议**:
1. 使用浏览器 DevTools Network 面板抓取 tRPC 请求
2. 分析 `__NEXT_DATA__` 中的 dehydratedState
3. 检查 JS bundle 中的 tRPC router 定义

### 7.3 Opinion.trade - 地区限制

**发现**: Opinion API 有地区限制（禁止美国、中国访问）。

**错误响应**:
```json
{
  "errmsg": "API is not available to persons located in the United States, China...",
  "errno": 10403,
  "result": null
}
```

**解决方案**: 使用非限制地区的代理服务器访问。

---

## 8. 结论 (更新)

| 平台 | 可行性 | 建议 |
|------|--------|------|
| Polymarket | ✅ 高 | 首选接入，完全开放的 Gamma API |
| Predict.fun | ✅ 高 | Testnet 无需认证，可用于开发 |
| Opinion | ⚠️ 中 | 需代理绕过地区限制 |
| Worm.wtf | ⚠️ 中 | tRPC 可逆向，需浏览器抓包 |

**建议实现顺序**:
1. Polymarket - 文档完善，API 开放
2. Predict.fun - Testnet 开放，Mainnet 申请后可用
3. Opinion - 需代理，但 SDK 可用
4. Worm.wtf - 需逆向 tRPC，复杂度较高
