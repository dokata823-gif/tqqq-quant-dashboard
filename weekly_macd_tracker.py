"""
================================================================================
주봉 MACD 신호 트래커 (Weekly MACD Signal Tracker)
================================================================================
설명:
  - 사용자가 지정한 기준 종목(SIGNAL_TICKER)과 타깃 종목(TARGET_TICKER)에 대해
    로컬 SQLite DB에 일봉 수정종가를 증분 업데이트(Incremental Update)하고,
    금요일 기준(W-FRI) 주봉으로 리샘플링하여 MACD(12, 26, 9) 추세 및 크로스오버 신호를 분석합니다.
  - tabulate를 활용하여 터미널에 요약 카드 및 최근 10주간의 상세 분석 표를 출력합니다.

실행 방법:
  python weekly_macd_tracker.py
  python weekly_macd_tracker.py --signal QQQ --target TQQQ
  python weekly_macd_tracker.py -s SPY -t UPRO --weeks 12
  python weekly_macd_tracker.py -s SOXX -t SOXL --force-refresh
================================================================================
"""

import argparse
import datetime
import os
import sqlite3
import sys
from typing import Optional

# Windows 콘솔 UTF-8 출력 인코딩 보장
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd
import tabulate
import yfinance as yf

# ==============================================================================
# 기본 환경 설정 (Default Configuration)
# ==============================================================================
DEFAULT_SIGNAL_TICKER = "QQQ"
DEFAULT_TARGET_TICKER = "TQQQ"
DEFAULT_DB_PATH = "market_data.db"
DEFAULT_VIEW_WEEKS = 10
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
HISTORICAL_START_DATE = "2018-01-01"  # 신규 종목 적재 시 시작 기준일 (충분한 EMA 웜업용)


# ==============================================================================
# 1. 데이터베이스 관리 (Database & Pipeline)
# ==============================================================================
def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """SQLite 데이터베이스 및 테이블 초기화"""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_prices (
                date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                adj_close REAL NOT NULL,
                PRIMARY KEY (date, ticker)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ticker_date 
            ON daily_prices (ticker, date)
            """
        )
        conn.commit()


def get_latest_date_in_db(ticker: str, db_path: str = DEFAULT_DB_PATH) -> Optional[str]:
    """DB에 저장된 특정 티커의 마지막 일자 조회 (YYYY-MM-DD)"""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(date) FROM daily_prices WHERE ticker = ?",
            (ticker.upper(),),
        )
        result = cursor.fetchone()
        return result[0] if result and result[0] else None


def fetch_and_save_incremental(
    ticker: str,
    db_path: str = DEFAULT_DB_PATH,
    force_refresh: bool = False,
) -> int:
    """
    증분 업데이트 로직:
    - DB에 저장된 마지막 일자를 확인하고, 이후 영업일 데이터만 yfinance로 다운로드하여 DB에 적재
    - force_refresh=True일 경우 HISTORICAL_START_DATE부터 전체 재수신
    """
    ticker = ticker.upper()
    latest_date_str = None if force_refresh else get_latest_date_in_db(ticker, db_path)

    if latest_date_str:
        # 마지막 저장일 다음 날부터 다운로드
        last_dt = datetime.datetime.strptime(latest_date_str, "%Y-%m-%d").date()
        start_dt = last_dt + datetime.timedelta(days=1)
        start_date_str = start_dt.strftime("%Y-%m-%d")
    else:
        start_date_str = HISTORICAL_START_DATE

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # 이미 최신 날짜까지 동기화되어 있는 경우
    if latest_date_str and start_date_str > today_str:
        return 0

    print(f"[{ticker}] 데이터 수신 확인 중 (요청 시작일: {start_date_str}) ...")
    try:
        # yfinance 다운로드 (auto_adjust=False로 명시적 수정종가 확보)
        df_yf = yf.download(
            ticker,
            start=start_date_str,
            progress=False,
            auto_adjust=False,
        )
    except Exception as e:
        print(f"⚠️ [{ticker}] yfinance 다운로드 실패: {e}")
        return 0

    if df_yf.empty:
        return 0

    # 멀티인덱스 컬럼 처리 (yfinance 최신 버전 대응)
    if isinstance(df_yf.columns, pd.MultiIndex):
        if "Adj Close" in df_yf.columns.levels[0]:
            series = df_yf["Adj Close"][ticker] if ticker in df_yf["Adj Close"] else df_yf["Adj Close"].iloc[:, 0]
        elif "Close" in df_yf.columns.levels[0]:
            series = df_yf["Close"][ticker] if ticker in df_yf["Close"] else df_yf["Close"].iloc[:, 0]
        else:
            series = df_yf.iloc[:, 0]
    else:
        if "Adj Close" in df_yf.columns:
            series = df_yf["Adj Close"]
        elif "Close" in df_yf.columns:
            series = df_yf["Close"]
        else:
            series = df_yf.iloc[:, 0]

    series = series.dropna()
    if series.empty:
        return 0

    # DB 저장용 레코드 생성
    records = []
    for idx, val in series.items():
        if isinstance(idx, pd.Timestamp):
            d_str = idx.strftime("%Y-%m-%d")
        else:
            d_str = str(idx)[:10]
        records.append((d_str, ticker, float(val)))

    if not records:
        return 0

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO daily_prices (date, ticker, adj_close)
            VALUES (?, ?, ?)
            """,
            records,
        )
        conn.commit()

    return len(records)


def load_daily_prices(ticker: str, db_path: str = DEFAULT_DB_PATH) -> pd.Series:
    """DB에서 특정 티커의 일봉 수정종가 시계열(pd.Series) 로드"""
    ticker = ticker.upper()
    with sqlite3.connect(db_path) as conn:
        query = """
            SELECT date, adj_close 
            FROM daily_prices 
            WHERE ticker = ? 
            ORDER BY date ASC
        """
        df = pd.read_sql_query(query, conn, params=(ticker,))

    if df.empty:
        return pd.Series(dtype=float)

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")["adj_close"].sort_index()
    return df


# ==============================================================================
# 2. 지표 산출 엔진 (Calculation Engine)
# ==============================================================================
def resample_to_weekly(daily_series: pd.Series) -> pd.Series:
    """
    일봉 데이터를 금요일 마감 기준(W-FRI) 주봉으로 리샘플링
    - 공휴일로 금요일 휴장 시 해당 주의 마지막 영업일 종가 반영
    """
    if daily_series.empty:
        return pd.Series(dtype=float)

    # W-FRI 기준 리샘플링 후 마지막 값(종가) 추출
    weekly = daily_series.resample("W-FRI").last().dropna()
    return weekly


def calculate_macd(
    series: pd.Series,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> pd.DataFrame:
    """
    주어진 시계열(주봉)을 바탕으로 MACD(12, 26, 9) 및 시그널, 히스토그램 산출
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    df = pd.DataFrame(
        {
            "close": series,
            "macd": macd_line,
            "signal": signal_line,
            "hist": histogram,
        },
        index=series.index,
    )
    return df


def build_analysis_dataframe(
    signal_ticker: str,
    target_ticker: str,
    db_path: str = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """
    SIGNAL_TICKER와 TARGET_TICKER의 데이터를 결합하고,
    SIGNAL_TICKER의 주봉 MACD 지표 및 크로스오버/상태를 판정하여 종합 DataFrame 반환
    """
    sig_daily = load_daily_prices(signal_ticker, db_path)
    tgt_daily = load_daily_prices(target_ticker, db_path)

    if sig_daily.empty:
        raise ValueError(f"[{signal_ticker}] 데이터를 찾을 수 없거나 불러오지 못했습니다.")
    if tgt_daily.empty:
        raise ValueError(f"[{target_ticker}] 데이터를 찾을 수 없거나 불러오지 못했습니다.")

    # 주봉 리샘플링
    sig_weekly = resample_to_weekly(sig_daily)
    tgt_weekly = resample_to_weekly(tgt_daily)

    # 동일 티커인 경우 컬럼 충돌 방지
    is_same_ticker = signal_ticker.upper() == target_ticker.upper()

    # MACD 계산 (기준 종목 기반)
    macd_df = calculate_macd(sig_weekly)

    # 타깃 종목 주봉 종가 결합
    if is_same_ticker:
        combined_df = macd_df.copy()
        combined_df.rename(columns={"close": "sig_close"}, inplace=True)
        combined_df["tgt_close"] = combined_df["sig_close"]
    else:
        combined_df = macd_df.rename(columns={"close": "sig_close"}).join(
            tgt_weekly.rename("tgt_close"), how="inner"
        )

    combined_df = combined_df.dropna()

    # 상태 (Trend) 판정: MACD >= Signal 이면 BULL, 미만이면 BEAR
    combined_df["trend"] = combined_df.apply(
        lambda row: "🟢 BULL" if row["macd"] >= row["signal"] else "🔴 BEAR",
        axis=1,
    )

    # 크로스오버 (Crossover / Change) 판정
    # 이전 주 상태와 비교
    prev_trend = combined_df["trend"].shift(1)
    change_labels = []

    for curr, prev in zip(combined_df["trend"], prev_trend):
        if pd.isna(prev):
            change_labels.append("-")
        elif "BEAR" in prev and "BULL" in curr:
            change_labels.append("★ 골든크로스")
        elif "BULL" in prev and "BEAR" in curr:
            change_labels.append("★ 데드크로스")
        else:
            change_labels.append("-")

    combined_df["change"] = change_labels
    return combined_df


# ==============================================================================
# 3. 콘솔 텍스트 뷰어 (Console UI)
# ==============================================================================
def print_summary_card(
    signal_ticker: str,
    target_ticker: str,
    latest_row: pd.Series,
    latest_date_str: str,
) -> None:
    """헤더 요약 카드 박스 출력"""
    trend_str = latest_row["trend"]
    change_str = latest_row["change"]

    # 신호 알림 메시지 구성
    if "골든크로스" in change_str:
        alert_msg = "🚨 [신호 변동] 이번 주 ★ 골든크로스 발생! (상승 전환 / 매수 구간 진입)"
    elif "데드크로스" in change_str:
        alert_msg = "🚨 [신호 변동] 이번 주 ★ 데드크로스 발생! (하락 전환 / 매도·축소 구간 진입)"
    else:
        state_kor = "매수/보유 지속" if "BULL" in trend_str else "매도/관망 지속"
        alert_msg = f"신호 유지 중 ({state_kor})"

    sig_price = f"${latest_row['sig_close']:,.2f}"
    tgt_price = f"${latest_row['tgt_close']:,.2f}"
    macd_val = f"{latest_row['macd']:+.2f}"
    sig_val = f"{latest_row['signal']:+.2f}"
    hist_val = f"{latest_row['hist']:+.2f}"

    card = f"""
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        📊 주봉 MACD 추세 & 시그널 모니터                              │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  • 기준 종목 (Signal) : {signal_ticker:<6} │ 최신 주봉 종가 : {sig_price:<12}                │
│  • 타깃 종목 (Target) : {target_ticker:<6} │ 최신 주봉 종가 : {tgt_price:<12}                │
│  • 분석 기준 일자     : {latest_date_str:<10} │ 현재 추세 상태 : {trend_str:<12}                │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  • MACD 지표 상태    : MACD({macd_val}) | Signal({sig_val}) | Hist({hist_val})
│  • 트레이딩 알림      : {alert_msg}
└────────────────────────────────────────────────────────────────────────────────────────┘
"""
    print(card)


def print_table_view(
    df: pd.DataFrame,
    signal_ticker: str,
    target_ticker: str,
    weeks: int = DEFAULT_VIEW_WEEKS,
) -> None:
    """최근 N주간의 상세 테이블 출력 (tabulate 그리드)"""
    recent_df = df.tail(weeks).copy()

    table_data = []
    for idx, row in recent_df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if isinstance(idx, pd.Timestamp) else str(idx)[:10]
        table_data.append(
            [
                date_str,
                f"${row['sig_close']:,.2f}",
                f"${row['tgt_close']:,.2f}",
                f"{row['macd']:+.2f}",
                f"{row['signal']:+.2f}",
                f"{row['hist']:+.2f}",
                row["trend"],
                row["change"],
            ]
        )

    headers = [
        "주봉 마감일 (Date)",
        f"기준종가 ({signal_ticker})",
        f"타깃종가 ({target_ticker})",
        "MACD (12,26)",
        "Signal (9)",
        "Histogram",
        "추세 (Trend)",
        "신호 변동 (Change)",
    ]

    print(f"\n📋 [최근 {min(weeks, len(recent_df))}주간 상세 지표 테이블]")
    print(
        tabulate.tabulate(
            table_data,
            headers=headers,
            tablefmt="fancy_grid",
            stralign="center",
            numalign="center",
            disable_numparse=True,
        )
    )
    print("\n* 참고: 주봉은 매주 금요일 마감(W-FRI) 기준이며, 공휴일 주간은 해당 주의 마지막 영업일 종가가 반영됩니다.")
    print("=" * 90 + "\n")


# ==============================================================================
# 4. 메인 실행 컨트롤러 (Main Application)
# ==============================================================================
def parse_arguments() -> argparse.Namespace:
    """명령행 인자 파싱"""
    parser = argparse.ArgumentParser(
        description="유연한 티커 설정을 지원하는 텍스트(CLI) 기반 주봉 MACD 신호 트래커",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s", "--signal",
        dest="signal_ticker",
        type=str,
        default=DEFAULT_SIGNAL_TICKER,
        help="추세 판단 및 MACD 지표를 생성할 기준 종목 티커 (예: QQQ, SPY, SOXX 등)",
    )
    parser.add_argument(
        "-t", "--target",
        dest="target_ticker",
        type=str,
        default=DEFAULT_TARGET_TICKER,
        help="실제 매매 및 주가를 추적할 대상 종목 티커 (예: TQQQ, UPRO, SOXL 등)",
    )
    parser.add_argument(
        "-w", "--weeks",
        dest="weeks",
        type=int,
        default=DEFAULT_VIEW_WEEKS,
        help="상세 테이블에 표시할 최근 주(week) 수",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        type=str,
        default=DEFAULT_DB_PATH,
        help="로컬 SQLite 데이터베이스 파일 경로",
    )
    parser.add_argument(
        "--force-refresh",
        dest="force_refresh",
        action="store_true",
        help="캐시된 DB 데이터를 무시하고 전체 데이터를 재수신",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    signal_ticker = args.signal_ticker.strip().upper()
    target_ticker = args.target_ticker.strip().upper()
    db_path = args.db_path
    view_weeks = max(1, args.weeks)
    force_refresh = args.force_refresh

    print("=" * 90)
    print(f"🚀 [MACD Tracker] 기준 종목: {signal_ticker} | 타깃 종목: {target_ticker} | DB: {db_path}")
    print("=" * 90)

    # 1. DB 초기화
    init_db(db_path)

    # 2. 증분 데이터 파이프라인 가동
    tickers_to_fetch = {signal_ticker, target_ticker}
    for t in tickers_to_fetch:
        added_cnt = fetch_and_save_incremental(t, db_path=db_path, force_refresh=force_refresh)
        if added_cnt > 0:
            print(f"✅ [{t}] 신규 데이터 {added_cnt}건 DB 적재 완료.")
        else:
            print(f"⚡ [{t}] DB 데이터가 이미 최신 상태입니다.")

    # 3. 지표 산출 엔진 가동
    try:
        df_analysis = build_analysis_dataframe(signal_ticker, target_ticker, db_path=db_path)
    except Exception as e:
        print(f"\n❌ 지표 산출 중 오류가 발생했습니다: {e}")
        sys.exit(1)

    if df_analysis.empty:
        print("❌ 분석할 주봉 데이터가 부족합니다.")
        sys.exit(1)

    # 4. 콘솔 UI 출력
    latest_row = df_analysis.iloc[-1]
    latest_idx = df_analysis.index[-1]
    latest_date_str = latest_idx.strftime("%Y-%m-%d") if isinstance(latest_idx, pd.Timestamp) else str(latest_idx)[:10]

    print_summary_card(signal_ticker, target_ticker, latest_row, latest_date_str)
    print_table_view(df_analysis, signal_ticker, target_ticker, weeks=view_weeks)


if __name__ == "__main__":
    main()
