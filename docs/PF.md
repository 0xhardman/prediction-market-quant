# [PY] How to authenticate your API requests

# Authentication Guide

## Authentication with API Key and JWT Token

To interact with Predict's API, you'll need two things:

1. **API Key**: Required for all endpoints (only on Mainnet).
2. **JWT Token**: Required for performing personal operations for a specific wallet (e.g., sending a new order or viewing active orders).

**Sections**:
- [**Obtaining a JWT Token _(for EOAs)_**](#obtaining-a-jwt-token-for-eoas)
- [**Obtaining a JWT Token _(for Predict accounts)_**](#obtaining-a-jwt-token-for-predict-accounts)
- [**Passing the API Key and JWT Token in the requests**](#passing-the-api-key-and-jwt-token-in-requests)

---

## Obtaining a JWT Token _(for Predict accounts)_

An API key is required to obtain a JWT token. Follow these steps to generate a JWT token for your wallet:

1. **Retrieve the message to sign**:
   Send a `GET` request to `/v1/auth/message` to retrieve a message for signing.

2. **Sign the message with your wallet via our SDK**:
   You will need two wallets:
    - Your Predict account address (aka deposit address)
    - Your Privy Wallet private key (can be exported from the account's settings)

    **NOTE**: It's recommended to fund your Privy Wallet with BNB to be able to set approvals and cancel orders.

3. **Send the signature**:
   Send a `POST` request to `/v1/auth` with the following JSON structure:

```python
import os
import requests
from predict_sdk import OrderBuilder, ChainId, OrderBuilderOptions

# You can export this private key from your account's settings at https://predict.fun/account/settings
privy_wallet_private_key = os.environ["PRIVY_WALLET_PRIVATE_KEY"]

# Replace with your Predict account address/deposit address
PREDICT_ACCOUNT_ADDRESS = "0x..."


def main():
    api_key = "YOUR_API_KEY"

    # Create a new instance of the OrderBuilder class
    # Note: This should only be done once per signer
    builder = OrderBuilder.make(
        ChainId.BNB_MAINNET,
        privy_wallet_private_key,
        OrderBuilderOptions(predict_account=PREDICT_ACCOUNT_ADDRESS),
    )

    # Get the JWT token
    jwt = get_auth_jwt(builder, api_key)
    print(f"JWT Token: {jwt}")


def get_auth_jwt(builder: OrderBuilder, api_key: str) -> str:
    """
    Get a JWT token for authentication with the Predict API.

    Args:
        builder: An OrderBuilder instance configured with a Predict account.
        api_key: Your Predict API key.

    Returns:
        The JWT token string.
    """
    # Send the `GET auth/message` request
    message_response = requests.get(
        "https://api.predict.fun/v1/auth/message",
        headers={"x-api-key": api_key},
    )
    message_data = message_response.json()

    # Retrieve the message to sign
    message = message_data["data"]["message"]

    # Sign the message using the SDK function for Predict accounts
    # The standard `sign_message` won't work for Predict accounts
    signature = builder.sign_predict_account_message(message)

    # The body's data to request the JWT via `POST auth`
    body = {
        "signer": PREDICT_ACCOUNT_ADDRESS,
        "message": message,
        "signature": signature,
    }

    # Send the `POST auth` request
    jwt_response = requests.post(
        "https://api.predict.fun/v1/auth",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        json=body,
    )
    jwt_data = jwt_response.json()

    # Fetch the JWT token
    jwt = jwt_data["data"]["token"]

    return jwt


if __name__ == "__main__":
    main()
```

---

## Obtaining a JWT Token _(for EOAs)_

An API key is required to obtain a JWT token. Follow these steps to generate a JWT token for your wallet:

1. **Retrieve the message to sign**:
   Send a `GET` request to `/v1/auth/message` to retrieve a message for signing.

2. **Sign the message with your wallet**:
   Use the wallet you want to authenticate with to sign the message retrieved in step 1.

3. **Send the signature**:
   Send a `POST` request to `/v1/auth` with the following JSON structure:

```python
import os
import requests
from eth_account import Account
from eth_account.messages import encode_defunct

# Create a wallet to sign the message (must be the orders' `maker`)
private_key = os.environ["WALLET_PRIVATE_KEY"]
signer = Account.from_key(private_key)

def main():
    api_key = "YOUR_API_KEY"

    # Send the `GET auth/message` request
    message_response = requests.get(
        "https://api.predict.fun/v1/auth/message",
        headers={"x-api-key": api_key},
    )
    message_data = message_response.json()

    # Retrieve the message to sign
    message = message_data["data"]["message"]

    # Sign the message using EIP-191 personal sign
    signable_message = encode_defunct(text=message)
    signed = signer.sign_message(signable_message)
    signature = signed.signature.hex()

    # The body's data to request the JWT via `POST auth`
    body = {
        "signer": signer.address,
        "message": message,
        "signature": signature,
    }

    # Send the `POST auth` request
    jwt_response = requests.post(
        "https://api.predict.fun/v1/auth",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        json=body,
    )
    jwt_data = jwt_response.json()

    # Fetch the JWT token
    jwt = jwt_data["data"]["token"]

    return jwt


if __name__ == "__main__":
    main()
```

---

## Passing the API Key and JWT Token in Requests

To authenticate your requests, you need to include both the API key and the JWT token in the request headers. **The API key and `x-api-key` header are not required on Testnet**.

Request headers:

```python
headers = {
    "x-api-key": "YOUR_API_KEY",
    "Authorization": "Bearer YOUR_JWT_TOKEN",
}
```

Example usage:

```python
import requests

def make_authenticated_request(jwt: str, api_key: str):
    """
    Example of how to send API requests with authentication.

    Args:
        jwt: Your JWT token obtained from the auth endpoint.
        api_key: Your Predict API key.
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {jwt}",
    }

    # Example: Get active orders
    response = requests.get(
        "https://api.predict.fun/v1/orders",
        headers=headers,
    )
    data = response.json()

    return data


def make_post_request(jwt: str, api_key: str, some_data: dict):
    """
    Example of how to send a POST request with authentication.

    Args:
        jwt: Your JWT token obtained from the auth endpoint.
        api_key: Your Predict API key.
        some_data: The data to send in the request body.
    """
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {jwt}",
    }

    response = requests.post(
        "https://api.predict.fun/v1/orders",
        headers=headers,
        json=some_data,
    )
    data = response.json()

    return data
```