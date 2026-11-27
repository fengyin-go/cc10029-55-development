"""集体备课领域服务：备课记录登记、签阅、存档，含跨模块触发同行评议。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LessonPrepService(BaseModuleService):
    MODULE = "lesson_prep"
    ENTITY = "备课记录"
    OPERATION_TERM = "签阅记录"
    LIST_FIELDS = ["备课章节", "主备教师", "参与教师", "备课日期", "研讨要点", "教学资源", "课后反思", "备注说明"]
    REQUIRED_FIELDS = ["备课章节", "主备教师", "参与教师"]
    STATUS_ORDER = ["草稿", "待签阅", "已签阅", "已存档"]
    ACTION_RULES = {"签阅记录": "已签阅", "补充意见": "草稿", "存档记录": "已存档"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含跨模块联动：签阅后可触发同行评议。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 签阅记录的幂等保护
        if action == "签阅记录":
            if entry.get("status") == "已签阅":
                return None, f"{self.ENTITY} {entry_id} 已是已签阅状态，无需重复签阅"

        result_entry, message = super().run_action(entry_id, action)

        # 签阅完成后，检查是否需要触发同行评议
        if result_entry and action == "签阅记录":
            reflection = entry.get("课后反思", "")
            teacher = entry.get("主备教师", "")
            if reflection and ("改进" in str(reflection) or "评议" in str(reflection)):
                self._maybe_create_peer_review(teacher, entry_id)

        return result_entry, message

    def _maybe_create_peer_review(self, teacher: str, prep_id: int) -> None:
        """根据备课课后反思创建同行评议请求。"""
        review_rows = store.rows("peer_review")
        # 幂等检查
        for row in review_rows:
            existing_source = str(row.get("评议意见", ""))
            if f"lesson_prep:{prep_id}" in existing_source:
                return

        new_id = max((int(r.get("id", 0)) for r in review_rows), default=0) + 1
        review_rows.append({
            "id": new_id,
            "被评教师": teacher,
            "评议维度": "备课教学质量",
            "得分": "",
            "评议意见": f"由备课记录 #{prep_id} 课后反思触发 [lesson_prep:{prep_id}]",
            "评议人员": "",
            "评议日期": "",
            "是否匿名": "否",
            "备注说明": "备课签阅后自动生成评议请求",
            "status": "待汇总",
            "pending": True,
            "abnormal": False,
        })