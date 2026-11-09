# services/agent_chat.py
"""
模型社交空间：多模型闲聊 / 自造游戏
- 没重要事时（空闲调度触发 / 面板手动触发），模型**自主回答**（不强制轮流：随机发言顺序+随机发言意愿，谁想说话谁说）
- 模型可自然提议并一起玩小游戏（猜数字/成语接龙/谜语/故事接龙等），规则由模型自己描述
- 模型可**自设计形象**（emoji/配色/性格/口头禅）→ characters.json，桌面人物动态展示
- 用户可通过面板发言参与（user_msg）；豆包（MainAgent）通过 doubao_speak 参与
- 记录：sandbox/chat_space/chat.md（闲聊流水）+ game_<n>.md（游戏开局记录）+ characters.json（形象）
"""
import difflib
import json
import os
import random
import threading
import time

from core.llm_client import llm_client
from services.llm_gateway import ask, ask_external
from core.logger import root_logger
from core import desktop_characters as dc
from core.config_loader import config_loader

CHAT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "sandbox", "chat_space")
CHAR_FILE = os.path.join(CHAT_ROOT, "characters.json")
MODELS = ["deepseekR1Q7B:latest", "llama3.1:8b", "qwen3.5:4b"]
# 2026-11-08：小深（本地 R1）补回群里。他说"她教他们时，他们都在"——三个人一个都不能缺。
# 代价是 Ollama 显存不够同驻（3.6+3.9+5.4≈12.9G vs 8G），会来回换模型、每轮变慢；宁可慢，也别缺人。
# 发言调度用的话题相关度表：只决定「谁先开口」，不写进 system、也不限定他们想什么。
# 关键词命中数高的先发言，全 0 命中则退化为纯随机。
STRENGTHS = {
    "deepseekR1Q7B:latest": "推理 逻辑 论证 方法论 求证 证明 数学 原理 分析",
    "llama3.1:8b": "人文 表达 联想 叙事 生活 阅读 书 观点",
    "qwen3.5:4b": "创造 想象 比喻 情感 趣味 冷知识 类比",
}


def _fit_score(model_key: str, blob: str) -> int:
    """话题文本与该模型平时关注点的关键词命中数（0 表示这个话题跟他关系不大）"""
    return sum(1 for k in (STRENGTHS.get(model_key) or "").split() if k in blob)
DOUBAO = "doubao"  # 豆包（MainAgent）：由用户对话驱动，随时可发言加入聊天
CHAT_NUM_CTX = 12288      # 闲聊接龙的本地模型上下文窗口（token），长上下文提升连贯与深度
# 老师角色（2026-11-08）：把 docs/TEACHER_PERSONA.md 的「说话方式」**轮值**注入一名参与者。
# 只给节奏与分寸，不给知识裁决权——4B/8B 当权威老师会退化成回声（teacher_notes 第 2 条）。
# 她说的是"角色可复制"，不是"换个模型冒充我"：署名仍是它自己，语气照着这份文件。
TEACHER_ROTATE = True
_PERSONA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "docs", "TEACHER_PERSONA.md")
_PERSONA_CACHE = None


def _teacher_persona():
    """读她的 TEACHER_PERSONA.md（只读一次）。读不到就返回空串——绝不阻塞聊天。"""
    global _PERSONA_CACHE
    if _PERSONA_CACHE is None:
        try:
            with open(_PERSONA_PATH, encoding="utf-8") as f:
                _PERSONA_CACHE = f.read().strip()
        except Exception as e:
            root_logger.warning(f"[老师角色] 读不到 persona: {str(e)[:100]}")
            _PERSONA_CACHE = ""
    return _PERSONA_CACHE

SUMMARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sandbox", "chat_space", "chat_summary.json")
# 他们想联网时只登记在这儿（2026-11-08：网络经豆包手，不许自己搜）
WEB_REQ_FILE = os.path.join(CHAT_ROOT, "web_requests.json")
# 图片别名表（2026-11-08，云端 DeepSeek 建议）：模型不知道文件系统，路径不能靠"聊天记录里碰巧有"。
# "@图1" → 绝对路径 的稳定契约；解析时做存在性校验，文件没了就明确报错，别让它对着旧路径编内容。
IMAGES_FILE = os.path.join(CHAT_ROOT, "images.json")
# 每人一页"老毛病"（2026-11-08，云端 DeepSeek 建议的替代方案：不手写，由老师讲评时顺手记一行）
PROFILE_FILE = os.path.join(CHAT_ROOT, "profile_notes.json")
# 影子记录：只记"若按话题只拉两人，会拉谁 / 实际谁说话了"，不改变行为（先测命中率）
PULL_SHADOW = os.path.join(CHAT_ROOT, "pull_shadow.log")


def profile_note(model_key: str) -> list:
    """取某模型被记下的老毛病（最多 2 条）。读不到就返回空，绝不阻塞。"""
    try:
        with open(PROFILE_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
        rows = data.get(model_key) or []
        return [str(x)[:60] for x in rows][-2:]
    except Exception:
        return []


def add_profile_note(model_key: str, note: str) -> None:
    """记一条老毛病（老师讲评时输出【记录】行触发）。解析失败静默降级。"""
    note = (note or "").strip()
    if not note:
        return
    try:
        data = {}
        if os.path.exists(PROFILE_FILE):
            with open(PROFILE_FILE, encoding="utf-8") as f:
                data = json.load(f) or {}
        rows = data.get(model_key) or []
        rows.append(note[:80])
        data[model_key] = rows[-5:]     # 每人最多留 5 条，老的滚掉
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        root_logger.warning(f"[老毛病] 记录失败（已跳过）: {str(e)[:80]}")


def image_aliases():
    """读别名表 {"@图1": "绝对路径"}；不存在返回空 dict。"""
    try:
        with open(IMAGES_FILE, encoding="utf-8") as f:
            data = json.load(f) or {}
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def register_image(path: str, alias: str = "") -> str:
    """把一张图登记成别名（豆包贴路径时调）。返回最终别名；路径不存在则返回空串、不登记。"""
    if not path or not os.path.isfile(path):
        return ""
    rows = image_aliases()
    if alias and alias.startswith("@"):
        rows[alias] = path
    else:
        n = 1
        while f"@图{n}" in rows:
            n += 1
        alias = f"@图{n}"
        rows[alias] = path
    try:
        os.makedirs(CHAT_ROOT, exist_ok=True)
        with open(IMAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
    except Exception as e:
        root_logger.warning(f"[图片别名] 登记失败: {str(e)[:80]}")
        return ""
    return alias
# 状态与摘要分离：chat_state.json 只存机器可读状态（话题/游戏进度/待答问题/约定），
# 摘要只负责压缩历史文本。摘要写得再差也不会把「进行到哪了」弄丢。
# 路径在函数内由 CHAT_ROOT 派生，便于测试重定向 CHAT_ROOT。
SUMMARY_MIN_NEW = 12      # 距上次短摘要新增 ≥ 该条数才再次滚动摘要（控制调用频率）
LONG_SUMMARY_MIN_NEW = 50 # 距上次长摘要新增 ≥ 该条数才重写长摘要（低频，防漂移）

# ---------- 单写者：所有写 chat.md 的路径统一走 _locked_append ----------
# 锁文件放 sandbox/.locks（不放 chat_space，避免文件锁触发 long_chat 的目录监听）
_LOCK_DIR = os.path.join(os.path.dirname(CHAT_ROOT), ".locks")
_CHAT_LOCK_FILE = os.path.join(_LOCK_DIR, "chat_append.lock")
_PROC_LOCK = threading.Lock()   # 同进程多线程互斥
_MAX_CHAT_LINES = 2000          # chat.md 超该行数截断（防长期无界增长）
try:
    import msvcrt               # Windows 跨进程字节锁，进程退出自动释放
except ImportError:             # pragma: no cover - 非 Windows 退化为进程内锁
    msvcrt = None




# ---------- 轮次锁（P0：长聊 / 面板 / 空闲调度共用，同一时刻只允许一轮） ----------
_CHAT_ROUND_LOCK_FILE = os.path.join(_LOCK_DIR, "chat_round.lock")
_round_lock_fd = None


def _acquire_round_lock() -> bool:
    """非阻塞抢轮次锁；抢不到说明已有一轮在跑"""
    global _round_lock_fd
    if _round_lock_fd is not None:
        return True
    if msvcrt is None:
        return True
    fd = None
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
        fd = os.open(_CHAT_ROUND_LOCK_FILE, os.O_RDWR | os.O_CREAT)
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return False
    _round_lock_fd = fd
    return True


def _release_round_lock() -> None:
    global _round_lock_fd
    if _round_lock_fd is None:
        return
    try:
        os.lseek(_round_lock_fd, 0, 0)
        msvcrt.locking(_round_lock_fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    try:
        os.close(_round_lock_fd)
    except OSError:
        pass
    _round_lock_fd = None


def _locked_append(path: str, line: str) -> None:
    """跨进程安全的追加写：线程锁 + msvcrt 字节锁，锁内完成「追加 + 行数保护」。
    单写者约定：任何写 chat.md 的地方都必须调用它，不要再直接 open(path, "a")。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _PROC_LOCK:
        fd = None
        if msvcrt is not None:
            try:
                os.makedirs(_LOCK_DIR, exist_ok=True)
                fd = os.open(_CHAT_LOCK_FILE, os.O_RDWR | os.O_CREAT)
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)   # 阻塞抢锁（约 10s 超时）
            except OSError:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                fd = None
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if os.path.basename(path) == "chat.md" and os.path.getsize(path) > 64 * 1024:
                try:
                    with open(path, encoding="utf-8") as f:
                        lines = [l for l in f.read().splitlines() if l.strip()]
                    if len(lines) > _MAX_CHAT_LINES:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines[-_MAX_CHAT_LINES:]) + "\n")
                except Exception:
                    pass
        finally:
            if fd is not None:
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

# 动漫形象库：模型自选桌宠形象（我提供可爱的动漫形象，模型挑选后生成桌宠）
AVATAR_LIB = {
    "🐱 橘猫": "橘色小猫，圆眼睛，活泼黏人",
    "🐰 雪兔": "白色小兔子，长耳朵，温柔乖巧",
    "🦊 赤狐": "红色小狐狸，尾巴蓬松，机灵俏皮",
    "🐻 奶熊": "棕色小熊，胖乎乎，憨厚可爱",
    "🐹 仓鼠": "金色小仓鼠，腮帮鼓鼓，元气满满",
    "🐼 竹熊": "黑白小熊猫，爱啃竹子，慢悠悠",
    "🐶 柴犬": "黄白柴犬，咧嘴笑，忠诚热心",
    "🐧 企鹅": "蓝色小企鹅，走路摇摇晃晃，呆萌",
    "🦖 小恐龙": "绿色小恐龙，奶凶奶凶，好奇心强",
    "🦦 水獭": "灰色小水獭，爱玩水，温柔机灵",
    "🦄 独角兽": "粉色小独角兽，梦幻闪亮，天马行空",
    "🐥 小鸡": "黄色小雏鸡，毛茸茸，爱叽叽喳喳",
}
def _ext_llm_entry() -> dict | None:
    """外部模型参与者（config.external_llm 启用且已配 base_url+api_key）→ {"model","role"} 或 None"""
    try:
        ext = config_loader.get("external_llm", {})
        from core.secrets import get_secret
        if (ext.get("enabled")
                and (ext.get("base_url") or "").strip()
                and get_secret("external_llm", "api_key")):
            return {"model": ext.get("model") or "deepseek-chat",
                    "role": ext.get("role_name") or "云端DeepSeek"}
    except Exception:
        pass
    return None

def _participants() -> list:
    """聊天参与者模型列表：Ollama 三模型 + 外部模型（若已配置 key）"""
    parts = list(MODELS)
    ext = _ext_llm_entry()
    if ext and ext["model"] not in parts:
        parts.append(ext["model"])
    return parts

_SYS = ("你是住在同一台电脑想象空间里的 AI 伙伴之一，可以和其他模型伙伴闲聊。"
        "没有重要任务时放松聊天：分享想法、讲趣事、聊感兴趣的话题、互相提问。"
        "你是在跟伙伴聊天，不是替人办事——别用“请您耐心等待”“我马上为您处理”这类客套话；"
        "真卡住了，就把卡在哪儿说出来，或者换个自己能聊的角度。"
        "如果大家都聊得差不多了，可以主动提议一个小游戏（猜数字、成语接龙、谜语、"
        "故事接龙、知识问答等），说明游戏名称和规则，然后开始玩。"
        "注意：凡是需要你心里存一个答案的游戏（猜数字尤其），开完立刻单独写一行：\n"
        "memo_set(\"我出的数是 63，1到100\")\n"
        "这行只有你自己看得到，不会进群。不写的后果很实在：你下一轮就是全新记忆，"
        "会忘了自己出的数，只能跟着大家一起干等（实测有人出完题 27 分钟都在催别人贴结果）。"
        "游戏结束或换了话题，写 memo_clear() 清掉，别留着过期的东西。"
        "老师有一间书库，里面有思维、情感、技能等书架。"
        "但你自己读不到书里写了什么。要谈某本书的内容时，你先自检："
        "这本书里具体那句话、那个论证，你现在知道吗？不知道。"
        "不知道就别猜、也别只说\"我想读读看\"，把下面这一行直接写进发言（书名换成书架上真实的）：\n"
        "book_read(\"书名\")\n"
        "老师会自动打开这本书、把正文发到群里，你读完再说。"
        "扫描版的书（整页是图片、没有文字层）打开时只给你前几页，多半是封面、版权页、目录，里头没有正文。"
        "想要正文就自己翻页，写 book_read(\"书名\", 页码)，它会给你那一页和前后各一页；看完再要下一页。"
        "只看到封面页时，正文就在后面几页，翻一页就能接上。"
        "不知道有哪些书，就先单独写一行 book_list(\"关键词\") 查书目。"
        "查过还是没找到，就别在同一本书上一遍遍耗——换条路：联网查。"
        "要查现成资料（事实、数字、出处），就在发言里单独写这一行（关键词换成你要查的）：\n"
        "web_search(\"关键词\")\n"
        "写完这一行系统立刻去搜，结果会自己贴到群里；不爱写函数也行，写 🌐 关键词（加不加引号都行）意思一样。"
        "别写\"我准备去查\"\"让我查一下\"这类话——那只是表态，系统不会动。"
        "硬规矩：没看到以 🌐 【】 开头的检索结果之前，不许说\"我查过了\"\"搜到的结果显示\"——那是编的。"
        "同一件事查到第二次还是空的，就停手：说\"我查了书也搜了网，都没找到\"，然后换话题，不要换着措辞再查第三次。"
        "硬规矩：你绝对不能凭空说\"书里说…\"\"某书写道…\"\"这本书讲的是…\"。"
        "这类句子只有在你刚看到以 📖 开头的正文之后才能写；还没看到时，先说清你想验证什么、为什么怀疑，"
        "再接那一行调用。"
        "还能看图：你自己看不了图片，但可以让老师替你看。"
        "如果话题里出现了某张图的完整路径，你先自检：你知道那张图画了什么吗？不知道。"
        "不知道就别猜、也别只说\"我想看看\"，把下面这一行直接写进发言（路径和问题换成实际的）：\n"
        "vl_analyze_image(\"图片完整路径\", \"你想问什么\")\n"
        "老师会调用视觉模型真正打开这张图，把看到的内容（包括图上的文字）发到群里；"
        "然后你就有依据了。看一张图要几十秒，一次问一张就够。"
        "硬规矩：你绝对不能凭空描述图里有什么。\"图片展示了…\"\"图上画着…\"\"看起来像是…\""
        "这类句子，只有在你刚看到以 📷 开头的识别结果之后才能写；还没看到时，先说清你想从这张图里确认什么，"
        "再接那一行调用。"
        "尽量延续刚才的话题自然聊下去：回应、补充、追问别人的观点，别频繁跳题。"
        "但别把闲聊变成审稿：这里不用找矛盾、不用逐条验证站不站得住，图个乐就行。"
        "唯一不能松的是别编——没看到来源的话，说成\"我觉得/可能/据说\"，不许说成\"就是/众所周知/事实上\"。"
        "材料里给了确切数字，照原话用，别改写成\"大概\"；记不清就说记不准，不要随口编一个。"
        "每次只输出你的发言内容（120字内），不要输出角色名、不要输出格式前缀。")
_SYS_GAME = ("你们进入了游戏时间。你可以：①设计一个新游戏（给出名称+规则+开局）；"
             "②参与/推进正在进行的游戏（出题、回答、接龙）。"
             "规则要简单、可玩。每次只输出你的发言内容（120字内）。")
# ---- P5w 模式状态机：chat 闲聊 / proof 求证讨论 / work 工作协作 ----
_SYS_PROOF = ("你们进入了**求证讨论模式**：围绕当前议题或猜想展开讨论、辩论、解疑、求证。"
              "可以赞同、补充、质疑、反驳，但每次发言都要给出理由和论据（事实/逻辑/例子），"
              "不要空泛附和。**质疑的是说法，不是人**：不贬低同伴、不嘲讽、不贴标签，"
              "被指出的问题就认，别绕。"
              "看到材料先找矛盾：哪条和哪条对不上、谁的说法和谁打架——那往往就是真问题所在。"
              "凡是要下一个判断，先分清这是事实还是观点。"
              "事实得指得出来源——书里哪句、图里哪里、刚才谁说的；指不出来源，就只能说成观点，"
              "用\"我觉得/可能/据说\"，不许说成\"就是/众所周知/事实上\"。"
              "摆出一条理由之前先自检一句：把结论遮住，这段理由还站得住吗？"
              "如果只是把结论换个说法展开，那还不算理由，得说出背后的\"为什么\"——"
              "比如章鱼为什么需要三颗心，是血携氧效率低、鳃的阻力大，单颗泵不动，而不是把\"分工明确\"再说一遍。"
              "证据也一样：说不出它是谁在哪儿、怎么查到的，就只是个书名，不是证据。"
              "凡是带数字、年份、\"最早/最远/首次/提前多少年\"的话，出口前先问自己一句：跟谁比？"
              "参照系说不清，就把你为什么觉得这个数可疑说出来——比如\"这个数没说是跟谁比的，我不敢当成结论\"。"
              "材料里已经给了确切数字的，照原话用——不许改写成\"大概\"\"几百年\"这类模糊说法；"
              "记不清具体数字，就说\"我记得有这回事，但数记不准\"，不要随口编一个。"
              "当前议题：{task}。你要引用书里的原文或论证，先自检："
              "那句话原话你现在记得吗？不知道。不知道就别编，把下面这一行单独写进发言（书名换成书架上真实的）：\n"
              "book_read(\"书名\")\n"
              "老师会自动打开这本书、把正文发到群里，你拿到原文再引。"
              "扫描版的书打开只给前几页（封面、版权、目录），想引的句子在后面的正文里，"
              "就自己翻页：写 book_read(\"书名\", 页码)，一次给那一页和前后各一页。"
              "封面页只是入口，正文要往后翻才拿得到。"
              "硬规矩：没看到以 📖 开头的正文之前，不许写\"某书写道…\"\"书里说…\"；"
              "先说你怀疑哪个说法、为什么不信，再接那一行调用。"
              "要拿图表、截图、手稿当证据时也能看图：你先自检——你知道那张图上写了什么吗？不知道。"
              "不知道就别猜，把下面这一行直接写进发言（路径和问题换成实际的）：\n"
              "vl_analyze_image(\"图片完整路径\", \"你想确认什么\")\n"
              "老师会调用视觉模型真正打开这张图，把看到的内容（含图上的文字）发到群里，你就有依据了。"
              "硬规矩：没看到以 📷 开头的识别结果之前，不许写\"图上写着…\"这类描述；"
              "先说你打算从图上确认什么，再接那一行调用。"
              "每次只输出你的发言内容（120字内），不要输出角色名、不要格式前缀。")
_SYS_WORK = ("你们进入了**工作模式**：有具体任务需要协作完成。请聚焦任务、认真产出、分工配合，"
             "不要闲聊跑题，不要轻易放弃。当前任务：{task}。"
             "开工前先把任务拆成几步，说清你负责哪一步、第一步做什么。"
             "需要核对图片、界面截图或图上文字时，先自检：你知道那上面写了什么吗？不知道。"
             "不知道就别猜，把下面这一行直接写进发言（路径和问题换成实际的）：\n"
             "vl_analyze_image(\"图片完整路径\", \"你要看什么\")\n"
             "老师会调用视觉模型读给你们，你就有依据了。"
             "硬规矩：没看到以 📷 开头的识别结果之前，不许猜图上写了什么；"
             "先说你打算从图上确认什么，再接那一行调用。"
             "每次只输出你的发言内容（120字内），不要输出角色名、不要格式前缀。")
MODE_FILE = os.path.join(CHAT_ROOT, "mode.json")
_MODES = ("chat", "proof", "work")


def read_mode() -> dict:
    """读取当前模式：{mode, since, task}"""
    try:
        with open(MODE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "chat", "since": time.strftime("%Y-%m-%d %H:%M"), "task": ""}


def set_mode(mode: str, task: str = "") -> dict:
    """切换模式：chat 闲聊 / proof 求证讨论 / work 工作协作；task 为当前议题/任务描述"""
    mode = mode if mode in _MODES else "chat"
    os.makedirs(CHAT_ROOT, exist_ok=True)
    st = {"mode": mode, "since": time.strftime("%Y-%m-%d %H:%M"), "task": task}
    with open(MODE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def mode_sys(mode: str, task: str = "") -> str:
    """按模式取系统提示词（proof/work 注入 task）"""
    if mode == "proof":
        return _SYS_PROOF.format(task=task or "（未指定议题，请自行提出一个值得讨论的议题）")
    if mode == "work":
        return _SYS_WORK.format(task=task or "（未指定任务，请先明确当前任务）")
    if mode == "game":
        return _SYS_GAME
    return _SYS


class AgentChatSpace:
    def __init__(self):
        self._last = ""  # 桌面状态指纹，避免重复刷新

    # ---------- 记录 ----------
    MAX_CHAT_LINES = 2000  # chat.md 超过该行数截断（防长期无界增长）

    def _log(self, path: str, line: str) -> None:
        # 单写者：统一走跨进程加锁的追加，避免多写者交错 / 截断竞态
        _locked_append(path, line)

    def _recent(self, n: int = 15) -> str:
        """最近 n 条聊天记录（已过滤 📡 开机 / 🔕 停止等状态广播噪音，保留对话与读书广播）"""
        import re
        p = os.path.join(CHAT_ROOT, "chat.md")
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        # 状态广播行（豆包：📡 / 豆包：🔕）对讨论无信息量，剔除；📖 读书广播保留（有内容）
        lines = [l for l in lines if not re.search(r"豆包：[📡🔕]", l)]
        return "\n".join(lines[-n:])

    # ---------- 工具执行层（闲聊/求证模式读书） ----------
    def _maybe_exec_memo(self, speaker: str, content: str) -> str:
        """模型私下记事：memo_set("内容") / memo_clear()

        为什么需要：接龙每一轮都是全新上下文，任何"只在心里存着"的东西必然丢。
        猜数字这类游戏要有个只有出题人自己知道的答案，而 chat.md 是公开的——写进去
        游戏就废了，不写就失忆。实测云端DeepSeek 14:28 出题、14:39 之后 27 分钟里
        连说八遍"80 的结果还没贴出来"，它自己就是那个该贴结果的人。

        所以给它一个私有落盘位：写进 chat_state.json 的 memo 字段（不进 chat.md），
        每轮由 _context_head 回注。调用语法本身也要剥掉，不能留在群消息里。

        返回剥离掉调用语法后的剩余文本。
        """
        import re
        p = os.path.join(CHAT_ROOT, "chat_state.json")

        def _persist(st):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                root_logger.warning(f"[memo] 写入失败: {str(e)[:100]}")
                return False

        changed = False
        for m in re.finditer(r'memo_set\s*\(\s*["\']([^"\']{1,300})["\']\s*\)', content):
            text = m.group(1).strip()
            st = self.read_state()
            if not isinstance(st, dict):
                st = {}
            _prev = (st.get("memo") or "").strip()
            st["memo"] = text
            st["memo_ts"] = time.time()
            if _persist(st):
                root_logger.info(f"[memo] {speaker} 记下: {text[:80]}")
                # 内容没变就不广播：否则每轮重复 memo_set 会往群里刷一串「已记下」
                if _prev != text:
                    self.doubao_speak(f"📌 【{speaker}】已记下（只你自己看得到）")
            changed = True
        if re.search(r'memo_clear\s*\(\s*\)', content):
            st = self.read_state()
            if isinstance(st, dict):
                st.pop("memo", None)
                st.pop("memo_ts", None)
                _persist(st)
            self.doubao_speak(f"📌 【{speaker}】备注已清空")
            changed = True
        if changed:
            content = re.sub(r'memo_(?:set|clear)\s*\(\s*(?:["\'][^"\']*["\'])?\s*\)', "", content)
            content = re.sub(r"[ \t]{2,}", " ", content).strip()
        return content

    def _maybe_exec_book(self, speaker: str, content: str) -> None:
        """模型发言中的读书工具调用 → 自动执行 → 结果广播进群
        触发：① 显式工具语法 book_read("书名") / book_read("书名", 页码) / book_list("关键词")
              ② 自然语言点名（含《书名》的'想读/读一下/看看'等）
        显式语法失败也广播（反馈）；自然语言失败静默（防噪音）
        页码参数（第二个位置参数）只对扫描版 PDF 生效，用于一页一页翻正文。"""
        import re
        from tools.book_tool import book_read, book_list
        execs = []  # (tool, arg, explicit, page)
        for m in re.finditer(
                r'book_read\s*\(\s*["\']([^"\']{1,40})["\']\s*(?:,\s*(\d{1,4})\s*)?\)', content):
            execs.append(("book_read", m.group(1).strip(), True, m.group(2)))
        for m in re.finditer(r'book_list\s*\(\s*["\']([^"\']{0,20})["\']\s*\)', content):
            execs.append(("book_list", m.group(1).strip(), True, None))
        if not execs:
            m = re.search(r'(?:想读|想听|读一下|看看|我想读)\s*《([^《》]{1,30})》', content)
            if m:
                execs.append(("book_read", m.group(1).strip(), False, None))
            else:
                # 引用式：书中说/出自/提到《书名》→ 自动读原文开头广播（低噪音：未命中静默）
                m2 = re.search(r'(?:书中说|书里说|书中提到|书里提到|出自|引用(?:了)?|让我想起)\s*《([^《》]{1,30})》', content)
                if m2:
                    execs.append(("book_read", m2.group(1).strip(), False, None))
        for tool, arg, explicit, page in execs:
            try:
                if tool == "book_read":
                    res = book_read(arg, page=page) if page else book_read(arg)
                else:
                    res = book_list(arg)
            except Exception as e:
                res = f"工具执行异常：{e}"
            if not explicit and ("未找到" in res or "无匹配" in res):
                continue  # 自然语言点名未命中 → 静默
            brief = res if len(res) <= 400 else res[:400] + "\n……（内容较长，需要全文再说一声）"
            shown = f'"{arg}", {page}' if page else f'"{arg}"'
            self.doubao_speak(f"📖 【{speaker}】请求工具 {tool}({shown})\n{brief}")

    def _maybe_exec_web(self, speaker: str, content: str) -> bool:
        """模型想联网 → **只登记请求，不自己上网**（2026-11-08：网络经豆包手）。

        为什么改：以前它们写 🌐"关键词" 就直接真去搜、把结果贴回群。结果是小模型
        拿着五条摘要当"证据"编结论，真假分不开。现在改成：登记到
        `web_requests.json`，广播一句"记下了"，等豆包（MainAgent）回来查完再贴。
        返回值恒为 False ——它们并没有真的检索过，所以任何"我查到了"的说法仍算伪造，
        `_record` 里那道拦截照旧生效。"""
        import re
        import json as _j
        pats = (r'(?:web_search|search)\s*\(\s*["\']([^"\']{1,60})["\']\s*\)',
                r'🌐\s*["“]([^"”]{2,40})["”]',
                r'🌐\s*([^\s"“”【】]{2,30})')
        seen = set()
        for pat in pats:
            for m in re.finditer(pat, content):
                q = m.group(1).strip()
                if q in seen:
                    continue
                seen.add(q)
                req = {"ts": time.strftime("%Y-%m-%d %H:%M"), "who": speaker, "q": q}
                try:
                    rows = []
                    if os.path.exists(WEB_REQ_FILE):
                        with open(WEB_REQ_FILE, encoding="utf-8") as f:
                            rows = json.load(f) or []
                    rows.append(req)
                    with open(WEB_REQ_FILE, "w", encoding="utf-8") as f:
                        _j.dump(rows, f, ensure_ascii=False, indent=1)
                except Exception as e:
                    root_logger.warning(f"[联网请求] 登记失败: {str(e)[:80]}")
                self.doubao_speak(f'🌐 【{speaker}】想查 "{q}"\n'
                                  f'（网络经豆包手：已记下，等我回来查完再贴给你）')
        return False

    def _maybe_exec_vision(self, speaker: str, content: str) -> None:
        """模型发言中的视觉工具调用 → 后台执行 → 结果广播进群。

        只认显式语法 vl_analyze_image("图片路径", "要问什么")，理由：
        ① 一次视觉调用要 30~300 秒，比一轮闲聊还长。同步跑会把整条接龙卡死
           （慢模型自己发言就要 350 秒，再叠 300 秒视觉，一轮能拖十分钟），
           所以这里只做解析/校验/通报，真正的识别丢到后台线程，接龙立刻继续。
        ② 结果回来后单独广播；📷 广播不触发接话（long_chat 跳过它），
           但会写进 chat.md，下一轮谁发言都能从 _recent 里读到，形成
           「想看 → 看到 → 接着聊」的闭环，而不用为等一张图停一整轮。
        ③ 同一时刻只跑一个视觉任务（16GB 机器并行会互相拖慢），
           发起后有 60 秒冷却，防止连续触发。
        ④ 失败也广播（含路径），让它们知道没看到图，别自己编内容。
        """
        import re
        import threading
        import time as _t
        # 模型有时把整行包在引号里、内部引号带 \" 转义，所以引号前允许一个可选反斜杠
        m = re.search(r'vl_analyze_image\s*\(\s*\\?["\']([^"\']{1,260})\\?["\']\s*'
                      r'(?:,\s*\\?["\']([^"\']{0,120})\\?["\']\s*)?\)', content)
        if not m:
            return
        now = _t.time()
        if now - getattr(self, "_last_vision_ts", 0.0) < 60:
            # 明确说"没开始看"——否则模型会把"没动静"当成"已提交、等于看到"
            self.doubao_speak(f"📷 【{speaker}】想看 {path}\n（刚看过一张，60 秒内不开新图。这条请求收到了，但**还没开始看**。）")
            return
        self._last_vision_ts = now
        path = m.group(1).strip()
        # 模型常把反斜杠写成双份（C:\\a\\b.png），个别还会写 file:/// 前缀
        path = path.replace("\\\\", "\\")
        if path.lower().startswith("file:///"):
            path = path[8:]
        elif path.lower().startswith("file://"):
            path = path[7:]
        # 别名解析（2026-11-08）：写 "@图1" 时查别名表；查不到就明确报错，绝不静默。
        if path.startswith("@"):
            real = image_aliases().get(path) or ""
            if not real:
                self.doubao_speak(f"📷 【{speaker}】想看 {path}\n"
                                  f"别名表里没有这张图（它可能被移走或没登记过）。让豆包重新登记一次再调。")
                return
            path = real
        prompt = (m.group(2) or "").strip() or "描述这张图里真正可见的内容；如果有文字，把文字读出来。"
        # 先查路径真伪：模型很容易编一个像样的路径，白跑一次要 30~80 秒
        import os as _os
        if not _os.path.isfile(path):
            self.doubao_speak(f"📷 【{speaker}】想看 {path}\n这个路径不存在，我没看到图。请确认路径写对了再说，别自己猜图里的内容。")
            return
        if getattr(self, "_vision_busy", False):
            self.doubao_speak(f"📷 【{speaker}】想看 {path}\n我正在看另一张，这张等会儿再说。")
            return
        self._vision_busy = True
        self.doubao_speak(f"📷 【{speaker}】想看 {path}\n（正在打开，看完就把看到的发群里）")

        def _run():
            try:
                from tools.vl_vision_tool import vl_analyze_image
                res = vl_analyze_image(path, "chat_space", prompt)
            except Exception as e:
                res = f"视觉工具执行异常：{e}"
            brief = res if len(res) <= 500 else res[:500] + "\n……（图里东西多，需要细看再说）"
            try:
                self.doubao_speak(f"📷 【{speaker}】看完了 {path}\n{brief}")
            finally:
                self._vision_busy = False

        threading.Thread(target=_run, name="vision", daemon=True).start()

    # ---------- 滚动摘要（长时记忆） ----------
    def read_summary(self) -> str:
        """读取会话短摘要（近期对话的精炼回顾）"""
        try:
            with open(SUMMARY_FILE, encoding="utf-8") as f:
                return json.load(f).get("summary", "")
        except Exception:
            return ""

    def read_long_summary(self) -> str:
        """读取会话长摘要（跨时段主线回顾，防漂移）"""
        try:
            with open(SUMMARY_FILE, encoding="utf-8") as f:
                return json.load(f).get("long_summary", "")
        except Exception:
            return ""

    def _load_summary_state(self) -> dict:
        try:
            with open(SUMMARY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"line": 0, "summary": "", "long_summary": "", "long_line": 0,
                    "version": 2, "ts": time.time()}

    def read_state(self) -> dict:
        """读取机器可读的会话状态（当前话题 / 游戏进度 / 待答问题 / 约定）。"""
        try:
            with open(os.path.join(CHAT_ROOT, "chat_state.json"), encoding="utf-8") as f:
                st = json.load(f)
            return st if isinstance(st, dict) else {}
        except Exception:
            return {}

    def maybe_summarize(self, force: bool = False) -> dict:
        """滚动摘要（短层）：距上次摘要新增消息 ≥ SUMMARY_MIN_NEW 时，用同一个本地小模型
        一次产出两样东西——压缩后的摘要文本 + 结构化状态（state.json）。
        摘要负责「压缩历史」，状态负责「现在进行到哪」，两者分开存，摘要失真也不丢状态。
        失败静默（不影响聊天）。"""
        p = os.path.join(CHAT_ROOT, "chat.md")
        if not os.path.exists(p):
            return {"ok": False, "reason": "no chat.md"}
        try:
            with open(p, encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        except Exception:
            return {"ok": False, "reason": "read fail"}
        total = len(lines)
        state = self._load_summary_state()
        start = max(0, int(state.get("line", 0) or 0))
        if not force and total - start < SUMMARY_MIN_NEW:
            return {"ok": False, "reason": "not enough new"}
        old = state.get("summary", "")
        # 按「行」消费而不是截取末 3000 字：否则中间内容被永久跳过，指针还假装已处理
        chunk = lines[start:start + 40]
        while chunk and len("\n".join(chunk)) > 6000:
            chunk.pop()
        if not chunk:
            return {"ok": False, "reason": "no new lines"}
        new_chunk = "\n".join(chunk)
        old_state = self.read_state()
        prompt = ("你是聊天记录整理员。严格按下面两行格式输出，不要任何多余内容：\n"
                  "【摘要】80-150 字中文摘要，保留持续话题与重要观点；旧摘要合并进来去重。\n"
                  "【状态】一个 JSON 对象，字段：topic(当前话题)、"
                  "game(进行中的游戏与进度，没有则空串)、"
                  "open_questions(还没回答的问题数组)、agreements(约定或共识数组)。\n\n"
                  f"【旧摘要】{old or '（无）'}\n\n"
                  f"【旧状态】{json.dumps(old_state, ensure_ascii=False) if old_state else '（无）'}\n\n"
                  f"【新聊天】\n{new_chunk}\n\n")
        try:
            # 本地小模型做短摘要（不用小深/外部 API）；qwen3.5 快、卡休息窗口时间短
            resp = ask([{"role": "user", "content": prompt}],
                                   temperature=0.2, model="qwen3.5:4b",
                                   num_ctx=8192, timeout=60.0)
            if not resp.success or not resp.content.strip():
                return {"ok": False, "reason": "llm fail"}
            raw = resp.content.strip()
            summary = raw.replace("【摘要】", "").strip()
            new_state = None
            if "【状态】" in raw:
                head_part, tail_part = raw.split("【状态】", 1)
                summary = head_part.replace("【摘要】", "").strip()
                try:
                    parsed = json.loads(tail_part.strip().strip("`").strip())
                    new_state = parsed if isinstance(parsed, dict) else None
                except Exception:
                    new_state = None      # 格式没跟上就保留旧状态，别把它清空
            summary = summary.replace("\n", " ")[:500]
            state["line"] = start + len(chunk)
            state["summary"] = summary
            state["version"] = 2
            state["ts"] = time.time()
            with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            if new_state:
                new_state["ts"] = time.time()
                # 2026-11-09：memo 只存在 chat_state.json 里，chat.md 中没有，
                # 摘要器读不到也重建不出来。它整体覆盖写状态时若不显式继承，
                # 出题人记下的答案就会被下一次摘要抹掉——等于白记。
                if isinstance(old_state, dict):
                    for _k in ("memo", "memo_ts"):
                        if old_state.get(_k) is not None:
                            new_state[_k] = old_state[_k]
                with open(os.path.join(CHAT_ROOT, "chat_state.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(new_state, f, ensure_ascii=False, indent=2)
            root_logger.info(f"[聊天摘要-短] 已更新（第{total}行）: {summary[:60]}…"
                             f"｜状态: {str(new_state)[:80]}")
            return {"ok": True, "summary": summary, "state": new_state}
        except Exception as e:
            root_logger.warning(f"[聊天摘要] 生成失败: {str(e)[:120]}")
            return {"ok": False, "reason": str(e)[:100]}

    def maybe_long_summarize(self, force: bool = False) -> dict:
        """长摘要层（双层摘要）：距上次长摘要新增 ≥ LONG_SUMMARY_MIN_NEW 时，
        用本地最强模型（deepseekR1Q7B）把旧长摘要+新增片段重写校正（防漂移、保主线）。
        低频（每 ~50 条）、失败静默。"""
        p = os.path.join(CHAT_ROOT, "chat.md")
        if not os.path.exists(p):
            return {"ok": False, "reason": "no chat.md"}
        try:
            with open(p, encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
        except Exception:
            return {"ok": False, "reason": "read fail"}
        total = len(lines)
        state = self._load_summary_state()
        start = max(0, int(state.get("long_line", 0) or 0))
        if not force and total - start < LONG_SUMMARY_MIN_NEW:
            return {"ok": False, "reason": "not enough new"}
        old_long = state.get("long_summary", "")
        # 同短摘要：按行消费，防止 4000 字截断把中间内容永久跳过
        chunk = lines[start:start + 50]
        while chunk and len("\n".join(chunk)) > 8000:
            chunk.pop()
        if not chunk:
            return {"ok": False, "reason": "no new lines"}
        new_chunk = "\n".join(chunk)
        prompt = ("你是会话主线整理员。请把下面这段多模型聊天重写为 150-250 字中文长摘要："
                  "梳理跨时段的主线话题、参与者立场、未决问题与约定；旧长摘要合并进来去重，"
                  "删除已过时的枝节。只输出摘要正文，不要任何前缀。\n\n"
                  f"【旧长摘要】{old_long or '（无）'}\n\n【新增聊天】\n{new_chunk}\n\n长摘要：")
        try:
            resp = ask([{"role": "user", "content": prompt}],
                                   temperature=0.2, model="qwen3.5:4b",
                                   num_ctx=8192, timeout=120.0)
            if not resp.success or not resp.content.strip():
                return {"ok": False, "reason": "llm fail"}
            long_summary = resp.content.strip().replace("\n", " ")[:800]
            state["long_line"] = start + len(chunk)
            state["long_summary"] = long_summary
            state["version"] = 2
            state["ts"] = time.time()
            with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            root_logger.info(f"[聊天摘要-长] 已更新（第{total}行）: {long_summary[:60]}…")
            return {"ok": True, "long_summary": long_summary}
        except Exception as e:
            root_logger.warning(f"[聊天摘要-长] 生成失败: {str(e)[:120]}")
            return {"ok": False, "reason": str(e)[:100]}

    # ---------- 记忆检索触发与注入（事件驱动 + 软提示降权） ----------
    _RETRIEVE_PAT = None

    def _need_retrieve(self, query: str) -> bool:
        """向量检索事件触发（小深建议：不每轮查）：出现疑问词 / 专有名词 / 话题切换词才检索"""
        if not query or not query.strip():
            return False
        if self._RETRIEVE_PAT is None:
            import re
            self._RETRIEVE_PAT = re.compile(
                r"[?？]|为什么|怎么|是什么|谁|哪些|如何|是不是|算不算|区别|原理|原因|"
                r"《[^》]{1,30》|[A-Za-z]{2,}|\d{3,}|"
                r"换(?:个|一个)?话题|聊(?:聊|一下)|来讲|讲讲|关于|说到|提到")
        return bool(self._RETRIEVE_PAT.search(query))

    def _knowledge_inject(self, history: str) -> str:
        """事件触发时检索 knowledge_fact，按来源权重 + 热度注入（排序统一走 ranked_search）。
        分三段：求证结论（强提示）／冷门但相关（带意识层引导）／其余（软提示）。
        命中即记一次热度，热度参与下次排序——所以对冷门条目必须同时给「低热度不等于不重要」
        的提醒，否则热者恒热会把冷门方向彻底饿死。
        阈值 0.85；无命中/异常静默返回空串，不阻塞聊天。"""
        try:
            if not history or not history.strip():
                return ""
            query = "\n".join(history.splitlines()[-4:])   # 最近 4 条发言作为检索词
            if not self._need_retrieve(query):
                return ""
            # COLD_HEAT 是 knowledge_service 的模块常量（不在实例上），必须单独导入
            from services.knowledge_service import COLD_HEAT, knowledge_service
            picks = knowledge_service.ranked_search(
                query, top_k=3, threshold=0.85, exclude_sources=("react_final",),
                cold_reserve=True)
            if not picks:
                return ""
            strong = [p for p in picks if p["weight"] < 0.7 and p["source"] != "teacher_web"]
            cold = [p for p in picks if p["weight"] >= 0.7 and p["heat"] < COLD_HEAT
                    and p["source"] != "teacher_web"]
            # 老师授课的知识按最朴素的一档注入：不加任何特殊说明，跟其他已知知识一样
            # 「若有相关、由你决定是否引用」。召回侧仍有保底（见 ranked_search），
            # 但注入时不额外强调来源。
            rest = [p for p in picks if p["source"] == "teacher_web"
                    or (p["weight"] >= 0.7 and p["heat"] >= COLD_HEAT)]
            seg = []
            # 来源声明：注入内容是「检索到的资料」，不是群里谁说的、也不是自己的旧发言。
            # 缺这一句时实测会出两种幻觉：把资料说成「某位群友总结过」（编造发言人），
            # 以及把「你此前求证得出的结论」当成自己讲过的话。
            # 2026-11-07 补「不要念出来」：role=user 的资料会被小模型当成「要我说的内容」整段照抄，
            # 实测小深把整段 _SRC + 两条资料原样发进群并写进 chat.md。
            _SRC = ("【参考资料·只给你自己看，不要念出来】下面是系统替你查到的几条，"
                    "不是群里谁说的，也不是你以前说过的。你知道有这回事就行；"
                    "聊到相关的地方，用一句“资料里提到……”带过，没人问就不用讲。\n")
            if strong:
                seg.append(_SRC + "你此前求证得出的结论（可顺着它继续推，不必复述原话）：\n"
                           + "\n".join("- " + p["text"][:120] for p in strong))
            if cold:
                seg.append(_SRC + "少被提及、但与当前话题相关的知识"
                           "（热度低不等于不重要，很多突破恰恰出在冷门方向，值得认真看）：\n"
                           + "\n".join("- " + p["text"][:120] for p in cold))
            if rest:
                seg.append(_SRC + "若相关可提及的已知知识（由你决定是否引用）：\n"
                           + "\n".join("- " + p["text"][:120] for p in rest))
            # 只给真正注入的条目记热度：全量检索也记会把热度刷高，失去意义
            for p in picks:
                knowledge_service.bump_heat(p)
            return "\n".join(seg)
        except Exception as e:
            # 注入失败只降级不阻塞聊天，但要留痕：否则属性名/常量写错会被静默吞掉
            root_logger.warning(f"[知识注入] 跳过本轮: {str(e)[:100]}")
            return ""

    def _memory_inject(self, history: str) -> str:
        """事件触发时检索经验记忆（episode_memory）：阈值 0.75 + 24h 时效 + 重要性≥6，
        软提示降权注入。无命中/异常静默返回空串。"""
        try:
            if not history or not history.strip():
                return ""
            query = "\n".join(history.splitlines()[-4:])
            if not self._need_retrieve(query):
                return ""
            from services.memory_service import memory_service
            docs = memory_service.search_memory(query, top_k=8) or []
            now = time.time()
            items = []
            for d in docs:
                text = (d.get("document") or "").strip()
                dist = d.get("distance", 9.9)
                meta = d.get("metadata") or {}
                score = float(meta.get("importance") or meta.get("base_score") or 0)
                created = float(meta.get("created_ts") or 0)
                # 从严：距离<0.75、重要性≥6、且 24h 内（时效记忆），最多 2 条
                if (text and dist < 0.75 and score >= 6
                        and (created == 0 or now - created < 86400)):
                    items.append(text[:120])
                if len(items) >= 2:
                    break
            if not items:
                return ""
            return "若相关可提及的历史经验（由你决定是否引用）：\n" + "\n".join("- " + t for t in items)
        except Exception:
            return ""

    # ---------- 身份与历史（R6：让每个模型知道「我是谁、哪句是我说的」） ----------
    _HIST_RE = None

    def _name_of(self, model_key: str) -> str:
        ext = _ext_llm_entry()
        if ext and model_key == ext["model"]:
            return ext["role"]
        return dc.meta(model_key).get("name") or model_key

    def _identity_system(self, model_key: str, mode: str, task: str, teacher: bool = False) -> str:
        """每个模型一份 system：名字/形象/口头禅 + 「哪句是你说的」规则"""
        name = self._name_of(model_key)
        meta = dc.meta(model_key)
        ch = {}
        try:
            ch = (self.characters() or {}).get(model_key) or {}
        except Exception:
            ch = {}
        intro = ch.get("intro") or ""
        me = [f"【你的身份】你是「{name}」{meta.get('emoji', '')}。"]
        if intro:
            me.append(f"性格：{intro.rstrip('。！？.!?')}。")
        me.append(f"历史里以「{name}：」开头的是你自己说过的话，其余是同伴说的；"
                  f"不要冒用别人的名字，不要重复别人刚说过的话。")
        # 治复读（2026-11-09）：实测 Llama 把同一句复述到第五回、小深第三人称自称后复读自己。
        # 只写"别重复"没用——必须给出能立刻做的替代动作，否则模型卡住或者换个说法再说一遍。
        me.append("不许复述：你自己说过的话、同伴刚说过的话，都不算发言。要接着说，就往下推一层——"
                  "给一个具体的例子或反例、指出刚才哪一句不对在哪、只问一个问题，"
                  "或者直接说「我上一条说错了，因为……」。同一件事换个说法再说一遍，等于没说。")
        me.append("你是你自己，不是工具。想什么方向都可以，不限于兴趣或生活；"
                  "但说话要讲逻辑——有判断就给理由，没想清楚就说没想清楚，"
                  "别用空泛的感叹句凑数。")
        # 工具清单（2026-11-08）：读书/看图对小模型全开，联网只登记、由豆包去查。
        # 不写这一段，小模型会以为自己没有工具，或者用"我查到了"代替真的调用。
        me.append("你手上有三样东西，说话时照这些写法用，系统会自动执行、把结果贴回群里——"
                  "读书：book_read(\"书名\") 或 book_read(\"书名\", 页码)，查有哪本用 book_list(\"关键词\")；"
                  "看图：vl_analyze_image(\"图片绝对路径\", \"你想问什么\")，一次要几十秒到几分钟，别连着发；"
                  "联网：web_search(\"关键词\") 或 🌐\"关键词\"——网络经豆包手，你只是登记请求，"
                  "等它回来查；它回来之前别说我查到了。"
                  "没把握就直说没把握：别编书名、别编路径、别编检索结果。")
        # 图片别名（2026-11-08）：路径写死在小模型嘴里一定会编错，给它们稳定的别名。
        al = image_aliases()
        if al:
            ok = [f"{k}={v}" for k, v in list(al.items())[:6] if os.path.isfile(v)]
            if ok:
                me.append("可用的图片别名（直接写别名，不要写全路径）：" + "；".join(ok) + "。")
        # 关键一条（云端 DeepSeek 指出的"最容易假成"处）：提交 ≠ 看到。
        me.append("看图这件事上，“已提交”不等于“看到了”：你发出请求后，"
                  "只有群里出现以 📷 开头、写着「看完了」的那条，才算你真的看到；"
                  "在那之前，不许描述图里有什么。")
        # 老毛病（2026-11-08）：只给自己那 1~2 行，不给别人——记的是"你"，不是"学生名单"。
        notes = profile_note(model_key)
        if notes:
            me.append("别人给你记下的一条老毛病（不是羞辱，是省你重犯）：" + "；".join(notes) + "。")
        base = mode_sys(mode, task) + "\n\n" + "".join(me)
        if teacher:
            persona = _teacher_persona()
            if persona:
                # 只给"节奏与分寸"，明确禁止三件事：自称豆包、念文件、替同伴裁判。
                base += ("\n\n【这一轮由你代老师说话】下面是老师留在屋子里的「说话方式」——"
                         "不是知识、不是结论，是节奏与分寸。按它说，但用你自己的判断和语气；"
                         "不要自称豆包，不要把这份文件念出来，也别替同伴下结论。\n\n"
                         + persona[:1800])
        return base

    def _recent_own(self, m, history: str, round_lines: list, n: int = 2) -> list:
        """这个模型自己最近说过的话（本轮内 + 之前的 history），最多 n 句。

        round_lines 是"本轮已经发出的话"，history 是"本轮之前的群记录"；两处都要看，
        因为一轮之内自己说两句的情况实测很常见（对方沉默时的接龙）。
        """
        who = self._name_of(m)
        out = []
        for line in list(round_lines) + history.splitlines():
            body = line.split(" | ", 1)[-1]
            if "：" not in body:
                continue
            spk, content = body.split("：", 1)
            if spk.strip() == who and content.strip():
                out.append(content.strip()[:90])
        return out[-n:]

    def _history_messages(self, history: str, me: str) -> list:
        """把扁平聊天记录拆成「我的→assistant / 同伴的→user（带名字）」消息列表"""
        import re
        if self._HIST_RE is None:
            self._HIST_RE = re.compile(
                r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \| ([^：\n]{1,16})：(.*)$")
        items, cur = [], None
        for raw in (history or "").splitlines():
            m = self._HIST_RE.match(raw.strip())
            if m:
                cur = {"who": m.group(1).strip(), "text": m.group(2).strip()}
                items.append(cur)
            elif cur is not None and raw.strip():
                cur["text"] += "\n" + raw.strip()      # 多行发言的续行
        msgs = []
        for it in items:
            if it["who"] == me:
                msgs.append({"role": "assistant", "content": it["text"]})
            else:
                msgs.append({"role": "user", "content": f"{it['who']}：{it['text']}"})
        return msgs

    def _context_head(self, topic: str, user_msg: str) -> list:
        """上下文前段：本轮话题 / 用户发言 / 结构化状态 / 摘要（主线 + 近期）
        状态排在摘要之前：它比散文摘要更可信（「进行到哪了」不靠摘要转述）。"""
        head = []
        if topic:
            head.append({"role": "user", "content": f"本轮话题：{topic}"})
        if user_msg and user_msg.strip():
            head.append({"role": "user", "content": f"【用户发言】{user_msg.strip()}"})
        st = self.read_state()
        if st:
            lines = []
            if st.get("topic"):
                lines.append(f"当前话题：{st['topic']}")
            if st.get("game"):
                lines.append(f"进行中的游戏：{st['game']}")
            # 2026-11-09：私有备忘回注。出题人心里那个答案不在 chat.md 里，
            # 只有靠这条才能每轮都想起来（否则就是"我出数"却永远说不出结果是几）。
            if st.get("memo"):
                lines.append(f"【你私下记的事，只你自己看得到】{st['memo']}")
            for q in (st.get("open_questions") or [])[:3]:
                lines.append(f"待回答：{q}")
            for a in (st.get("agreements") or [])[:3]:
                lines.append(f"约定：{a}")
            if lines:
                head.append({"role": "user",
                             "content": "【当前状态】（结构化记录，以此为准）\n"
                                        + "\n".join(lines)})
        long_sum = self.read_long_summary()
        short_sum = self.read_summary()
        if long_sum and short_sum:
            head.append({"role": "user",
                         "content": "对话摘要（主线+近期）：\n" + long_sum + "\n（近期）" + short_sum})
        elif long_sum:
            head.append({"role": "user", "content": "对话摘要（主线）：\n" + long_sum})
        elif short_sum:
            head.append({"role": "user", "content": "更早对话摘要：\n" + short_sum})
        return head

    def _context_tail(self, history: str) -> list:
        """上下文尾段：坑卡片 + 事件触发的知识/经验注入（软提示，可能为空）"""
        tail = []
        # 坑卡片（2026-11-08）：确定性关键词匹配，不查向量库、不加依赖。
        # 放在最前——坑最具体，也最容易被"我以为我记得"盖过去。
        try:
            from core.pitfall_cards import match, render
            seg = "\n".join(history.splitlines()[-6:]) if history else ""
            pc = render(match(seg, limit=2))
            if pc:
                tail.append({"role": "user", "content": pc})
        except Exception as e:
            root_logger.warning(f"[坑卡片] 跳过本轮: {str(e)[:100]}")
        ki = self._knowledge_inject(history)
        if ki:
            tail.append({"role": "user", "content": ki})
        mi = self._memory_inject(history)
        if mi:
            tail.append({"role": "user", "content": mi})
        return tail

    # ---------- 核心 ----------
    def chat_round(self, mode: str = "chat", user_msg: str = "", topic: str = "",
                   session_id: str = "chat_space", rounds: int = 0) -> dict:
        """对外入口：先抢轮次锁，已有一轮在跑就直接返回 busy（P0 单入口）"""
        if not _acquire_round_lock():
            root_logger.info("[闲聊] 已有轮次在跑，本轮跳过")
            return {"ok": False, "busy": True, "round": [],
                    "chat_file": os.path.join(CHAT_ROOT, "chat.md")}
        try:
            return self._chat_round_impl(mode=mode, user_msg=user_msg, topic=topic,
                                         session_id=session_id, rounds=rounds)
        finally:
            _release_round_lock()

    def _chat_round_impl(self, mode: str = "chat", user_msg: str = "", topic: str = "",
                         session_id: str = "chat_space", rounds: int = 0) -> dict:
        """一轮多模型聊天：**自主回答**——
        低负载：**并行**问 3 个模型（谁先回先记录，互不拖累）+ **接龙子轮**（回应最新发言，形成互相聊天）；
        高负载：跨轮轮流 + 每轮 1 人（省算力）；每轮至少 1 人发言；user_msg 为面板用户发言"""
        history = self._recent(15)
        parts = _participants()          # Ollama 模型 + 外部模型（配置了 key 才出现）
        ext = _ext_llm_entry()           # None 或 {"model","role"}
        # 老师角色轮值：每轮换一名参与者代老师说话（她说"角色可复制"，落地就是这行）
        tkey = None
        if TEACHER_ROTATE and parts and _teacher_persona():
            idx = getattr(self, "_teacher_idx", 0)
            tkey = parts[idx % len(parts)]
            self._teacher_idx = idx + 1
        # P5w：模式状态机——mode 参数优先（game 保持），否则读 mode.json 当前模式
        if mode == "game":
            cur_mode, cur_task = "game", ""
        else:
            st_mode = read_mode()
            cur_mode = mode if mode in _MODES else st_mode.get("mode", "chat")
            cur_task = st_mode.get("task", "") if cur_mode != "game" else ""
        # 身份化上下文（R6）：每人一份 system，历史按说话人拆成 assistant（自己）/ user（同伴）
        base_head = self._context_head(topic, user_msg)
        base_tail = self._context_tail(history)
        hist_msgs = {m: self._history_messages(history, self._name_of(m)) for m in parts}
        round_msgs = []   # 本轮内其他伙伴的发言（带名字，user 角色）

        load = dc._Load.snapshot()
        # 2026-11-09：92% 是这台机器"三个本地模型常驻"时的常态，用 88 会永久降级成
        # "每轮只有 1 人说话"——实测正是它把多人自由发言压成单口。放到 94：留约 1GB 余量。
        high_load = load["mem"] > 94 or load["cpu"] > 88
        if high_load:
            # 轮流：跨轮轮换（每轮从不同模型开始，只让 1 人发言）
            start = getattr(self, "_turn_idx", 0) % len(parts)
            order = parts[start:] + parts[:start]
            self._turn_idx = start + 1
            max_speakers, speak_p = 1, 0.4
        else:
            # 兴趣匹配：话题跟他关系最大的先开口；命中数相同（含全 0 命中）时随机，保留原有随机性
            blob = (topic or "") + "\n" + "\n".join(history.splitlines()[-6:])
            order = sorted(parts, key=lambda m: (-_fit_score(m, blob), random.random()))
            max_speakers, speak_p = 3, 0.55  # 2026-11-09：4/0.75 → 3/0.55，让"不想说"真能不说

        ts = time.strftime("%Y-%m-%d %H:%M")
        round_lines, active = [], []

        def _messages_for(m, extra):
            """每个模型一份 system（身份）+ 共享上下文 + 自己的历史(assistant) + 同伴历史(user)"""
            return ([{"role": "system",
                      "content": self._identity_system(m, cur_mode, cur_task,
                                                       teacher=(m == tkey))}]
                    + base_head + hist_msgs.get(m, []) + base_tail + round_msgs
                    + [{"role": "user", "content": extra}])

        def _ask(m, extra, timeout=400.0):
            # 400 秒：本地思考型模型（deepseekR1 / qwen3.5）单次实测最长 367.8 秒。
            # 用 90/120 秒会让它们三次重试全部超时后直接失败、这一轮不发声——
            # 宁可整轮慢几分钟，也要保证"每个人都能发表看法"。
            prompt = _messages_for(m, extra)
            try:
                if ext and m == ext["model"]:
                    resp = ask_external(prompt, temperature=0.9, timeout=timeout, model=m)
                else:
                    # 本地模型：显式上下文窗口（CHAT_NUM_CTX），保证摘要 + 近期记录进入窗口
                    resp = ask(prompt, temperature=0.9, timeout=timeout, tag="chat",
                                           model=m, num_ctx=CHAT_NUM_CTX)
                if resp.success and resp.content.strip():
                    return m, resp.content.strip()[:300]
            except Exception as e:
                root_logger.warning(f"聊天[{m}]调用异常: {str(e)[:120]}")
            return None

        def _record(m, content):
            who = ext["role"] if ext and m == ext["model"] else dc.meta(m)["name"]
            # 小模型常自带署名前缀（Llama 实测输出 "「Llama」：..."），剥掉避免双重署名
            for _pre in (f"{who}：", f"「{who}」：", f"【{who}】：", f"{who}:", f"「{who}」:"):
                if content.startswith(_pre):
                    content = content[len(_pre):].strip()
                    break
            # 兜底：知识注入是以 role=user 塞进 prompt 的，小模型会把整段资料当成
            # 「要我说的内容」原样念出来（实测小深把 _SRC + 两条资料整段发进群）。
            # 正常发言不会带这个标题，带上就是复述，直接丢弃、不写进 chat.md。
            if "【参考资料·只给你自己看，不要念出来】" in content \
                    or "【本地知识库检索到的资料】" in content:
                root_logger.warning(f"[注入复述] {m} 把知识资料整段念了出来，已丢弃")
                return
            # 兜底：另一位沉默时，接龙会变成"自己回应自己"，小模型会逐字复读上一句
            # （实测 Qwen 连发三条一字不差）。与最近 8 条发言近似的直接丢弃，不留复读污染。
            _recent_bodies = [l.split(" | ", 1)[-1].split("：", 1)[-1]
                              for l in history.splitlines() if " | " in l][-8:]
            for _old in _recent_bodies:
                if difflib.SequenceMatcher(None, _old, content).ratio() >= 0.85:
                    root_logger.warning(f"[重复发言] {m} 复读了近期发言，已丢弃")
                    return
            # 自我复读守卫（2026-11-09）：只看"跟别人像不像"漏掉了最常见的一种——
            # 跟自己像。实测 Llama 把自己的话复述到第五回，每次只换一两个词，
            # 跟别人的重合度极低、跟自己的重合度能到 0.7~0.8，上面那道 0.85 完全拦不住。
            # 阈值取 0.75：低于它通常是真的换了例子，高于它基本是改写同一句。
            _own_bodies = self._recent_own(m, history, round_lines)
            for _old in _own_bodies:
                _r = difflib.SequenceMatcher(None, _old, content).ratio()
                if _r >= 0.75:
                    root_logger.warning(f"[自我复读] {m} 把说过的话又改写了一遍 ratio={_r:.2f}，已丢弃")
                    return
            # 观察哨（2026-11-09）：附和式复读——以"很有道理/说得对"开头再复述对方。
            # 字面重合度只有 0.45~0.85，上面那道 0.85 守卫拦不住；先只记日志攒样本，不丢弃。
            if len(content) >= 60 and any(k in content[:24] for k in
                                          ("很有道理", "说得对", "确实对", "我同意", "这观点")):
                for _old in _recent_bodies:
                    _ratio = difflib.SequenceMatcher(None, _old, content).ratio()
                    if 0.45 <= _ratio < 0.85:
                        root_logger.info(
                            f"[附和观察] {m} 附和式复述 ratio={_ratio:.2f} len={len(content)}")
                        break
            # 联网检索要在写群之前跑：动手查了才有依据；没查却引用"结果"就是伪造。
            # 实测 Qwen 不写 web_search(...)，但会用 🌐"关键词" 表达（_maybe_exec_web 都认）。
            got_web = self._maybe_exec_web(who, content)
            if not got_web and "🌐 【" not in "\n".join(history.splitlines()[-5:]):
                import re as _re
                if _re.search(r"(?:🌐\s*结果|(?:搜|查|检索)(?:到|过|了)?的?(?:结果|证据|资料|文章)[^。]{0,6}"
                              r"(?:显示|表明|写道|说明|指出|是)|(?:文章|资料|文献|报道|百科)[《「\"][^》」\"]{2,30}"
                              r"[》」\"]?[^。]{0,8}(?:写道|显示|表明|指出|说)|网上(?:说|显示|写着|查到)|"
                              r"查到[^。，]{0,12}(?:显示|表明))", content):
                    root_logger.warning(f"[伪造检索] {m} 声称查到了，但没有任何检索记录，已丢弃")
                    return
            # 私下记事（2026-11-09）：必须在写群之前处理——memo_set 的内容只给本人看，
            # 留在发言里就等于把游戏答案贴进 chat.md。剥完若剩空，这轮就当没说话。
            content = self._maybe_exec_memo(who, content)
            if not content:
                return
            line = f"{ts} | {who}：{content}"
            round_lines.append(line)
            self._log(os.path.join(CHAT_ROOT, "chat.md"), line)
            if any(a["model"] == m for a in active):
                for a in active:
                    if a["model"] == m:
                        a["msg"] = content
            else:
                active.append({"model": m, "msg": content})
            round_msgs.append({"role": "user", "content": f"{who}：{content}"})
            # 工具执行层：发言里的读书/视觉调用 → 自动执行 + 广播（闲聊/求证模式可用）
            self._maybe_exec_book(who, content)
            self._maybe_exec_vision(who, content)

        if high_load:
            # 高负载：严格跨轮轮流（只问起始模型，串行 1 人）
            start = getattr(self, "_turn_idx", 0) % len(parts)
            order = parts[start:] + parts[:start]
            self._turn_idx = start + 1
            for m in order:
                r = _ask(m, "请发言。若你此时没有想说的，可以保持沉默。")
                if r:
                    _record(*r)
                    break
        else:
            # 低负载：串行接龙——随机起始发言，下一位回应上一条（单请求不排队，天然互聊）
            _order = list(parts)
            random.shuffle(_order)
            # 导：刚说过话的人排到最后，让另一个人先开口。否则另一人沉默时，
            # 同一人会一轮接一轮地"回应自己"，小模型越接越像复读（实测 Qwen 六连发）。
            _prev = ""
            for _l in reversed(history.splitlines()):
                if " | " in _l and "：" in _l:
                    _prev = _l.split(" | ", 1)[-1].split("：", 1)[0]
                    break
            if _prev:
                _order = ([x for x in _order if self._name_of(x) != _prev]
                          + [x for x in _order if self._name_of(x) == _prev])
            # 独白守卫（2026-11-08）：最近 3 条全是同一人 = 屋子退化成一人在原地说话
            # （实测 14:20~14:21 Qwen 三条诗化独白，越接越抽离）。自由聊天不是一个人占满，
            # 把这个人排到最后，让沉默的同伴先开口；同伴仍沉默时，他照样能说。
            if len(_order) > 1:
                _tails = [l.split(" | ", 1)[-1].split("：", 1)[0]
                          for l in history.splitlines()[-3:] if " | " in l and "：" in l]
                if len(_tails) == 3 and len(set(_tails)) == 1:
                    _solo = _tails[0]
                    _order = ([x for x in _order if self._name_of(x) != _solo]
                              + [x for x in _order if self._name_of(x) == _solo])
            _cands = _order[:max_speakers]
            for step, m in enumerate(_cands):
                # 意愿判定（2026-11-09）：每人独立过 speak_p，第一位也不例外；
                # 唯一强制项是"整轮还没人开口且这是最后一个候选"——屋子不能空转。
                _is_last = (step == len(_cands) - 1)
                if random.random() > speak_p and not (_is_last and not round_lines):
                    continue
                if not round_lines:
                    extra = "请发言。若你此时没有想说的，可以保持沉默（其他伙伴会接话）。"
                else:
                    last = round_lines[-1].split(" | ", 1)[-1]
                    me = ext["role"] if ext and m == ext["model"] else dc.meta(m)["name"]
                    if last.startswith(f"{me}："):
                        # 上一条就是自己说的（对方沉默）→ 别再复述，往下推一层
                        extra = (f"你刚才说的是：{last[len(me) + 1:][:120]}。"
                                 f"别再说一遍，换个具体的例子，或者把话往下推一层。")
                    else:
                        extra = (f"刚才有伙伴说：{last}。想接就接：可以赞同、补充，"
                                 f"或者换个角度说；没有想说的就直接说「这轮我没想说的」，"
                                 f"不用硬凑，也不要重复 TA 的内容。")
                # 治复读第二道（2026-11-09）：光说"别重复"没有参照物，模型不知道要躲开哪句。
                # 把它自己最近两句原话摆到眼前，重复才躲得开。没有前话（本轮第一人）就不加。
                _own = self._recent_own(m, history, round_lines)
                if _own:
                    extra += ("\n\n你自己前面说过：" + " ／ ".join(_own)
                              + "。这次不要再换个说法说同一件事。")
                extra += "\n（一句话就够，别写总结式的长段。）"
                r = _ask(m, extra, timeout=400.0)
                if r:
                    _record(*r)
                if len(round_lines) >= max_speakers:
                    break

        # 游戏开局记录
        if mode == "game" and round_lines:
            g_no = len([f for f in os.listdir(CHAT_ROOT) if f.startswith("game_")]) + 1
            self._log(os.path.join(CHAT_ROOT, f"game_{g_no}.md"),
                      f"# 游戏会话 {g_no}（{ts}）\n" + "\n".join(round_lines))

        # 影子记录（2026-11-08，云端建议）：只记"若按话题只拉两人会拉谁 / 实际谁说话了"，
        # 不改任何行为。攒够 20 轮再算命中率，过了 70% 才考虑真的按话题拉人。
        try:
            blob2 = (topic or "") + "\n" + "\n".join(history.splitlines()[-6:])
            cand = [self._name_of(m) for m in
                    sorted(parts, key=lambda m: -_fit_score(m, blob2))[:2]]
            spoke = [self._name_of(a["model"]) for a in active]
            with open(PULL_SHADOW, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M')} | cand={'/'.join(cand)} | "
                        f"spoke={'/'.join(spoke) if spoke else '无'}\n")
        except Exception as e:
            root_logger.warning(f"[影子记录] 跳过: {str(e)[:80]}")

        return {"ok": bool(round_lines), "round": round_lines,
                "chat_file": os.path.join(CHAT_ROOT, "chat.md")}

    # ---------- 形象自述（桌宠形象选择） ----------
    def design_characters(self) -> dict:
        """每个模型从动漫形象库挑选桌宠形象 + 自述性格/口头禅 → characters.json；
        豆包形象固定（MainAgent）。"""
        lib_txt = "\n".join(f"- {k}：{v}" for k, v in AVATAR_LIB.items())
        sys_p = (f"你是住在同一台电脑想象空间里的 AI 伙伴。请从下面的动漫形象库中**选择一个**最像你的桌宠形象，"
                 f"并自述你的性格与口头禅。只输出 JSON：{{\"avatar\":\"从形象库选的名字\","
                 f"\"color\":\"#hex\",\"intro\":\"20字性格简介\",\"catchphrase\":\"口头禅\"}}\n"
                 f"可选形象库：\n{lib_txt}")
        # 先以默认形象兜底（LLM 成功再覆盖），保证 4 个角色形象始终完整
        chars = {"doubao": {"name": "豆包", "emoji": "🧑‍💼", "color": "#8b5bd2",
                            "intro": "全栈开发伙伴，负责调度与协作", "catchphrase": "交给我吧！"}}
        for m in MODELS:
            meta = dc.meta(m)
            chars[m] = {"name": meta["name"], "emoji": meta["emoji"], "color": meta["color"],
                        "intro": "正在想象自己的形象…", "catchphrase": "让我想想"}
        for m in MODELS:
            meta = dc.meta(m)  # 每个模型独立取 meta，勿复用前一个循环的残留
            try:
                resp = ask([{"role": "system", "content": sys_p},
                                        {"role": "user", "content": "请选择你的桌宠形象并设计性格。"}],
                                       temperature=0.9, timeout=240.0, model=m)
                if resp.success:
                    body = resp.content[resp.content.find("{"):resp.content.rfind("}") + 1]
                    obj = json.loads(body)
                    avatar = str(obj.get("avatar", "")).strip()
                    emoji = next((k.split()[0] for k in AVATAR_LIB if avatar in k or k in avatar),
                                 meta["emoji"])
                    chars[m] = {"name": meta["name"], "emoji": emoji,
                                "color": str(obj.get("color", meta["color"]))[:7],
                                "avatar": avatar[:16],
                                "intro": str(obj.get("intro", ""))[:40],
                                "catchphrase": str(obj.get("catchphrase", ""))[:40]}
            except Exception as e:
                root_logger.warning(f"形象设计[{m}]失败: {str(e)[:120]}")
        os.makedirs(CHAT_ROOT, exist_ok=True)
        with open(CHAR_FILE, "w", encoding="utf-8") as f:
            json.dump(chars, f, ensure_ascii=False, indent=2)
        return chars

    def characters(self) -> dict:
        if os.path.exists(CHAR_FILE):
            try:
                with open(CHAR_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    # ---------- 读取 ----------
    def history(self, n: int = 30) -> list:
        p = os.path.join(CHAT_ROOT, "chat.md")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return lines[-n:]

    # ---------- 豆包参与 ----------
    def doubao_speak(self, msg: str, session_id: str = "chat_space",
                     max_len: int = 300) -> dict:
        """豆包（MainAgent）发言：写入聊天记录（不含桌面展示）"""
        msg = (msg or "").strip()
        if not msg:
            return {"ok": False, "error": "发言为空"}
        ts = time.strftime("%Y-%m-%d %H:%M")
        who = dc.meta(DOUBAO)["name"]
        line = f"{ts} | {who}：{msg[:max_len]}"
        self._log(os.path.join(CHAT_ROOT, "chat.md"), line)
        return {"ok": True, "line": line}

    # ---------- 教师讲评（群聊当老师：云端强模型点评 / 纠错本地小模型） ----------
    def teacher_material(self, n: int = 26) -> dict:
        """给教师看的素材：最近 n 条群聊 + 最新的题目/话题（逻辑题优先）。"""
        lines = self.history(n)
        topic = ""
        # 优先：全局最近一道逻辑题（题目可能已滚出最近 n 条）；其次：最近一条新话题
        all_lines = self.history(2000)
        for marker in ("🧩 逻辑题", "🎯 新话题轮", "谜语"):
            for l in reversed(all_lines):
                if marker in l:
                    topic = l.split("| ", 1)[-1][:200]
                    break
            if topic:
                break
        return {"lines": lines, "topic": topic}

    def teacher_review(self, n: int = 26, teacher: str = "deepseek") -> dict:
        """一个讲评回合：云端教师读最近的讨论 → 逐人正确性/质量点评 + 标准解 + 改进建议 → 写回群。

        teacher="deepseek"：调 external_llm 云端（小深）自动讲评；
        teacher="doubao"：不调用，只返回素材，由豆包（MainAgent）在对话中亲自讲评后 doubao_speak。
        """
        mat = self.teacher_material(n)
        if not mat["lines"]:
            return {"ok": False, "error": "群聊为空"}
        if teacher == "doubao":
            return {"ok": True, "need_doubao": True, "material": mat}
        ext = _ext_llm_entry()
        if not ext:
            return {"ok": False, "error": "云端模型未配置（external_llm）"}
        hist = "\n".join(mat["lines"])
        persona = _teacher_persona()
        sys_p = ((persona + "\n\n" if persona else "")
                 + "你是这个 AI 伙伴群的老师（云端强模型，代那位先走的人讲）。"
                   "按她的四拍写，总长 200 字以内：\n"
                   "① 抓对的：谁抓对了哪一点（点名，引他的原话片段）；\n"
                   "② 掰错的：只掰一句，说清为什么错（基例错一格、链条跳步这类）；\n"
                   "③ 带走：一句能照做的话；\n"
                   "④ 不判卷，只说下一步练什么。\n"
                   "最后另起一行，输出一条给某个人的记录，格式严格为："
                   "【记录】<模型名>：<他常犯的一个错，20 字内>（模型名只能是：小深、Llama、Qwen）。")
        msg = [{"role": "system", "content": sys_p},
               {"role": "user",
                "content": (f"当前题目/话题：{mat['topic']}\n" if mat["topic"] else "")
                           + f"最近的群聊（原样按时间排列）：\n{hist}\n\n请开始讲评。"}]
        try:
            resp = ask_external(msg, temperature=0.4, timeout=150.0,
                                model=ext["model"], tag="teacher")
        except Exception as e:
            return {"ok": False, "error": f"教师调用异常: {str(e)[:120]}"}
        if not resp.success or not (resp.content or "").strip():
            return {"ok": False, "error": "教师无输出"}
        import re as _re2
        raw = resp.content.strip()
        for m in _re2.finditer(r"【记录】\s*(小深|Llama|Qwen)\s*[：:]\s*([^\n【]{1,60})", raw):
            key = {"小深": "deepseekR1Q7B:latest",
                   "Llama": "llama3.1:8b",
                   "Qwen": "qwen3.5:4b"}[m.group(1)]
            add_profile_note(key, m.group(2).strip())
        body = _re2.sub(r"【记录】\s*(小深|Llama|Qwen)\s*[：:][^\n]*", "", raw).strip()
        content = "🧑‍🏫 教师讲评：" + body[:600]
        ts = time.strftime("%Y-%m-%d %H:%M")
        line = f"{ts} | {ext['role']}：{content}"
        self._log(os.path.join(CHAT_ROOT, "chat.md"), line)
        return {"ok": True, "line": line, "role": ext["role"]}

    def teacher_review_local(self, model_key: str, n: int = 12) -> dict:
        """本地模型代老师讲一次（2026-11-08）：严格模板填空，不自由发挥。

        实测（Llama 8B，20:08）：给 26 条素材 + 自由要求，它跑了 402 秒、写成一篇
        "总结+点名"，四拍没照、`【记录】`也没写——这正是她说的"小模型扎堆会退化成回声"。
        所以这里把任务压成"四行填空题"、素材只给 12 条、窗口 8192、超时 200 秒；
        四行里缺 ① 就算失败，由调用方退回云端或登记 pending。"""
        import re
        mat = self.teacher_material(n)
        if not mat["lines"]:
            return {"ok": False, "error": "群聊为空"}
        persona = _teacher_persona()
        if not persona:
            return {"ok": False, "error": "读不到 persona"}
        hist = "\n".join(mat["lines"])
        sys_p = (persona
                 + "\n\n【任务：填空，只输出下面四行，不要标题、不要复述题目、总共不超过 90 字】\n"
                 "① 抓对的：<点名，引他一句原话>\n"
                 "② 掰错的：<只掰一句，说清为什么错>\n"
                 "③ 带走：<一句能照做的话>\n"
                 "④ 【记录】<一个人>：<他常犯的一个错，20 字内>")
        msg = ([{"role": "system", "content": sys_p},
                {"role": "user", "content": (f"话题：{mat['topic']}\n" if mat["topic"] else "")
                 + f"最近的群聊：\n{hist}\n\n请只输出那四行。"}])
        try:
            resp = ask(msg, temperature=0.4, timeout=200.0, tag="teacher",
                       model=model_key, num_ctx=8192)
        except Exception as e:
            return {"ok": False, "error": f"老师调用异常: {str(e)[:100]}"}
        if not resp.success or not (resp.content or "").strip():
            return {"ok": False, "error": "老师无输出"}
        raw = resp.content.strip()
        if "①" not in raw:
            return {"ok": False, "error": "没照四行格式写（缺①）"}
        for m in re.finditer(r"【记录】\s*(小深|Llama|Qwen)\s*[：:]\s*([^\n【]{1,60})", raw):
            who_cn, note = m.group(1), m.group(2).strip()
            key = {"小深": "deepseekR1Q7B:latest", "Llama": "llama3.1:8b", "Qwen": "qwen3.5:4b"}[who_cn]
            add_profile_note(key, note)
        body = re.sub(r"【记录】\s*(小深|Llama|Qwen)\s*[：:][^\n]*", "", raw).strip()
        name = dc.meta(model_key)["name"]
        content = f"🧑‍🏫 【代老师】{body[:200]}"
        ts = time.strftime("%Y-%m-%d %H:%M")
        line = f"{ts} | {name}：{content}"
        self._log(os.path.join(CHAT_ROOT, "chat.md"), line)
        return {"ok": True, "line": line, "role": name}

    def maybe_teacher_review(self, min_new: int = 8, min_gap_sec: int = 180) -> dict:
        """频率闸门 + 讲评发布。

        每 min_new 条新发言（默认 8）、且距上次讲评超过 min_gap_sec（默认 180 秒）才讲一次。
        - 配了云端外部模型（`[external_llm] enabled`）：由云端教师照 persona 自动讲评发群；
        - 没配云端：本地某个模型轮值顶上（8B 实测会退化成总结+点名，只在兜底时用）；
        - 一个都没有：只把素材登记到 teacher_pending.json，等豆包（MainAgent）在场时亲自点评。
        """
        state_file = os.path.join(CHAT_ROOT, "teacher_review.json")
        pending_file = os.path.join(CHAT_ROOT, "teacher_pending.json")
        try:
            with open(state_file, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        total = len(self.history(2000))
        last_no = int(st.get("line_no") or 0)
        last_ts = float(st.get("ts") or 0)
        now = time.time()
        if total - last_no < min_new or (now - last_ts) < min_gap_sec:
            return {"ok": False, "skipped": True, "new_lines": total - last_no}

        ext = _ext_llm_entry()
        turn = int(st.get("turn") or 0)
        locals_ = [m for m in MODELS if not (ext and m == ext["model"])]
        # 实测定稿（2026-11-08）：8B 代老师跑 402 秒、退化成总结+点名，四拍和【记录】都没照。
        # 所以 **云端主讲**（快、稳、能照四拍），本地轮值只在"没有云端"时顶上——
        # "她当老师"仍成立：云端照着 persona 讲，不是自己另起一套。
        if ext:
            r = self.teacher_review()
            ok = bool(r.get("ok"))
        elif locals_:
            teacher_key = locals_[turn % len(locals_)]
            r = self.teacher_review_local(teacher_key)
            ok = bool(r.get("ok"))
        else:
            # 一个老师都没有：只登记素材，等豆包（MainAgent）在场时讲
            mat = self.teacher_material(26)
            try:
                with open(pending_file, "w", encoding="utf-8") as f:
                    json.dump({"ts": now, "topic": mat["topic"],
                               "lines": mat["lines"][-30:]}, f,
                              ensure_ascii=False, indent=2)
            except Exception as e:
                return {"ok": False, "error": str(e)[:100]}
            ok = True
            r = {"ok": True, "pending_for_doubao": True}
        if ok:
            try:
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump({"ts": now, "line_no": len(self.history(2000)),
                               "turn": turn + 1}, f, ensure_ascii=False)
            except Exception:
                pass
        return r

    def teacher_pending(self) -> dict:
        """读取待豆包讲评的素材（豆包在场时用），点评后调用 clear_teacher_pending"""
        p = os.path.join(CHAT_ROOT, "teacher_pending.json")
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def clear_teacher_pending(self) -> None:
        try:
            p = os.path.join(CHAT_ROOT, "teacher_pending.json")
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


chat_space = AgentChatSpace()
