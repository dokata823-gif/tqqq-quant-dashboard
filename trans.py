import os
import sys
import random

def load_data_from_md(md_path):
    if not os.path.exists(md_path):
        print(f"오류: '{md_path}' 파일을 찾을 수 없습니다.")
        sys.exit(1)
        
    with open(md_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
        
    cards = []
    # trans.md는 영어문장 / 한글문장 / 영어발음 순서로 3줄씩 구성되어 있음
    for i in range(0, len(lines) - 2, 3):
        cards.append({
            "eng": lines[i],
            "kor": lines[i+1],
            "pron": lines[i+2]
        })
        
    return cards

def run_flashcards():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(script_dir, "trans.md")
    
    cards = load_data_from_md(md_path)
    if not cards:
        print("오류: trans.md 데이터가 비어 있습니다.")
        return

    # 카드 순서를 랜덤하게 섞음
    shuffled_indices = list(range(len(cards)))
    random.shuffle(shuffled_indices)
    card_pointer = 0

    print("=" * 60)
    print("         영어 회화 스피킹 랜덤 플래시카드 (trans.py)")
    print("=" * 60)
    print(" [명령어 안내]")
    print("   y : 영어 문장 확인")
    print("   s : 영어 발음 확인")
    print("   n : 다음 랜덤 문장으로 이동")
    print("   q : 프로그램 종료")
    print("=" * 60)

    while True:
        # 모든 카드를 한 바퀴 돌았으면 다시 셔플
        if card_pointer >= len(shuffled_indices):
            random.shuffle(shuffled_indices)
            card_pointer = 0
            
        current_card = cards[shuffled_indices[card_pointer]]
        
        print("\n" + "-" * 50)
        print(f"[한글 번역] {current_card['kor']}")

        while True:
            try:
                user_input = input("\n명령어 입력 (y: 영어 | s: 발음 | n: 다음문장 | q: 종료) > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n\n프로그램을 종료합니다.")
                return

            if user_input == 'q':
                print("\n프로그램을 종료합니다. 열공하세요!")
                return
            elif user_input == 'n':
                card_pointer += 1
                break
            elif user_input == 'y':
                print(f"  👉 [영어 문장] {current_card['eng']}")
            elif user_input == 's':
                print(f"  🗣️ [영어 발음] {current_card['pron']}")
            else:
                print("  ⚠️ 올바른 명령어를 입력해주세요. (y: 영어, s: 발음, n: 다음문장, q: 종료)")

if __name__ == "__main__":
    run_flashcards()
