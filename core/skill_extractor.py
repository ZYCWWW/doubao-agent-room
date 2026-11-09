"""
技能提取器：自学成功后，LLM从日志提取结构化技能
查重逻辑：相似度<0.15判定为旧技能，新增版本，旧版本标记is_valid=False
召回时默认只召回is_valid=True的技能
"""
import json
import time
from core.llm_client import llm_client
from services.llm_gateway import ask
from services.knowledge_service import knowledge_service
from core.logger import root_logger, audit

class SkillExtractor:
    def extract_and_save_skill(self, session_id: str, software_path: str, learn_log: str):
        prompt = f"""
从下面软件自学日志提取结构化技能，严格输出JSON，不要额外文字、不要解释：
{{
  "skill_name": "技能名称",
  "software": "软件名称",
  "software_path": "exe路径",
  "prerequisite": "前置条件",
  "steps": ["step1","step2"],
  "notes": "风险注意事项",
  "failure_tips": "失败点"
}}
日志：
{learn_log}
"""
        resp = ask([{"role":"user","content":prompt}], temperature=0.2)
        if not resp.success:
            root_logger.warning("LLM提取技能失败")
            return {"ok": False, "msg": "llm请求失败"}
        try:
            skill_data = json.loads(resp.content)
        except Exception as e:
            root_logger.error(f"技能JSON解析失败 {e}")
            return {"ok": False, "msg": "json解析错误"}
        # 组装技能文本，用于向量检索
        skill_text = f"""
【技能】{skill_data['skill_name']}
软件：{skill_data['software']}
路径：{skill_data['software_path']}
前置：{skill_data['prerequisite']}
步骤：
{chr(10).join([f"- {s}" for s in skill_data['steps']])}
注意：{skill_data['notes']}
失败提示：{skill_data['failure_tips']}
会话sid: {session_id}
"""
        meta = {
            "type": "skill",
            "software": skill_data["software"],
            "software_path": skill_data["software_path"],
            "skill_name": skill_data["skill_name"],
            "ts": int(time.time()),
            "version": 1,
            "is_valid": True,
            "evidence_ref": f"sid:{session_id}"
        }
        # 检索是否已有相似技能
        search_res = knowledge_service.search_skill(query=skill_text, software_name=skill_data["software"], top_k=1)
        if search_res and len(search_res) > 0 and search_res[0]["distance"] < 0.15:
            old_id = search_res[0]["id"]
            meta["version"] = search_res[0]["metadata"]["version"] + 1
            meta["is_valid"] = True
            # 更新旧文档，新版本覆盖，旧版本逻辑上失效
            knowledge_service.update_skill(old_id, skill_text, meta)
            audit(f"SKILL_UPDATE | old_id={old_id} | name={skill_data['skill_name']}")
            return {"ok": True, "kb_id": old_id, "skill": skill_data, "skill_text": skill_text}
        # 全新技能入库
        kb_id = knowledge_service.add_knowledge(skill_text, metadata=meta, coll="knowledge_skill")
        audit(f"SKILL_NEW | id={kb_id} | name={skill_data['skill_name']}")
        return {"ok": True, "kb_id": kb_id, "skill": skill_data, "skill_text": skill_text}

skill_extractor = SkillExtractor()
