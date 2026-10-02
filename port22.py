# -*- coding: utf-8 -*-
"""
port22.py - 전문가 스타일 포트폴리오 비중(Weight) 분석 & 시각화 GUI 대시보드
- 종목명, 티커, 매수금액/평가금액 기반 비중(%) 분석 리포트
- 고해상도 JPG 이미지 파일 자동/수동 저장 기능 탑재
"""

import os
import sys
from datetime import datetime
import pandas as pd
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame, QSplitter,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox,
    QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.ticker as ticker

# 한글 폰트 설정 (Windows 기본 맑은 고딕)
plt.rcParams['font.family'] = 'Malgun Gothic'
plt.rcParams['axes.unicode_minus'] = False

# 사진에서 추출한 포트폴리오 데이터 (매수금액 및 평가금액 반영)
INITIAL_DATA = [
    {"name": "블룸 에너지", "ticker": "BE", "country": "미국", "buy_usd": 8503.04, "eval_usd": 7592.55, "currency": "USD"},
    {"name": "스페이스X", "ticker": "SPCX", "country": "미국", "buy_usd": 8207.65, "eval_usd": 7552.54, "currency": "USD"},
    {"name": "테슬라", "ticker": "TSLA", "country": "미국", "buy_usd": 8015.26, "eval_usd": 7533.76, "currency": "USD"},
    {"name": "인텔", "ticker": "INTC", "country": "미국", "buy_usd": 4040.40, "eval_usd": 3660.47, "currency": "USD"},
    {"name": "애플", "ticker": "AAPL", "country": "미국", "buy_usd": 3909.72, "eval_usd": 3908.56, "currency": "USD"},
    {"name": "라운드힐 메모리 ETF", "ticker": "DRAM", "country": "미국", "buy_usd": 3785.60, "eval_usd": 3755.77, "currency": "USD"},
    {"name": "비스트라", "ticker": "VST", "country": "미국", "buy_usd": 3704.00, "eval_usd": 3826.05, "currency": "USD"},
    {"name": "스미토모상사", "ticker": "8053", "country": "일본", "buy_usd": 2284.20, "eval_usd": 2312.30, "raw_buy": 342630.00, "currency": "JPY"}, # 342,630 JPY (~$2,284.20)
]

# 프리미엄 핀테크 테마 컬러 팔레트
PALETTE = [
    "#3A86FF", "#8338EC", "#FF006E", "#FB5607", "#FFBE0B",
    "#06D6A0", "#118AB2", "#4CC9F0", "#7209B7", "#F72585",
    "#4895EF", "#560BAD", "#10B981", "#6366F1"
]

class StatCard(QFrame):
    """전문가 대시보드용 요약 지표 카드 위젯"""
    def __init__(self, title, value, subtext="", bg_color="#1E293B", text_color="#38BDF8"):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border-radius: 12px;
                border: 1px solid #334155;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: #94A3B8; font-size: 13px; font-weight: 600;")

        self.lbl_value = QLabel(value)
        self.lbl_value.setStyleSheet(f"color: {text_color}; font-size: 22px; font-weight: 800;")

        self.lbl_sub = QLabel(subtext)
        self.lbl_sub.setStyleSheet("color: #64748B; font-size: 11px;")

        layout.addWidget(lbl_title)
        layout.addWidget(self.lbl_value)
        layout.addWidget(self.lbl_sub)

    def update_data(self, value, subtext=""):
        self.lbl_value.setText(value)
        if subtext:
            self.lbl_sub.setText(subtext)


class PortfolioDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.portfolio_data = [dict(item) for item in INITIAL_DATA]
        self.init_ui()
        self.refresh_dashboard()

    def init_ui(self):
        self.setWindowTitle("🏛️ Portfolio Weight Pro Analytics - 포트폴리오 비중 분석 대시보드")
        self.resize(1450, 920)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0F172A;
            }
            QWidget {
                color: #F8FAFC;
                font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
            }
            QTableWidget {
                background-color: #1E293B;
                gridline-color: #334155;
                border: 1px solid #334155;
                border-radius: 8px;
                color: #F8FAFC;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 6px;
            }
            QTableWidget::item:selected {
                background-color: #3B82F6;
                color: white;
            }
            QHeaderView::section {
                background-color: #0F172A;
                color: #94A3B8;
                font-weight: bold;
                padding: 8px;
                border: 1px solid #334155;
            }
            QPushButton {
                background-color: #2563EB;
                color: white;
                font-weight: bold;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton#btn_export {
                background-color: #059669;
            }
            QPushButton#btn_export:hover {
                background-color: #047857;
            }
            QPushButton#btn_reset {
                background-color: #475569;
            }
            QPushButton#btn_reset:hover {
                background-color: #334155;
            }
        """)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)

        # 1. 상단 헤더 & 툴바
        header_layout = QHBoxLayout()
        title_layout = QVBoxLayout()
        
        lbl_main_title = QLabel("📊 PORTFOLIO ALLOCATION & ASSET WEIGHT")
        lbl_main_title.setStyleSheet("font-size: 20px; font-weight: 900; color: #F1F5F9; letter-spacing: 1px;")
        
        lbl_sub_title = QLabel("글로벌 포트폴리오 종목별 비중(%) 및 자산 배분 분석 리포트")
        lbl_sub_title.setStyleSheet("font-size: 12px; color: #94A3B8;")
        
        title_layout.addWidget(lbl_main_title)
        title_layout.addWidget(lbl_sub_title)
        header_layout.addLayout(title_layout)

        header_layout.addStretch()

        self.btn_export = QPushButton("📸 비중 리포트 JPG 저장")
        self.btn_export.setObjectName("btn_export")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_to_jpg)
        header_layout.addWidget(self.btn_export)

        self.btn_add_item = QPushButton("➕ 종목 추가")
        self.btn_add_item.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_item.clicked.connect(self.add_item_dialog)
        header_layout.addWidget(self.btn_add_item)

        self.btn_reset = QPushButton("🔄 초기화")
        self.btn_reset.setObjectName("btn_reset")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(self.reset_data)
        header_layout.addWidget(self.btn_reset)

        main_layout.addLayout(header_layout)

        # 2. 비중 중심 요약 지표 카드 4개
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self.card_total_count = StatCard("보유 종목 수", "0 종목", "Total Holdings", bg_color="#1E293B", text_color="#38BDF8")
        self.card_top1 = StatCard("최대 비중 종목 (TOP 1)", "-", "Largest Holding", bg_color="#1E293B", text_color="#34D399")
        self.card_top3_ratio = StatCard("상위 TOP 3 비중 합계", "0.0%", "Top 3 Concentration", bg_color="#1E293B", text_color="#FBBF24")
        self.card_top5_ratio = StatCard("상위 TOP 5 비중 합계", "0.0%", "Top 5 Concentration", bg_color="#1E293B", text_color="#A78BFA")

        cards_layout.addWidget(self.card_total_count)
        cards_layout.addWidget(self.card_top1)
        cards_layout.addWidget(self.card_top3_ratio)
        cards_layout.addWidget(self.card_top5_ratio)

        main_layout.addLayout(cards_layout)

        # 3. 본문 스플리터 (좌측 차트, 우측 상세 테이블)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        # [좌측] 차트 컨테이너
        chart_container = QFrame()
        chart_container.setStyleSheet("background-color: #1E293B; border-radius: 12px; border: 1px solid #334155;")
        chart_layout = QVBoxLayout(chart_container)
        chart_layout.setContentsMargins(10, 10, 10, 10)

        self.fig = plt.Figure(figsize=(9, 6.8), facecolor='#1E293B', dpi=100)
        self.canvas = FigureCanvas(self.fig)
        chart_layout.addWidget(self.canvas)
        splitter.addWidget(chart_container)

        # [우측] 테이블 컨테이너
        table_container = QFrame()
        table_container.setStyleSheet("background-color: #1E293B; border-radius: 12px; border: 1px solid #334155; padding: 10px;")
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(10, 10, 10, 10)
        table_layout.setSpacing(10)

        lbl_table_header = QLabel("📋 보유 종목 비중 명세표 (비중 순위)")
        lbl_table_header.setStyleSheet("font-size: 14px; font-weight: 700; color: #F1F5F9;")
        table_layout.addWidget(lbl_table_header)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "순위", "종목명", "티커", "국가", "비중(%)", "누적 비중(%)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                alternate-background-color: #243044;
            }
        """)
        table_layout.addWidget(self.table)

        btn_row_layout = QHBoxLayout()
        self.btn_del_item = QPushButton("🗑️ 선택 종목 삭제")
        self.btn_del_item.setStyleSheet("background-color: #DC2626;")
        self.btn_del_item.clicked.connect(self.delete_selected_item)
        btn_row_layout.addWidget(self.btn_del_item)

        table_layout.addLayout(btn_row_layout)
        splitter.addWidget(table_container)

        splitter.setSizes([800, 600])
        main_layout.addWidget(splitter, 1)

    def refresh_dashboard(self):
        """데이터를 기반으로 카드, 차트, 테이블 갱신"""
        df = pd.DataFrame(self.portfolio_data)
        if df.empty:
            return

        df = df.sort_values(by="buy_usd", ascending=False).reset_index(drop=True)
        total_usd = df["buy_usd"].sum()
        df["weight"] = (df["buy_usd"] / total_usd) * 100

        # 1. 지표 카드 갱신
        self.card_total_count.update_data(f"{len(df)} 개 종목", f"미국 {len(df[df['country']=='미국'])}개, 일본 {len(df[df['country']=='일본'])}개")
        
        top1_name = df.iloc[0]['name']
        top1_ticker = df.iloc[0]['ticker']
        top1_weight = df.iloc[0]['weight']
        self.card_top1.update_data(f"{top1_name} ({top1_ticker})", f"비중 {top1_weight:.1f}%")

        top3_sum_weight = df.iloc[:3]['weight'].sum()
        self.card_top3_ratio.update_data(f"{top3_sum_weight:.1f}%", "TOP 3 종목 비중 합계")

        top5_sum_weight = df.iloc[:5]['weight'].sum()
        self.card_top5_ratio.update_data(f"{top5_sum_weight:.1f}%", "TOP 5 종목 비중 합계")

        # 2. 테이블 갱신
        self.table.setRowCount(len(df))
        cum_w = 0.0
        for row_idx, row in df.iterrows():
            cum_w += row['weight']
            item_rank = QTableWidgetItem(f"#{row_idx + 1}")
            item_rank.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_name = QTableWidgetItem(str(row['name']))
            item_name.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            item_ticker = QTableWidgetItem(str(row['ticker']))
            item_ticker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_ticker.setForeground(QColor("#38BDF8"))

            item_country = QTableWidgetItem(str(row['country']))
            item_country.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            item_weight = QTableWidgetItem(f"{row['weight']:.2f}%")
            item_weight.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_weight.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            item_weight.setForeground(QColor("#34D399"))

            item_cum = QTableWidgetItem(f"{cum_w:.2f}%")
            item_cum.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_cum.setForeground(QColor("#FBBF24"))

            self.table.setItem(row_idx, 0, item_rank)
            self.table.setItem(row_idx, 1, item_name)
            self.table.setItem(row_idx, 2, item_ticker)
            self.table.setItem(row_idx, 3, item_country)
            self.table.setItem(row_idx, 4, item_weight)
            self.table.setItem(row_idx, 5, item_cum)

        # 3. 차트 갱신
        self.render_charts(df)

    def render_charts(self, df):
        self.fig.clear()
        
        ax1 = self.fig.add_subplot(121)
        ax2 = self.fig.add_subplot(122)

        colors = (PALETTE * 2)[:len(df)]

        # --- [좌측] 도넛 차트 (비중 %) ---
        wedges, texts, autotexts = ax1.pie(
            df['weight'],
            labels=None,
            autopct=lambda pct: f"{pct:.1f}%" if pct >= 4.0 else "",
            pctdistance=0.78,
            startangle=140,
            colors=colors,
            wedgeprops=dict(width=0.42, edgecolor='#1E293B', linewidth=2.5)
        )

        for at in autotexts:
            at.set_color('#FFFFFF')
            at.set_fontsize(9)
            at.set_weight('bold')

        ax1.text(0, 0.08, "PORTFOLIO", ha='center', va='center', fontsize=9, color='#94A3B8', weight='bold')
        ax1.text(0, -0.08, "100.0%", ha='center', va='center', fontsize=14, color='#38BDF8', weight='bold')
        ax1.set_title("종목별 자산 비중 (%)", fontsize=13, color='#F1F5F9', weight='bold', pad=15)

        legend_labels = [f"{row['name']} ({row['ticker']}) - {row['weight']:.1f}%" for _, row in df.iterrows()]
        ax1.legend(
            wedges, legend_labels,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.25),
            ncol=2,
            fontsize=8,
            frameon=False,
            labelcolor='#CBD5E1'
        )

        # --- [우측] 상위 종목 비중 순위 바 차트 (%) ---
        top_df = df.iloc[::-1]
        y_pos = np.arange(len(top_df))

        bar_colors = [colors[df.index.get_loc(idx)] for idx in top_df.index]
        bars = ax2.barh(y_pos, top_df['weight'], color=bar_colors, height=0.62, edgecolor='#334155', linewidth=1)

        ax2.set_yticks(y_pos)
        ax2.set_yticklabels([f"{row['name']}\n({row['ticker']})" for _, row in top_df.iterrows()], fontsize=9, color='#CBD5E1', weight='bold')
        ax2.set_xlabel("비중 (%)", fontsize=10, color='#94A3B8', labelpad=8)
        ax2.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, pos: f"{x:.0f}%"))
        ax2.tick_params(axis='x', colors='#94A3B8', labelsize=8)
        ax2.set_facecolor('#1E293B')
        ax2.set_title("보유 종목 비중 순위 (%)", fontsize=13, color='#F1F5F9', weight='bold', pad=15)

        ax2.grid(axis='x', linestyle='--', alpha=0.25, color='#64748B')
        for spine in ax2.spines.values():
            spine.set_color('#334155')

        for bar, (_, row) in zip(bars, top_df.iterrows()):
            width = bar.get_width()
            ax2.text(
                width + 0.3, bar.get_y() + bar.get_height() / 2,
                f" {row['weight']:.1f}%",
                ha='left', va='center',
                fontsize=9, color='#F8FAFC', weight='bold'
            )

        ax2.set_xlim(0, top_df['weight'].max() * 1.25)

        self.fig.tight_layout(rect=[0, 0.05, 1, 0.96])
        self.canvas.draw()

    def export_to_jpg(self):
        """전체 대시보드 리포트를 비중 중심 고해상도 JPG 파일로 저장"""
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"portfolio_report_{now_str}.jpg"
        default_path = os.path.join(os.getcwd(), default_filename)

        file_path, _ = QFileDialog.getSaveFileName(
            self, "포트폴리오 비중 리포트 JPG 저장", default_path, "JPEG Image (*.jpg *.jpeg)"
        )

        if not file_path:
            return

        try:
            generate_portfolio_jpg(file_path, self.portfolio_data)
            QMessageBox.information(
                self, "저장 완료",
                f"✅ 포트폴리오 비중 리포트가 성공적으로 JPG 파일로 저장되었습니다!\n\n경로:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "저장 오류", f"JPG 저장 중 오류가 발생했습니다:\n{str(e)}")

    def add_item_dialog(self):
        """새로운 종목 추가 다이얼로그"""
        dialog = QDialog(self)
        dialog.setWindowTitle("종목 추가")
        dialog.setFixedWidth(360)
        dialog.setStyleSheet("""
            QDialog { background-color: #1E293B; color: white; }
            QLabel { color: #CBD5E1; font-weight: bold; }
            QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
                background-color: #0F172A;
                border: 1px solid #334155;
                border-radius: 6px;
                color: white;
                padding: 6px;
            }
        """)

        layout = QVBoxLayout(dialog)
        form_layout = QGridLayout()

        txt_name = QLineEdit()
        txt_ticker = QLineEdit()
        combo_country = QComboBox()
        combo_country.addItems(["미국", "일본", "한국", "기타"])

        spin_amount = QDoubleSpinBox()
        spin_amount.setRange(0.01, 100000000.0)
        spin_amount.setValue(1000.0)
        spin_amount.setPrefix("매수금액/비중값: ")

        form_layout.addWidget(QLabel("종목명:"), 0, 0)
        form_layout.addWidget(txt_name, 0, 1)

        form_layout.addWidget(QLabel("티커 (Ticker):"), 1, 0)
        form_layout.addWidget(txt_ticker, 1, 1)

        form_layout.addWidget(QLabel("국가:"), 2, 0)
        form_layout.addWidget(combo_country, 2, 1)

        form_layout.addWidget(QLabel("기준 매수금액($):"), 3, 0)
        form_layout.addWidget(spin_amount, 3, 1)

        layout.addLayout(form_layout)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = txt_name.text().strip()
            ticker = txt_ticker.text().strip().upper()
            if not name or not ticker:
                QMessageBox.warning(self, "경고", "종목명과 티커를 입력해주세요.")
                return

            self.portfolio_data.append({
                "name": name,
                "ticker": ticker,
                "country": combo_country.currentText(),
                "buy_usd": spin_amount.value(),
                "eval_usd": spin_amount.value(),
                "currency": "USD"
            })
            self.refresh_dashboard()

    def delete_selected_item(self):
        """테이블에서 선택된 종목 삭제"""
        cur_row = self.table.currentRow()
        if cur_row < 0:
            QMessageBox.information(self, "안내", "삭제할 종목을 테이블에서 선택해주세요.")
            return

        ticker_item = self.table.item(cur_row, 2)
        if not ticker_item:
            return
        ticker_val = ticker_item.text()

        self.portfolio_data = [d for d in self.portfolio_data if d["ticker"] != ticker_val]
        self.refresh_dashboard()

    def reset_data(self):
        """초기 데이터로 복원"""
        self.portfolio_data = [dict(item) for item in INITIAL_DATA]
        self.refresh_dashboard()


def generate_portfolio_jpg(output_path="portfolio_report.jpg", data=None):
    """비중(%) 중심의 고해상도 포트폴리오 리포트 JPG 생성 함수 (금액 비공개)"""
    if data is None:
        data = INITIAL_DATA
    
    df = pd.DataFrame(data).sort_values(by="buy_usd", ascending=False).reset_index(drop=True)
    total_usd = df["buy_usd"].sum()
    df["weight"] = (df["buy_usd"] / total_usd) * 100

    export_fig = plt.figure(figsize=(15, 10.5), facecolor='#0F172A', dpi=300)
    
    # 상단 타이틀 & 메타 정보
    export_fig.suptitle(
        "PORTFOLIO PRO ALLOCATION & WEIGHT REPORT",
        fontsize=20, color='#F8FAFC', weight='bold', y=0.97
    )
    
    top1_info = f"{df.iloc[0]['name']} ({df.iloc[0]['ticker']}) {df.iloc[0]['weight']:.1f}%"
    top3_sum = df.iloc[:3]['weight'].sum()
    
    export_fig.text(
        0.5, 0.938,
        f"기준일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   |   보유 종목: {len(df)}개   |   최대 비중(TOP 1): {top1_info}   |   TOP 3 비중 합계: {top3_sum:.1f}%",
        ha='center', fontsize=10, color='#94A3B8', weight='bold'
    )

    # 1. 도넛 차트 (비중 %)
    ax1 = export_fig.add_subplot(221)
    colors = (PALETTE * 2)[:len(df)]

    wedges, texts, autotexts = ax1.pie(
        df['weight'],
        labels=None,
        autopct=lambda pct: f"{pct:.1f}%" if pct >= 3.5 else "",
        pctdistance=0.78,
        startangle=140,
        colors=colors,
        wedgeprops=dict(width=0.42, edgecolor='#0F172A', linewidth=2)
    )
    for at in autotexts:
        at.set_color('#FFFFFF')
        at.set_fontsize(9)
        at.set_weight('bold')

    ax1.text(0, 0.08, "PORTFOLIO", ha='center', va='center', fontsize=9, color='#94A3B8', weight='bold')
    ax1.text(0, -0.08, "100.0%", ha='center', va='center', fontsize=14, color='#38BDF8', weight='bold')
    ax1.set_title("종목별 자산 비중 (%)", fontsize=13, color='#F1F5F9', weight='bold', pad=12)

    legend_labels = [f"{row['name']} ({row['ticker']}) - {row['weight']:.1f}%" for _, row in df.iterrows()]
    ax1.legend(
        wedges, legend_labels,
        loc="upper center", bbox_to_anchor=(0.5, -0.05),
        ncol=2, fontsize=7.5, frameon=False, labelcolor='#CBD5E1'
    )

    # 2. 바 차트 (비중 %)
    ax2 = export_fig.add_subplot(222)
    top_df = df.iloc[::-1]
    y_pos = np.arange(len(top_df))
    bar_colors = [colors[df.index.get_loc(idx)] for idx in top_df.index]
    bars = ax2.barh(y_pos, top_df['weight'], color=bar_colors, height=0.65, edgecolor='#334155')

    ax2.set_yticks(y_pos)
    ax2.set_yticklabels([f"{row['name']} ({row['ticker']})" for _, row in top_df.iterrows()], fontsize=8.5, color='#CBD5E1', weight='bold')
    ax2.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, pos: f"{x:.0f}%"))
    ax2.tick_params(axis='x', colors='#94A3B8', labelsize=8)
    ax2.set_facecolor('#1E293B')
    ax2.set_title("보유 종목 비중 순위 (%)", fontsize=13, color='#F1F5F9', weight='bold', pad=12)
    ax2.grid(axis='x', linestyle='--', alpha=0.3, color='#64748B')
    for spine in ax2.spines.values():
        spine.set_color('#334155')

    for bar, (_, row) in zip(bars, top_df.iterrows()):
        width = bar.get_width()
        ax2.text(
            width + 0.3, bar.get_y() + bar.get_height() / 2,
            f"{row['weight']:.1f}%",
            ha='left', va='center', fontsize=8, color='#F8FAFC', weight='bold'
        )
    ax2.set_xlim(0, top_df['weight'].max() * 1.25)

    # 3. 하단 상세 표 요약 (비중 중심)
    ax3 = export_fig.add_subplot(212)
    ax3.axis('tight')
    ax3.axis('off')

    table_data = []
    cum_weight = 0.0
    for idx, row in df.iterrows():
        cum_weight += row['weight']
        table_data.append([
            f"#{idx+1}", row['name'], row['ticker'], row['country'],
            f"{row['weight']:.2f}%", f"{cum_weight:.2f}%"
        ])

    table = ax3.table(
        cellText=table_data,
        colLabels=["순위", "종목명", "티커", "국가", "종목 비중(%)", "누적 비중(%)"],
        loc='center',
        cellLoc='center'
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.3)

    # 표 스타일링
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor('#334155')
        if r == 0:
            cell.set_facecolor('#1E293B')
            cell.set_text_props(color='#38BDF8', weight='bold')
        else:
            cell.set_facecolor('#0F172A' if r % 2 == 0 else '#1E293B')
            cell.set_text_props(color='#F1F5F9')

    export_fig.tight_layout(rect=[0.02, 0.02, 0.98, 0.92])
    export_fig.savefig(output_path, format='jpg', dpi=300, facecolor='#0F172A', edgecolor='none')
    plt.close(export_fig)
    print(f"[SUCCESS] 고해상도 포트폴리오 비중 리포트 JPG 저장 완료 -> {output_path}")
    return output_path


def generate_portfolio_html(output_path="index.html", data=None):
    """모바일 반응형 웹 대시보드 HTML 파일 생성 함수"""
    if data is None:
        data = INITIAL_DATA

    df = pd.DataFrame(data).sort_values(by="buy_usd", ascending=False).reset_index(drop=True)
    total_usd = df["buy_usd"].sum()
    df["weight"] = (df["buy_usd"] / total_usd) * 100

    colors = (PALETTE * 2)[:len(df)]
    items_js = []
    for idx, row in df.iterrows():
        items_js.append({
            "rank": idx + 1,
            "name": row['name'],
            "ticker": row['ticker'],
            "country": row['country'],
            "weight": round(row['weight'], 2),
            "color": colors[idx]
        })

    import json
    data_json = json.dumps(items_js, ensure_ascii=False, indent=8)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Portfolio Pro - 모바일 자산 비중 대시보드</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
        * {{ font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif; }}
        body {{ background-color: #0B1120; }}
        .glass-card {{ background: #131E32; border: 1px solid #1E293B; backdrop-filter: blur(12px); }}
    </style>
</head>
<body class="text-slate-100 min-h-screen pb-12">
    <header class="border-b border-slate-800 bg-[#0F172A]/80 sticky top-0 z-50 backdrop-blur-md px-4 py-3.5">
        <div class="max-w-4xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <div class="p-2 bg-blue-600/20 text-blue-400 rounded-xl border border-blue-500/30">
                    <i data-lucide="pie-chart" class="w-5 h-5"></i>
                </div>
                <div>
                    <h1 class="text-base sm:text-lg font-bold tracking-tight text-white flex items-center gap-2">
                        PORTFOLIO PRO
                        <span class="text-[10px] bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full border border-emerald-500/20 font-semibold">LIVE</span>
                    </h1>
                    <p class="text-[11px] text-slate-400">글로벌 자산 배분 비중 리포트</p>
                </div>
            </div>
            <div class="text-right">
                <span class="text-[11px] text-slate-400 block">{datetime.now().strftime('%Y-%m-%d %H:%M')} 기준</span>
                <span class="text-[11px] text-blue-400 font-medium">{len(df)}개 종목 구성</span>
            </div>
        </div>
    </header>

    <main class="max-w-4xl mx-auto px-4 mt-5 space-y-4">
        <!-- 1. 핵심 지표 카드 -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div class="glass-card rounded-2xl p-3.5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-1">
                    <span class="text-xs font-semibold">보유 자산</span>
                    <i data-lucide="layers" class="w-4 h-4 text-blue-400"></i>
                </div>
                <div class="text-xl sm:text-2xl font-black text-blue-400">{len(df)} <span class="text-xs font-normal text-slate-400">종목</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">글로벌 포트폴리오</div>
            </div>

            <div class="glass-card rounded-2xl p-3.5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-1">
                    <span class="text-xs font-semibold">최대 비중 (1위)</span>
                    <i data-lucide="crown" class="w-4 h-4 text-emerald-400"></i>
                </div>
                <div class="text-xl sm:text-2xl font-black text-emerald-400">{df.iloc[0]['weight']:.1f}<span class="text-xs font-normal">%</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5 truncate">{df.iloc[0]['name']} ({df.iloc[0]['ticker']})</div>
            </div>

            <div class="glass-card rounded-2xl p-3.5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-1">
                    <span class="text-xs font-semibold">TOP 3 합계</span>
                    <i data-lucide="trending-up" class="w-4 h-4 text-amber-400"></i>
                </div>
                <div class="text-xl sm:text-2xl font-black text-amber-400">{df.iloc[:3]['weight'].sum():.1f}<span class="text-xs font-normal">%</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">상위 3종목 집중도</div>
            </div>

            <div class="glass-card rounded-2xl p-3.5 border border-slate-800">
                <div class="flex items-center justify-between text-slate-400 mb-1">
                    <span class="text-xs font-semibold">TOP 5 합계</span>
                    <i data-lucide="shield-check" class="w-4 h-4 text-purple-400"></i>
                </div>
                <div class="text-xl sm:text-2xl font-black text-purple-400">{df.iloc[:5]['weight'].sum():.1f}<span class="text-xs font-normal">%</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">상위 5종목 집중도</div>
            </div>
        </div>

        <!-- 2. 차트 영역 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="glass-card rounded-2xl p-4 border border-slate-800">
                <div class="flex items-center justify-between mb-3">
                    <h2 class="text-sm font-bold text-white flex items-center gap-1.5">
                        <i data-lucide="donut" class="w-4 h-4 text-blue-400"></i>
                        자산별 배분 비중 (%)
                    </h2>
                    <span class="text-[11px] text-slate-400">터치 인터랙션</span>
                </div>
                <div class="relative h-[250px] sm:h-[280px] flex items-center justify-center">
                    <canvas id="donutChart"></canvas>
                </div>
            </div>

            <div class="glass-card rounded-2xl p-4 border border-slate-800">
                <div class="flex items-center justify-between mb-3">
                    <h2 class="text-sm font-bold text-white flex items-center gap-1.5">
                        <i data-lucide="bar-chart-3" class="w-4 h-4 text-indigo-400"></i>
                        보유 종목 비중 순위
                    </h2>
                    <span class="text-[11px] text-slate-400">상위 순 정렬</span>
                </div>
                <div class="relative h-[250px] sm:h-[280px]">
                    <canvas id="barChart"></canvas>
                </div>
            </div>
        </div>

        <!-- 3. 비중 명세표 -->
        <div class="glass-card rounded-2xl p-4 border border-slate-800 overflow-hidden">
            <div class="flex items-center justify-between mb-3.5">
                <h2 class="text-sm font-bold text-white flex items-center gap-1.5">
                    <i data-lucide="table-2" class="w-4 h-4 text-emerald-400"></i>
                    종목별 비중 명세표
                </h2>
                <span class="text-[11px] text-slate-400 font-medium">액수 비공개 모드</span>
            </div>

            <div class="overflow-x-auto -mx-4 px-4">
                <table class="w-full text-left text-xs sm:text-sm">
                    <thead>
                        <tr class="border-b border-slate-800 text-slate-400 text-[11px] uppercase tracking-wider">
                            <th class="py-2.5 px-2 text-center">순위</th>
                            <th class="py-2.5 px-3">종목명</th>
                            <th class="py-2.5 px-2 text-center">티커</th>
                            <th class="py-2.5 px-2 text-center">국가</th>
                            <th class="py-2.5 px-3 text-right">종목 비중</th>
                            <th class="py-2.5 px-3 text-right">누적 비중</th>
                        </tr>
                    </thead>
                    <tbody id="table-body" class="divide-y divide-slate-800/60 font-medium">
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <footer class="text-center text-slate-400 text-xs py-6">
        <p>© 2026 Portfolio Pro Analytics • Mobile Optimized</p>
    </footer>

    <script>
        const portfolioData = {data_json};

        const tbody = document.getElementById("table-body");
        let cumWeight = 0;
        portfolioData.forEach(item => {{
            cumWeight += item.weight;
            const tr = document.createElement("tr");
            tr.className = "hover:bg-slate-800/40 transition-colors";
            tr.innerHTML = `
                <td class="py-3 px-2 text-center text-slate-400 font-semibold text-xs">#${{item.rank}}</td>
                <td class="py-3 px-3 font-bold text-white flex items-center gap-2">
                    <span class="w-2.5 h-2.5 rounded-full inline-block" style="background-color: ${{item.color}};"></span>
                    ${{item.name}}
                </td>
                <td class="py-3 px-2 text-center text-blue-400 font-bold">${{item.ticker}}</td>
                <td class="py-3 px-2 text-center text-slate-400">${{item.country}}</td>
                <td class="py-3 px-3 text-right font-extrabold text-emerald-400">${{item.weight.toFixed(2)}}%</td>
                <td class="py-3 px-3 text-right text-amber-400 font-semibold">${{cumWeight.toFixed(2)}}%</td>
            `;
            tbody.appendChild(tr);
        }});

        const ctxDonut = document.getElementById('donutChart').getContext('2d');
        new Chart(ctxDonut, {{
            type: 'doughnut',
            data: {{
                labels: portfolioData.map(d => `${{d.name}} (${{d.ticker}})`),
                datasets: [{{
                    data: portfolioData.map(d => d.weight),
                    backgroundColor: portfolioData.map(d => d.color),
                    borderColor: '#131E32',
                    borderWidth: 3,
                    hoverOffset: 6
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom',
                        labels: {{ color: '#94A3B8', boxWidth: 10, padding: 10, font: {{ size: 11, family: 'Pretendard' }} }}
                    }},
                    tooltip: {{
                        callbacks: {{ label: (ctx) => ` 비중: ${{ctx.raw}}%` }}
                    }}
                }},
                cutout: '62%'
            }}
        }});

        const ctxBar = document.getElementById('barChart').getContext('2d');
        new Chart(ctxBar, {{
            type: 'bar',
            data: {{
                labels: portfolioData.map(d => `${{d.name}} (${{d.ticker}})`),
                datasets: [{{
                    label: '비중 (%)',
                    data: portfolioData.map(d => d.weight),
                    backgroundColor: portfolioData.map(d => d.color),
                    borderRadius: 6,
                    borderSkipped: false
                }}]
            }},
            options: {{
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{ callbacks: {{ label: (ctx) => ` 비중: ${{ctx.raw}}%` }} }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: '#1E293B' }},
                        ticks: {{ color: '#94A3B8', callback: v => v + '%' }},
                        max: Math.ceil(Math.max(...portfolioData.map(d => d.weight)) * 1.25)
                    }},
                    y: {{
                        grid: {{ display: false }},
                        ticks: {{ color: '#E2E8F0', font: {{ size: 11, weight: 'bold' }} }}
                    }}
                }}
            }}
        }});

        lucide.createIcons();
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[SUCCESS] 모바일 반응형 대시보드 HTML 생성 완료 -> {output_path}")
    return output_path


def main():
    if "--export-html" in sys.argv:
        out_name = "index.html"
        for arg in sys.argv:
            if arg.endswith(".html"):
                out_name = arg
        generate_portfolio_html(out_name)
        return

    if "--export-only" in sys.argv or "--save" in sys.argv:
        out_name = "portfolio_report.jpg"
        for arg in sys.argv:
            if arg.endswith(".jpg") or arg.endswith(".jpeg"):
                out_name = arg
        generate_portfolio_jpg(out_name)
        return

    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    window = PortfolioDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
