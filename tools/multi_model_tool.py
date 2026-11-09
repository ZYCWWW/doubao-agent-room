# tools/multi_model_tool.py
"""多模型协同讨论工具：不同角色由不同 Ollama 模型真实发言"""
from services.multi_model_discuss import multi_model_discuss

plugin_export = {
    "multi_model_discuss": {
        "desc": "多模型协同讨论：proposer/critic/constraint_checker/synthesizer 四角色分别由不同模型真实发言，返回共识/分歧/开放问题",
        "func": multi_model_discuss,
    }
}
