"""同行评议领域服务：评议登记、汇总、公示，含跨模块触发反馈和学期目标关联。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class PeerReviewService(BaseModuleService):
    MODULE = "peer_review"
    ENTITY = "评议表"
    OPERATION_TERM = "汇总评议"
    LIST_FIELDS = ["被评教师", "评议维度", "得分", "评议意见", "评议人员", "评议日期", "是否匿名", "备注说明"]
    REQUIRED_FIELDS = ["被评教师", "评议维度", "得分"]
    STATUS_ORDER = ["草稿", "待汇总", "已汇总", "已公示"]
    ACTION_RULES = {"汇总评议": "已汇总", "退回评议": "草稿", "公示结果": "已公示"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含跨模块联动：汇总评议可关联学期目标。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 汇总评议的状态约束
        if action == "汇总评议":
            if entry.get("status") not in ("待汇总", "草稿"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可汇总"

        # 公示结果：必须先汇总
        if action == "公示结果":
            if entry.get("status") != "已汇总":
                return None, f"{self.ENTITY} {entry_id} 必须先汇总评议，才能公示"

        result_entry, message = super().run_action(entry_id, action)

        # 评议汇总后，检查是否需要触发教学反馈
        if result_entry and action == "汇总评议":
            teacher = entry.get("被评教师", "")
            dimension = entry.get("评议维度", "")
            opinion = entry.get("评议意见", "")
            # 如果评议意见含有"改进"关键词，自动生成教学反馈
            if opinion and ("改进" in str(opinion) or "不足" in str(opinion)):
                self._maybe_create_feedback(teacher, dimension, opinion, entry_id)

        return result_entry, message

    def _maybe_create_feedback(self, teacher: str, dimension: str, opinion: str, review_id: int) -> None:
        """根据评议结果创建教学反馈：如果评议指出不足，生成反馈单。"""
        feedback_rows = store.rows("feedback")
        # 检查是否已有相同内容的反馈（幂等保护）
        for row in feedback_rows:
            existing_source = str(row.get("反馈来源", ""))
            if existing_source == f"peer_review:{review_id}":
                return  # 已存在，不重复创建

        new_id = max((int(r.get("id", 0)) for r in feedback_rows), default=0) + 1
        feedback_rows.append({
            "id": new_id,
            "反馈对象": teacher,
            "反馈要点": f"同行评议中{dimension}维度反馈",
            "改进建议": opinion,
            "反馈来源": f"peer_review:{review_id}",
            "接收教师": teacher,
            "反馈日期": "",
            "处理期限": "",
            "备注说明": "由评议系统自动生成",
            "status": "待签收",
            "pending": True,
            "abnormal": False,
        })