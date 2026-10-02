import sys
import time
import datetime
import re
import argparse
import ctypes
import winsound
import requests

# Windows 콘솔 인코딩 설정 (가능한 경우 UTF-8)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 한국 표준시 (KST, UTC+9) 설정
KST = datetime.timezone(datetime.timedelta(hours=9))

def fetch_navyism_time(host="bt.korail.com", samples=3):
    """
    네이비즘(time.navyism.com)에서 대상 서버시간을 여러 번(기본 3회) 샘플링하여 
    DNS/SSL 핸드셰이크 지연을 제거하고 가장 RTT(왕복시간)가 적고 정확한 오프셋을 선택합니다.
    """
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://time.navyism.com/',
    })
    
    url = f"https://time.navyism.com/?host={host}"
    results = []
    
    for i in range(samples):
        t_start = time.time()
        try:
            resp = session.get(url, timeout=4)
            t_end = time.time()
            rtt = t_end - t_start
            
            # HTML 내 정규식을 이용해 서버 타임스탬프 추출
            m1 = re.search(r'var\s+thisTime2\s*=\s*(\d+)', resp.text)
            m2 = re.search(r'show\((\d+)\s*-\s*time\(\)', resp.text)
            m3 = re.search(r'alertDate[=\":\s]+(\d+)', resp.text)
            
            val = m1.group(1) if m1 else (m2.group(1) if m2 else (m3.group(1) if m3 else None))
            if val:
                server_ts = int(val)
                # RTT/2 보정: 응답 도착 시점의 예상 서버 타임스탬프
                estimated_server_time = server_ts + (rtt / 2.0)
                offset = estimated_server_time - t_end
                results.append((rtt, offset, estimated_server_time))
        except Exception as e:
            pass
        time.sleep(0.1)  # 샘플 간 최소 대기
        
    if results:
        # RTT가 가장 작은 (가장 튀지 않고 안정적인) 샘플 선택
        results.sort(key=lambda x: x[0])
        best_rtt, best_offset, best_server_time = results[0]
        return best_server_time, best_offset, best_rtt
    
    return None, None, None

def perform_left_click():
    """
    마우스 왼쪽 클릭을 1회 실행합니다. (pyautogui 또는 Windows Win32 API 사용)
    """
    try:
        import pyautogui
        pyautogui.click()
    except ImportError:
        # pyautogui가 설치되어 있지 않은 경우 Windows ctypes Win32 API 사용
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.01)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP

def get_target_datetime(target_time_str=None, test_seconds=None):
    """
    목표 시간(기본: 아침 7시)을 설정합니다.
    """
    now_kst = datetime.datetime.now(KST)
    
    if test_seconds is not None:
        # 테스트 모드: 현재 시간으로부터 N초 후
        return now_kst + datetime.timedelta(seconds=test_seconds)
    
    if target_time_str:
        h, m, s = map(int, target_time_str.split(':'))
    else:
        h, m, s = 7, 0, 0
    
    target_dt = now_kst.replace(hour=h, minute=m, second=s, microsecond=0)
    
    # 이미 오늘 목표 시간이 지났다면 내일 목표 시간으로 설정
    if now_kst >= target_dt:
        target_dt += datetime.timedelta(days=1)
        
    return target_dt

def main():
    parser = argparse.ArgumentParser(description="네이비즘 서버시간 멀티 샘플링 1회 동기화 및 아침 7시 0.5초 자동 클릭")
    parser.add_argument("--host", type=str, default="bt.korail.com", help="대상 서버 호스트명 (기본값: bt.korail.com [명절예매], 일반: www.letskorail.com)")
    parser.add_argument("--time", type=str, default="07:00:00", help="목표 시간 (형식: HH:MM:SS, 기본값: 07:00:00)")
    parser.add_argument("--delay", type=float, default=0.95, help="목표 시간 대비 클릭 지연 초 (기본값: 0.5초, 즉 7시 0.5초에 클릭)")
    parser.add_argument("--test", type=int, default=None, help="테스트용: 현재 시간 N초 후 클릭 실행")
    args = parser.parse_args()

    print("=" * 65)
    print("   [ KTX/레츠코레일 네이비즘 멀티샘플링 1회 동기화 & 7시 0.5초 클릭 ]")
    print("=" * 65)
    print(f"대상 URL : https://time.navyism.com/?host={args.host}")

    # 목표 시간 설정 (07:00:00 기준 + delay 0.5초)
    base_target_dt = get_target_datetime(args.time if args.test is None else None, test_seconds=args.test)
    target_ts = base_target_dt.timestamp() + args.delay
    actual_target_dt = datetime.datetime.fromtimestamp(target_ts, tz=KST)
    
    print(f"목표 클릭 시점 (KST) : {actual_target_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (지연: +{args.delay:.2f}초)")
    print("-" * 65)
    print(f"네이비즘 서버시간({args.host}) 멀티 샘플링 동기화 진행 중...")

    # 프로그램 시작 시 3회 샘플링 중 최저 RTT 샘플 선택
    server_time, offset, rtt = fetch_navyism_time(args.host, samples=3)
    if offset is None:
        print("[!] 초기 서버시간을 가져오지 못했습니다. 네트워크 연결을 확인하세요.")
        return

    print(f"[V] 동기화 완료! 최적 RTT: {rtt*1000:.1f}ms | 서버-로컬 오프셋: {offset:+.3f}초")
    print("※ 최적의 네트워크 지연값으로 보정되었습니다. 내부 로컬 타이머로 카운트다운합니다.")
    print("-" * 65)

    try:
        while True:
            local_now = time.time()
            estimated_server_time = local_now + offset
            remaining = target_ts - estimated_server_time
            
            # 목표 시점 도달 시 클릭 진행
            if remaining <= 0:
                break

            # 2초 전까지는 화면에 0.05초 간격으로 카운트다운 표시 (네트워크 요청은 안함)
            if remaining > 2.0:
                curr_server_dt = datetime.datetime.fromtimestamp(estimated_server_time, tz=KST)
                sys.stdout.write(
                    f"\r[추정 서버시간] {curr_server_dt.strftime('%H:%M:%S.%f')[:-3]} | "
                    f"남은시간: {remaining:6.2f}초   "
                )
                sys.stdout.flush()
                time.sleep(0.05)
            else:
                # 2초 이내: 초정밀 마이크로 대기 루프 (CPU 지연 최소화)
                sys.stdout.write(f"\r[!] 최종 카운트다운 진입 (남은시간: {remaining:4.2f}초) - 고정밀 대기 중...   \n")
                sys.stdout.flush()
                while True:
                    local_now = time.time()
                    estimated_server_time = local_now + offset
                    if estimated_server_time >= target_ts:
                        break
                    time.sleep(0.0002) # 0.2ms 마이크로 대기
                break

        # 클릭 실행!
        click_local_time = time.time()
        click_server_time = click_local_time + offset
        perform_left_click()
        
        # 알림음 재생 (1000Hz, 0.5초)
        try:
            winsound.Beep(1000, 500)
        except Exception:
            pass
        
        click_dt = datetime.datetime.fromtimestamp(click_server_time, tz=KST)
        print("\n" + "=" * 65)
        print(f"[*] 마우스 왼쪽 클릭 완료!")
        print(f"    - 클릭 시점 서버시간 (추정): {click_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        print(f"    - 클릭 시점 PC 로컬시간   : {datetime.datetime.fromtimestamp(click_local_time, tz=KST).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        print("=" * 65)

    except KeyboardInterrupt:
        print("\n[!] 사용자에 의해 프로그램이 중단되었습니다.")

if __name__ == "__main__":
    main()
