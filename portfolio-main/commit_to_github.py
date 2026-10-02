import base64
import json
import urllib.request
import urllib.error
import os
import sys

# GitHub 저장소 설정
OWNER = "dokata823-gif"
REPO = "portfolio"
BRANCH = "main"
FILE_PATH = "index.html"
LOCAL_FILE = os.path.join(os.path.dirname(__file__), "index.html")

def commit_file(token):
    # 1. 로컬 index.html 파일 읽기
    with open(LOCAL_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    # 2. 기존 파일의 SHA 조회 (업데이트를 위해 필수)
    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}?ref={BRANCH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Portfolio-Pro-Updater"
    }

    req = urllib.request.Request(api_url, headers=headers)
    sha = None

    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            sha = res_data.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # master 브랜치 시도
            api_url_master = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}?ref=master"
            req_m = urllib.request.Request(api_url_master, headers=headers)
            try:
                with urllib.request.urlopen(req_m) as resp_m:
                    res_data = json.loads(resp_m.read().decode("utf-8"))
                    sha = res_data.get("sha")
            except Exception:
                pass
        else:
            print(f"❌ 파일 정보 조회 실패 (HTTP {e.code}): {e.read().decode('utf-8')}")
            return False

    # 3. GitHub API로 커밋 & 푸시 (PUT 요청)
    put_url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}"
    payload = {
        "message": "Update: 보유 수량 기반 실시간 시세 및 비중 자동 연동 대시보드 반영",
        "content": encoded_content,
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha

    req_put = urllib.request.Request(
        put_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="PUT"
    )

    try:
        with urllib.request.urlopen(req_put) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            commit_url = res_data.get("commit", {}).get("html_url", "")
            print("==================================================")
            print("✅ GitHub 커밋 & 배포 성공!")
            print(f"🔗 커밋 링크: {commit_url}")
            print(f"🌐 라이브 페이지: https://{OWNER}.github.io/{REPO}/")
            print("==================================================")
            return True
    except urllib.error.HTTPError as e:
        print(f"❌ 커밋 실패 (HTTP {e.code}): {e.read().decode('utf-8')}")
        return False

if __name__ == "__main__":
    token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_TOKEN")
    if not token:
        token = input("GitHub Personal Access Token(PAT)을 입력하세요: ").strip()
    
    if token:
        commit_file(token)
    else:
        print("토큰이 입력되지 않아 작업을 취소했습니다.")
