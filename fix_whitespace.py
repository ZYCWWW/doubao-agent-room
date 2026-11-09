import os
# 清理所有Python文件中的全角空格
def fix_all_file(root_dir: str):
    for root, _, files in os.walk(root_dir):
        for fname in files:
            if fname.endswith(".py"):
                fp = os.path.join(root, fname)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        content = f.read()
                    new_content = content.replace("\u3000", " ")
                    if new_content != content:
                        with open(fp, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        print(f"✅ 已清理：{fp}")
                except Exception as e:
                    print(f"❌ 跳过 {fp}: {e}")

if __name__ == "__main__":
    fix_all_file(".")
