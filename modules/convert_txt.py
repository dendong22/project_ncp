import json

def convert_txt_to_json(txt_path, output_path, law_id, law_name):
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()
    
    data = {
        "law_id": law_id,
        "law_name": law_name,
        "source_type": "statute",
        "text": text
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"변환 완료: {output_path}")

# 실행
# 파일 경로를 이렇게 전체 경로로 작성해 보세요
txt_file = r"C:\Users\zzabs\Desktop\legal-screening-agent\개인정보보호법.txt"
json_file = r"C:\Users\zzabs\Desktop\legal-screening-agent\data\corpus\pipa.json"

convert_txt_to_json(txt_file, json_file, "pipa", "개인정보 보호법")