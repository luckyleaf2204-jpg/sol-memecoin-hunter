# Cross-check giao dịch on-chain

Tạo lúc: 2026-11-22T06:52:54.053037+00:00

Nguồn đối chiếu độc lập: GeckoTerminal `/pools/{pool}/trades` (khớp theo chữ ký giao dịch) và hai nhà cung cấp RPC độc lập (api.mainnet-beta.solana.com, solana-rpc.publicnode.com): so sánh số dư SOL/token trước–sau.

- Giao dịch khớp được: **40**
- Chiều mua/bán trùng khớp: 40/40
- Số lượng token trùng khớp tuyệt đối: 40/40
- Chênh lệch SOL nằm trong phạm vi phí giao dịch: 40/40
- Ví giao dịch trùng khớp: 37/40 (khác nhau ở 3 bundle: GeckoTerminal ghi ví trả phí, hệ thống ghi ví nhận token)
- Dữ liệu thô giống hệt giữa 2 RPC: 36/40

| # | Giao dịch | Lệnh/tx | Chiều | SOL (hệ thống) | SOL (GeckoTerminal) | Chênh lệch | Phí giao dịch | Token khớp | Xác minh |
|---|---|---:|---|---:|---:|---:|---:|---|---|
| 1 | [2U32VKgNHGww…](https://solscan.io/tx/2U32VKgNHGwwy8fGx8i1qobqocfJhf4ipae3FAQiayqmoDg4AnDF1MPkw2J38T6ss51XyjbopdF27mjneuX6ju3V) | 3 | buy | 1.708472 | 1.708472 | +0.000000 | 0.021356 | ✓ | verified |
| 2 | [5cPdQ8BtdgJf…](https://solscan.io/tx/5cPdQ8BtdgJfyxCYWeu4GJMBJCdMuJCbKhtoyk13AUhF9RXtgzu1cjrimoYdsn54k4nJnYdZArEtV8H8NjWVYFni) | 3 | buy | 0.829163 | 0.829163 | +0.000000 | 0.010365 | ✓ | partially_verified |
| 3 | [5aEEZV72NxJA…](https://solscan.io/tx/5aEEZV72NxJAtfB3RuLb5AGN6QEqLrpMemJgCtAKb5b3GRhq1AQ1fpWJsRCKwUrTHqLYs4psBqzzNekpbNyfDnn7) | 4 | buy | 1.004304 | 1.004304 | +0.000000 | 0.012554 | ✓ | verified |
| 4 | [49xTGnuj2nyH…](https://solscan.io/tx/49xTGnuj2nyHdeLxcG6D98ygLXk36WGL6xc1fsoRSCeTboN3AwcZeMSsrtxkoeDvQzxA3wz9qgEUMmKzhnnW5M2j) | 1 | buy | 0.488889 | 0.488889 | +0.000000 | 0.006111 | ✓ | verified |
| 5 | [2SpAgFkCc7in…](https://solscan.io/tx/2SpAgFkCc7in4MQHvGSvR6HrPx3fEhRkceydS3WYcDRwgKuE8ArNxwKXodgjk5gscku2ciwfkjVy7U31d9rg9azj) | 1 | buy | 0.043832 | 0.043832 | +0.000000 | 0.000548 | ✓ | verified |
| 6 | [37goiWzQfcV1…](https://solscan.io/tx/37goiWzQfcV1vqPTAWBYAcX526xbBrebHN21tkxKvGzJd4d2kN52bQsPLgJ5CqQNcyyVVhjDCEwAXo1m96rT7A2Z) | 1 | buy | 0.049383 | 0.049383 | +0.000000 | 0.000617 | ✓ | verified |
| 7 | [2Sn1kMZV5RZ9…](https://solscan.io/tx/2Sn1kMZV5RZ9yQ63LPkEmip7dgFDWfZvs7Rr3MyLTSC4JLxqQCmYpSZsFE4AAPyf3kjuQQWKU4wf7tiSvQkvJ3MS) | 1 | buy | 0.049375 | 0.049375 | +0.000000 | 0.000617 | ✓ | verified |
| 8 | [2Ne7kxa8d8Nx…](https://solscan.io/tx/2Ne7kxa8d8Nxxgwp61o98mEebY4gi89rxEKuBX1ZhaUTVpEzHSZGyGsuLLtZUq7nshHidfgQ3iYrEf1bMz84u4WZ) | 1 | buy | 0.024432 | 0.024432 | +0.000000 | 0.000305 | ✓ | verified |
| 9 | [2b81FxmtrD87…](https://solscan.io/tx/2b81FxmtrD87jgPuVG4Ep9kzGuArZnB1tanHA2oh9P7hFr4Hr8VgT4jG8pp6eqF4yACMaHL69JMcDCfU1duNrz7i) | 1 | buy | 0.098765 | 0.098765 | +0.000000 | 0.001235 | ✓ | verified |
| 10 | [4QmMdVhV2mgF…](https://solscan.io/tx/4QmMdVhV2mgF6zvdvZ6iwbTrP9Mz8J9BiDmkohuoRdAx1iihdk3ArEXE53d9hNG81k3zuXnSUcJw1jrXY7rrr7NU) | 1 | buy | 0.049383 | 0.049383 | +0.000000 | 0.000617 | ✓ | verified |
| 11 | [5jkPN1y6MoVT…](https://solscan.io/tx/5jkPN1y6MoVTCvgY1f4AqC21DS5GQH1ny7vqig1AFUuFJnSftCQ9VFwTncfhgtKXWd7MHFpRcHdSgrTN8j4jNZDC) | 1 | buy | 0.024387 | 0.024387 | +0.000000 | 0.000305 | ✓ | verified |
| 12 | [4kkihykr2gux…](https://solscan.io/tx/4kkihykr2guxWyeZR6Dk9SMcEaD92zVRdLLXMbNNHU9NLG3J2eoM4zuXkFdnmoHtsPr3AbjM6qgfi6FeJh3fVSES) | 1 | sell | 0.058724 | 0.058724 | +0.000000 | 0.000734 | ✓ | verified |
| 13 | [3w3iSXq9q6ej…](https://solscan.io/tx/3w3iSXq9q6eju3NaTQFTDnDTc7n2spf2Ex85eeb2Ajk5c8YMN4jDMH6fJqbztQRCMwqnNVGN4qgKDs5z4jeYXPn5) | 1 | buy | 0.049383 | 0.049383 | +0.000000 | 0.000617 | ✓ | verified |
| 14 | [M9RDX2GGdUzW…](https://solscan.io/tx/M9RDX2GGdUzWHK4yfnXkDWJByqN5nJm4pT5QwCQ1AQfGtwibfpUuA95tyobM4MjC2nwzBPFTh7Jqr716pWpWzhR) | 1 | buy | 0.058667 | 0.058667 | +0.000000 | 0.000733 | ✓ | verified |
| 15 | [5wPA9vSxbVT5…](https://solscan.io/tx/5wPA9vSxbVT56d61jhP8vqnfZQyBvyfSVsyqsLhnjP6nvfEhQgFgGdWjc3Ze1jzcGSQ23cwGT2b4sXoEKWzmUFmz) | 1 | sell | 0.265582 | 0.265582 | +0.000000 | 0.003320 | ✓ | verified |
| 16 | [4t8Fvs3VBVfw…](https://solscan.io/tx/4t8Fvs3VBVfwzVf7QxHLGuEPt3QAHWjsQMxamsz68t9QSWGtyyPiSuL7tuvqYU3XrUSAXuYXPs8tVUBK22C6L6Ze) | 1 | buy | 0.321840 | 0.321840 | +0.000000 | 0.004023 | ✓ | verified |
| 17 | [58utc5ZFEuQu…](https://solscan.io/tx/58utc5ZFEuQudT8KwZDeb6DERt6AYR6imxxgtXqyhHd5tUXfF4YvN8RAATaYmaDjWBfU6PrtE174eiaqQyxCK6zE) | 1 | buy | 0.321069 | 0.321069 | +0.000000 | 0.004013 | ✓ | verified |
| 18 | [5xe8tUTwFAEz…](https://solscan.io/tx/5xe8tUTwFAEzUAeeh61bAhmnUic1K5iwtPEuhrM6HhSL946eNJbE1AnsnUryVyYzBYr5pqkNz65J9YLSM7r9sxyi) | 1 | sell | 0.098223 | 0.098223 | +0.000000 | 0.001228 | ✓ | verified |
| 19 | [F1jgt1ocs8Az…](https://solscan.io/tx/F1jgt1ocs8AzZSdi3Fysjr3bngtUEoJ8rVTrhSHYprvC1MqCibemE14YazqE1N9SDtLL6zqA14enoowqKN9hnnT) | 1 | sell | 0.271745 | 0.271745 | +0.000000 | 0.003397 | ✓ | verified |
| 20 | [64Wihxr2bE7m…](https://solscan.io/tx/64Wihxr2bE7m4S4pcU44vGek2fuLKPrVz9kHr7dps46G3Q1hfLSKCmvGW1BkH7ge99R72UKPRABtqGnsigtQfjoo) | 1 | buy | 0.325636 | 0.325636 | +0.000000 | 0.004070 | ✓ | verified |
| 21 | [3rmjwjyLQnLi…](https://solscan.io/tx/3rmjwjyLQnLib9yMurKeqSv4Rd5Qb89RJrVEaWvyanUTVZSSK3bMU1Yd9FbjYXZ75kwrBgYmjSW1JLeh2rchocNF) | 1 | sell | 0.244267 | 0.244267 | +0.000000 | 0.003053 | ✓ | verified |
| 22 | [5gSbmC18KqnD…](https://solscan.io/tx/5gSbmC18KqnD4AiskokwtbbxjtqTP7JuLW8YVUkpdWxtP1SR6Y2ugDeEqhVHw1yLTrWvk3kSre3iEjUHm5L7wbef) | 1 | buy | 0.341997 | 0.341997 | +0.000000 | 0.004275 | ✓ | verified |
| 23 | [4T4FqhyZYxLt…](https://solscan.io/tx/4T4FqhyZYxLtymBBQZcoxeR5oKqwms3S4xgAYtDu34Rhb8HLXSnCVM7gSVnFZTLQwu1RpoyVusyB2ZrtkJeFfmmi) | 1 | sell | 0.299482 | 0.299482 | +0.000000 | 0.003744 | ✓ | verified |
| 24 | [55g2tNWJarZ2…](https://solscan.io/tx/55g2tNWJarZ2v6Y89oUY7gSkqwx3Jfkq6assDovjzPNmk82CxsqH5Aaz4fKCYC4nxXBRasGo5ntF8WZE2xvKsLzf) | 1 | buy | 0.305478 | 0.305478 | +0.000000 | 0.003818 | ✓ | verified |
| 25 | [2pyBGpW9vjCH…](https://solscan.io/tx/2pyBGpW9vjCHUqZr1LNqHWSBsWuUMw6TCfhsyycsfnQ8gwpWzWDkNooHTMLy4Zi6wYRg1BoLRVSBf7CwXhvyWwUm) | 1 | sell | 0.269264 | 0.269264 | +0.000000 | 0.003366 | ✓ | verified |
| 26 | [5RPCDsBdbaL6…](https://solscan.io/tx/5RPCDsBdbaL6oMK3qXKYmnGKDqVyBmdA3CQxNn2SK1kX8sevZijxxPhNZ9i1hS7w8ipCuxysJfoG5vW31UURkxVW) | 1 | sell | 0.235748 | 0.235748 | +0.000000 | 0.002947 | ✓ | verified |
| 27 | [5FjuPrK8E6HK…](https://solscan.io/tx/5FjuPrK8E6HK8CHxXyj6xhDv8jvtiH9rxLTNHzjLHuUijG5Uwza5gLJSeryNM1dUCPyKeZPxCKZaWPDX5f8eDF9s) | 1 | buy | 0.295581 | 0.295581 | +0.000000 | 0.003695 | ✓ | verified |
| 28 | [5ndNEk4ERySe…](https://solscan.io/tx/5ndNEk4ERySeB8cwnjcKBqMKGPdDLkDauWeFGYCuRKnisuqSGVvGMTY2ZkfaNweLubZ7BQ6pQytGpnpeEqKHCCTe) | 1 | sell | 0.248936 | 0.248936 | +0.000000 | 0.003112 | ✓ | verified |
| 29 | [5ydeKyAWV6au…](https://solscan.io/tx/5ydeKyAWV6auPKvEU6vLkRvxerR89rxo2uLp13CL1j3X98j4X3mwPpuZYTN7aVUUnJybH9JPJpjjqrxcsnGjc7x5) | 1 | buy | 0.097778 | 0.097778 | +0.000000 | 0.001222 | ✓ | verified |
| 30 | [3UGpKscpxWTk…](https://solscan.io/tx/3UGpKscpxWTktADxvmc5faVegiywjP3NX4yqT6H762o55nmewh9BCtYT3mTVGmD6pLYukB2hs79WgrvNxnzbaswc) | 1 | buy | 0.318232 | 0.318232 | +0.000000 | 0.003978 | ✓ | verified |
| 31 | [4LB6fajFvEnG…](https://solscan.io/tx/4LB6fajFvEnGDQGKk2FVMUCT6s1E2zKizTBd4NdPeQHAds9RmcgyhR7bKNT92X1d4s9SBQmo86Zbh56yFsDB8EZy) | 1 | buy | 0.321203 | 0.321203 | +0.000000 | 0.004015 | ✓ | verified |
| 32 | [4c8DrvCtsn68…](https://solscan.io/tx/4c8DrvCtsn68M9f9mAkB9gcGJmYoLqvXHZzoJc1tgcCiBtoP3uJDkScQtD2dN6UZZEiCZ1eQF9u3bFJo2JjGGyJD) | 1 | sell | 0.306765 | 0.306765 | +0.000000 | 0.003835 | ✓ | verified |
| 33 | [4JxYJ6921RVb…](https://solscan.io/tx/4JxYJ6921RVbQ2RR1QAfppJTYubtdx4U1Nh3bTx9EAuQpGkZ3yUgYosaRybj3s585cNT4ueDWaapXHfNpCS1NeRv) | 1 | buy | 0.348043 | 0.348043 | +0.000000 | 0.004351 | ✓ | verified |
| 34 | [5uEaSgMMgu7R…](https://solscan.io/tx/5uEaSgMMgu7RHFzEHo2U2iNTKtVw2tNQT9Rp46KQSHCc1xDHwogLUb4FZVXQZJeUmpAxAhHJmQppqr8MchfQ6xDM) | 1 | buy | 0.344115 | 0.344115 | +0.000000 | 0.004301 | ✓ | verified |
| 35 | [3T3hfeTb7Mdr…](https://solscan.io/tx/3T3hfeTb7MdrGoT8jq36VN1ssxCEqgGnPdpWMXvPAgW41qYMwMTPgNKVzn7b5vSCge4QVMWCNqJKohhncAxVck8M) | 1 | sell | 0.274773 | 0.274773 | +0.000000 | 0.003435 | ✓ | verified |
| 36 | [odHFzXk1XAsc…](https://solscan.io/tx/odHFzXk1XAscaTmne2zehmA5ELDiu9XjZyyJRHNu3u7fKgPijchFDjkAvtABCEstTzaSsqRq1q4MimAEfSH3HuK) | 1 | sell | 0.048983 | 0.048983 | +0.000000 | 0.000612 | ✓ | verified |
| 37 | [5akUvrpk4aEf…](https://solscan.io/tx/5akUvrpk4aEffECK74tutz75w2zhMvSRAXsi1pdvfsTAunaiwUw7W8xew141KF2hzmYyWckSpNUMpg7v54yC4LVV) | 1 | buy | 0.316050 | 0.316050 | +0.000000 | 0.003951 | ✓ | verified |
| 38 | [2KZQmTcYxXZU…](https://solscan.io/tx/2KZQmTcYxXZUniMENYL7ex8LRszU5xaUJm4hDKDLQ61XC81F5GynY2KwxaHX1NfJuS4j15Kmvyd7Ck8W2Cgh3Dkc) | 1 | sell | 0.293450 | 0.293450 | +0.000000 | 0.003668 | ✓ | verified |
| 39 | [59SqY7983rsE…](https://solscan.io/tx/59SqY7983rsEYfHmkBRAHEhthbGGuFeMnKvStw1hdKxo4GoPDgHonp5HkNgskC4Dz8kdEJnTAvnoPqDNU8tp9xEG) | 1 | buy | 0.330275 | 0.330275 | +0.000000 | 0.004128 | ✓ | verified |
| 40 | [3tA3QAzAWSRw…](https://solscan.io/tx/3tA3QAzAWSRw4MAnZjNKPRC5k3QtS8sN8DcaXBZN8MKJJbegFfnZBA5XhYcSKZi1KK1uWzHMrpmo6wf6JiZWASjE) | 1 | buy | 0.347774 | 0.347774 | +0.000000 | 0.004347 | ✓ | verified |
