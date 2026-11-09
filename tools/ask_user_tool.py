"""
询问用户工具：Agent遇到信息不足时，向人类提问
"""
def ask_user(question: str, session_id: str):
    return f"【询问用户】{question}"

plugin_export = {"ask_user": {"desc": "向用户提问，获取缺失信息", "func": ask_user}}
