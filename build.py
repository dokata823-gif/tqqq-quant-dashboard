# -*- coding: utf-8 -*-
"""
build.py - QQQ 기반 TQQQ(3X), SOXL(3X), QLD(2X) 멀티 계좌 퀀트 자산배분 매매 시뮬레이션 및
GitHub Pages 배포용 증권사 MTS 스타일 멀티 계좌 index.html 자동 생성 파이프라인
"""

import os
import sys
import json
from datetime import datetime
import pandas as pd
import numpy as np
import yfinance as yf

# ---------------------------------------------------------
# 1. 단일 전략 시뮬레이션 엔진 (공통 함수)
# ---------------------------------------------------------
def run_quant_strategy(signal_ticker, target_ticker, target_name, account_id, account_num, account_title, start_date="2025-01-07", initial_krw=5_000_000.0, max_buy_krw=2_500_000.0):
    cap_text = f"상한: {max_buy_krw/10000:,.0f}만원" if max_buy_krw is not None else "상한 제외(전액진입)"
    print(f"\n[시뮬레이션 실행] {account_title} ({signal_ticker} -> {target_ticker}) 시작일: {start_date} | 초기원금: {initial_krw:,.0f}원 | {cap_text}...")
    
    # 1) 시세 데이터 수집 (60개월 이평선 산출을 위해 2004년부터)
    sig_df = yf.download(signal_ticker, start="2004-01-01", progress=False)
    tgt_df = yf.download(target_ticker, start="2004-01-01", progress=False)
    fx_df = yf.download("USDKRW=X", start="2004-01-01", progress=False)

    # MultiIndex 컬럼 평탄화
    for d in [sig_df, tgt_df, fx_df]:
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)

    # 2) 신호 종목 지표 산출
    sig_df['ATH'] = sig_df['High'].cummax()
    sig_df['DD'] = (sig_df['Close'] - sig_df['ATH']) / sig_df['ATH'] * 100.0
    sig_df['MA1260'] = sig_df['Close'].rolling(window=1260).mean()
    sig_df['Disparity'] = ((sig_df['Close'] / sig_df['MA1260']) - 1.0) * 100.0

    # 3) 시작일 이후 데이터 결합
    avail_dates = tgt_df.loc[tgt_df.index >= start_date].index
    if len(avail_dates) == 0:
        avail_dates = tgt_df.index[-100:]

    dates = avail_dates
    df = pd.DataFrame(index=dates)
    df['Sig_Close'] = sig_df['Close'].reindex(dates).ffill()
    df['Sig_High'] = sig_df['High'].reindex(dates).ffill()
    df['Sig_ATH'] = sig_df['ATH'].reindex(dates).ffill()
    df['Sig_DD'] = sig_df['DD'].reindex(dates).ffill()
    df['Sig_MA1260'] = sig_df['MA1260'].reindex(dates).ffill()
    df['Sig_Disparity'] = sig_df['Disparity'].reindex(dates).fillna(0.0)

    df['Tgt_Close'] = tgt_df['Close'].reindex(dates).ffill()
    df['USDKRW'] = fx_df['Close'].reindex(dates).ffill()

    df = df.dropna(subset=['Tgt_Close', 'Sig_Close'])
    df['USDKRW'] = df['USDKRW'].bfill().ffill()

    # 월말 영업일 여부 마킹
    df['YearMonth'] = df.index.to_period('M')
    month_last_dates = df.groupby('YearMonth').apply(lambda x: x.index[-1]).values
    df['Is_Month_End'] = df.index.isin(month_last_dates)

    # 4) 자본 및 상태 머신 설정
    INITIAL_KRW = float(initial_krw)
    init_fx = float(df['USDKRW'].iloc[0])
    INITIAL_USD = INITIAL_KRW / init_fx

    usd_cash = INITIAL_USD
    shares = 0
    avg_price = 0.0

    trades = []
    daily_history = []

    cycle_in_dd10 = False
    rebound_sold = False
    cycle_bought_shares = 0
    rebalance_tier = 0
    principal_recovered = False
    lock_base_value = None

    # Day 1 초기 진입 (레버리지 매수 상한선 적용 여부)
    day1_date = df.index[0]
    day1_price = float(df['Tgt_Close'].iloc[0])
    day1_fx = float(df['USDKRW'].iloc[0])
    
    if max_buy_krw is not None:
        day1_buy_usd_target = min(float(max_buy_krw), INITIAL_KRW) / day1_fx
        reason_str = f"Day 1 초기 진입 (원화 {max_buy_krw/10000:,.0f}만 원 상한 정수 매수)"
    else:
        day1_buy_usd_target = INITIAL_KRW / day1_fx
        reason_str = f"Day 1 초기 진입 (원화 {INITIAL_KRW/10000:,.0f}만 원 전액 정수 매수)"

    day1_shares = int(day1_buy_usd_target // day1_price)

    if day1_shares > 0:
        cost = day1_shares * day1_price
        usd_cash -= cost
        shares += day1_shares
        avg_price = day1_price
        trades.append({
            "date": day1_date.strftime("%Y-%m-%d"),
            "type": "매수",
            "reason": reason_str,
            "ticker": target_ticker,
            "shares": day1_shares,
            "price": day1_price,
            "amount_usd": cost,
            "amount_krw": cost * day1_fx,
            "cash_after": usd_cash
        })

    # 5) 일별 시뮬레이션 루프
    for idx, (dt, row) in enumerate(df.iterrows()):
        tgt_p = float(row['Tgt_Close'])
        sig_dd = float(row['Sig_DD'])
        disparity = float(row['Sig_Disparity'])
        fx_val = float(row['USDKRW'])
        is_m_end = bool(row['Is_Month_End'])
        dt_str = dt.strftime("%Y-%m-%d")

        total_equity_usd = usd_cash + (shares * tgt_p)
        total_return_pct = ((total_equity_usd / INITIAL_USD) - 1.0) * 100.0

        if idx > 0:
            # 사이클 상태 갱신
            if sig_dd >= -0.5:
                cycle_in_dd10 = False
                rebound_sold = False
                cycle_bought_shares = 0
                rebalance_tier = 0
            elif sig_dd <= -10.0:
                cycle_in_dd10 = True

            sold_today = False

            # [1순위 - 반등 분할 매도]
            if cycle_in_dd10 and (sig_dd >= -5.0) and (not rebound_sold) and (cycle_bought_shares > 0) and (shares > 0):
                min_keep_krw = float(max_buy_krw) if max_buy_krw is not None else INITIAL_KRW
                min_keep_usd = min_keep_krw / fx_val
                cur_eval_usd = shares * tgt_p
                if cur_eval_usd >= min_keep_usd:
                    sell_shares = min(shares, cycle_bought_shares)
                    if sell_shares > 0:
                        sold_amount = sell_shares * tgt_p
                        usd_cash += sold_amount
                        shares -= sell_shares
                        cycle_bought_shares = 0
                        rebound_sold = True
                        sold_today = True
                        trades.append({
                            "date": dt_str,
                            "type": "매도",
                            "reason": f"1순위 반등 분할 매도 (하락장 추가 매수분 {sell_shares}주 익절 매도)",
                            "ticker": target_ticker,
                            "shares": sell_shares,
                            "price": tgt_p,
                            "amount_usd": sold_amount,
                            "amount_krw": sold_amount * fx_val,
                            "cash_after": usd_cash
                        })

            # [2순위 - 원금 회수]
            elif (not principal_recovered) and (total_return_pct >= 200.0) and (shares > 0):
                recover_target_usd = INITIAL_USD
                sell_shares = min(shares, int(recover_target_usd // tgt_p))
                if sell_shares > 0:
                    sold_amount = sell_shares * tgt_p
                    usd_cash += sold_amount
                    shares -= sell_shares
                    principal_recovered = True
                    sold_today = True
                    trades.append({
                        "date": dt_str,
                        "type": "매도",
                        "reason": f"2순위 원금 회수 (누적 수익률 +200% 달성)",
                        "ticker": target_ticker,
                        "shares": sell_shares,
                        "price": tgt_p,
                        "amount_usd": sold_amount,
                        "amount_krw": sold_amount * fx_val,
                        "cash_after": usd_cash
                    })

            # [3순위 - 총액고정법]
            elif principal_recovered and (shares > 0):
                if (lock_base_value is None) and (total_return_pct >= 300.0):
                    lock_base_value = total_equity_usd
                elif lock_base_value is not None and (total_equity_usd >= lock_base_value * 1.05):
                    excess_usd = lock_base_value * 0.05
                    sell_shares = min(shares, int(excess_usd // tgt_p))
                    if sell_shares > 0:
                        sold_amount = sell_shares * tgt_p
                        usd_cash += sold_amount
                        shares -= sell_shares
                        lock_base_value = usd_cash + (shares * tgt_p)
                        sold_today = True
                        trades.append({
                            "date": dt_str,
                            "type": "매도",
                            "reason": f"3순위 총액고정법 (기준액 대비 5% 초과 수익 실현)",
                            "ticker": target_ticker,
                            "shares": sell_shares,
                            "price": tgt_p,
                            "amount_usd": sold_amount,
                            "amount_krw": sold_amount * fx_val,
                            "cash_after": usd_cash
                        })

            # [상시 - 월봉 이격도 과열 매도]
            if (not sold_today) and is_m_end and (disparity >= 50.0) and (shares > 0):
                target_stock_usd = total_equity_usd * 0.70
                cur_stock_usd = shares * tgt_p
                if cur_stock_usd > target_stock_usd:
                    excess_usd = cur_stock_usd - target_stock_usd
                    sell_shares = int(excess_usd // tgt_p)
                    if sell_shares > 0:
                        sold_amount = sell_shares * tgt_p
                        usd_cash += sold_amount
                        shares -= sell_shares
                        sold_today = True
                        trades.append({
                            "date": dt_str,
                            "type": "매도",
                            "reason": f"상시 월봉 이격도 과열 조절 (7:3 리밸런싱)",
                            "ticker": target_ticker,
                            "shares": sell_shares,
                            "price": tgt_p,
                            "amount_usd": sold_amount,
                            "amount_krw": sold_amount * fx_val,
                            "cash_after": usd_cash
                        })

            # 하락장 매수 규칙
            if not sold_today:
                if sig_dd > -10.0:
                    pass
                elif -15.0 <= sig_dd <= -10.0:
                    if usd_cash >= tgt_p:
                        usd_cash -= tgt_p
                        avg_price = ((shares * avg_price) + tgt_p) / (shares + 1)
                        shares += 1
                        cycle_bought_shares += 1
                        trades.append({
                            "date": dt_str,
                            "type": "매수",
                            "reason": f"하락장 분할 매수 1주 ({signal_ticker} DD {sig_dd:.1f}%)",
                            "ticker": target_ticker,
                            "shares": 1,
                            "price": tgt_p,
                            "amount_usd": tgt_p,
                            "amount_krw": tgt_p * fx_val,
                            "cash_after": usd_cash
                        })
                elif -20.0 <= sig_dd < -15.0:
                    if rebalance_tier < 1:
                        target_stock_usd = total_equity_usd * 0.80
                        cur_stock_usd = shares * tgt_p
                        if target_stock_usd > cur_stock_usd:
                            needed_usd = min(usd_cash, target_stock_usd - cur_stock_usd)
                            buy_shares = int(needed_usd // tgt_p)
                            if buy_shares > 0:
                                cost = buy_shares * tgt_p
                                usd_cash -= cost
                                avg_price = ((shares * avg_price) + cost) / (shares + buy_shares)
                                shares += buy_shares
                                cycle_bought_shares += buy_shares
                                trades.append({
                                    "date": dt_str,
                                    "type": "매수",
                                    "reason": f"구간 리밸런싱 8:2 비중 ({signal_ticker} DD {sig_dd:.1f}%)",
                                    "ticker": target_ticker,
                                    "shares": buy_shares,
                                    "price": tgt_p,
                                    "amount_usd": cost,
                                    "amount_krw": cost * fx_val,
                                    "cash_after": usd_cash
                                })
                        rebalance_tier = 1
                elif sig_dd < -20.0:
                    if rebalance_tier < 2:
                        target_stock_usd = total_equity_usd * 0.90
                        cur_stock_usd = shares * tgt_p
                        if target_stock_usd > cur_stock_usd:
                            needed_usd = min(usd_cash, target_stock_usd - cur_stock_usd)
                            buy_shares = int(needed_usd // tgt_p)
                            if buy_shares > 0:
                                cost = buy_shares * tgt_p
                                usd_cash -= cost
                                avg_price = ((shares * avg_price) + cost) / (shares + buy_shares)
                                shares += buy_shares
                                cycle_bought_shares += buy_shares
                                trades.append({
                                    "date": dt_str,
                                    "type": "매수",
                                    "reason": f"구간 리밸런싱 9:1 비중 ({signal_ticker} DD {sig_dd:.1f}%)",
                                    "ticker": target_ticker,
                                    "shares": buy_shares,
                                    "price": tgt_p,
                                    "amount_usd": cost,
                                    "amount_krw": cost * fx_val,
                                    "cash_after": usd_cash
                                })
                        rebalance_tier = 2

        final_total_usd = usd_cash + (shares * tgt_p)
        final_total_krw = final_total_usd * fx_val

        daily_history.append({
            "date": dt,
            "total_usd": final_total_usd,
            "total_krw": final_total_krw,
            "cash_usd": usd_cash,
            "shares": shares,
            "price": tgt_p,
            "fx": fx_val,
            "sig_dd": sig_dd,
            "disparity": disparity
        })

    latest = daily_history[-1]
    last_row = df.iloc[-1]
    cur_dd = float(last_row['Sig_DD'])
    cur_disp = float(last_row['Sig_Disparity'])

    if cur_dd > -10.0:
        signal_title = "관망 및 현금 대기 중"
        signal_desc = f"{signal_ticker} 고점 대비 낙폭이 -10% 미만({cur_dd:.2f}%)으로 안정 구간입니다. 신규 매수 없이 대기합니다."
        signal_badge = "bg-blue-500/20 text-blue-400 border-blue-500/30"
        signal_icon = "shield"
    elif -15.0 <= cur_dd <= -10.0:
        signal_title = "매일 1주 분할 매수 구간"
        signal_desc = f"{signal_ticker} 낙폭이 -10%~-15% 구간({cur_dd:.2f}%)입니다. 가용 현금 내 매 영업일 {target_ticker} 1주씩 정량 매수합니다."
        signal_badge = "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
        signal_icon = "shopping-cart"
    elif -20.0 <= cur_dd < -15.0:
        signal_title = "8:2 비중 리밸런싱 구간"
        signal_desc = f"{signal_ticker} 낙폭 -15%~-20% 구간({cur_dd:.2f}%)입니다. {target_ticker} 80% : 현금 20% 비중으로 맞추는 집중 매수 구간입니다."
        signal_badge = "bg-amber-500/20 text-amber-400 border-amber-500/30"
        signal_icon = "layers"
    else:
        signal_title = "9:1 비중 적극 리밸런싱 구간"
        signal_desc = f"{signal_ticker} 낙폭 -20% 초과({cur_dd:.2f}%) 대하락장입니다. {target_ticker} 90% : 현금 10% 비중으로 강력 리밸런싱 매수를 집행합니다."
        signal_badge = "bg-rose-500/20 text-rose-400 border-rose-500/30"
        signal_icon = "flame"

    total_buy_count = sum(1 for t in trades if t['type'] == "매수")
    total_sell_count = sum(1 for t in trades if t['type'] == "매도")

    result = {
        "account_id": account_id,
        "account_num": account_num,
        "account_title": account_title,
        "signal_ticker": signal_ticker,
        "target_ticker": target_ticker,
        "target_name": target_name,
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
        
        "shares": latest['shares'],
        "price": latest['price'],
        "avg_price": avg_price,
        "eval_usd": latest['shares'] * latest['price'],
        "eval_krw": latest['shares'] * latest['price'] * latest['fx'],
        "stock_ratio": ((latest['shares'] * latest['price']) / latest['total_usd']) * 100.0,
        "profit_pct": ((latest['price'] / avg_price) - 1.0) * 100.0 if avg_price > 0 else 0.0,
        "profit_usd": (latest['shares'] * latest['price']) - (latest['shares'] * avg_price),
        
        "sig_dd": cur_dd,
        "disparity": cur_disp,
        "signal_title": signal_title,
        "signal_desc": signal_desc,
        "signal_badge": signal_badge,
        "signal_icon": signal_icon,
        
        "all_trades": trades[::-1],
        "recent_trades": trades[-5:][::-1],
        "total_trade_count": len(trades),
        "total_buy_count": total_buy_count,
        "total_sell_count": total_sell_count
    }

    print(f" -> {account_title} 완료: 총자산 KRW {result['total_krw']:,.0f} ({result['cum_return_pct']:+.2f}%) | {target_ticker} {result['shares']}주 | 예수금 ${result['cash_usd']:,.2f} | 체결 {result['total_trade_count']}회")
    return result


# ---------------------------------------------------------
# 2. 멀티 계좌 MTS HTML 생성기 (TQQQ, SOXL, QLD 등 N개 계좌 지원)
# ---------------------------------------------------------
def render_multi_account_html(accounts, output_path="index.html"):
    print(f"\n[HTML 생성] {len(accounts)}개 멀티 계좌 MTS index.html 렌더링 중...")

    def build_account_views(acc):
        aid = acc["account_id"]
        profit_color = "text-rose-400" if acc['cum_return_pct'] >= 0 else "text-blue-400"
        profit_sign = "+" if acc['cum_return_pct'] >= 0 else ""

        # 전체 체결 내역 HTML
        all_trades_html = ""
        for t in acc["all_trades"]:
            is_buy = t["type"] == "매수"
            type_badge = (
                '<span class="px-2 py-0.5 rounded text-[11px] font-bold bg-rose-500/15 text-rose-400 border border-rose-500/30">매수</span>'
                if is_buy else
                '<span class="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-500/15 text-blue-400 border border-blue-500/30">매도</span>'
            )
            shares_text = f"+{t['shares']}주" if is_buy else f"-{t['shares']}주"
            shares_color = "text-rose-400 font-bold" if is_buy else "text-blue-400 font-bold"

            all_trades_html += f"""
            <div class="trade-item-{aid} p-3.5 bg-slate-900/60 rounded-xl border border-slate-800/90 flex items-center justify-between text-xs hover:border-slate-700 transition" data-type="{t['type']}">
                <div class="space-y-1.5">
                    <div class="flex items-center gap-2">
                        {type_badge}
                        <span class="font-bold text-white text-[13px]">{t['date']}</span>
                        <span class="text-[10px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-medium">{t['ticker']}</span>
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

        # 최근 체결 내역 HTML
        recent_trades_html = ""
        for t in acc["recent_trades"]:
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

        return f"""
        <!-- 계좌 하위 서브탭 (잔고 / 체결내역) -->
        <div class="pt-2">
            <div class="bg-slate-900/90 p-1 rounded-xl border border-slate-800 grid grid-cols-2 gap-1 text-xs">
                <button id="subtab-btn-{aid}-balance" onclick="switchSubTab('{aid}', 'balance')" class="subtab-btn-{aid} py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm">
                    <i data-lucide="layout-dashboard" class="w-3.5 h-3.5 text-rose-400"></i>
                    <span>계좌 잔고</span>
                </button>
                <button id="subtab-btn-{aid}-trades" onclick="switchSubTab('{aid}', 'trades')" class="subtab-btn-{aid} py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200">
                    <i data-lucide="receipt" class="w-3.5 h-3.5"></i>
                    <span>체결 내역 ({acc['total_trade_count']}건)</span>
                </button>
            </div>
        </div>

        <!-- [SUB-TAB 1] 계좌 잔고 -->
        <div id="subtab-{aid}-balance" class="subtab-content-{aid} active space-y-3.5 mt-3">

            <!-- 총 자산 평가 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-xl">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
                    <div class="flex items-center gap-1.5">
                        <span class="font-bold text-slate-300">{acc['account_title']}</span>
                        <span class="text-[10px] text-slate-500">|</span>
                        <span>총 평가금액</span>
                    </div>
                    <span class="text-[11px] bg-rose-500/10 text-rose-400 font-bold px-2 py-0.5 rounded-full border border-rose-500/20">
                        {acc['start_date']} 시작
                    </span>
                </div>
                
                <div class="text-2xl sm:text-3xl font-extrabold text-white tracking-tight mt-0.5">
                    ₩{acc['total_krw']:,.0f}
                </div>

                <div class="flex items-center gap-2 mt-2 text-xs">
                    <span class="{profit_color} font-black text-sm">{profit_sign}{acc['cum_return_pct']:,.2f}%</span>
                    <span class="{profit_color} font-bold">({profit_sign}₩{acc['profit_krw']:,.0f})</span>
                    <span class="text-[10px] bg-slate-800 text-slate-300 px-1.5 py-0.5 rounded font-semibold ml-auto">
                        초기 원금 {acc['initial_krw']/10000:,.0f}만원
                    </span>
                </div>

                <div class="mts-subcard rounded-xl p-3 mt-3.5 grid grid-cols-2 gap-2 text-xs">
                    <div>
                        <span class="text-[11px] text-slate-400 block">총 자산 (USD)</span>
                        <span class="font-bold text-slate-200 text-sm">${acc['total_usd']:,.2f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">초기 투자 환산액</span>
                        <span class="font-bold text-slate-400 text-sm">${acc['initial_usd']:,.2f}</span>
                    </div>
                </div>
            </div>

            <!-- 외화 예수금 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-300">
                        <i data-lucide="wallet" class="w-4 h-4 text-emerald-400"></i>
                        <span>외화 예수금 (USD 현금)</span>
                    </div>
                    <span class="text-xs font-bold text-emerald-400">비중 {acc['cash_ratio']:.1f}%</span>
                </div>

                <div class="flex items-baseline justify-between mt-1">
                    <div class="text-xl font-black text-emerald-400">
                        ${acc['cash_usd']:,.2f}
                    </div>
                    <div class="text-xs text-slate-400">
                        약 ₩{acc['cash_krw']:,.0f}
                    </div>
                </div>

                <div class="mt-2.5 pt-2.5 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
                    <span>적용 환율 (USDKRW)</span>
                    <span class="font-semibold text-slate-300">₩{acc['fx_rate']:,.2f} / USD</span>
                </div>
            </div>

            <!-- 오늘의 주문 가이드 -->
            <div class="mts-card rounded-2xl p-4 border border-indigo-500/30 shadow-lg relative overflow-hidden">
                <div class="absolute -right-8 -top-8 w-24 h-24 bg-indigo-500/10 rounded-full blur-xl pointer-events-none"></div>

                <div class="flex items-center justify-between mb-2.5">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-indigo-400">
                        <i data-lucide="compass" class="w-4 h-4"></i>
                        <span>오늘의 주문 가이드 ({acc['signal_ticker']} 기준)</span>
                    </div>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold {acc['signal_badge']}">
                        {acc['signal_title']}
                    </span>
                </div>

                <div class="grid grid-cols-2 gap-2 my-2.5">
                    <div class="bg-slate-900/80 rounded-xl p-2.5 border border-slate-800">
                        <span class="text-[10px] text-slate-400 block">{acc['signal_ticker']} 고점대비 낙폭 (DD)</span>
                        <span class="text-sm font-black text-rose-400">{acc['sig_dd']:.2f}%</span>
                    </div>
                    <div class="bg-slate-900/80 rounded-xl p-2.5 border border-slate-800">
                        <span class="text-[10px] text-slate-400 block">{acc['signal_ticker']} 60월선 이격도</span>
                        <span class="text-sm font-black text-indigo-400">+{acc['disparity']:.2f}%</span>
                    </div>
                </div>

                <div class="bg-slate-900/90 rounded-xl p-3 border border-slate-800 text-xs text-slate-300 leading-relaxed">
                    <p class="font-bold text-white flex items-center gap-1.5 mb-0.5">
                        <i data-lucide="{acc['signal_icon']}" class="w-3.5 h-3.5 text-indigo-400"></i>
                        <span>{acc['signal_title']}</span>
                    </p>
                    <p class="text-[11px] text-slate-400 mt-1">{acc['signal_desc']}</p>
                </div>
            </div>

            <!-- 보유 종목 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5">
                        <span class="font-extrabold text-white text-base">{acc['target_ticker']}</span>
                        <span class="text-[11px] text-slate-400">{acc['target_name']}</span>
                    </div>
                    <span class="text-xs font-bold text-indigo-400">비중 {acc['stock_ratio']:.1f}%</span>
                </div>

                <div class="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-slate-800 text-xs">
                    <div>
                        <span class="text-[11px] text-slate-400 block">보유 수량</span>
                        <span class="font-black text-white text-sm">{acc['shares']} 주</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">현재가 (USD)</span>
                        <span class="font-black text-white text-sm">${acc['price']:,.2f}</span>
                    </div>
                    <div>
                        <span class="text-[11px] text-slate-400 block">평균 매입단가</span>
                        <span class="font-bold text-slate-300 text-xs">${acc['avg_price']:,.2f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400 block">수익률</span>
                        <span class="font-bold text-rose-400 text-xs">+{acc['profit_pct']:,.2f}%</span>
                    </div>
                </div>

                <div class="mts-subcard rounded-xl p-3 mt-3 flex items-center justify-between text-xs">
                    <div>
                        <span class="text-[10px] text-slate-400 block">평가 금액</span>
                        <span class="font-extrabold text-white text-sm">₩{acc['eval_krw']:,.0f}</span>
                    </div>
                    <div class="text-right">
                        <span class="text-[10px] text-slate-400 block">평가 손익 (USD)</span>
                        <span class="font-bold text-rose-400 text-xs">+${acc['profit_usd']:,.2f}</span>
                    </div>
                </div>
            </div>

            <!-- 최근 체결 내역 카드 -->
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-2">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-200">
                        <i data-lucide="receipt" class="w-4 h-4 text-slate-400"></i>
                        <span>최근 체결 내역 (최근 5건)</span>
                    </div>
                    <button onclick="switchSubTab('{aid}', 'trades')" class="text-[11px] text-rose-400 hover:text-rose-300 font-bold flex items-center gap-0.5">
                        <span>전체 {acc['total_trade_count']}건 보기</span>
                        <i data-lucide="chevron-right" class="w-3.5 h-3.5"></i>
                    </button>
                </div>

                <div class="divide-y divide-slate-800/80">
                    {recent_trades_html}
                </div>
            </div>

        </div>

        <!-- [SUB-TAB 2] 전체 체결 내역 -->
        <div id="subtab-{aid}-trades" class="subtab-content-{aid} space-y-3.5 mt-3" style="display: none;">
            <div class="mts-card rounded-2xl p-4 shadow-lg">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-1.5 text-xs font-bold text-slate-200">
                        <i data-lucide="history" class="w-4 h-4 text-indigo-400"></i>
                        <span>{acc['target_ticker']} 전체 체결 기록</span>
                    </div>
                    <span class="text-[11px] bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full font-bold border border-slate-700">
                        총 {acc['total_trade_count']}건
                    </span>
                </div>

                <div class="grid grid-cols-2 gap-2 text-xs">
                    <div class="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
                        <span class="text-slate-400">총 매수 체결</span>
                        <span class="font-bold text-rose-400 text-sm">{acc['total_buy_count']} 회</span>
                    </div>
                    <div class="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 flex items-center justify-between">
                        <span class="text-slate-400">총 매도 체결</span>
                        <span class="font-bold text-blue-400 text-sm">{acc['total_sell_count']} 회</span>
                    </div>
                </div>

                <!-- 필터 버튼 -->
                <div class="flex gap-1.5 mt-3 pt-3 border-t border-slate-800 text-[11px]">
                    <button onclick="filterTrades('{aid}', 'all')" class="filter-btn-{aid} active px-3 py-1 rounded-md font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">전체</button>
                    <button onclick="filterTrades('{aid}', '매수')" class="filter-btn-{aid} px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white">매수만</button>
                    <button onclick="filterTrades('{aid}', '매도')" class="filter-btn-{aid} px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white">매도만</button>
                </div>
            </div>

            <!-- 전체 체결 내역 리스트 -->
            <div class="space-y-2">
                {all_trades_html}
            </div>
        </div>
        """

    # 탭 버튼 HTML 생성
    tab_buttons_html = ""
    account_views_html = ""
    footer_accounts_info = []

    dot_colors = ["bg-rose-500", "bg-blue-500", "bg-emerald-500", "bg-amber-500", "bg-purple-500"]

    for i, acc in enumerate(accounts):
        aid = acc["account_id"]
        is_first = (i == 0)
        active_btn_class = "bg-slate-800 text-white border border-slate-700 shadow-md" if is_first else "text-slate-400 hover:text-slate-200 border border-transparent"
        active_view_class = "active" if is_first else ""
        dot_color = dot_colors[i % len(dot_colors)]
        
        profit_color = "text-rose-400" if acc['cum_return_pct'] >= 0 else "text-blue-400"
        profit_sign = "+" if acc['cum_return_pct'] >= 0 else ""

        tab_buttons_html += f"""
        <button id="acc-tab-btn-{aid}" onclick="switchAccount('{aid}')" class="py-2.5 px-2 rounded-xl flex flex-col items-center justify-center gap-0.5 transition {active_btn_class}">
            <div class="flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full {dot_color}"></span>
                <span class="font-bold text-[11px] sm:text-xs truncate">{acc['target_ticker']}</span>
            </div>
            <div class="text-[10px] sm:text-[11px] font-extrabold {profit_color}">
                {profit_sign}{acc['cum_return_pct']:,.1f}%
            </div>
        </button>
        """

        account_views_html += f"""
        <!-- [{acc['target_ticker']}] {acc['account_title']} 뷰 -->
        <div id="acc-view-{aid}" class="acc-view {active_view_class} space-y-3.5">
            {build_account_views(acc)}
        </div>
        """

        footer_accounts_info.append(f"{acc['target_ticker']} ({acc['start_date'][:4]} 시작 / {acc['initial_krw']/10000:,.0f}만)")

    first_acc = accounts[0]
    footer_text = " | ".join(footer_accounts_info)

    # JS용 계좌 메타데이터
    acc_js_meta = {
        acc["account_id"]: {
            "title": acc["account_title"],
            "num": acc["account_num"]
        }
        for acc in accounts
    }

    acc_js_json = json.dumps(acc_js_meta, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>위탁종합 퀀트 멀티 계좌 잔고 | TQQQ • SOXL • QLD</title>
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
        .acc-view {{
            display: none;
        }}
        .acc-view.active {{
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
                        <span id="header-acc-name" class="text-xs font-bold text-slate-200">{first_acc['account_title']}</span>
                        <span id="header-acc-num" class="text-[10px] text-slate-400">{first_acc['account_num']}</span>
                    </div>
                    <div class="text-[10px] text-slate-400 flex items-center gap-1">
                        <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                        <span>{first_acc['latest_date']} 기준 (자동갱신)</span>
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-2">
                <span class="text-[10px] bg-slate-800 text-slate-300 px-2 py-1 rounded-md border border-slate-700 font-medium">트리플 계좌 운용</span>
            </div>
        </div>
    </header>

    <!-- 2. 최상단 메인 계좌 전환 탭 바 -->
    <div class="max-w-md mx-auto px-4 pt-3.5">
        <div class="bg-slate-900/95 p-1 rounded-2xl border border-slate-800 grid grid-cols-3 gap-1 shadow-lg">
            {tab_buttons_html}
        </div>
    </div>

    <!-- 메인 컨테이너 -->
    <main class="max-w-md mx-auto px-4 space-y-3.5">
        {account_views_html}
    </main>

    <!-- 하단 고정 정보 바 -->
    <footer class="max-w-md mx-auto text-center text-slate-500 text-[11px] py-6 px-4">
        <p>Quantitative Multi-Asset Allocation Engine • GitHub Pages Automated</p>
        <p class="mt-1 text-[10px]">{footer_text}</p>
    </footer>

    <!-- 클라이언트 탭 전환 및 필터 스크립트 -->
    <script>
        lucide.createIcons();

        const accountMeta = {acc_js_json};
        const allAccountIds = Object.keys(accountMeta);

        // 1. 최상단 메인 계좌 전환 함수
        function switchAccount(accId) {{
            // 계좌 뷰 전환
            document.querySelectorAll('.acc-view').forEach(el => el.classList.remove('active'));
            const targetView = document.getElementById('acc-view-' + accId);
            if (targetView) targetView.classList.add('active');

            // 탭 버튼 스타일 전환
            allAccountIds.forEach(id => {{
                const btn = document.getElementById('acc-tab-btn-' + id);
                if (btn) {{
                    if (id === accId) {{
                        btn.className = 'py-2.5 px-2 rounded-xl flex flex-col items-center justify-center gap-0.5 transition bg-slate-800 text-white border border-slate-700 shadow-md';
                    }} else {{
                        btn.className = 'py-2.5 px-2 rounded-xl flex flex-col items-center justify-center gap-0.5 transition text-slate-400 hover:text-slate-200 border border-transparent';
                    }}
                }}
            }});

            // 헤더 정보 갱신
            const meta = accountMeta[accId];
            if (meta) {{
                document.getElementById('header-acc-name').textContent = meta.title;
                document.getElementById('header-acc-num').textContent = meta.num;
            }}

            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        // 2. 계좌별 하위 서브탭 (잔고 / 체결내역) 전환 함수
        function switchSubTab(accId, tabName) {{
            document.querySelectorAll('.subtab-content-' + accId).forEach(el => el.style.display = 'none');
            const targetSubtab = document.getElementById('subtab-' + accId + '-' + tabName);
            if (targetSubtab) targetSubtab.style.display = 'block';

            const btnBalance = document.getElementById('subtab-btn-' + accId + '-balance');
            const btnTrades = document.getElementById('subtab-btn-' + accId + '-trades');

            if (tabName === 'balance') {{
                if (btnBalance) btnBalance.className = 'subtab-btn-' + accId + ' py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm';
                if (btnTrades) btnTrades.className = 'subtab-btn-' + accId + ' py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200';
            }} else {{
                if (btnTrades) btnTrades.className = 'subtab-btn-' + accId + ' py-2 px-3 rounded-lg font-bold flex items-center justify-center gap-1.5 transition bg-slate-800 text-white shadow-sm';
                if (btnBalance) btnBalance.className = 'subtab-btn-' + accId + ' py-2 px-3 rounded-lg font-medium flex items-center justify-center gap-1.5 transition text-slate-400 hover:text-slate-200';
            }}

            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        // 3. 체결 내역 필터링 함수
        function filterTrades(accId, type) {{
            const items = document.querySelectorAll('.trade-item-' + accId);
            const buttons = document.querySelectorAll('.filter-btn-' + accId);

            buttons.forEach(btn => {{
                if (btn.textContent.includes(type) || (type === 'all' && btn.textContent === '전체')) {{
                    btn.className = 'filter-btn-' + accId + ' px-3 py-1 rounded-md font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30';
                }} else {{
                    btn.className = 'filter-btn-' + accId + ' px-3 py-1 rounded-md font-medium bg-slate-900 text-slate-400 border border-slate-800 hover:text-white';
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
    print(f"[4/4] 멀티 계좌 index.html 생성 완료 -> {output_path}")


def main():
    # 1) 계좌 1: QQQ -> TQQQ (나스닥 3X, 2026-06-03 시작, 원금 500만원, 250만 원 상한 적용)
    acc_tqqq = run_quant_strategy(
        signal_ticker="QQQ",
        target_ticker="TQQQ",
        target_name="ProShares UltraPro QQQ",
        account_id="tqqq",
        account_num="112-92-****01",
        account_title="위탁종합 (나스닥 3X)",
        start_date="2026-06-03",
        initial_krw=5_000_000.0,
        max_buy_krw=2_500_000.0
    )

    # 2) 계좌 2: QQQ -> SOXL (반도체 3X, 2026-09-30 시작, 원금 750만원, 250만 원 상한 적용)
    acc_soxl = run_quant_strategy(
        signal_ticker="QQQ",
        target_ticker="SOXL",
        target_name="Direxion Daily Semiconductor Bull 3X",
        account_id="soxl",
        account_num="112-92-****02",
        account_title="위탁종합 (반도체 3X)",
        start_date="2026-09-30",
        initial_krw=7_500_000.0,
        max_buy_krw=2_500_000.0
    )

    # 3) 계좌 3: QQQ -> QLD (나스닥 2X, 2013-01-07 시작, 원금 500만원, 상한 룰 제외: max_buy_krw=None)
    acc_qld = run_quant_strategy(
        signal_ticker="QQQ",
        target_ticker="QLD",
        target_name="ProShares Ultra QQQ (2X)",
        account_id="qld",
        account_num="112-92-****03",
        account_title="위탁종합 (나스닥 2X)",
        start_date="2013-01-07",
        initial_krw=5_000_000.0,
        max_buy_krw=None
    )

    # 4) 멀티 계좌 MTS HTML 생성
    render_multi_account_html([acc_tqqq, acc_soxl, acc_qld], "index.html")
    print("\n[SUCCESS] TQQQ, SOXL, QLD 멀티 계좌 대시보드 빌드 성공!")


if __name__ == "__main__":
    main()
