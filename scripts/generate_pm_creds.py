#!/usr/bin/env python3
"""
Polymarket 凭据生成脚本

使用方法:
    uv run python scripts/generate_pm_creds.py

或者先安装依赖后直接运行:
    python scripts/generate_pm_creds.py
"""

import sys

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("错误: 需要先安装 py-clob-client")
    print("运行: uv add py-clob-client 或 pip install py-clob-client")
    sys.exit(1)


def main():
    print("=" * 50)
    print("Polymarket API 凭据生成工具")
    print("=" * 50)
    print()
    print("注意: 私钥非常敏感，请确保:")
    print("  1. 在安全的环境中运行此脚本")
    print("  2. 不要将私钥提交到版本控制")
    print("  3. 使用专门的交易钱包，不要用主钱包")
    print()

    # 获取私钥
    private_key = input("请输入你的 Polygon 钱包私钥 (0x开头): ").strip()

    if not private_key:
        print("错误: 私钥不能为空")
        sys.exit(1)

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    if len(private_key) != 66:  # 0x + 64 hex chars
        print(f"警告: 私钥长度异常 ({len(private_key)}), 标准长度为 66 字符")

    print()
    print("正在连接 Polymarket...")

    try:
        # 初始化客户端
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,  # Polygon Mainnet
        )

        print("正在生成 API 凭据...")

        # 生成 API 凭据
        creds = client.create_or_derive_api_creds()

        # 提取凭据 (ApiCreds 是对象，用属性访问)
        api_key = creds.api_key if hasattr(creds, 'api_key') else getattr(creds, 'apiKey', str(creds))
        api_secret = creds.api_secret if hasattr(creds, 'api_secret') else getattr(creds, 'secret', '')
        api_passphrase = creds.api_passphrase if hasattr(creds, 'api_passphrase') else getattr(creds, 'passphrase', '')

        print("正在获取 Proxy Address...")

        # 获取 Proxy Address
        try:
            proxy_address = client.get_proxy_address()
        except Exception:
            proxy_address = "需要手动从 polymarket.com 获取"

        print()
        print("=" * 50)
        print("生成成功! 请将以下内容添加到 .env 文件:")
        print("=" * 50)
        print()
        print(f"PM_PRIVATE_KEY={private_key}")
        print(f"PM_API_KEY={api_key}")
        print(f"PM_API_SECRET={api_secret}")
        print(f"PM_API_PASSPHRASE={api_passphrase}")
        print(f"PM_PROXY_ADDRESS={proxy_address}")
        print()

        # 询问是否自动写入
        save = input("是否自动写入到 .env 文件? (y/n): ").strip().lower()

        if save == 'y':
            env_content = f"""# Polymarket Credentials (自动生成)
PM_PRIVATE_KEY={private_key}
PM_API_KEY={api_key}
PM_API_SECRET={api_secret}
PM_API_PASSPHRASE={api_passphrase}
PM_PROXY_ADDRESS={proxy_address}

# Opinion Credentials (需要手动填写)
OPINION_API_KEY=
OPINION_PRIVATE_KEY=

# Proxy (Opinion 地区限制，需要时填写)
HTTP_PROXY=
HTTPS_PROXY=
"""
            with open(".env", "w") as f:
                f.write(env_content)

            print()
            print("已写入 .env 文件!")
            print("请继续填写 Opinion 相关的凭据")

    except Exception as e:
        print(f"错误: {e}")
        print()
        print("常见问题:")
        print("  1. 私钥格式不正确")
        print("  2. 网络连接问题")
        print("  3. 该钱包未在 Polymarket 注册")
        sys.exit(1)


if __name__ == "__main__":
    main()
