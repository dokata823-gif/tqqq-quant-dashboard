import json

with open('transcript_raw.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f'{h:02d}:{m:02d}:{s:02d}'
    return f'{m:02d}:{s:02d}'

# Group items into readable blocks
blocks = []
curr_start = 0
curr_texts = []

for item in data:
    start = item.get('start', 0)
    text = item.get('text', '').strip()
    if not text:
        continue
    
    if not curr_texts:
        curr_start = start
        curr_texts.append(text)
    else:
        # Group by approx 25-35s interval or speaker turn
        if (start - curr_start >= 25 and (text.startswith('>>') or text.endswith('.') or text.endswith('?'))) or (start - curr_start >= 40):
            blocks.append((curr_start, ' '.join(curr_texts)))
            curr_start = start
            curr_texts = [text]
        else:
            curr_texts.append(text)

if curr_texts:
    blocks.append((curr_start, ' '.join(curr_texts)))

output = []
output.append('# [유튜브 전체 대화록] 580% 수익 파이어족의 주식 투자 비법 및 전체 대화 전문\n')
output.append('- **영상 원본 링크**: https://youtu.be/-A0tSDnS830')
output.append('- **대화 참여자**: 유과장 (명수), 호동 (친구/진행자), 김PD')
output.append('- **총 분량**: 26분 04초\n')
output.append('---\n')
output.append('## 📑 타임라인별 대화 전문 (Full Dialogue Transcript)\n')

for start_sec, text in blocks:
    t_str = format_time(start_sec)
    
    # Format speaker turns if '>>' is present
    formatted_parts = []
    for part in text.split('>>'):
        part = part.strip()
        if part:
            formatted_parts.append(f"> {part}")
    
    output.append(f"### ⏱️ [{t_str}]")
    output.append('\n'.join(formatted_parts))
    output.append('')

with open('youtube_full_dialogue.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print('Complete! Generated youtube_full_dialogue.md with', len(blocks), 'blocks.')
