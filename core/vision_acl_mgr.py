"""
ACL权限管理器：白名单路径管控
所有文件读写、启动软件、扫描文件夹都必须先经过这里校验
默认拒绝模式，仅白名单内路径允许操作
"""
import copy
import json
import os
from pathlib import Path
from core.logger import root_logger

# ACL配置文件固定位置：<项目根>/config/vision_acl.json
ACL_PATH = Path(__file__).resolve().parent.parent / "config" / "vision_acl.json"
DEFAULT_CFG = {
    "enable_acl": True,
    "default_deny": True,
    "whitelist": [],
    "rules": {
        "allow_read_file": True,
        "allow_write_file": False,
        "allow_launch_app": True,
        "allow_mouse_keyboard": False,
        "allow_folder_scan": True
    },
    # P2 动作级：路径/应用 → 允许的动作集合（如 {"/path/to/app": ["read_file", "launch_app", "mouse_keyboard"]}）
    "path_actions": {},
    "max_scan_depth": 2,
    # 视觉工作台：能进舞台的软件（对应 config/software_registry.json 里的登记名）
    "workspace": {
        "enabled": True,
        "allow_software": []
    }
}

class VisionACLManager:
    def __init__(self):
        self.cfg = {}
        self.load()

    def load(self):
        """加载acl配置，文件不存在自动生成默认配置"""
        if not ACL_PATH.exists():
            ACL_PATH.parent.mkdir(exist_ok=True)
            self.cfg = copy.deepcopy(DEFAULT_CFG)
            self.save()
        else:
            with open(ACL_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            # 按默认深合并：老文件里没有的段（如 workspace）自动补齐，
            # 否则新能力会因为旧文件缺键而静默失效
            merged = copy.deepcopy(DEFAULT_CFG)
            for k, v in cfg.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            self.cfg = merged
        root_logger.info("✅ ACL权限配置加载完成")

    def save(self):
        """保存acl配置到json文件"""
        with open(ACL_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def add_path(self, path: str):
        """添加白名单路径，自动resolve绝对路径"""
        p = str(Path(path).resolve())
        if p not in self.cfg["whitelist"]:
            self.cfg["whitelist"].append(p)
            self.save()
        return p

    def remove_path(self, path: str):
        """移除白名单路径"""
        p = str(Path(path).resolve())
        if p in self.cfg["whitelist"]:
            self.cfg["whitelist"].remove(p)
            self.save()

    def check_path_in_whitelist(self, target_path: str) -> bool:
        """判断目标路径是否在白名单内（子目录也匹配）"""
        if not self.cfg.get("enable_acl", True):
            return True
        target = str(Path(target_path).resolve())
        for w in self.cfg["whitelist"]:
            w_res = str(Path(w).resolve())
            if target.startswith(w_res):
                return True
        return False

    def get_rule(self, rule_name: str) -> bool:
        """读取单项开关规则"""
        return self.cfg.get("rules", {}).get(rule_name, False)

    def set_rule(self, rule_name: str, value: bool) -> None:
        """修改单项开关规则并落盘（面板用；ACL json 是唯一权威）"""
        self.cfg.setdefault("rules", {})[rule_name] = bool(value)
        self.save()

    def rules(self) -> dict:
        """当前规则快照"""
        return dict(self.cfg.get("rules", {}))

    # ---------- P2 动作级权限 ----------

    def add_path_action(self, path: str, actions: list) -> dict:
        """给路径/应用配置允许的动作集合（动作级白名单）"""
        p = str(Path(path).resolve())
        acts = list(dict.fromkeys(actions))  # 去重保序
        self.cfg.setdefault("path_actions", {})[p] = acts
        self.save()
        return {"path": p, "actions": acts}

    def remove_path_action(self, path: str, actions: list = None) -> bool:
        """移除路径的动作配置；actions=None 移除全部"""
        p = str(Path(path).resolve())
        pa = self.cfg.setdefault("path_actions", {})
        if p not in pa:
            return False
        if actions is None:
            del pa[p]
        else:
            pa[p] = [a for a in pa[p] if a not in actions]
            if not pa[p]:
                del pa[p]
        self.save()
        return True

    def get_path_actions(self, path: str) -> list:
        """读取路径的动作级配置（含父目录匹配）"""
        target = str(Path(path).resolve())
        pa = self.cfg.get("path_actions", {})
        for w, acts in pa.items():
            if target.startswith(w):
                return list(acts)
        return []

    def _action_rule_map(self) -> dict:
        return {
            "read_file": "allow_read_file",
            "write_file": "allow_write_file",
            "launch_app": "allow_launch_app",
            "mouse_keyboard": "allow_mouse_keyboard",
            "scan_folder": "allow_folder_scan"
        }

    def check_action_permit(self, target_path: str, action_type: str) -> tuple[bool, str]:
        """
        完整权限校验（P2 动作级优先，其次全局规则）
        :param target_path: 目标文件/目录
        :param action_type: read_file / write_file / launch_app / mouse_keyboard / scan_folder
        :return: (是否允许, 原因文本)
        """
        if not self.cfg.get("enable_acl", True):
            return True, "ACL已全局关闭"
        if not os.path.exists(target_path):
            return False, f"路径不存在：{target_path}"
        if not self.check_path_in_whitelist(target_path):
            return False, f"路径不在白名单：{target_path}"
        rule_map = self._action_rule_map()
        rule_key = rule_map.get(action_type)
        if rule_key is None:
            return False, "未知操作类型"
        # 动作级优先：路径配置了动作集合 → 按集合判定
        path_acts = self.get_path_actions(target_path)
        if path_acts:
            if action_type in path_acts:
                return True, f"动作级放行（{action_type}）"
            return False, f"动作级拒绝：{target_path} 未允许 {action_type}（允许：{path_acts}）"
        # 回退全局规则
        if self.get_rule(rule_key):
            return True, "权限校验通过"
        return False, f"规则拒绝：{rule_key}"

    # ---------- 视觉工作台 ----------

    def workspace_cfg(self) -> dict:
        """工作台放行配置（面板第 5 页可直接改）"""
        return dict(self.cfg.get("workspace", {}))

    def set_workspace_allow(self, names: list) -> dict:
        ws = self.cfg.setdefault("workspace", {"enabled": True, "allow_software": []})
        ws["allow_software"] = list(dict.fromkeys(str(n) for n in names))
        self.save()
        return dict(ws)

    def check_workspace_software(self, name: str) -> tuple[bool, str]:
        """软件能不能进舞台：只有放行名单里的软件才允许启动/嵌入。

        名单为空表示未配置，按放行处理（面板默认如此），一旦填了就只认名单。
        """
        if not self.cfg.get("enable_acl", True):
            return True, "ACL已全局关闭"
        ws = self.cfg.get("workspace", {})
        if not ws.get("enabled", True):
            return True, "工作台权限未启用"
        allow = ws.get("allow_software", [])
        if not allow:
            return True, "工作台未配置放行名单，默认放行"
        if str(name) in allow:
            return True, f"工作台放行：{name}"
        return False, f"「{name}」不在工作台放行名单：{allow}"


vision_acl_mgr = VisionACLManager()
