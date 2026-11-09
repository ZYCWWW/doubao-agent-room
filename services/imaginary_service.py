"""
想象沙盘服务：Agent的草稿本
写入MD推理草稿、绘图记录、VL识别日志
写入时自动调用sandbox_index建立索引
新增：list_files、read_md 供dashboard浏览沙盘文件
P2：节点化（write_node/create_node/list_nodes）、快照/回滚/分支、逻辑校验（validator）
"""
import os
from core.config_loader import config_loader
from core.sandbox_index import index_add_file
from core.logger import root_logger

SANDBOX = config_loader["sandbox"]["root_path"]

class ImaginaryService:
    def __init__(self):
        self.sandbox_root = SANDBOX   # 实例级沙盘根（供外部追加/检索使用）

    def write_md(self, session_id: str, filename: str, content: str):
        """
        写入沙盘md文件
        :param session_id: 当前会话
        :param filename: 文件名，建议带后缀
        :param content: markdown文本
        :return: 文件完整路径
        """
        dirp = os.path.join(SANDBOX, session_id)
        os.makedirs(dirp, exist_ok=True)
        fp = os.path.join(dirp, filename)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
        # 写入索引，摘要取前180字符
        index_add_file(session_id, fp, "md", summary=content[:180], tag="draft")
        return fp

    def list_files(self, session_id: str):
        """列出当前会话沙盘全部文件"""
        dirp = os.path.join(SANDBOX, session_id)
        if not os.path.exists(dirp):
            return []
        return [os.path.join(dirp, f) for f in os.listdir(dirp)]

    def read_md(self, file_path: str):
        """读取沙盘md文本"""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # ---------- P2 节点化 ----------

    def create_node(self, session_id: str, title: str, content: str = "",
                    node_type: str = "note", parent_id: str = None) -> str:
        """创建想象空间节点，返回 node_id"""
        from core.imagination.node import create_node as _create
        return _create(session_id, title, content, node_type, parent_id)

    def write_node(self, session_id: str, node_id: str, content: str) -> bool:
        """写入节点正文（保留节点元信息头）"""
        from core.imagination.node import write_node as _write
        return _write(session_id, node_id, content)

    def read_node(self, session_id: str, node_id: str) -> str:
        """读取节点正文"""
        from core.imagination.node import read_node as _read
        return _read(session_id, node_id)

    def list_nodes(self, session_id: str) -> list:
        """列出节点摘要列表"""
        from core.imagination.node import list_nodes as _list
        return _list(session_id)

    # ---------- P2 快照 / 回滚 / 分支 ----------

    def snapshot(self, session_id: str, label: str = "") -> str:
        """创建全量快照，返回快照名"""
        from core.imagination.snapshot import snapshot as _snap
        return _snap(session_id, label)

    def list_snapshots(self, session_id: str) -> list:
        """列出快照"""
        from core.imagination.snapshot import list_snapshots as _list
        return _list(session_id)

    def rollback(self, session_id: str, snapshot_name: str) -> bool:
        """回滚到指定快照"""
        from core.imagination.snapshot import rollback as _rb
        return _rb(session_id, snapshot_name)

    def branch(self, session_id: str, snapshot_name: str, new_sid: str) -> bool:
        """从快照创建分支到新会话"""
        from core.imagination.snapshot import branch as _br
        return _br(session_id, snapshot_name, new_sid)

    # ---------- P2 逻辑校验 ----------

    def validate(self, session_id: str, defined_symbols: set = None,
                 assertions: list = None) -> dict:
        """
        对当前会话全部节点做逻辑校验
        :param assertions: 可选断言列表 [{text, source?}]，用于矛盾检测
        :return: {ok, undefined, cycle, contradictions}
        """
        from core.imagination.validator import validate as _val
        nodes = self.list_nodes(session_id)
        node_objs = []
        for nd in nodes:
            node_objs.append({
                "id": nd["node_id"],
                "title": nd["title"],
                "type": nd["type"],
                "content": self.read_node(session_id, nd["node_id"]),
            })
        return _val(session_id, node_objs, defined_symbols=defined_symbols,
                    assertions=assertions)


imaginary_service = ImaginaryService()
