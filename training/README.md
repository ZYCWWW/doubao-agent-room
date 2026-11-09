# 本地模型训练启动包（QLoRA）

> 目标：把项目里的小模型（首选 `qwen3.5:4b`）调成「懂这个项目、守格式、有同伴人格」的助手。
> 硬件：RTX 3070 Laptop **8 GB VRAM** / 15.7 GB RAM / i7-12700。
> 结论：**8 GB 只能做 4B 的 QLoRA**（7B/8B 会 OOM 或极慢）；真跑训练需要 HF 权重 + CUDA 环境，环境搭建比训练本身更费时间。

## 一、三层「训练」，成本差一个数量级

| 层 | 做什么 | 成本 | 效果 |
|---|---|---|---|
| L1 提示词 / few-shot | 系统提示词 + 3–5 条示范塞进上下文 | 几乎为零，项目已有 | 立刻见效，但占窗口 |
| L2 数据飞轮 | 云端模型产出示范/偏好数据，人工筛选 | 低（token 费） | 为 L3 准备燃料；也可直接做 few-shot |
| L3 LoRA 微调 | 改权重，导回 Ollama | **高**：环境 0.5–1 天 + 训练数小时 + 评测 | 稳定人格/格式，不占窗口 |

**当前余额只够做 L2 的启动包**（本目录），L3 等你有整块时间再开。

## 二、数据规范

`train.jsonl` / `val.jsonl`，每行一个样本（OpenAI messages 格式，Ollama 可直接吃）：

```json
{"messages":[{"role":"system","content":"你是项目里的助手…"},{"role":"user","content":"…"},{"role":"assistant","content":"…"}]}
```

来源（按质量排序）：
1. `sandbox/chat_space/chat.md` 里本地模型的高质量回合（已被身份化上下文调过，天然带格式）
2. `sandbox/hypothesis/*.md`：猜想问题 → 结论/求证记录
3. 豆包在聊天室里产的「示范对话」（让豆包按 §四 的规范产出）
4. ReAct 成功轨迹：`sandbox/<sid>/react_final.md`

筛选规则：回答 ≥20 字、去重、剔除 `📡/🔕` 广播、剔除复读（与上一条相似 >0.9）。

## 三、训练配方（4B QLoRA，8 GB 可跑）

```python
# train_qlora.py（骨架，需先装环境）
from unsloth import FastLanguageModel
model, tok = FastLanguageModel.from_pretrained(
    "unsloth/Qwen3-4B-unsloth-bnb-4bit", max_seq_length=1024, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16,
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"])
# Trainer: bs=1, grad_accum=8, lr=2e-4, epochs=1~2, warmup=0.03
# 预计：500 条 × 1 epoch ≈ 40–90 分钟；2000 条 ≈ 3–6 小时
```

环境（Windows 最省事的顺序）：
1. `pip install torch --index-url https://download.pytorch.org/whl/cu121`
2. `pip install unsloth trl peft bitsandbytes datasets`
3. 若 bitsandbytes 报错 → 换 WSL2（Ubuntu + CUDA），或先用 `Qwen3-1.7B` 跑通流程

导出回 Ollama：
```bash
python -m llama.cpp.convert_hf_to_gguf ...
ollama create my-agent -f Modelfile   # FROM ./ggml-model-q4_k_m.gguf
```

## 四、豆包的产出规范（给聊天室用）

让豆包按下面格式产「示范对话」，每段 3–6 轮，覆盖：日常闲聊、求证讨论、拒绝越权、解释项目概念。

```
### 场景：<一句话>
用户：<一句>
助手：<≥20 字，口语，不超 200 字>
用户：<追问>
助手：<回应>
```

每段开头写 `【训练样本】`，我这边用脚本抽取，不会混进闲聊语料。

## 五、评测（不训练也能先建基线）

`eval.py`：对 `eval.jsonl` 逐条跑本地模型，输出 长度/格式/关键词 三项规则分；若设了 `DEEPSEEK_API_KEY`，再让云端模型当裁判打 1–5 分。

**先建基线，再谈训练效果**——否则没法证明微调有用。
