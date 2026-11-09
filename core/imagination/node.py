"""
想象沙盘节点管理
节点 = 沙盘中的一个推理单元（一节点一文件），支持结构化管理
目录：sandbox/<sid>/nodes/<node_id>.md
"""
import os
import time
import uuid
from core.config_loader import config_loader
from core.logger import root_logger

SANDBOX = config_loader["sandbox"]["root_path"]


def nodes_dir(session_id: str) -> str:
    d = os.path.join(SANDBOX, session_id, "nodes")
    os.makedirs(d, exist_ok=True)
    return d


def create_node(session_id: str, title: str, content: str = "", node_type: str = "note",
                parent_id: str = None) -> str:
    """创建节点，返回 node_id"""
    node_id = str(uuid.uuid4())[:8]
    d = nodes_dir(session_id)
    fp = os.path.join(d, f"{node_id}.md")
    ts = time.time()
    header = (f"# {title}\n\n"
              f"<!-- node: {node_id} | type: {node_type} | created: {ts}"
              + (f" | parent: {parent_id}" if parent_id else "") + " -->\n\n")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(header + content)
    root_logger.info(f"沙盘节点创建 sid={session_id} node={node_id} type={node_type}")
    return node_id


def write_node(session_id: str, node_id: str, content: str) -> bool:
    """写节点内容（保留头部元信息），返回是否成功"""
    fp = node_path(session_id, node_id)
    if not fp:
        return False
    with open(fp, "r", encoding="utf-8") as f:
        old = f.read()
    # 保留 frontmatter 注释头
    head_end = old.find("-->")
    head = old[: head_end + 3] if head_end >= 0 else old.split("\n", 1)[0]
    body = content.lstrip("\n")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(head + "\n\n" + body)
    return True


def node_path(session_id: str, node_id: str) -> str:
    """节点文件路径；不存在返回空串"""
    fp = os.path.join(nodes_dir(session_id), f"{node_id}.md")
    return fp if os.path.exists(fp) else ""


def read_node(session_id: str, node_id: str) -> str:
    """读取节点正文（去掉元信息头）"""
    fp = node_path(session_id, node_id)
    if not fp:
        return ""
    with open(fp, "r", encoding="utf-8") as f:
        text = f.read()
    head_end = text.find("-->")
    return text[head_end + 3:].strip() if head_end >= 0 else text


def list_nodes(session_id: str) -> list:
    """列出全部节点：{node_id, title, type, parent, path}"""
    d = nodes_dir(session_id)
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md"):
            continue
        fp = os.path.join(d, fn)
        with open(fp, "r", encoding="utf-8") as f:
            first = f.read(600)
        node_id = fn[:-3]
        title = first.split("\n", 1)[0].lstrip("# ").strip()
        ntype = "note"
        parent = None
        if "<!-- node:" in first:
            import re
            m = re.search(r"node: (\S+) \| type: (\S+)(?: \| created: [\d.]+)?(?: \| parent: (\S+))?", first)
            if m:
                node_id = m.group(1)
                ntype = m.group(2)
                parent = m.group(3)
        out.append({"node_id": node_id, "title": title, "type": ntype,
                    "parent": parent, "path": fp})
    return out
