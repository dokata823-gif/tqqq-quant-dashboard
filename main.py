"""
================================================================================
QQQ / TQQQ 주봉 MACD 퀀트 트레이딩 데스크톱 대시보드
(Quant Weekly MACD Desktop Dashboard)
================================================================================
- 프레임워크: PyQt6, pyqtgraph
- 데이터 파이프라인: yfinance, SQLite3 (market_data.db)
- 다크 테마: 블룸버그 / 트레이딩뷰 스타일 모던 QSS
- 비동기 백그라운드 워커: QThread 기반 UI 프리징 방지
================================================================================
"""

import sys
import os
import sqlite3
import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

# PyQt6 모듈
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QDateTime, QPointF, QRectF
)
from PyQt6.QtGui import (
    QColor, QFont, QPen, QBrush, QIcon, QPainter, QPalette
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QFrame,
    QStatusBar, QProgressBar, QSizePolicy, QGraphicsTextItem,
    QButtonGroup, QRadioButton
)

# pyqtgraph 모듈
import pyqtgraph as pg

# pyqtgraph 글로벌 설정 (다크 테마 최적화 및 안티앨리어싱)
pg.setConfigOption('background', '#121212')
pg.setConfigOption('foreground', '#E0E0E0')
pg.setConfigOptions(antialias=True)


# ==============================================================================
# 1. 전역 상수 및 설정 (Constants & Configurations)
# ==============================================================================
DB_PATH = "market_data.db"
DEFAULT_START_DATE = "2012-01-01"
TICKERS = ["QQQ", "TQQQ"]

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# 컬러 팔레트 (Dark Financial Theme)
COLOR_BG = "#121212"
COLOR_CARD_BG = "#1E1E1E"
COLOR_CARD_BORDER = "#2A2E39"
COLOR_TEXT_PRIMARY = "#F0F0F0"
COLOR_TEXT_MUTED = "#888888"
COLOR_BULLISH = "#00E676"     # 산뜻한 네온 그린 (골든크로스 / 상승)
COLOR_BEARISH = "#FF5252"     # 산뜻한 네온 레드 (데드크로스 / 하락)
COLOR_MACD_LINE = "#2979FF"   # 블루
COLOR_SIGNAL_LINE = "#FF9100" # 오렌지
COLOR_ACCENT = "#7C4DFF"      # 보라 액센트
COLOR_GRID = "#222630"


# ==============================================================================
# 2. 데이터베이스 매니저 (SQLite Database Manager)
# ==============================================================================
class DatabaseManager:
    """SQLite DB 연결 및 증분 업데이트/조회를 담당하는 클래스"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def init_db(self) -> None:
        """테이블 및 인덱스 초기화 및 누락 컬럼 자동 마이그레이션"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_prices (
                    date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    adj_close REAL NOT NULL,
                    volume INTEGER,
                    PRIMARY KEY (date, ticker)
                )
                """
            )
            # 기존 DB와의 호환성을 위한 컬럼 마이그레이션 검사
            cursor.execute("PRAGMA table_info(daily_prices)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            required_cols = {
                "open": "REAL",
                "high": "REAL",
                "low": "REAL",
                "close": "REAL",
                "volume": "INTEGER"
            }
            for col_name, col_type in required_cols.items():
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE daily_prices ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ticker_date 
                ON daily_prices (ticker, date)
                """
            )
            conn.commit()

    def get_latest_date(self, ticker: str) -> Optional[str]:
        """특정 티커의 DB 내 최신 날짜 반환 (YYYY-MM-DD)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT MAX(date) FROM daily_prices WHERE ticker = ?",
                (ticker.upper(),)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def get_db_overall_latest_date(self) -> Optional[str]:
        """전체 DB 내 최신 기록 날짜 반환"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM daily_prices")
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def save_df(self, df: pd.DataFrame, ticker: str) -> int:
        """DataFrame 데이터를 SQLite에 안전하게 Upsert/Insert"""
        if df.empty:
            return 0

        ticker = ticker.upper()
        records = []
        for idx, row in df.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if isinstance(idx, (pd.Timestamp, datetime.date, datetime.datetime)) else str(idx)[:10]
            open_val = float(row["Open"]) if "Open" in row and pd.notna(row["Open"]) else None
            high_val = float(row["High"]) if "High" in row and pd.notna(row["High"]) else None
            low_val = float(row["Low"]) if "Low" in row and pd.notna(row["Low"]) else None
            close_val = float(row["Close"]) if "Close" in row and pd.notna(row["Close"]) else None
            adj_close_val = float(row["Adj Close"]) if "Adj Close" in row and pd.notna(row["Adj Close"]) else (close_val or 0.0)
            volume_val = int(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else 0

            records.append((date_str, ticker, open_val, high_val, low_val, close_val, adj_close_val, volume_val))

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO daily_prices 
                (date, ticker, open, high, low, close, adj_close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records
            )
            conn.commit()
        return len(records)

    def load_daily_prices(self, ticker: str) -> pd.DataFrame:
        """DB에서 일봉 데이터를 로드하여 DataFrame으로 반환"""
        with self.get_connection() as conn:
            query = """
                SELECT date, open, high, low, close, adj_close, volume 
                FROM daily_prices 
                WHERE ticker = ? 
                ORDER BY date ASC
            """
            df = pd.read_sql_query(query, conn, params=(ticker.upper(),), parse_dates=["date"])
            if not df.empty:
                df.set_index("date", inplace=True)
            return df


# ==============================================================================
# 3. 퀀트 지표 엔진 (Quant Indicator Engine)
# ==============================================================================
class QuantEngine:
    """주봉 리샘플링, MACD 및 골든/데드크로스 신호 연산 엔진"""

    @staticmethod
    def resample_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
        """
        일봉 데이터를 금요일 마감 기준(W-FRI) 주봉으로 리샘플링.
        - 휴장일로 조기 마감된 주의 경우 마지막 영업일 종가를 해당 주 금요일 종가로 처리.
        - 현재 진행 중인 주(Unclosed Bar) 플래그 및 시작일/종료일 메타데이터 생성.
        """
        if daily_df.empty:
            return pd.DataFrame()

        df = daily_df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 리샘플링 애그리게이션
        agg_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'adj_close': 'last',
            'volume': 'sum'
        }
        # 존재하는 컬럼만 선택
        valid_aggs = {k: v for k, v in agg_dict.items() if k in df.columns}
        
        # W-FRI 기준으로 리샘플링
        weekly = df.resample('W-FRI').agg(valid_aggs)
        weekly.dropna(subset=['adj_close'], inplace=True)

        if weekly.empty:
            return weekly

        # 각 주의 실제 마지막 거래일 및 진행 중 여부 계산
        weekly['last_trading_date'] = df['adj_close'].resample('W-FRI').apply(lambda s: s.index[-1] if len(s) > 0 else None)
        
        today = pd.Timestamp.now().normalize()
        # 현재 주의 금요일 날짜
        current_friday = (today + pd.Timedelta(days=(4 - today.weekday()) % 7))
        
        # 마지막 행이 현재 주인지 여부 판단
        weekly['is_unclosed'] = False
        if not weekly.empty and weekly.index[-1] >= current_friday:
            # 아직 금요일 장마감이 끝나지 않은 경우
            weekly.iloc[-1, weekly.columns.get_loc('is_unclosed')] = True

        return weekly

    @staticmethod
    def calculate_macd(
        series: pd.Series,
        fast: int = MACD_FAST,
        slow: int = MACD_SLOW,
        signal: int = MACD_SIGNAL
    ) -> pd.DataFrame:
        """MACD (12, 26, 9) 및 히스토그램 산출"""
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        df_macd = pd.DataFrame({
            'macd': macd_line,
            'signal': signal_line,
            'hist': histogram,
            'ema_fast': ema_fast,
            'ema_slow': ema_slow
        }, index=series.index)
        return df_macd

    @classmethod
    def compute_all_indicators(cls, db: DatabaseManager) -> Dict[str, dict]:
        """QQQ 및 TQQQ의 주봉 및 MACD 종합 분석 데이터셋 생성"""
        results = {}
        for ticker in TICKERS:
            daily = db.load_daily_prices(ticker)
            if daily.empty:
                continue

            weekly = cls.resample_weekly(daily)
            if weekly.empty:
                continue

            macd_df = cls.calculate_macd(weekly['adj_close'])
            combined = weekly.join(macd_df)

            # 주간 수익률 계산
            combined['weekly_return'] = combined['adj_close'].pct_change() * 100.0

            # 크로스오버 신호 탐지 (골든: 1, 데드: -1, 없음: 0)
            combined['crossover'] = 0
            prev_hist = combined['hist'].shift(1)
            
            # 히스토그램이 음수/0에서 양수로 전환 -> 골든크로스
            golden = (combined['hist'] > 0) & (prev_hist <= 0)
            # 히스토그램이 양수/0에서 음수로 전환 -> 데드크로스
            dead = (combined['hist'] < 0) & (prev_hist >= 0)

            combined.loc[golden, 'crossover'] = 1
            combined.loc[dead, 'crossover'] = -1

            # 신호 상태 (BULLISH or BEARISH)
            combined['status'] = np.where(combined['hist'] >= 0, 'BULLISH', 'BEARISH')

            results[ticker] = {
                'daily': daily,
                'weekly': combined
            }

        return results


# ==============================================================================
# 4. 백그라운드 데이터 수기/자동 동기화 워커 (QThread Worker)
# ==============================================================================
class DataSyncWorker(QThread):
    """yfinance로부터 데이터를 백그라운드 다운로드하여 DB에 적재하는 스레드"""
    progress_signal = pyqtSignal(str)          # 진행 상황 텍스트
    finished_signal = pyqtSignal(bool, str)     # 성공 여부, 결과 메시지

    def __init__(self, db_manager: DatabaseManager, force_refresh: bool = False):
        super().__init__()
        self.db = db_manager
        self.force_refresh = force_refresh

    def run(self):
        try:
            total_added = 0
            updated_tickers = []

            for ticker in TICKERS:
                self.progress_signal.emit(f"[{ticker}] 최신 데이터 확인 중...")
                latest_date_str = None if self.force_refresh else self.db.get_latest_date(ticker)

                if latest_date_str is None:
                    # 최초 다운로드
                    start_date = DEFAULT_START_DATE
                    self.progress_signal.emit(f"[{ticker}] {start_date}부터 전체 과거 데이터 다운로드 중...")
                else:
                    # 마지막 날짜 + 1일 계산
                    last_dt = datetime.datetime.strptime(latest_date_str, "%Y-%m-%d")
                    next_dt = last_dt + datetime.timedelta(days=1)
                    today = datetime.datetime.now()

                    if next_dt.date() > today.date():
                        self.progress_signal.emit(f"[{ticker}] 이미 최신 데이터 상태입니다 ({latest_date_str}).")
                        continue

                    start_date = next_dt.strftime("%Y-%m-%d")
                    self.progress_signal.emit(f"[{ticker}] {start_date}부터 증분 데이터 다운로드 중...")

                # yfinance 호출
                try:
                    df = yf.download(
                        ticker,
                        start=start_date,
                        auto_adjust=False,
                        progress=False,
                        multi_level_index=False
                    )
                except Exception as e:
                    self.progress_signal.emit(f"[{ticker}] 다운로드 실패: {str(e)}")
                    continue

                if df is None or df.empty:
                    self.progress_signal.emit(f"[{ticker}] 신규 거래 데이터 없음.")
                    continue

                # MultiIndex 컬럼 평탄화 (yfinance 버전에 따른 대응)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

                # Adj Close 누락 시 Close로 대체
                if "Adj Close" not in df.columns and "Close" in df.columns:
                    df["Adj Close"] = df["Close"]

                added_count = self.db.save_df(df, ticker)
                total_added += added_count
                updated_tickers.append(f"{ticker}(+{added_count}건)")

            msg = f"동기화 완료! 총 {total_added}건 적재됨. {', '.join(updated_tickers) if updated_tickers else '최신 상태 유지'}"
            self.finished_signal.emit(True, msg)

        except Exception as e:
            self.finished_signal.emit(False, f"동기화 오류: {str(e)}")


# ==============================================================================
# 5. 커스텀 pyqtgraph 위젯 (Custom Date Axis & Plot Items)
# ==============================================================================
class IndexedDateAxis(pg.AxisItem):
    """
    정수 인덱스를 날짜 문자열(YYYY-MM-DD)로 변환해주는 커스텀 X축.
    주말/휴장일 공백 없이 깔끔하고 연속적인 캔들/라인 차트를 구현합니다.
    """
    def __init__(self, date_labels: List[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.date_labels = date_labels

    def update_labels(self, date_labels: List[str]):
        self.date_labels = date_labels

    def tickStrings(self, values, scale, spacing):
        strings = []
        for val in values:
            idx = int(round(val))
            if 0 <= idx < len(self.date_labels):
                strings.append(self.date_labels[idx])
            else:
                strings.append("")
        return strings


# ==============================================================================
# 6. 모던 UI 스타일시트 (Bloomberg/TradingView Style QSS)
# ==============================================================================
DARK_STYLE_SHEET = """
QMainWindow {
    background-color: #121212;
}

QWidget {
    color: #E0E0E0;
    font-family: 'Segoe UI', 'Malgun Gothic', 'Noto Sans KR', sans-serif;
    font-size: 13px;
}

/* 상단 헤더 컨테이너 */
QFrame#HeaderFrame {
    background-color: #181A20;
    border-bottom: 1px solid #2B313A;
    padding: 6px 12px;
}

/* KPI 카드 위젯 */
QFrame.KpiCard {
    background-color: #1E222D;
    border: 1px solid #2A2E39;
    border-radius: 8px;
    padding: 10px;
}

QFrame.KpiCard:hover {
    border: 1px solid #3E4556;
    background-color: #242936;
}

QLabel#CardTitle {
    color: #848E9C;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
}

QLabel#CardValue {
    font-size: 22px;
    font-weight: bold;
    color: #EAECEF;
}

QLabel#CardSubText {
    font-size: 12px;
    font-weight: 500;
}

/* 버튼 스타일 */
QPushButton {
    background-color: #2962FF;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 13px;
}

QPushButton:hover {
    background-color: #1E4BD8;
}

QPushButton:pressed {
    background-color: #1538A6;
}

QPushButton:disabled {
    background-color: #373A40;
    color: #686D76;
}

/* 토글 라디오 버튼 / 콤보박스 */
QComboBox {
    background-color: #2A2E39;
    color: #EAECEF;
    border: 1px solid #363C4E;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
}

QComboBox::drop-down {
    border: none;
}

QComboBox QAbstractItemView {
    background-color: #1E222D;
    color: #EAECEF;
    selection-background-color: #2962FF;
    border: 1px solid #363C4E;
}

QRadioButton {
    color: #B2B5BE;
    font-weight: 600;
    spacing: 6px;
}

QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border-radius: 7px;
    border: 2px solid #505668;
    background-color: #1E222D;
}

QRadioButton::indicator:checked {
    border: 2px solid #2962FF;
    background-color: #2962FF;
}

/* 프로그레스 바 */
QProgressBar {
    background-color: #1E222D;
    border: 1px solid #2A2E39;
    border-radius: 4px;
    text-align: center;
    color: #EAECEF;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #2962FF;
    border-radius: 3px;
}

/* 상태바 */
QStatusBar {
    background-color: #181A20;
    border-top: 1px solid #2B313A;
    color: #848E9C;
    font-size: 12px;
}
"""


# ==============================================================================
# 7. 메인 윈도우 UI (Main Dashboard Window)
# ==============================================================================
class QuantDashboard(QMainWindow):
    """QQQ / TQQQ 주봉 MACD 퀀트 대시보드 메인 클래스"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QUANT DASHBOARD — QQQ / TQQQ Weekly MACD Signal Tracker")
        self.resize(1380, 920)
        self.setStyleSheet(DARK_STYLE_SHEET)

        # 데이터베이스 및 엔진 인스턴스
        self.db = DatabaseManager()
        self.indicators_data: Dict[str, dict] = {}
        self.selected_ticker = "QQQ"  # 상단 차트 표시용 선택 티커
        self.date_labels: List[str] = []

        # 백그라운드 동기화 스레드
        self.sync_worker: Optional[DataSyncWorker] = None

        # UI 초기화 및 빌드
        self._init_ui()

        # 시계 타이머
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start(1000)
        self._update_clock()

        # 앱 기동 즉시 로컬 데이터 로드 & 백그라운드 자동 최신화
        self._load_and_refresh_views()
        self._start_data_sync(force=False)

    # --------------------------------------------------------------------------
    # UI 빌드 및 레이아웃 설정
    # --------------------------------------------------------------------------
    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 6)
        main_layout.setSpacing(10)

        # 1. 헤더 영역
        header_widget = self._build_header()
        main_layout.addWidget(header_widget)

        # 2. KPI 카드 영역
        kpi_widget = self._build_kpi_cards()
        main_layout.addWidget(kpi_widget)

        # 3. 차트 컨트롤러 (티커 토글)
        chart_ctrl = self._build_chart_controls()
        main_layout.addWidget(chart_ctrl)

        # 4. 차트 뷰 영역 (상하 2분할 pyqtgraph)
        charts_layout = self._build_charts()
        main_layout.addLayout(charts_layout, stretch=1)

        # 5. 상태바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("시스템 준비 완료")
        self.status_bar.addWidget(self.status_label, 1)

        self.weeks_elapsed_label = QLabel("최근 신호 경과: -")
        self.weeks_elapsed_label.setStyleSheet("color: #2979FF; font-weight: bold; margin-right: 15px;")
        self.status_bar.addPermanentWidget(self.weeks_elapsed_label)

    def _build_header(self) -> QWidget:
        header_frame = QFrame()
        header_frame.setObjectName("HeaderFrame")
        layout = QHBoxLayout(header_frame)
        layout.setContentsMargins(8, 4, 8, 4)

        # 로고 및 타이틀
        title_box = QVBoxLayout()
        title_label = QLabel("QUANT WEEKLY MACD SIGNAL TRACKER")
        title_label.setStyleSheet("font-size: 16px; font-weight: 800; color: #FFFFFF; letter-spacing: 1px;")
        sub_title = QLabel("QQQ Weekly MACD(12,26,9) Trend Engine & TQQQ Tactical Allocation")
        sub_title.setStyleSheet("font-size: 11px; color: #848E9C;")
        title_box.addWidget(title_label)
        title_box.addWidget(sub_title)
        layout.addLayout(title_box)

        layout.addStretch(1)

        # 현재 시각
        self.lbl_clock = QLabel()
        self.lbl_clock.setStyleSheet("font-size: 13px; color: #B2B5BE; font-weight: 600; margin-right: 15px;")
        layout.addWidget(self.lbl_clock)

        # DB 최신 일자 레이블
        self.lbl_db_date = QLabel("DB 최신일: -")
        self.lbl_db_date.setStyleSheet("font-size: 13px; color: #848E9C; margin-right: 15px;")
        layout.addWidget(self.lbl_db_date)

        # 프로그레스 바 (동기화 중 표시)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # 무한 펄스
        self.progress_bar.setFixedWidth(130)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # 데이터 최신화 버튼
        self.btn_sync = QPushButton("⚡ Sync Data (데이터 최신화)")
        self.btn_sync.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync.clicked.connect(lambda: self._start_data_sync(force=False))
        layout.addWidget(self.btn_sync)

        return header_frame

    def _build_kpi_cards(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 카드 1: QQQ 현황
        self.card_qqq, self.lbl_qqq_price, self.lbl_qqq_ret = self._create_card("QQQ (나스닥 100)", "$---.--", "주간 변동률: --%")
        layout.addWidget(self.card_qqq)

        # 카드 2: TQQQ 현황
        self.card_tqqq, self.lbl_tqqq_price, self.lbl_tqqq_ret = self._create_card("TQQQ (3X 레버리지)", "$---.--", "주간 변동률: --%")
        layout.addWidget(self.card_tqqq)

        # 카드 3: QQQ 주봉 MACD 신호 상태
        self.card_signal, self.lbl_signal_title, self.lbl_signal_desc = self._create_card(
            "QQQ 주봉 MACD 신호", "WAITING", "Hist: -- | Signal: --"
        )
        layout.addWidget(self.card_signal)

        # 카드 4: 권장 포지션 가이드
        self.card_guide, self.lbl_guide_title, self.lbl_guide_desc = self._create_card(
            "전술적 포지션 가이드", "포지션 산출 중...", "위험 관리 모니터링"
        )
        layout.addWidget(self.card_guide)

        return container

    def _create_card(self, title: str, main_val: str, sub_val: str) -> Tuple[QFrame, QLabel, QLabel]:
        card = QFrame()
        card.setProperty("class", "KpiCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("CardTitle")
        card_layout.addWidget(lbl_title)

        lbl_main = QLabel(main_val)
        lbl_main.setObjectName("CardValue")
        card_layout.addWidget(lbl_main)

        lbl_sub = QLabel(sub_val)
        lbl_sub.setObjectName("CardSubText")
        card_layout.addWidget(lbl_sub)

        return card, lbl_main, lbl_sub

    def _build_chart_controls(self) -> QWidget:
        ctrl_frame = QFrame()
        layout = QHBoxLayout(ctrl_frame)
        layout.setContentsMargins(4, 2, 4, 2)

        lbl_chart_select = QLabel("상단 차트 종목 선택:")
        lbl_chart_select.setStyleSheet("font-weight: bold; color: #B2B5BE; margin-right: 8px;")
        layout.addWidget(lbl_chart_select)

        # 라디오 버튼 토글 그룹
        self.btn_group = QButtonGroup(self)
        self.rb_qqq = QRadioButton("QQQ (1X Benchmark)")
        self.rb_tqqq = QRadioButton("TQQQ (3X Target)")
        self.rb_qqq.setChecked(True)

        self.btn_group.addButton(self.rb_qqq, 0)
        self.btn_group.addButton(self.rb_tqqq, 1)
        self.btn_group.idClicked.connect(self._on_ticker_toggle)

        layout.addWidget(self.rb_qqq)
        layout.addWidget(self.rb_tqqq)

        layout.addStretch(1)

        # 안내 텍스트
        lbl_info = QLabel("💡 휠 스크롤: 줌(Zoom) | 드래그: 이동(Pan) | QQQ MACD 크로스오버 지점 자동 마킹")
        lbl_info.setStyleSheet("color: #707584; font-size: 11px;")
        layout.addWidget(lbl_info)

        return ctrl_frame

    def _build_charts(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # 커스텀 X축 인덱스 날짜 생성
        self.x_axis_top = IndexedDateAxis(self.date_labels, orientation='bottom')
        self.x_axis_bottom = IndexedDateAxis(self.date_labels, orientation='bottom')

        # 그래픽스 레이아웃 위젯
        self.win = pg.GraphicsLayoutWidget()
        self.win.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.win.ci.layout.setSpacing(8)

        # 1. 상단 주가 차트 (Price Plot)
        self.plot_price = self.win.addPlot(row=0, col=0, axisItems={'bottom': self.x_axis_top})
        self.plot_price.showGrid(x=True, y=True, alpha=0.25)
        self.plot_price.setLabel('left', 'Price ($)', color='#A0A0A0')
        self.plot_price.getAxis('left').setTextPen(QColor('#A0A0A0'))
        self.plot_price.getAxis('bottom').setTextPen(QColor('#A0A0A0'))

        # 2. 하단 MACD 차트 (MACD / Signal / Histogram Plot)
        self.plot_macd = self.win.addPlot(row=1, col=0, axisItems={'bottom': self.x_axis_bottom})
        self.plot_macd.showGrid(x=True, y=True, alpha=0.25)
        self.plot_macd.setLabel('left', 'MACD', color='#A0A0A0')
        self.plot_macd.getAxis('left').setTextPen(QColor('#A0A0A0'))
        self.plot_macd.getAxis('bottom').setTextPen(QColor('#A0A0A0'))

        # 상하 차트 X축 줌/팬 동기화 (Link X Axis)
        self.plot_macd.setXLink(self.plot_price)

        # 높이 비율 설정 (상단 65%, 하단 35%)
        self.win.ci.layout.setRowStretchFactor(0, 3)
        self.win.ci.layout.setRowStretchFactor(1, 2)

        # 플롯 아이템 핸들러들
        self.curve_price = self.plot_price.plot(pen=pg.mkPen(color='#00E5FF', width=2), name="Price")
        self.scatter_golden = pg.ScatterPlotItem(
            size=14, pen=pg.mkPen(None), brush=pg.mkBrush(COLOR_BULLISH), symbol='t1'
        )  # 상향 화살표
        self.scatter_dead = pg.ScatterPlotItem(
            size=14, pen=pg.mkPen(None), brush=pg.mkBrush(COLOR_BEARISH), symbol='t'
        )  # 하향 화살표
        self.plot_price.addItem(self.scatter_golden)
        self.plot_price.addItem(self.scatter_dead)

        # 하단 MACD 아이템들
        self.curve_macd = self.plot_macd.plot(pen=pg.mkPen(color=COLOR_MACD_LINE, width=2), name="MACD (12,26)")
        self.curve_signal = self.plot_macd.plot(pen=pg.mkPen(color=COLOR_SIGNAL_LINE, width=2), name="Signal (9)")
        self.zero_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(color='#555555', width=1, style=Qt.PenStyle.DashLine))
        self.plot_macd.addItem(self.zero_line)

        self.hist_bar_item: Optional[pg.BarGraphItem] = None

        # 크로스헤어 (Hover Crosshair Line)
        self.v_line_top = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color='#777777', style=Qt.PenStyle.DotLine))
        self.h_line_top = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color='#777777', style=Qt.PenStyle.DotLine))
        self.v_line_bot = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color='#777777', style=Qt.PenStyle.DotLine))
        
        self.plot_price.addItem(self.v_line_top, ignoreBounds=True)
        self.plot_price.addItem(self.h_line_top, ignoreBounds=True)
        self.plot_macd.addItem(self.v_line_bot, ignoreBounds=True)

        # 마우스 이동 이벤트 연결
        self.proxy_mouse = pg.SignalProxy(
            self.win.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_hover
        )

        layout.addWidget(self.win)
        return layout

    # --------------------------------------------------------------------------
    # 비즈니스 로직 & 데이터 흐름
    # --------------------------------------------------------------------------
    def _update_clock(self):
        """실시간 시계 갱신"""
        now_str = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.lbl_clock.setText(f"🕒 {now_str}")

    def _start_data_sync(self, force: bool = False):
        """백그라운드 스레드로 yfinance 데이터 동기화 시작"""
        if self.sync_worker and self.sync_worker.isRunning():
            return

        self.btn_sync.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setText("데이터 동기화 진행 중...")

        self.sync_worker = DataSyncWorker(self.db, force_refresh=force)
        self.sync_worker.progress_signal.connect(self._on_sync_progress)
        self.sync_worker.finished_signal.connect(self._on_sync_finished)
        self.sync_worker.start()

    def _on_sync_progress(self, msg: str):
        self.status_label.setText(msg)

    def _on_sync_finished(self, success: bool, msg: str):
        self.btn_sync.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(msg)

        # 뷰 갱신
        self._load_and_refresh_views()

    def _load_and_refresh_views(self):
        """DB에서 데이터를 가져와 지표를 연산하고 모든 위젯과 차트를 갱신"""
        latest_db_date = self.db.get_db_overall_latest_date()
        self.lbl_db_date.setText(f"DB 최신일: {latest_db_date if latest_db_date else '없음'}")

        # 지표 연산
        try:
            self.indicators_data = QuantEngine.compute_all_indicators(self.db)
        except Exception as e:
            self.status_label.setText(f"지표 계산 오류: {str(e)}")
            return

        if not self.indicators_data or "QQQ" not in self.indicators_data:
            self.status_label.setText("데이터가 부족합니다. 동기화를 실행해 주세요.")
            return

        # KPI 카드 갱신
        self._update_kpi_cards()

        # 차트 플롯 갱신
        self._render_charts()

    def _update_kpi_cards(self):
        """상단 4대 핵심 지표 카드 업데이트"""
        qqq_data = self.indicators_data.get("QQQ", {})
        tqqq_data = self.indicators_data.get("TQQQ", {})

        qqq_weekly = qqq_data.get("weekly", pd.DataFrame())
        tqqq_weekly = tqqq_data.get("weekly", pd.DataFrame())

        if qqq_weekly.empty or tqqq_weekly.empty:
            return

        # 1. QQQ 카드
        qqq_last = qqq_weekly.iloc[-1]
        qqq_price = qqq_last['adj_close']
        qqq_ret = qqq_last['weekly_return']
        qqq_color = COLOR_BULLISH if qqq_ret >= 0 else COLOR_BEARISH
        qqq_sign = "+" if qqq_ret >= 0 else ""
        self.lbl_qqq_price.setText(f"${qqq_price:,.2f}")
        self.lbl_qqq_ret.setText(f"주간 변동률: {qqq_sign}{qqq_ret:.2f}%")
        self.lbl_qqq_ret.setStyleSheet(f"color: {qqq_color}; font-weight: bold;")

        # 2. TQQQ 카드
        tqqq_last = tqqq_weekly.iloc[-1]
        tqqq_price = tqqq_last['adj_close']
        tqqq_ret = tqqq_last['weekly_return']
        tqqq_color = COLOR_BULLISH if tqqq_ret >= 0 else COLOR_BEARISH
        tqqq_sign = "+" if tqqq_ret >= 0 else ""
        self.lbl_tqqq_price.setText(f"${tqqq_price:,.2f}")
        self.lbl_tqqq_ret.setText(f"주간 변동률: {tqqq_sign}{tqqq_ret:.2f}%")
        self.lbl_tqqq_ret.setStyleSheet(f"color: {tqqq_color}; font-weight: bold;")

        # 3. QQQ 주봉 MACD 신호 카드
        macd_val = qqq_last['macd']
        signal_val = qqq_last['signal']
        hist_val = qqq_last['hist']
        status = qqq_last['status']  # BULLISH / BEARISH
        is_unclosed = qqq_last.get('is_unclosed', False)

        unclosed_tag = " (진행 중인 주봉)" if is_unclosed else " (확정 주봉)"

        if status == "BULLISH":
            self.lbl_signal_title.setText("BULLISH (상승 추세)")
            self.lbl_signal_title.setStyleSheet(f"color: {COLOR_BULLISH}; font-size: 20px; font-weight: bold;")
            self.card_signal.setStyleSheet(f"border-left: 5px solid {COLOR_BULLISH};")
        else:
            self.lbl_signal_title.setText("BEARISH (하락 추세)")
            self.lbl_signal_title.setStyleSheet(f"color: {COLOR_BEARISH}; font-size: 20px; font-weight: bold;")
            self.card_signal.setStyleSheet(f"border-left: 5px solid {COLOR_BEARISH};")

        self.lbl_signal_desc.setText(f"Hist: {hist_val:+.2f} | MACD: {macd_val:.2f}{unclosed_tag}")

        # 4. 전술적 포지션 가이드 카드 & 최근 크로스 경과 주수 계산
        cross_indices = qqq_weekly.index[qqq_weekly['crossover'] != 0].tolist()
        if cross_indices:
            last_cross_date = cross_indices[-1]
            last_cross_type = qqq_weekly.loc[last_cross_date, 'crossover']
            # 경과 주수 계산
            weeks_ago = len(qqq_weekly.loc[last_cross_date:]) - 1
            
            last_cross_date_str = last_cross_date.strftime("%Y-%m-%d")
            cross_name = "골든크로스(매수)" if last_cross_type == 1 else "데드크로스(매도)"
            self.weeks_elapsed_label.setText(
                f"⚡ 최근 {cross_name} 발생일: {last_cross_date_str} ({weeks_ago}주 경과)"
            )

            if status == "BULLISH":
                self.lbl_guide_title.setText("TQQQ 비중 확대 (Long)")
                self.lbl_guide_title.setStyleSheet(f"color: {COLOR_BULLISH}; font-size: 19px; font-weight: bold;")
                self.lbl_guide_desc.setText(f"골든크로스 추세 지속 중 ({weeks_ago}주차 유지)")
                self.card_guide.setStyleSheet(f"border-left: 5px solid {COLOR_BULLISH};")
            else:
                self.lbl_guide_title.setText("TQQQ 축소 / 현금화 (Risk Off)")
                self.lbl_guide_title.setStyleSheet(f"color: {COLOR_BEARISH}; font-size: 19px; font-weight: bold;")
                self.lbl_guide_desc.setText(f"데드크로스 하락 국면 ({weeks_ago}주차 리스크 관리)")
                self.card_guide.setStyleSheet(f"border-left: 5px solid {COLOR_BEARISH};")
        else:
            self.weeks_elapsed_label.setText("크로스오버 이력 없음")

    def _on_ticker_toggle(self, btn_id: int):
        self.selected_ticker = "QQQ" if btn_id == 0 else "TQQQ"
        self._render_charts()

    def _render_charts(self):
        """차트 영역 재렌더링"""
        if not self.indicators_data:
            return

        target_data = self.indicators_data.get(self.selected_ticker, {})
        qqq_data = self.indicators_data.get("QQQ", {})

        target_weekly = target_data.get("weekly", pd.DataFrame())
        qqq_weekly = qqq_data.get("weekly", pd.DataFrame())

        if target_weekly.empty or qqq_weekly.empty:
            return

        # 날짜 라벨 생성 (YYYY-MM-DD)
        self.date_labels = [d.strftime("%Y-%m-%d") for d in target_weekly.index]
        self.x_axis_top.update_labels(self.date_labels)
        self.x_axis_bottom.update_labels(self.date_labels)

        n_bars = len(target_weekly)
        x_indices = np.arange(n_bars)

        # ----------------------------------------------------------------------
        # 1. 상단 주가 차트 렌더링
        # ----------------------------------------------------------------------
        prices = target_weekly['adj_close'].values
        self.plot_price.setTitle(
            f"<span style='color: #00E5FF; font-weight: bold; font-size: 14px;'>{self.selected_ticker}</span> "
            f"<span style='color: #848E9C; font-size: 12px;'>Weekly Adj Close & QQQ Signal Markers</span>"
        )
        self.curve_price.setData(x=x_indices, y=prices)

        # QQQ 주봉 크로스오버 마커 설정
        golden_x, golden_y = [], []
        dead_x, dead_y = [], []

        # target_weekly와 qqq_weekly의 날짜 인덱스 매핑
        for i, (dt, row) in enumerate(target_weekly.iterrows()):
            if dt in qqq_weekly.index:
                cross = qqq_weekly.loc[dt, 'crossover']
                price_at_bar = row['adj_close']
                if cross == 1:  # 골든크로스
                    golden_x.append(i)
                    golden_y.append(price_at_bar * 0.96) # 캔들 아래 표시
                elif cross == -1:  # 데드크로스
                    dead_x.append(i)
                    dead_y.append(price_at_bar * 1.04) # 캔들 위 표시

        self.scatter_golden.setData(pos=list(zip(golden_x, golden_y)))
        self.scatter_dead.setData(pos=list(zip(dead_x, dead_y)))

        # ----------------------------------------------------------------------
        # 2. 하단 MACD 차트 렌더링 (QQQ 주봉 MACD)
        # ----------------------------------------------------------------------
        self.plot_macd.setTitle(
            "<span style='color: #2979FF; font-weight: bold; font-size: 13px;'>QQQ MACD(12,26)</span> "
            "<span style='color: #FF9100; font-weight: bold; font-size: 13px;'>Signal(9)</span> "
            "<span style='color: #848E9C; font-size: 12px;'>Histogram</span>"
        )

        macd_line = qqq_weekly['macd'].values
        signal_line = qqq_weekly['signal'].values
        hist = qqq_weekly['hist'].values

        self.curve_macd.setData(x=x_indices, y=macd_line)
        self.curve_signal.setData(x=x_indices, y=signal_line)

        # 히스토그램 바 차트 제거 및 재생성
        if self.hist_bar_item is not None:
            self.plot_macd.removeItem(self.hist_bar_item)

        # 양수/음수 히스토그램 색상 분기
        brushes = [pg.mkBrush(QColor(0, 230, 118, 160) if h >= 0 else QColor(255, 82, 82, 160)) for h in hist]
        pens = [pg.mkPen(QColor(0, 230, 118, 220) if h >= 0 else QColor(255, 82, 82, 220), width=1) for h in hist]

        self.hist_bar_item = pg.BarGraphItem(
            x=x_indices, height=hist, width=0.65, brushes=brushes, pens=pens
        )
        self.plot_macd.addItem(self.hist_bar_item)

        # 초기 뷰포트 설정: 최근 150주(약 3년) 범위로 자동 포커싱
        if n_bars > 150:
            self.plot_price.setXRange(n_bars - 150, n_bars - 1, padding=0.02)
        else:
            self.plot_price.enableAutoRange(axis=pg.ViewBox.XAxis)

        self.plot_price.enableAutoRange(axis=pg.ViewBox.YAxis)
        self.plot_macd.enableAutoRange(axis=pg.ViewBox.YAxis)

    # --------------------------------------------------------------------------
    # 마우스 호버 크로스헤어 & 툴팁 정보
    # --------------------------------------------------------------------------
    def _on_mouse_hover(self, evt):
        pos = evt[0]
        if not self.date_labels or not self.indicators_data:
            return

        target_weekly = self.indicators_data.get(self.selected_ticker, {}).get("weekly", pd.DataFrame())
        qqq_weekly = self.indicators_data.get("QQQ", {}).get("weekly", pd.DataFrame())
        if target_weekly.empty or qqq_weekly.empty:
            return

        # 상단 차트 내부 마우스 호버
        if self.plot_price.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_price.vb.mapSceneToView(pos)
            idx = int(round(mouse_point.x()))
            if 0 <= idx < len(target_weekly):
                self.v_line_top.setPos(mouse_point.x())
                self.h_line_top.setPos(mouse_point.y())
                self.v_line_bot.setPos(mouse_point.x())

                dt_str = self.date_labels[idx]
                dt = target_weekly.index[idx]
                row = target_weekly.iloc[idx]
                qqq_row = qqq_weekly.loc[dt] if dt in qqq_weekly.index else None

                price_info = f"[{dt_str}] {self.selected_ticker}: ${row['adj_close']:,.2f}"
                macd_info = f"QQQ MACD: {qqq_row['macd']:.2f} | Sig: {qqq_row['signal']:.2f} | Hist: {qqq_row['hist']:+.2f}" if qqq_row is not None else ""
                
                self.status_label.setText(f"{price_info}  |  {macd_info}")

        # 하단 차트 내부 마우스 호버
        elif self.plot_macd.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_macd.vb.mapSceneToView(pos)
            idx = int(round(mouse_point.x()))
            if 0 <= idx < len(qqq_weekly):
                self.v_line_top.setPos(mouse_point.x())
                self.v_line_bot.setPos(mouse_point.x())

                dt_str = self.date_labels[idx]
                qqq_row = qqq_weekly.iloc[idx]
                macd_info = f"[{dt_str}] QQQ MACD: {qqq_row['macd']:.2f} | Sig: {qqq_row['signal']:.2f} | Hist: {qqq_row['hist']:+.2f}"
                self.status_label.setText(macd_info)


# ==============================================================================
# 8. 메인 실행 엔트리포인트 (Main Entry Point)
# ==============================================================================
def main():
    # 고해상도 DPI 스케일링 설정
    if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    dashboard = QuantDashboard()
    dashboard.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
