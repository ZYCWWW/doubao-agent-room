# tools/debate_tool.py
"""
辩论工具：debate_start / debate_speak / debate_extract / debate_list
（现有 llm_discuss 保留为快速版）

【2026-11-09 迁移】四个工具已搬进 plugins/installed/debate_plugin/module.py，
并且该插件自带「🗣️ 辩论现场」整页面板。本文件保留作回滚参照，
main.py 不再从这里 import。
"""
# 迁移说明见上；内容未改。
from services.debate_service import (debate_start, debate_speak,
                                     debate_extract, debate_list, debate_roles)

plugin_export = {
    "debate_start": {"desc": "发起多角色辩论（proposer/critic/constraint/synthesizer），返回 debate_id", "func": debate_start},
    "debate_speak": {"desc": "在辩论中加入一轮发言（debate_id, role, content），返回收敛分数", "func": debate_speak},
    "debate_extract": {"desc": "提取辩论的共识/分歧/开放问题清单", "func": debate_extract},
    "debate_list": {"desc": "列出辩论会话及其状态", "func": debate_list},
}
