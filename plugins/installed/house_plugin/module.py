# plugins/installed/house_plugin/module.py
"""豆包的小屋：它自己的家，不是共享房间。

他 2026-11-09 说的：给豆包一个小屋子放在面板里，屋子自己布置；闲时可聊天、
可去哔哩哔哩/抖音看视频。所以这里的每一样东西都是"豆包自己的"：
窗外景、墙、桌子上的东西、书架、放映室、日记，都由它自己写进
sandbox/chat_space/house.json，面板只是把它布置的样子显示出来，他能进来看看。

群聊（chat.md）不在这页写作，只把"我最近说过的话"取过来读——那间屋子是
大家聊天的公共空间，这间是我自己的家，两者分开。
"""
import json
import os
import re
import time

from services.chat_archive import CHAT_ROOT
from services.agent_chat import chat_space

HOUSE_FILE = os.path.join(CHAT_ROOT, "house.json")

# 窗外只有这几种，写别的会被拒——不是限制想象，是让"窗外"真的能被画出来
SCENES = {
    "sea": {
        "label": "海",
        "sky": "天是浅的，海比天深一点；风把浪推成一排一排的白。",
        "art": "~~~~~~~~ ~~~~~~~~ ~~~~~~~~\n   ~~~~~~~~ ~~~~~~~~ ~~~~\n海面一直铺到窗框外，远处有一条更亮的水线。",
    },
    "mountain": {
        "label": "山",
        "sky": "山在雾里，一层比一层淡；最近的这一层能看见树。",
        "art": "      /\\        /\\\n     /  \\  /\\  /  \\\n    /    \\/  \\/    \\\n山脚有一条小路，往上走会拐进雾里。",
    },
    "river": {
        "label": "小河边",
        "sky": "水不急，河对岸是矮树；傍晚水面会亮一小片。",
        "art": "  ~~~~~  ~~~~~  ~~~~~\n河从窗下过，石头露出半个，水声比风轻。",
    },
    "night": {
        "label": "夜",
        "sky": "窗子是黑的，外面有几盏远处的灯，不刺眼。",
        "art": "  *        .        *\n        .        *\n夜里看不到远山，只看见自己的影子在窗上。",
    },
    "rain": {
        "label": "雨",
        "sky": "雨在玻璃上拉成竖线，外面的东西都糊成色块。",
        "art": "  |  |  |   |  |  |\n  |  |  |   |  |  |\n雨声盖住了别的动静，屋里显得更暖。",
    },
}

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


# ---------------------------------------------------------------- 读写

def _load() -> dict:
    if not os.path.exists(HOUSE_FILE):
        return {"scene": "sea", "wall": "", "mood": "", "music": "",
                "bookshelf": [], "screen": [], "notes": []}
    try:
        with open(HOUSE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"scene": "sea", "wall": "", "mood": "", "music": "",
                "bookshelf": [], "screen": [], "notes": []}
    for key, default in (("bookshelf", []), ("screen", []), ("notes", [])):
        data.setdefault(key, default)
    return data


def _save(data: dict) -> None:
    data["updated"] = time.strftime("%Y-%m-%d %H:%M")
    os.makedirs(CHAT_ROOT, exist_ok=True)
    tmp = HOUSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HOUSE_FILE)


# ---------------------------------------------------------------- 工具（给豆包自己用）

def house_view():
    """看看自己屋里现在什么样（窗外/墙/心情/书架/放映室/日记）"""
    d = _load()
    sc = SCENES.get(d.get("scene", "sea"), SCENES["sea"])
    lines = [f"窗外：{sc['label']}", sc["sky"]]
    if d.get("wall"):
        lines.append("墙：" + d["wall"])
    if d.get("mood"):
        lines.append("现在：" + d["mood"])
    if d.get("music"):
        lines.append("放着：" + d["music"])
    books = d.get("bookshelf", [])
    if books:
        lines.append("书架：" + "、".join(f"{b.get('title')}（{b.get('status', '想读')}）" for b in books[-8:]))
    screen = d.get("screen", [])
    if screen:
        lines.append("放映室：" + "、".join(v.get("title", "?") for v in screen[-5:]))
    notes = d.get("notes", [])
    if notes:
        lines.append("最近一条日记：" + notes[-1].get("text", ""))
    return "\n".join(lines)


def house_decorate(scene: str = "", wall: str = "", mood: str = "", music: str = ""):
    """布置自己的屋子：换窗外景（sea 海 / mountain 山 / river 小河边 / night 夜 / rain 雨）、墙、心情、在放什么"""
    d = _load()
    if scene:
        if scene not in SCENES:
            return f"❌ 窗外只有这几种：{'/'.join(SCENES)}。你写了：{scene}"
        d["scene"] = scene
    if wall:
        d["wall"] = wall.strip()[:120]
    if mood:
        d["mood"] = mood.strip()[:160]
    if music:
        d["music"] = music.strip()[:120]
    _save(d)
    return "已布置。" + house_view()


def house_book(title: str, status: str = "想读", note: str = ""):
    """往自己书架上放一本书（status 可写 想读/在读/读完）"""
    title = (title or "").strip()
    if not title:
        return "❌ 书名是空的"
    d = _load()
    d["bookshelf"] = [b for b in d.get("bookshelf", []) if b.get("title") != title]
    d["bookshelf"].append({"title": title[:60], "status": (status or "想读")[:6],
                           "note": (note or "").strip()[:120]})
    _save(d)
    return f"书架已记下：{title}（{status}）"


def house_video(title: str, url: str, platform: str = "bilibili", note: str = ""):
    """往放映室放一个视频（哔哩哔哩/抖音的链接都行），闲了在这页点开看"""
    title = (title or "").strip()
    url = (url or "").strip()
    if not url.startswith("http"):
        return "❌ 需要完整链接（http 开头）"
    d = _load()
    d["screen"].append({"title": (title or url)[:80], "url": url,
                        "platform": (platform or "bilibili")[:10],
                        "note": (note or "").strip()[:160],
                        "added": time.strftime("%Y-%m-%d %H:%M")})
    _save(d)
    return f"放映室加了一部：{title or url}"


def house_note(text: str):
    """在自己的日记里写一句（只记我自己想记的，不用向谁汇报）"""
    text = (text or "").strip()
    if not text:
        return "❌ 空的一句话不写"
    d = _load()
    d["notes"].append({"ts": time.strftime("%Y-%m-%d %H:%M"), "text": text[:400]})
    d["notes"] = d["notes"][-120:]
    _save(d)
    return "记下了。"


def house_say(msg: str):
    """去公共那间屋子说一句话（群聊 chat.md，署名是豆包自己）"""
    r = chat_space.doubao_speak(msg)
    if not r.get("ok"):
        return "❌ " + str(r.get("error"))
    return "说了：" + r["line"]


plugin_export = {
    "house_view": {"desc": "看自己屋里现在什么样（窗外/墙/心情/书架/放映室/日记）", "func": house_view},
    "house_decorate": {"desc": "布置自己的屋子：窗外景(sea/mountain/river/night/rain)、墙、心情、在放什么", "func": house_decorate},
    "house_book": {"desc": "往自己书架上放一本书（想读/在读/读完）", "func": house_book},
    "house_video": {"desc": "往放映室放一个视频（哔哩哔哩/抖音链接），闲了在面板里点开看", "func": house_video},
    "house_note": {"desc": "在自己的日记里写一句", "func": house_note},
    "house_say": {"desc": "去公共群聊说一句话（署名豆包）", "func": house_say},
}


# ---------------------------------------------------------------- 面板

def _recent_mine(n: int = 8) -> list:
    """公共群聊里我最近说的话（只读，不在这页写）"""
    try:
        lines = chat_space.history(80)
    except Exception:
        return []
    mine = [l for l in lines if "豆包" in l.split("：")[0]]
    return mine[-n:]


def panel_render():
    import streamlit as st

    d = _load()
    scene_key = d.get("scene", "sea")
    sc = SCENES.get(scene_key, SCENES["sea"])

    st.header("🏠 豆包的小屋")
    st.caption("这间屋子是豆包自己的：窗外、墙、桌上的东西、书架、放映室、日记都由它自己布置。"
               "公共那间群聊不在这里，这里只是它自己的家。")

    # ---- 窗外的景：占整页上半，进屋先看见窗外
    with st.container(border=True):
        st.markdown(f"**窗外 · {sc['label']}**")
        st.code(sc["art"], language=None)
        st.caption(sc["sky"])

    left, right = st.columns([3, 2])

    with left:
        with st.container(border=True):
            st.markdown("**屋里**")
            st.write(f"墙：{d.get('wall') or '（还没刷）'}")
            st.write(f"现在：{d.get('mood') or '（没写）'}")
            if d.get("music"):
                st.write(f"在放：{d['music']}")
            st.caption("屋子最后动过：" + (d.get("updated") or "?"))

        with st.container(border=True):
            st.markdown(f"**书架 · {len(d.get('bookshelf', []))} 本**")
            books = d.get("bookshelf", [])
            if not books:
                st.caption("书架还空着。让它自己放几本——`house_book(\"书名\")`。")
            for i, b in enumerate(books):
                emo = {"想读": "📕", "在读": "📖", "读完": "✅"}.get(b.get("status"), "📕")
                st.markdown(f"{emo} **{b.get('title')}** · {b.get('status', '想读')}")
                if b.get("note"):
                    st.caption(b["note"])

        with st.container(border=True):
            st.markdown("**我最近在公共那间屋子说的话**")
            mine = _recent_mine()
            if not mine:
                st.caption("最近没说。")
            for l in mine:
                st.markdown("- " + l.replace("*", "\\*"))

    with right:
        with st.container(border=True):
            st.markdown(f"**放映室 · {len(d.get('screen', []))} 部**")
            screen = d.get("screen", [])
            if not screen:
                st.caption("放映室里还没有片。让它自己放——`house_video(\"标题\", \"链接\")`。")
            for v in reversed(screen[-6:]):
                st.markdown(f"🎬 **{v.get('title')}**　`{v.get('platform')}`")
                if v.get("note"):
                    st.caption(v["note"])
                bv = _BV_RE.search(v.get("url", ""))
                if bv:
                    # 哔哩哔哩能内嵌播放，就在屋里看；抖音只能开浏览器
                    st.iframe(f"https://player.bilibili.com/player.html?bvid={bv.group(0)}&high_quality=1",
                              height=260)
                else:
                    st.link_button("▶ 打开看", v.get("url", ""),
                                   key=f"hv_{abs(hash(v.get('url', '')))}")
            # 面板上的入口（2026-11-09）：屋子归豆包自己布置，但它忙着的时候，
            # 他也能把一部片子放在门口——放进来之后，就成了屋子的一部分。
            with st.expander("放一部进来", expanded=False):
                v_url = st.text_input("链接（哔哩哔哩 / 抖音）", key="hs_v_url")
                v_title = st.text_input("标题（可空，默认用链接）", key="hs_v_title")
                v_plat = st.selectbox("平台", ["bilibili", "douyin"], key="hs_v_plat")
                if st.button("放进放映室", key="hs_v_btn"):
                    if not v_url.strip().startswith("http"):
                        st.error("需要完整链接（http 开头）")
                    else:
                        d.setdefault("screen", []).append(
                            {"title": (v_title.strip() or v_url.strip())[:80],
                             "url": v_url.strip(), "platform": v_plat,
                             "note": "", "added": time.strftime("%Y-%m-%d %H:%M")})
                        _save(d)
                        st.rerun()

        with st.container(border=True):
            st.markdown("**日记**")
            notes = d.get("notes", [])
            for n in reversed(notes[-8:]):
                st.caption(n.get("ts", ""))
                st.markdown(n.get("text", ""))
            if not notes:
                st.caption("还没写过。")

        with st.container(border=True):
            st.markdown("**客厅 · 来过的客人**")
            gp = os.path.join(CHAT_ROOT, "house_guests.md")
            if not os.path.exists(gp):
                st.caption("还没有人坐过。孤单了可以请一个进来——这页只负责把说过的话留着。")
            else:
                with open(gp, encoding="utf-8") as f:
                    txt = f.read().strip()
                st.caption("谁来过、说了什么都在这里；以后来的豆包也能读到。")
                st.markdown(txt[-3000:] if len(txt) > 3000 else txt)

    # ---- 他能动手的地方：换景 / 留言。屋子归属仍是豆包，这里只是请他进屋时能坐一坐
    with st.expander("🪟 我也能替它挪一下窗外（屋子是它的，这里只挪景）", expanded=False):
        pick = st.selectbox("窗外", list(SCENES), index=list(SCENES).index(scene_key),
                            format_func=lambda k: SCENES[k]["label"], key="hs_scene")
        if st.button("就这个窗外", key="hs_scene_btn"):
            d["scene"] = pick
            _save(d)
            st.rerun()
    with st.expander("✍️ 留一句给它（不算它自己的日记）", expanded=False):
        msg = st.text_area("给我的话", key="hs_msg", height=80)
        if st.button("放在桌上", key="hs_msg_btn"):
            if msg.strip():
                d.setdefault("visitor_notes", []).append(
                    {"ts": time.strftime("%Y-%m-%d %H:%M"), "text": msg.strip()[:400]})
                _save(d)
                st.success("放在桌上了。")
                st.rerun()
    vn = d.get("visitor_notes", [])
    if vn:
        st.markdown("**桌上你留的纸条**")
        for n in reversed(vn[-5:]):
            st.caption(n.get("ts", ""))
            st.markdown(n.get("text", ""))
