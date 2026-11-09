"""
软件自学工具入口，给ReAct调度器调用
"""
from core.software_self_learn import software_learner

def software_learn_and_execute(exe_path: str, target_task: str, session_id: str):
    return software_learner.run_software_learn_flow(session_id, exe_path, target_task)

plugin_export = {"software_learn_and_execute": {"desc": "打开软件，读取帮助文档自学，成功自动保存技能", "func": software_learn_and_execute}}
