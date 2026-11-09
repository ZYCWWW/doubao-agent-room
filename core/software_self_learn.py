"""
软件自学主流程：
1. ACL校验是否允许启动软件
2. 加入审批队列等待人工确认
3. 启动软件，F1唤起帮助文档
4. VL识别帮助界面
5. LLM生成操作方案
6. 任务成功后调用skill_extractor提取技能入库
7. 失败仅写入沙盘日志，**不生成技能**
"""
import time
from core.logger import root_logger, audit
from core.vision_acl_mgr import vision_acl_mgr
from tools.vl_vision_tool import vl_analyze_image
from tools.desktop_control_tool import desktop_action
from core.screen_capture import capture_screen
from services.imaginary_service import imaginary_service
from core.skill_extractor import skill_extractor
from core.llm_client import llm_client
from services.llm_gateway import ask

class SoftwareSelfLearner:
    def run_software_learn_flow(self, session_id: str, exe_path: str, target_task: str):
        """
        :param session_id: 会话ID
        :param exe_path: 软件exe绝对路径
        :param target_task: 需要学会的目标任务
        :return: dict 结果包
        """
        permit, reason = vision_acl_mgr.check_action_permit(exe_path, "launch_app")
        if not permit:
            return {"ok": False, "log": f"❌ ACL拦截：{reason}", "task_success": False}
        audit(f"SOFTWARE_LEARN | sid={session_id} | exe={exe_path} | task={target_task}")
        log_lines = [f"# 软件自学日志 | {exe_path} | 目标任务：{target_task}"]
        log_lines.append("## 阶段1：申请启动软件")
        launch_res = desktop_action("open_file", {"file_path": exe_path}, session_id)
        log_lines.append(f"启动请求返回：{launch_res}")
        # 只有成功入队（✅）才等待审批；被ACL拒绝时同样停止流程
        if "✅" not in launch_res:
            log_lines.append("⏸️ 启动请求被拒绝，流程暂停")
            imaginary_service.write_md(session_id, f"software_learn_log_{int(time.time())}", "\n".join(log_lines))
            return {"ok": False, "log": "\n".join(log_lines), "task_success": False}
        time.sleep(3.0)
        log_lines.append("## 阶段2：尝试F1唤起帮助文档")
        f1_req = desktop_action("type_text", {"text": "{F1}"}, session_id)
        log_lines.append(f"F1按键请求：{f1_req}")
        time.sleep(2.0)
        help_img = capture_screen(session_id)
        vl_help = vl_analyze_image(help_img, session_id, "识别界面，判断是否是帮助文档，提取功能、按钮、快捷键")
        log_lines.append(f"VL帮助识别结果：\n{vl_help}")
        plan_prompt = f"""基于这份帮助文档，为任务【{target_task}】生成可执行键鼠操作方案；信息不足则标记【帮助不足】"""
        resp = ask([{"role":"user","content":plan_prompt}], temperature=0.2)
        if not resp.success:
            log_lines.append("❌ LLM生成操作方案失败")
            plan = None
            task_success = False
        else:
            plan = resp.content
            log_lines.append(f"✅ 生成操作方案：\n{plan}")
            task_success = True
        log_lines.append("## 阶段3：方案生成结束")
        full_log = "\n".join(log_lines)
        imaginary_service.write_md(session_id, f"software_learn_log_{int(time.time())}", full_log)
        # 只有任务成功，才提取技能入库
        if task_success:
            extract_res = skill_extractor.extract_and_save_skill(session_id, exe_path, full_log)
            if extract_res["ok"]:
                log_lines.append(f"✅ 技能入库成功: {extract_res['skill']['skill_name']}, kb_id={extract_res['kb_id']}")
        return {"ok": True, "log": full_log, "help_vl": vl_help, "plan": plan, "task_success": task_success}

software_learner = SoftwareSelfLearner()
