"""
视觉VL工具：调用qwen2.5vl:7b分析截图/图片
强制约束：无法识别路径时禁止编造路径
P2：集成置信度三信号（可选双采样）
"""
import base64
import time
import requests
from core.config_loader import config_loader
from core.logger import root_logger
from core.vision_acl_mgr import vision_acl_mgr
from core.vision.confidence import evaluate_confidence, route_by_confidence

VISION_MODEL = config_loader["ollama"]["vision_model"]
URL = config_loader["ollama"]["chat_url"]
TIMEOUT = config_loader["ollama"].get("vision_timeout") or config_loader["ollama"]["timeout"]
PS_URL = URL.replace("/api/chat", "/api/ps")
WARM_TIMEOUT = 90          # 模型已在显存里时的读超时

def image_to_base64(img_path: str):
    """图片转base64，给ollama vl接口使用"""
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def _model_loaded() -> bool:
    """视觉模型是否已在显存里。

    查不到 /api/ps 就按「没加载」处理（退回长超时），宁可慢也不误判。
    """
    try:
        data = requests.get(PS_URL, timeout=3).json()
        names = {(m.get("name") or m.get("model") or "").lower()
                 for m in (data.get("models") or [])}
        return VISION_MODEL.lower() in names
    except Exception:
        return False

def _vl_call(image_path: str, task_prompt: str) -> str:
    """单次 VL 调用，返回识别文本

    超时按「模型是否已在显存里」分级：冷加载确实要几十秒到几分钟，沿用
    vision_timeout（默认 300s）；模型已加载还卡 300s 才是真问题，
    所以热态降到 WARM_TIMEOUT，超时快速失败而不是把调用方挂五分钟。
    """
    b64 = image_to_base64(image_path)
    prompt = task_prompt + "\n> 重要约束：无法确认真实路径时，禁止编造、虚构路径。"
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "user", "content": prompt, "images": [b64]}
        ],
        "stream": False
    }
    warm = _model_loaded()
    timeout = min(TIMEOUT, WARM_TIMEOUT) if warm else TIMEOUT
    t0 = time.time()
    try:
        resp = requests.post(URL, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
    finally:
        root_logger.info(f"VL {'热态' if warm else '冷加载'} timeout={timeout}s "
                         f"用时={time.time() - t0:.1f}s model={VISION_MODEL}")
    return content

def vl_analyze_image(image_path: str, session_id: str, task_prompt: str,
                     double_sample: bool = False, **kwargs):
    """
    视觉分析图片
    :param image_path: 图片路径
    :param session_id: 会话id
    :param task_prompt: 识别指令
    :param double_sample: True 时做两次独立采样并输出置信度评估
    """
    try:
        content = _vl_call(image_path, task_prompt)
    except requests.exceptions.ReadTimeout:
        root_logger.error("VL 读超时：视觉模型可能还在冷加载")
        return (f"VL识别超时：视觉模型可能还在冷加载（首次加载要几十秒到几分钟）。"
                f"图片已保存：{image_path}；隔一会儿再调一次即可，不必重新截图。")
    except Exception as e:
        root_logger.error(f"VL调用失败 {e}")
        return f"VL识别失败：{str(e)}"

    result = f"""【VL识别结果】
图片路径：{image_path}
识别任务：{task_prompt}
识别输出：
{content}
"""
    if not double_sample:
        return result

    # 双采样：换一种措辞再问一次，评估置信度
    try:
        second = _vl_call(image_path, task_prompt + "（请再次独立观察，用不同措辞描述）")
        conf = evaluate_confidence(content, task_prompt, second_sample=second,
                                   image_path=image_path)
        result += f"\n【置信度】level={conf['level']} score={conf['score']} signals={conf['signals']} reason={conf['reason']}\n"
        result += f"【处理建议】{route_by_confidence(conf['level'])}\n"
    except Exception as e:
        root_logger.warning(f"置信度评估失败: {e}")
    return result

plugin_export = {
    "vl_analyze_image": {"desc": "VL视觉识别截图、界面图标、文字；double_sample=True 时双采样输出置信度", "func": vl_analyze_image}
}
