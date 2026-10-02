portfolio = [
    {"name": "블룸 에너지", "ticker": "BE", "shares": 32, "price": 276.10, "avg": 237.2671, "curr": "USD"},
    {"name": "스페이스X", "ticker": "SPCX", "shares": 55, "price": 150.28, "avg": 137.3189, "curr": "USD"},
    {"name": "테슬라", "ticker": "TSLA", "shares": 22, "price": 365.25, "avg": 342.4436, "curr": "USD"},
    {"name": "인텔", "ticker": "INTC", "shares": 40, "price": 103.0245, "avg": 91.5117, "curr": "USD"},
    {"name": "애플", "ticker": "AAPL", "shares": 12, "price": 332.55, "avg": 325.7133, "curr": "USD"},
    {"name": "라운드힐 메모리 ETF", "ticker": "DRAM", "shares": 64, "price": 58.96, "avg": 58.6839, "curr": "USD"},
    {"name": "비스트라", "ticker": "VST", "shares": 25, "price": 148.9187, "avg": 153.0420, "curr": "USD"},
    {"name": "스미토모상사", "ticker": "8053", "shares": 188, "price": 1822.0 / 154.50, "avg": 1844.9228 / 154.50, "curr": "JPY"}
]

total_val = sum(p["shares"] * p["price"] for p in portfolio)
print(f"현재 총 평가액: ${total_val:,.2f}")

target_total = 10000.0
scale = target_total / total_val

print("\n=== 1안. 소수점 매매(해외주식 소수점) 적용 시 (비율 100% 완벽 유지) ===")
for p in portfolio:
    orig_w = (p["shares"] * p["price"]) / total_val * 100
    new_shares = p["shares"] * scale
    val = new_shares * p["price"]
    print(f"- {p['name']} ({p['ticker']}): {new_shares:.2f}주 | ${val:,.2f} | 비중: {orig_w:.2f}%")

print("\n=== 2안. 온주(정수 1주 단위) 매매 최적화 시 ===")
# 최적 정수 수량 탐색
import itertools

best_diff = 999999
best_shares = None

# 그리디 + 라운딩
int_shares_list = []
for p in portfolio:
    raw_s = p["shares"] * scale
    int_shares_list.append(round(raw_s))

# [7, 13, 5, 9, 3, 15, 6, 44]
int_total = sum(s * p["price"] for s, p in zip(int_shares_list, portfolio))
print(f"정수 수량 기준 총 평가액: ${int_total:,.2f}")
for s, p in zip(int_shares_list, portfolio):
    val = s * p["price"]
    w = (val / int_total) * 100
    orig_w = (p["shares"] * p["price"]) / total_val * 100
    print(f"- {p['name']} ({p['ticker']}): {p['shares']}주 -> {s}주 | ${val:,.2f} | 비중: {w:.2f}% (기존 대비 {w-orig_w:+.2f}%)")
