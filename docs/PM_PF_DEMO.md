# Polymarket 和 Predict.fun API 集成指南

> 项目记忆文档 - 记录两个平台的关键集成细节

## 概览

| 平台 | 链 | API 类型 | 认证方式 |
|------|-----|---------|---------|
| Polymarket | Polygon (137) | REST + WebSocket | API Key + 链上签名 |
| Predict.fun | BNB (56) | REST | API Key + JWT |

---

## Polymarket

### 认证配置

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

client = ClobClient(
    host="https://clob.polymarket.com",
    key=private_key,
    chain_id=137,
    signature_type=2,  # 关键: POLY_GNOSIS_SAFE
    funder=proxy_address,
)

# 使用已有凭据或派生新凭据
creds = ApiCreds(api_key, api_secret, api_passphrase)
client.set_api_creds(creds)
```

### 关键发现

| 问题 | 解决方案 |
|------|---------|
| `invalid signature` | 使用 `signature_type=2` (POLY_GNOSIS_SAFE) |
| `Size lower than minimum` | 最小下单量为 **5** |

### Orderbook 获取

```python
# REST API
GET https://clob.polymarket.com/book?token_id={token_id}

# 响应格式
{
  "bids": [{"price": "0.50", "size": "100"}],
  "asks": [{"price": "0.51", "size": "100"}]
}
```

### 下单示例

```python
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

order_args = OrderArgs(
    price=0.50,
    size=5.0,  # 最小 5
    side=BUY,
    token_id=token_id,
)

signed_order = client.create_order(order_args)
resp = client.post_order(signed_order, OrderType.GTC)  # 或 FOK
order_id = resp.get("orderID")

# 取消订单
client.cancel(order_id)
```

---

## Predict.fun

### 认证配置

```python
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

# 1. 获取认证消息
resp = await client.get("/auth/message")
message = resp.json()["data"]["message"]

# 2. 签名
account = Account.from_key(private_key)
msg = encode_defunct(text=message)
signed = account.sign_message(msg)
signature = "0x" + signed.signature.hex()

# 3. 获取 JWT - 关键: 字段名是 'signer' 不是 'walletAddress'
resp = await client.post("/auth", json={
    "message": message,
    "signature": signature,
    "signer": account.address,  # 关键字段名
})

jwt = resp.json()["data"]["token"]

# 4. 后续请求添加 Authorization header
headers["Authorization"] = f"Bearer {jwt}"
```

### 关键发现

| 问题 | 解决方案 |
|------|---------|
| `Invalid signer` | 认证字段名是 **`signer`** 不是 `walletAddress` |
| API Key 可用于任意钱包 | 是的，API Key 和钱包是独立的 |

### Orderbook 获取

```python
# REST API - 使用 market_id 而不是 token_id
GET https://api.predict.fun/v1/markets/{market_id}/orderbook

# 响应格式
{
  "success": true,
  "data": {
    "bids": [[0.50, 100]],  # [price, size]
    "asks": [[0.51, 100]]
  }
}
```

### 下单示例

```python
# 获取市场信息
resp = await client.get("/markets", params={"limit": 20})
markets = resp.json()["data"]
market = [m for m in markets if m["status"] == "REGISTERED"][0]

# 下单 (需要 JWT 认证)
order_data = {
    "marketId": market["id"],
    "outcomeIndex": 0,
    "side": "BUY",
    "price": "0.50",
    "size": "10",
    "type": "LIMIT",
}
resp = await client.post("/orders", json=order_data)
```

---

## 环境变量配置

```bash
# .env 文件

# Polymarket
PM_PRIVATE_KEY=0x...
PM_API_KEY=xxx
PM_API_SECRET=xxx
PM_API_PASSPHRASE=xxx
PM_PROXY_ADDRESS=0x...

# Predict.fun
PREDICT_FUN_API_KEY=xxx
# 使用 PM_PRIVATE_KEY 进行签名认证
```

---

## 测试脚本

```bash
# 综合测试
python scripts/test_pm_pf_demo.py

# 单独测试
python scripts/test_pm_order.py
python scripts/test_pf_order.py
```

---

## 客户端使用

```python
from src.config import load_config
from src.clients.polymarket import PolymarketClient
from src.clients.predictfun import PredictFunClient

config = load_config("config.yaml")

# Polymarket
pm = PolymarketClient(config)
await pm.connect()
orderbook = await pm.get_orderbook(token_id)
result = await pm.place_order(token_id, Side.BUY, 0.50, 10)

# Predict.fun
pf = PredictFunClient(config)
await pf.connect()  # 自动认证
orderbook = await pf.fetch_orderbook(token_id, market_id=123)
result = await pf.place_order(token_id, Side.BUY, 0.50, 10)
```

---

## 已验证功能

| 功能 | Polymarket | Predict.fun |
|------|:----------:|:-----------:|
| 连接/认证 | ✅ | ✅ |
| 获取市场列表 | ✅ | ✅ |
| 获取 Orderbook | ✅ | ✅ |
| 下单 | ✅ | ✅ |
| 取消订单 | ✅ | ✅ |

---

*最后更新: 2025-12-20*
