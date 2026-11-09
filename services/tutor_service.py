# services/tutor_service.py
"""
Tutor 教学服务：基于知识库技能/事实的软件与知识教学问答
- ask：检索 knowledge_skill → LLM 生成分步教学回答
- teach：给定主题生成教学大纲（light 模型）
"""
import os
from core.llm_client import llm_client
from services.llm_gateway import ask
from core.model_router import router
from core.logger import root_logger


class TutorService:
    def ask(self, question: str, session_id: str = "") -> str:
        """教学问答：先召回技能/事实，再生成分步讲解"""
        try:
            from services.knowledge_service import knowledge_service
            skill_hits = knowledge_service.search_skill(question, top_k=2)
            fact_hits = knowledge_service.search_knowledge(question, top_k=2,
                                                           coll="knowledge_fact")
        except Exception:
            skill_hits, fact_hits = [], []
        ctx = ""
        if skill_hits:
            ctx += "【相关技能】\n" + "\n".join(f"- {s['document'][:200]}" for s in skill_hits) + "\n"
        if fact_hits:
            ctx += "【相关事实】\n" + "\n".join(f"- {f['document'][:200]}" for f in fact_hits) + "\n"
        prompt = f"""你是耐心细致的教学 Tutor。
学生问题：{question}
{ctx or "（无知识库命中，请基于通用知识回答）"}

请用分步、易懂的方式讲解：先给结论，再分步骤解释，最后给一个动手练习建议。"""
        try:
            resp = ask([{"role": "user", "content": prompt}],
                                   temperature=0.4, model=router.route("main"))
            return resp.content if resp.success else f"教学生成失败：{resp.content}"
        except Exception as e:
            root_logger.error(f"Tutor ask 失败: {e}")
            return f"教学服务异常：{e}"

    def teach(self, topic: str, session_id: str = "") -> str:
        """生成教学大纲（light 模型）"""
        prompt = (f"为主题「{topic}」生成一份 5 节的渐进式教学大纲，每节含：目标、核心知识点、"
                  f"一个小练习。只输出 JSON：{{\"title\": \"...\", \"sections\": [{{\"goal\": \"...\", "
                  f"\"points\": [\"...\"], \"practice\": \"...\"}}]}}")
        try:
            resp = ask([{"role": "user", "content": prompt}],
                                   temperature=0.4, model=router.route("light"))
            return resp.content if resp.success else f"大纲生成失败：{resp.content}"
        except Exception as e:
            root_logger.error(f"Tutor teach 失败: {e}")
            return f"教学服务异常：{e}"


tutor_service = TutorService()
