# -*- coding: utf-8 -*-
"""
build.py - 2026년 1월 7일부터 현재까지 TQQQ 퀀트 자산배분 매매 시뮬레이션 및
GitHub Pages 배포용 증권사 MTS 스타일 index.html 자동 생성 파이프라인 (계좌 잔고 / 전체 체결내역 탭 지원)
"""

import os
import sys
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf

# ---------------------------------------------------------
# 1. 시세 데이터 수집 및 전처리
# ---------------------------------------------------------
def fetch_market_data(start_date="2026-01-07"):
    print(f"[1/4] yfinance 시세 데이터 수집 중 (QQQ, TQQQ, USDKRW=X, 시작 기준: {start_date})...")
    
    # 60개월 이평선(1,260영업일) 및 사상 최고가(ATH) 계산을 위해 QQQ는 과거 데이터부터 충분히 수집
    qqq = yf.download("QQQ", start="2004-01-01", progress=False)
    tqqq = yf.download("TQQQ", start="2010-02-11", progress=False)
    fx = yf.download("USDKRW=X", start="2010-01-01", progress=False)

    # MultiIndex 컬럼 평탄화 (yfinance 최신버전 대응)
    for df in [qqq, tqqq, fx]:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

    # QQQ 지표 산출
    # 1) 사상 최고가 (ATH)
    qqq['ATH'] = qqq['High'].cummax()
    # 2) 고점 대비 하락률 (DD %)
    qqq['DD'] = (qqq['Close'] - qqq['ATH']) / qqq['ATH'] * 100.0
    # 3) 60개월 (1,260영업일) 이동평균선 및 이격도 (%)
    qqq['MA1260'] = qqq['Close'].rolling(window=1260).mean()
    qqq['Disparity'] = ((qqq['Close'] / qqq['MA1260']) - 1.0) * 100.0

    # 2026-01-07 이후 유효 날짜 인덱스 추출
    avail_dates = tqqq.loc[tqqq.index >= start_date].index
    if len(avail_dates) == 0:
        print(f"Warning: {start_date} 이후 데이터가 충분치 않아 가장 최근 데이터를 기준으로 설정합니다.")
        avail_dates = tqqq.index[-100:]

    dates = avail_dates
    
    df_merged = pd.DataFrame(index=dates)
    df_merged['QQQ_Close'] = qqq['Close'].reindex(dates).ffill()
    df_merged['QQQ_High'] = qqq['High'].reindex(dates).ffill()
    df_merged['QQQ_ATH'] = qqq['ATH'].reindex(dates).ffill()
    df_merged['QQQ_DD'] = qqq['DD'].reindex(dates).ffill()
    df_merged['QQQ_MA1260'] = qqq['MA1260'].reindex(dates).ffill()
    df_merged['QQQ_Disparity'] = qqq['Disparity'].reindex(dates).fillna(0.0)
    
    df_merged['TQQQ_Close'] = tqqq['Close'].reindex(dates).ffill()
    df_merged['USDKRW'] = fx['Close'].reindex(dates).ffill()

    # 결측치 최종 정리
    df_merged = df_merged.dropna(subset=['TQQQ_Close', 'QQQ_Close'])
    df_merged['USDKRW'] = df_merged['USDKRW'].bfill().ffill()

    # 월말 영업일 여부 마킹
    df_merged['YearMonth'] = df_merged.index.to_period('M')
    month_last_dates = df_merged.groupby('YearMonth').apply(lambda x: x.index[-1]).values
    df_merged['Is_Month_End'] = df_merged.index.isin(month_last_dates)

    print(f" -> 수집 완료: 총 {len(df_merged)} 영업일 데이터 ({df_merged.index[0].strftime('%Y-%m-%d')} ~ {df_merged.index[-1].strftime('%Y-%m-%d')})")
    return df_merged


# ---------------------------------------------------------
# 2. 퀀트 매매 및 자산 배분 백테스트 시뮬레이션
# ---------------------------------------------------------
def run_simulation(df):
    print("[2/4] 퀀트 자산배분 매매 시뮬레이션 실행 중 (2026-01-07 ~ 현재)...")

    # 기본 자본 설정
    INITIAL_KRW = 5_000_000.0
    init_fx = float(df['USDKRW'].iloc[0])
    INITIAL_USD = INITIAL_KRW / init_fx

    usd_cash = INITIAL_USD
    tqqq_shares = 0
    tqqq_avg_price = 0.0

    trades = [] # 전체 체결 내역
    daily_history = []

    # 상태 머신 변수들
    cycle_in_dd10 = False
    rebound_sold = False
    rebalance_tier = 0  # 0: 평시, 1: -15% 리밸런싱 완료, 2: -20% 리밸런싱 완료
    principal_recovered = False
    lock_base_value = None

    # Day 1 진입 규칙: 2,500,000 KRW 상당의 USD로 TQQQ 정수 주수 즉시 1회 매수
    day1_date = df.index[0]
    day1_tqqq_price = float(df['TQQQ_Close'].iloc[0])
    day1_fx = float(df['USDKRW'].iloc[0])
    day1_buy_usd_target = 2_500_000.0 / day1_fx
    day1_shares = int(day1_buy_usd_target // day1_tqqq_price)

    if day1_shares > 0:
        cost = day1_shares * day1_tqqq_price
        usd_cash -= cost
        tqqq_shares += day1_shares
        tqqq_avg_price = day1_tqqq_price
        trades.append({
            "date": day1_date.strftime("%Y-%m-%d"),
            "type": "매수",
            "reason": "Day 1 초기 진입 (원화 250만 원 상당 정수 매수)",
            "shares": day1_shares,
            "price": day1_tqqq_price,
            "amount_usd": cost,
            "amount_krw": cost * day1_fx,
            "cash_after": usd_cash
        })

    # 매 영업일 시뮬레이션 순회
    for idx, (dt, row) in enumerate(df.iterrows()):
        tqqq_p = float(row['TQQQ_Close'])
        qqq_dd = float(row['QQQ_DD'])
        disparity = float(row['QQQ_Disparity'])
        fx_val = float(row['USDKRW'])
        is_m_end = bool(row['Is_Month_End'])
        dt_str = dt.strftime("%Y-%m-%d")

        total_equity_usd = usd_cash + (tqqq_shares * tqqq_p)
        total_return_pct = ((total_equity_usd / INITIAL_USD) - 1.0) * 100.0

        # Day 1 첫날은 이미 초기매수를 진행했으므로 상태 갱신만 수행
        if idx > 0:
            # 1) 사이클 상태 갱신 (State Machine)
            if qqq_dd >= -0.5:
                cycle_in_dd10 = False
                rebound_sold = False
                rebalance_tier = 0
            elif qqq_dd <= -10.0:
                cycle_in_dd10 = True

            sold_today = False

            # [1순위 - 반등 분할 매도]
            if cycle_in_dd10 and (qqq_dd >= -5.0) and (not rebound_sold) and (tqqq_shares > 0):
                # TQQQ 주식 가치가 최소 250만 원 상당(USD)은 계좌에 항상 유지되도록 하고,
                # 250만 원을 초과하는 TQQQ 주식 평가액(하락 중 분할 매수분 등)만 최대 250만 원 한도로 분할 매도
                min_tqqq_keep_usd = 2_500_000.0 / fx_val
                cur_tqqq_eval_usd = tqqq_shares * tqqq_p
                avail_sell_usd = max(0.0, cur_tqqq_eval_usd - min_tqqq_keep_usd)
                max_sell_usd = 2_500_000.0 / fx_val
                actual_sell_usd = min(max_sell_usd, avail_sell_usd)
                sell_shares = min(tqqq_shares, int(actual_sell_usd // tqqq_p))

                if sell_shares > 0:
                    sold_amount = sell_shares * tqqq_p
                    usd_cash += sold_amount
                    tqqq_shares -= sell_shares
                    rebound_sold = True
                    sold_today = True
                    trades.append({
                        "date": dt_str,
                        "type": "매도",
                        "reason": f"1순위 반등 분할 매도 (250만원 주식 유지 후 초과 {sell_shares}주 매도)",
                        "shares": sell_shares,
                        "price": tqqq_p,
                        "amount_usd": sold_amount,
                        "amount_krw": sold_amount * fx_val,
                        "cash_after": usd_cash
                    })

            # [2순위 - 원금 회수]
            elif (not principal_recovered) and (total_return_pct >= 200.0) and (tqqq_shares > 0):
                recover_target_usd = INITIAL_USD
                sell_shares = min(tqqq_shares, int(recover_target_usd // tqqq_p))
                if sell_shares > 0:
                    sold_amount = sell_shares * tqqq_p
                    usd_cash += sold_amount
                    tqqq_shares -= sell_shares
                    principal_recovered = True
                    sold_today = True
                    trades.append({
                        "date": dt_str,
                        "type": "매도",
                        "reason": "2순위 원금 회수 (누적 수익률 +200% 달성)",
                        "shares": sell_shares,
                        "price": tqqq_p,
                        "amount_usd": sold_amount,
                        "amount_krw": sold_amount * fx_val,
                        "cash_after": usd_cash
                    })

            # [3순위 - 총액고정법]
            elif principal_recovered and (tqqq_shares > 0):
                if (lock_base_value is None) and (total_return_pct >= 300.0):
                    lock_base_value = total_equity_usd
                elif lock_base_value is not None and (total_equity_usd >= lock_base_value * 1.05):
                    excess_usd = lock_base_value * 0.05
                    sell_shares = min(tqqq_shares, int(excess_usd // tqqq_p))
                    if sell_shares > 0:
                        sold_amount = sell_shares * tqqq_p
                        usd_cash += sold_amount
                        tqqq_shares -= sell_shares
                        lock_base_value = usd_cash + (tqqq_shares * tqqq_p)
                        sold_today = True
                        trades.append({
                            "date": dt_str,
                            "type": "매도",
                            "reason": "3순위 총액고정법 (기준액 대비 5% 초과 수익 실현)",
                            "shares": sell_shares,
                            "price": tqqq_p,
                            "amount_usd": sold_amount,
                            "amount_krw": sold_amount * fx_val,
                            "cash_after": usd_cash
                        })

            # [상시 - 월봉 이격도 과열 매도]
            if (not sold_today) and is_m_end and (disparity >= 50.0) and (tqqq_shares > 0):
                target_tqqq_usd = total_equity_usd * 0.70
                cur_tqqq_usd = tqqq_shares * tqqq_p
                if cur_tqqq_usd > target_tqqq_usd:
                    excess_usd = cur_tqqq_usd - target_tqqq_usd
                    sell_shares = int(excess_usd // tqqq_p)
                    if sell_shares > 0:
                        sold_amount = sell_shares * tqqq_p
                        usd_cash += sold_amount
                        tqqq_shares -= sell_shares
                        sold_today = True
                        trades.append({
                            "date": dt_str,
                            "type": "매도",
                            "reason": "상시 월봉 이격도 과열 조절 (7:3 리밸런싱)",
                            "shares": sell_shares,
                            "price": tqqq_p,
                            "amount_usd": sold_amount,
                            "amount_krw": sold_amount * fx_val,
                            "cash_after": usd_cash
                        })

            # 3) 하락장 매수 규칙 (당일 매도가 발생하지 않은 경우)
            if not sold_today:
                if qqq_dd > -10.0:
                    # 0% ~ -10% 미만: 관망 / 대기
                    pass
                elif -15.0 <= qqq_dd <= -10.0:
                    # -10% ~ -15% 구간: 가용 현금 내 매 영업일 1주씩 정량 매수
                    if usd_cash >= tqqq_p:
                        usd_cash -= tqqq_p
                        tqqq_avg_price = ((tqqq_shares * tqqq_avg_price) + tqqq_p) / (tqqq_shares + 1)
                        tqqq_shares += 1
                        trades.append({
                            "date": dt_str,
                            "type": "매수",
                            "reason": f"하락장 분할 매수 1주 (QQQ DD {qqq_dd:.1f}%)",
                            "shares": 1,
                            "price": tqqq_p,
                            "amount_usd": tqqq_p,
                            "amount_krw": tqqq_p * fx_val,
                            "cash_after": usd_cash
                        })
                elif -20.0 <= qqq_dd < -15.0:
                    # -15% ~ -20% 구간: [TQQQ 80% : 현금 20%] 리밸런싱 (진입 시 1회)
                    if rebalance_tier < 1:
                        target_tqqq_usd = total_equity_usd * 0.80
                        cur_tqqq_usd = tqqq_shares * tqqq_p
                        if target_tqqq_usd > cur_tqqq_usd:
                            needed_usd = min(usd_cash, target_tqqq_usd - cur_tqqq_usd)
                            buy_shares = int(needed_usd // tqqq_p)
                            if buy_shares > 0:
                                cost = buy_shares * tqqq_p
                                usd_cash -= cost
                                tqqq_avg_price = ((tqqq_shares * tqqq_avg_price) + cost) / (tqqq_shares + buy_shares)
                                tqqq_shares += buy_shares
                                trades.append({
                                    "date": dt_str,
                                    "type": "매수",
                                    "reason": f"구간 리밸런싱 8:2 비중 (QQQ DD {qqq_dd:.1f}%)",
                                    "shares": buy_shares,
                                    "price": tqqq_p,
                                    "amount_usd": cost,
                                    "amount_krw": cost * fx_val,
                                    "cash_after": usd_cash
                                })
                        rebalance_tier = 1
                elif qqq_dd < -20.0:
                    # -20% 초과 하락 구간: [TQQQ 90% : 현금 10%] 리밸런싱 (진입 시 1회)
                    if rebalance_tier < 2:
                        target_tqqq_usd = total_equity_usd * 0.90
                        cur_tqqq_usd = tqqq_shares * tqqq_p
                        if target_tqqq_usd > cur_tqqq_usd:
                            needed_usd = min(usd_cash, target_tqqq_usd - cur_tqqq_usd)
                            buy_shares = int(needed_usd // tqqq_p)
                            if buy_shares > 0:
                                cost = buy_shares * tqqq_p
                                usd_cash -= cost
                                tqqq_avg_price = ((tqqq_shares * tqqq_avg_price) + cost) / (tqqq_shares + buy_shares)
                                tqqq_shares += buy_shares
                                trades.append({
                                    "date": dt_str,
                                    "type": "매수",
                                    "reason": f"구간 리밸런싱 9:1 비중 (QQQ DD {qqq_dd:.1f}%)",
                                    "shares": buy_shares,
                                    "price": tqqq_p,
                                    "amount_usd": cost,
                                    "amount_krw": cost * fx_val,
                                    "cash_after": usd_cash
                                })
                        rebalance_tier = 2

        # 당일 최종 자산 계산
        final_total_usd = usd_cash + (tqqq_shares * tqqq_p)
        final_total_krw = final_total_usd * fx_val

        daily_history.append({
            "date": dt,
            "total_usd": final_total_usd,
            "total_krw": final_total_krw,
            "cash_usd": usd_cash,
            "tqqq_shares": tqqq_shares,
            "tqqq_price": tqqq_p,
            "fx": fx_val,
            "qqq_dd": qqq_dd,
            "disparity": disparity
        })

    latest = daily_history[-1]
    last_row = df.iloc[-1]

    # 오늘의 주문 가이드 판정 로직
    cur_dd = float(last_row['QQQ_DD'])
    cur_disp = float(last_row['QQQ_Disparity'])

    if cur_dd > -10.0:
        signal_title = "관망 및 현금 대기 중"
        signal_desc = f"QQQ 고점 대비 낙폭이 -10% 미만({cur_dd:.2f}%)으로 안정 구간입니다. 신규 매수 없이 대기합니다."
        signal_badge = "bg-blue-500/20 text-blue-400 border-blue-500/30"
        signal_icon = "shield"
    elif -15.0 <= cur_dd <= -10.0:
        signal_title = "매일 1주 분할 매수 구간"
        signal_desc = f"QQQ 낙폭이 -10%~-15% 구간({cur_dd:.2f}%)입니다. 가용 현금 한도 내에서 매 영업일 TQQQ 1주씩 정량 매수합니다."
        signal_badge = "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
        signal_icon = "shopping-cart"
    elif -20.0 <= cur_dd < -15.0:
        signal_title = "8:2 비중 리밸런싱 구간"
        signal_desc = f"QQQ 낙폭 -15%~-20% 구간({cur_dd:.2f}%)입니다. TQQQ 80% : 현금 20% 비중으로 맞추는 집중 매수 구간입니다."
        signal_badge = "bg-amber-500/20 text-amber-400 border-amber-500/30"
        signal_icon = "layers"
    else:
        signal_title = "9:1 비중 적극 리밸런싱 구간"
        signal_desc = f"QQQ 낙폭 -20% 초과({cur_dd:.2f}%) 대하락장입니다. TQQQ 90% : 현금 10% 비중으로 강력 리밸런싱 매수를 집행합니다."
        signal_badge = "bg-rose-500/20 text-rose-400 border-rose-500/30"
        signal_icon = "flame"

    total_buy_count = sum(1 for t in trades if t['type'] == "매수")
    total_sell_count = sum(1 for t in trades if t['type'] == "매도")

    summary_result = {
        "start_date": df.index[0].strftime("%Y년 %m월 %d일"),
        "latest_date": df.index[-1].strftime("%Y년 %m월 %d일"),
        "initial_krw": INITIAL_KRW,
        "initial_usd": INITIAL_USD,
        "total_krw": latest['total_krw'],
        "total_usd": latest['total_usd'],
        "cum_return_pct": ((latest['total_usd'] / INITIAL_USD) - 1.0) * 100.0,
        "profit_krw": latest['total_krw'] - INITIAL_KRW,
        "profit_usd": latest['total_usd'] - INITIAL_USD,
        "multiple": latest['total_usd'] / INITIAL_USD,
        
        "cash_usd": latest['cash_usd'],
        "cash_krw": latest['cash_usd'] * latest['fx'],
        "cash_ratio": (latest['cash_usd'] / latest['total_usd']) * 100.0,
        "fx_rate": latest['fx'],
        
        "tqqq_shares": latest['tqqq_shares'],
        "tqqq_price": latest['tqqq_price'],
        "tqqq_avg_price": tqqq_avg_price,
        "tqqq_eval_usd": latest['tqqq_shares'] * latest['tqqq_price'],
        "tqqq_eval_krw": latest['tqqq_shares'] * latest['tqqq_price'] * latest['fx'],
        "tqqq_ratio": ((latest['tqqq_shares'] * latest['tqqq_price']) / latest['total_usd']) * 100.0,
        "tqqq_profit_pct": ((latest['tqqq_price'] / tqqq_avg_price) - 1.0) * 100.0 if tqqq_avg_price > 0 else 0.0,
        "tqqq_profit_usd": (latest['tqqq_shares'] * latest['tqqq_price']) - (latest['tqqq_shares'] * tqqq_avg_price),
        
        "qqq_dd": cur_dd,
        "disparity": cur_disp,
        "signal_title": signal_title,
        "signal_desc": signal_desc,
        "signal_badge": signal_badge,
        "signal_icon": signal_icon,
        
        "all_trades": trades[::-1], # 전체 체결 내역 (최신순 정렬)
        "recent_trades": trades[-5:][::-1], # 최근 5건 (간략보기용)
        "total_trade_count": len(trades),
        "total_buy_count": total_buy_count,
        "total_sell_count": total_sell_count
    }

    print(f" -> 시뮬레이션 완료 ({summary_result['start_date']} 시작):")
    print(f"    - 초기 투자금: KRW {INITIAL_KRW:,.0f} (${INITIAL_USD:,.2f})")
    print(f"    - 현재 총 자산: KRW {summary_result['total_krw']:,.0f} (${summary_result['total_usd']:,.2f})")
    print(f"    - 누적 수익률: {summary_result['cum_return_pct']:+.2f}%")
    print(f"    - TQQQ 보유: {summary_result['tqqq_shares']}주 | 예수금: ${summary_result['cash_usd']:,.2f}")
    print(f"    - 총 체결 횟수: {summary_result['total_trade_count']}회 (매수 {total_buy_count}회, 매도 {total_sell_count}회)")
    return summary_result


# ---------------------------------------------------------
# 3. 증권사 MTS 스타일 index.html 웹페이지 렌더링 (탭 인터페이스)
# ---------------------------------------------------------
def render_mts_html(res, output_path="index.html"):
    print("[3/4] 증권사 MTS 스타일 index.html 생성 중 (계좌 잔고 / 체결 내역 탭 포함)...")

    # 1) 전체 체결 내역 HTML 생성
    all_trades_html = ""
    for t in res["all_trades"]:
        is_buy = t["type"] == "매수"
        type_badge = (
            '<span class="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/15 text-rose-400 border border-rose-500/30">매수</span>'
            if is_buy else
            '<span class="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/15 text-blue-400 border border-blue-500/30">매도</span>'
        )
        shares_text = f"+{t['shares']}주" if is_buy else f"-{t['shares']}주"
        shares_color = "text-rose-400 font-bold" if is_buy else "text-blue-400 font-bold"

        all_trades_html += f"""
        <div class="trade-item p-3.5 bg-slate-900/60 rounded-xl border border-slate-800/90 flex items-center justify-between text-xs hover:border-slate-700 transition" data-type="{t['type']}">
            <div class="space-y-1.5">
                <div class="flex items-center gap-2">
                    {type_badge}
                    <span class="font-bold text-white text-[13px]">{t['date']}</span>
                    <span class="text-[10px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded font-medium">TQQQ</span>
                </div>
                <p class="text-[11px] text-slate-300 font-medium">{t['reason']}</p>
                <div class="text-[10px] text-slate-400">
                    체결 후 예수금: <span class="text-slate-300 font-semibold">${t['cash_after']:,.2f}</span>
                </div>
            </div>
            <div class="text-right space-y-0.5 pl-2">
                <div class="{shares_color} text-sm">{shares_text}</div>
                <div class="text-[11px] text-slate-300 font-medium">@ ${t['price']:,.2f}</div>
                <div class="text-[11px] text-slate-400 font-medium">₩{t['amount_krw']:,.0f}</div>
                <div class="text-[10px] text-slate-400">(${t['amount_usd']:,.2f})</div>
            </div>
        </div>
        """

    # 2) 최근 체결 내역 (간략 미리보기용) HTML 생성
    recent_trades_html = ""
    for t in res["recent_trades"]:
        is_buy = t["type"] == "매수"
        type_badge = (
            '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/15 text-rose-400 border border-rose-500/30">매수</span>'
            if is_buy else
            '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-blue-500/15 text-blue-400 border border-blue-500/30">매도</span>'
        )
        shares_text = f"+{t['shares']}주" if is_buy else f"-{t['shares']}주"
        shares_color = "text-rose-400 font-bold" if is_buy else "text-blue-400 font-bold"

        recent_trades_html += f"""
        <div class="py-2.5 border-b border-slate-800/80 last:border-b-0 flex items-center justify-between text-xs">
            <div class="space-y-0.5">
                <div class="flex items-center gap-1.5">
                    {type_badge}
                    <span class="font-bold text-white text-[12px]">{t['date']}</span>
                </div>
                <p class="text-[11px] text-slate-400 truncate max-w-[190px] sm:max-w-xs">{t['reason']}</p>
            </div>
            <div class="text-right space-y-0.5">
                <div class="{shares_color} text-xs">{shares_text}</div>
                <div class="text-[10px] text-slate-400">@ ${t['price']:,.2f}</div>
            </div>
        </div>
        """

    profit_color = "text-rose-400" if res['cum_return_pct'] >= 0 else "text-blue-400"
    profit_sign = "+" if res['cum_return_pct'] >= 0 else ""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>위탁종합 계좌 잔고 | QQQ 퀀트 포트폴리오</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Lucide Icons -->
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
        * {{ font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif; }}
        body {{ background-color: #0A0F1D; }}
        .mts-card {{
            background: #111827;
            border: 1px solid #1F2937;
        }}
        .mts-subcard {{
            background: #162032;
            border: 1px solid #243044;
        }}
        /* Tab transitions */
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
    </style>
</head>
<body class="text-slate-100 min-h-screen pb-20 antialiased selection:bg-rose-500 selection:text-white">

    <!-- 1. 증권사 MTS 상단 헤더 바 -->
    <header class="sticky top-0 z-50 bg-[#0A0F1D]/90 backdrop-blur-md border-b border-slate-800 px-4 py-3">
        <div class="max-w-md mx-auto flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <div class="w-8 h-8 rounded-lg bg-gradient-to-tr from-rose-600 to-indigo-600 flex items-center justify-center font-black text-white text-xs shadow-md shadow-rose-900/30">
                    MTS
                </div>
                <div>
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-200">위탁종합 (해외)</span>
                        <span class="text-[10px] text-slate-400">112-92-****01</span>
                    </div>
                    <div class="text-[10px] text-slate-400 flex items-center gap-1">
                        <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                        <span>{res['latest_date']} 기준 (자동갱신)</span>
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-2">
                <span class="text-[10px] bg-slate-800 text-slate-300 px-2 py-1 rounded-md border border-slate-700 font-medium">실시간 USD 운용</span>
            </div>
        </div>
    </header>

    <!-- MTS 상단 탭 네비게이션 컨트롤 -->
    <div class="max-w-md mx-auto px-4 pt-3">
        <div class="bg-slate-900/90 p-1 rounded-xl border border-slate-800 grid grid-cols-2 gap-1 text-xs">
            <button id="tab-btn-balance" onclick="switchTab('balance')" class="tab-btn py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm">
                <i data-lucide="layout-dashboard" class="w-3.5 h-3.5 text-rose-400"></i>
                <span>계좌 잔고</span>
            </button>
            <button id="tab-btn-trades" onclick="switchTab('trades')" class="tab-btn py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200">
                <i data-lucide="receipt" class="w-3.5 h-3.5"></i>
                <span>체결 내역 ({res['total_trade_count']}건)</span>
            </button>
        </div>
    </div>

    <!-- 메인 대시보드 컨테이너 -->
    <main class="max-w-md mx-auto px-4 mt-3 space-y-3.5">

        <!-- ============================================== -->
        <!-- [TAB 1] 계좌 잔고 (Overview Tab) -->
        <!-- ============================================== -->
        <div id="tab-balance" class="tab-content active space-y-3.5">

            <!-- 2. 총 자산 평가 요약 카드 (MTS 최상단 메인) -->
            <div class="mts-card rounded-2xl p-4 shadow-xl">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
                    <span>총 평가금액 (KRW)</span>
                    <span class="text-[11px] bg-rose-500/10 text-rose-400 font-bold px-2 py-0.5 rounded-full border border-rose-500/20">
                        {res['start_date']} 시작
                    </span>
                </div>
                
                <div class="text-2xl sm:text-3xl font-extrabold text-white tracking-tight mt-0.5">
                    ₩{res['total_krw']:,.0f}
                </div>

                <div class="flex items-center gap-2 mt-2 text-xs">
                    <span class="{profit_color} font-black text-sm">{profit_sign}{res['cum_return_pct']:,.2f}%</span>
                    <span class="{profit_color} font-bold">({profit_sign}₩{res['profit_krw']:,.0f})</span>
                    <span class="text-[10px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-semibold ml-auto">
                        초기 원금 500만원
                    </span>
                </div>

                <!-- 세부 환산 박스 -->
                <div class="mts-subcard rounded-xl p-3 mt-3.5 grid grid-cols-2 gap-2 text-xs">
                    <div>
                        <span class="text-[11px] text-slate-400 block">총 자산 (USD)</span>
                        <span class="font-bold text-slate-200 text-sm">${res['total_usd']:,.2f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">초기 투자 환산액</span>
                        <span class="font-bold text-slate-400 text-sm">${res['initial_usd']:,.2f}</span>
                    </div>
                </div>
            </div>

            <!-- 3. 외화 예수금 카드 (USD 현금) -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-300">
                        <i data-lucide="wallet" class="w-4 h-4 text-emerald-400"></i>
                        <span>외화 예수금 (USD 현금)</span>
                    </div>
                    <span class="text-xs font-bold text-emerald-400">비중 {res['cash_ratio']:.1f}%</span>
                </div>

                <div class="flex items-baseline justify-between mt-1">
                    <div class="text-xl font-black text-emerald-400">
                        ${res['cash_usd']:,.2f}
                    </div>
                    <div class="text-xs text-slate-400">
                        약 ₩{res['cash_krw']:,.0f}
                    </div>
                </div>

                <div class="mt-2.5 pt-2.5 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
                    <span>적용 환율 (USDKRW)</span>
                    <span class="font-semibold text-slate-300">₩{res['fx_rate']:,.2f} / USD</span>
                </div>
            </div>

            <!-- 4. 오늘의 주문 가이드 (핵심 시그널 카드) -->
            <div class="mts-card rounded-2xl p-4 border border-indigo-500/30 shadow-lg relative overflow-hidden">
                <div class="absolute -right-8 -top-8 w-24 h-24 bg-indigo-500/10 rounded-full blur-xl pointer-events-none"></div>

                <div class="flex items-center justify-between mb-2.5">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-indigo-400">
                        <i data-lucide="compass" class="w-4 h-4"></i>
                        <span>오늘의 주문 가이드 (Strategy Signal)</span>
                    </div>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold {res['signal_badge']}">
                        {res['signal_title']}
                    </span>
                </div>

                <!-- QQQ 상태 지표 배지 그리드 -->
                <div class="grid grid-cols-2 gap-2 my-2.5">
                    <div class="bg-slate-900/80 rounded-xl p-2.5 border border-slate-800">
                        <span class="text-[10px] text-slate-400 block">QQQ 고점 대비 낙폭 (DD)</span>
                        <span class="text-sm font-black text-rose-400">{res['qqq_dd']:.2f}%</span>
                    </div>
                    <div class="bg-slate-900/80 rounded-xl p-2.5 border border-slate-800">
                        <span class="text-[10px] text-slate-400 block">QQQ 60월선 이격도</span>
                        <span class="text-sm font-black text-indigo-400">+{res['disparity']:.2f}%</span>
                    </div>
                </div>

                <!-- 행동 요약 설명 -->
                <div class="bg-slate-900/90 rounded-xl p-3 border border-slate-800 text-xs text-slate-300 leading-relaxed">
                    <p class="font-bold text-white flex items-center gap-1.5 mb-0.5">
                        <i data-lucide="{res['signal_icon']}" class="w-3.5 h-3.5 text-indigo-400"></i>
                        <span>{res['signal_title']}</span>
                    </p>
                    <p class="text-[11px] text-slate-400 mt-1">{res['signal_desc']}</p>
                </div>
            </div>

            <!-- 5. 보유 종목 카드 (TQQQ) -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5">
                        <span class="font-extrabold text-white text-base">TQQQ</span>
                        <span class="text-[11px] text-slate-400">ProShares UltraPro QQQ</span>
                    </div>
                    <span class="text-xs font-bold text-indigo-400">비중 {res['tqqq_ratio']:.1f}%</span>
                </div>

                <div class="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-slate-800 text-xs">
                    <div>
                        <span class="text-[11px] text-slate-400 block">보유 수량</span>
                        <span class="font-black text-white text-sm">{res['tqqq_shares']} 주</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">현재가 (USD)</span>
                        <span class="font-black text-white text-sm">${res['tqqq_price']:,.2f}</span>
                    </div>
                    <div>
                        <span class="text-[11px] text-slate-400 block">평균 매입단가</span>
                        <span class="font-bold text-slate-300 text-xs">${res['tqqq_avg_price']:,.2f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">수익률</span>
                        <span class="font-bold text-rose-400 text-xs">+{res['tqqq_profit_pct']:,.2f}%</span>
                    </div>
                </div>

                <div class="mts-subcard rounded-xl p-3 mt-3 flex items-center justify-between text-xs">
                    <div>
                        <span class="text-[10px] text-slate-400 block">평가 금액</span>
                        <span class="font-extrabold text-white text-sm">₩{res['tqqq_eval_krw']:,.0f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[10px] text-slate-400 block">평가 손익 (USD)</span>
                        <span class="font-bold text-rose-400 text-xs">+${res['tqqq_profit_usd']:,.2f}</span>
                    </div>
                </div>
            </div>

            <!-- 6. 최근 체결 내역 미리보기 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-200">
                        <i data-lucide="receipt" class="w-4 h-4 text-slate-400"></i>
                        <span>최근 체결 내역 (최근 5건)</span>
                    </div>
                    <button onclick="switchTab('trades')" class="text-[11px] text-rose-400 hover:text-rose-300 font-bold flex items-center gap-0.5">
                        <span>전체 {res['total_trade_count']}건 보기</span>
                        <i data-lucide="chevron-right" class="w-3.5 h-3.5"></i>
                    </button>
                </div>

                <div class="divide-y divide-slate-800/80">
                    {recent_trades_html}
                </div>
            </div>

        </div>

        <!-- ============================================== -->
        <!-- [TAB 2] 전체 체결 내역 (Trade History Tab) -->
        <!-- ============================================== -->
        <div id="tab-trades" class="tab-content space-y-3.5">

            <!-- 체결 내역 종합 요약 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-200">
                        <i data-lucide="history" class="w-4 h-4 text-indigo-400"></i>
                        <span>전체 매매 체결 기록</span>
                    </div>
                    <span class="text-[11px] bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full font-bold border border-slate-700">
                        총 {res['total_trade_count']}건
                    </span>
                </div>

                <div class="grid grid-cols-2 gap-2 text-xs">
                    <div class="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
                        <span class="text-slate-400">총 매수 체결</span>
                        <span class="font-bold text-rose-400 text-sm">{res['total_buy_count']} 회</span>
                    </div>
                    <div class="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
                        <span class="text-slate-400">총 매도 체결</span>
                        <span class="font-bold text-blue-400 text-sm">{res['total_sell_count']} 회</span>
                    </div>
                </div>

                <!-- 필터 버튼 -->
                <div class="flex gap-1.5 mt-3 pt-3 border-t border-slate-800 text-[11px]">
                    <button onclick="filterTrades('all')" class="filter-btn active px-3 py-1 rounded-md font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">전체</button>
                    <button onclick="filterTrades('매수')" class="filter-btn px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white">매수만</button>
                    <button onclick="filterTrades('매도')" class="filter-btn px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white">매도만</button>
                </div>
            </div>

            <!-- 전체 체결 내역 리스트 -->
            <div id="trades-list-container" class="space-y-2">
                {all_trades_html}
            </div>

        </div>

    </main>

    <!-- 하단 고정 정보 바 -->
    <footer class="max-w-md mx-auto text-center text-slate-500 text-[11px] py-6 px-4">
        <p>TQQQ Quantitative Asset Allocation Engine • GitHub Pages Automated</p>
        <p class="mt-1 text-[10px]">투자 시작일: {res['start_date']} (초기 500만원) ~ 현재</p>
    </footer>

    <!-- 클라이언트 탭 전환 및 필터 스크립트 -->
    <script>
        lucide.createIcons();

        function switchTab(tabName) {{
            // 탭 컨텐츠 전환
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');

            // 탭 버튼 스타일 전환
            const btnBalance = document.getElementById('tab-btn-balance');
            const btnTrades = document.getElementById('tab-btn-trades');

            if (tabName === 'balance') {{
                btnBalance.className = 'tab-btn py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm';
                btnTrades.className = 'tab-btn py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200';
            }} else {{
                btnTrades.className = 'tab-btn py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm';
                btnBalance.className = 'tab-btn py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200';
            }}

            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        function filterTrades(type) {{
            const items = document.querySelectorAll('.trade-item');
            const buttons = document.querySelectorAll('.filter-btn');

            buttons.forEach(btn => {{
                if (btn.textContent.includes(type) || (type === 'all' && btn.textContent === '전체')) {{
                    btn.className = 'filter-btn px-3 py-1 rounded-md font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30';
                }} else {{
                    btn.className = 'filter-btn px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white';
                }}
            }});

            items.forEach(item => {{
                if (type === 'all' || item.getAttribute('data-type') === type) {{
                    item.style.display = 'flex';
                }} else {{
                    item.style.display = 'none';
                }}
            }});
        }}
    </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[4/4] index.html 생성 완료 -> {output_path}")


def main():
    # 2026년 1월 7일 시작 기준
    df = fetch_market_data(start_date="2026-01-07")
    summary = run_simulation(df)
    render_mts_html(summary, "index.html")
    print("[SUCCESS] 전체 시뮬레이션 및 MTS 웹 대시보드 빌드 성공!")


if __name__ == "__main__":
    main()
